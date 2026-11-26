import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import codex_acp_runtime as state
import codex_app_server_runtime as runtime
import provider_runtime
import app as morrow_app


STABLE = "Persistent conversation identity"


class _FakeAppServer:
    def __init__(self, *, live=(), resume_error=False, started_thread="thread-new"):
        self.live = set(live)
        self.resume_error = resume_error
        self.started_thread = started_thread
        self.calls = []
        self.notifications = queue.Queue()

    def start(self):
        return None

    def drain_notifications(self):
        while not self.notifications.empty():
            self.notifications.get_nowait()

    def has_live_thread(self, thread_id):
        return thread_id in self.live

    def mark_live_thread(self, thread_id):
        self.live.add(thread_id)

    def request(self, method, params=None, *, timeout=30):
        params = params or {}
        self.calls.append((method, params))
        if method == "thread/resume":
            if self.resume_error:
                raise RuntimeError("missing remote thread")
            return {"thread": {"id": params["threadId"]}}
        if method == "thread/start":
            return {"thread": {"id": self.started_thread}}
        if method == "turn/start":
            thread_id = params["threadId"]
            self.notifications.put({
                "method": "item/agentMessage/delta",
                "params": {"threadId": thread_id, "turnId": "turn-1", "delta": "ok"},
            })
            self.notifications.put({
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            })
            return {"turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    def next_notification(self, timeout=0.1):
        try:
            return self.notifications.get_nowait()
        except queue.Empty:
            return None


class CodexAppServerRuntimeTest(unittest.TestCase):
    def _db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE sessions (id INTEGER PRIMARY KEY);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attachments TEXT NOT NULL DEFAULT '[]',
                reasoning TEXT NOT NULL DEFAULT '',
                reasoning_ms INTEGER,
                versions TEXT,
                active INTEGER NOT NULL DEFAULT 0,
                hidden INTEGER NOT NULL DEFAULT 0,
                proactive INTEGER NOT NULL DEFAULT 0,
                tool_log TEXT,
                created_at TEXT NOT NULL DEFAULT '2026-11-27 01:30:00'
            );
            INSERT INTO sessions(id) VALUES (1);
            INSERT INTO messages(session_id,role,content) VALUES (1,'user','u1');
            INSERT INTO messages(session_id,role,content) VALUES (1,'assistant','a1');
            INSERT INTO messages(session_id,role,content) VALUES (1,'user','u2');
            """
        )
        conn.commit()
        conn.close()

    def _request(self):
        return provider_runtime.ChatRuntimeRequest(
            model="",
            system=STABLE,
            system_sections={
                "stable": STABLE,
                "thread_stable": STABLE,
                "volatile": "per-turn context",
                "conversation_summary": "",
            },
            history=[{"role": "user", "content": "u2"}],
            think=False,
            session_id=1,
            current_user_message_id=3,
            cache_ttl="off",
            tool_history=None,
            extra_tools=None,
            extra_executor=None,
            extra_narrations=None,
            max_tool_rounds=None,
            closing_tool_names=None,
            closing_prompt="",
            round_observer=None,
            max_elapsed_seconds=None,
            base_tool_definitions=[],
            anthropic_cache_profile="",
        )

    def _seed_binding(self, request):
        stable_hash = state._stable_prompt_hash(request)
        state._set_pending_binding(
            session_id=1,
            thread_id="thread-1",
            stable_hash=stable_hash,
            current_user_message_id=1,
            reset_checkpoint=True,
        )
        state.mark_thread_synced(
            session_id=1,
            thread_id="thread-1",
            through_message_id=2,
            stable_prompt_hash=stable_hash,
        )

    def _run_with_fake(self, fake, request):
        events = queue.Queue()
        with mock.patch.object(runtime, "_APP_SERVER", fake):
            runtime._run_turn(request, events, threading.Event())
        output = []
        while not events.empty():
            output.append(events.get_nowait())
        return output

    def test_live_thread_continues_without_resume_rpc(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "chat.sqlite3")
            self._db(db_path)
            with mock.patch.dict("os.environ", {"DB_PATH": db_path}, clear=False):
                request = self._request()
                self._seed_binding(request)
                fake = _FakeAppServer(live={"thread-1"})
                events = self._run_with_fake(fake, request)
        methods = [method for method, _ in fake.calls]
        self.assertNotIn("thread/resume", methods)
        self.assertNotIn("thread/start", methods)
        self.assertIn("turn/start", methods)
        provider_state = next(payload for kind, payload in events if kind == "provider_state")
        self.assertEqual(provider_state["thread_id"], "thread-1")
        self.assertTrue(provider_state["resumed"])

    def test_restart_resumes_bound_thread(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "chat.sqlite3")
            self._db(db_path)
            with mock.patch.dict("os.environ", {"DB_PATH": db_path}, clear=False):
                request = self._request()
                self._seed_binding(request)
                fake = _FakeAppServer()
                events = self._run_with_fake(fake, request)
        methods = [method for method, _ in fake.calls]
        self.assertIn("thread/resume", methods)
        self.assertNotIn("thread/start", methods)
        provider_state = next(payload for kind, payload in events if kind == "provider_state")
        self.assertEqual(provider_state["thread_id"], "thread-1")
        self.assertTrue(provider_state["resumed"])

    def test_history_edit_ignores_even_a_live_old_thread(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "chat.sqlite3")
            self._db(db_path)
            with mock.patch.dict("os.environ", {"DB_PATH": db_path}, clear=False):
                request = self._request()
                self._seed_binding(request)
                conn = sqlite3.connect(db_path)
                conn.execute("UPDATE messages SET content='u1-edited' WHERE id=1")
                conn.commit()
                conn.close()
                fake = _FakeAppServer(live={"thread-1"}, started_thread="thread-new")
                events = self._run_with_fake(fake, request)
        methods = [method for method, _ in fake.calls]
        self.assertNotIn("thread/resume", methods)
        self.assertIn("thread/start", methods)
        provider_state = next(payload for kind, payload in events if kind == "provider_state")
        self.assertEqual(provider_state["thread_id"], "thread-new")
        self.assertFalse(provider_state["resumed"])

    def test_app_holds_codex_conversation_lock_through_stream_completion(self):
        events = []

        class TrackingLock:
            active = False

            def __enter__(self):
                self.active = True
                events.append("lock-enter")
                return self

            def __exit__(self, exc_type, exc, tb):
                events.append("lock-exit")
                self.active = False

        lock = TrackingLock()

        def fake_stream(*args, **kwargs):
            self.assertTrue(lock.active)
            events.append("provider-turn")
            yield "chunk"
            self.assertTrue(lock.active)
            events.append("sqlite-and-checkpoint-finished")

        with mock.patch.object(morrow_app.chat_engine, "get_provider", return_value={"format": "codex"}), \
             mock.patch.object(morrow_app.chat_engine.codex_acp_runtime, "_conversation_lock", return_value=lock), \
             mock.patch.object(morrow_app, "_stream_reply_impl", side_effect=fake_stream):
            self.assertEqual(list(morrow_app._stream_reply(1, provider_id=5)), ["chunk"])

        self.assertFalse(lock.active)
        self.assertEqual(events, [
            "lock-enter",
            "provider-turn",
            "sqlite-and-checkpoint-finished",
            "lock-exit",
        ])


if __name__ == "__main__":
    unittest.main()
