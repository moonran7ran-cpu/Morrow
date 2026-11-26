import asyncio
import json
import queue
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import codex_acp_runtime
import provider_runtime
from acp.schema import DeniedOutcome


class CodexAcpRuntimeTest(unittest.TestCase):
    def _request(self, *, current_user_message_id=None, stable="IDENTITY", volatile=""):
        return provider_runtime.ChatRuntimeRequest(
            model="gpt-6.2-astra",
            system=stable,
            system_sections={
                "stable": stable,
                "thread_stable": stable,
                "volatile": volatile,
                "conversation_summary": "",
            },
            history=[],
            think=False,
            session_id=1,
            current_user_message_id=current_user_message_id,
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

    def _make_state_db(self, path):
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
                created_at TEXT NOT NULL DEFAULT '2026-11-27 00:00:00'
            );
            INSERT INTO sessions(id) VALUES (1);
            INSERT INTO messages(session_id,role,content) VALUES (1,'user','u1');
            INSERT INTO messages(session_id,role,content) VALUES (1,'assistant','a1');
            INSERT INTO messages(session_id,role,content) VALUES (1,'user','u2');
            """
        )
        conn.commit()
        conn.close()

    def test_codex_is_a_supported_provider_kind(self):
        runtime = provider_runtime.resolve_provider_runtime(
            {
                "id": 11,
                "name": "Codex Subscription",
                "format": "codex",
                "api_url": "",
                "api_key": "",
            },
            fallback_base_url="https://fallback.invalid",
            fallback_api_key="",
        )
        self.assertEqual(runtime.kind, "codex")
        self.assertEqual(runtime.api_key, "")
        self.assertEqual(runtime.provider_meta["format"], "codex")

    def test_split_history_keeps_prior_roles_and_latest_user_prompt(self):
        transcript, current = codex_acp_runtime._split_history(
            [
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
                {"role": "user", "content": "current question"},
            ]
        )
        self.assertIn("[user] earlier question", transcript)
        self.assertIn("[assistant] earlier answer", transcript)
        self.assertNotIn("current question", transcript)
        self.assertEqual(current, "current question")

    def test_system_prompt_appends_history_without_replacing_identity(self):
        text = codex_acp_runtime._system_prompt(
            "IDENTITY",
            "[user] hi\n\n[assistant] hello",
        )
        self.assertTrue(text.startswith("IDENTITY"))
        self.assertIn("Conversation history", text)
        self.assertIn("[assistant] hello", text)
        self.assertNotIn("supplied by Morrow", text)

    def test_codex_system_prompt_overrides_native_agent_persona(self):
        prompt = codex_acp_runtime._acp_system_prompt("IDENTITY")
        self.assertIsNotNone(prompt)
        self.assertEqual(prompt.mode, "override")
        self.assertEqual(prompt.content[0].text, "IDENTITY")

    def test_pinned_codex_adapter_reapplies_base_instructions_override(self):
        root = Path(__file__).parents[1] / "runtime" / "codex-acp"
        package = json.loads((root / "package.json").read_text())
        self.assertEqual(package["dependencies"]["@agentclientprotocol/codex-acp"], "2.12.0")
        self.assertEqual(package["scripts"]["postinstall"], "node apply-morrow-patch.mjs")
        patch = (root / "apply-morrow-patch.mjs").read_text()
        self.assertIn("baseInstructions", patch)
        self.assertIn('value.mode !== "override"', patch)
        self.assertIn("MORROW_PATCH_BASE_INSTRUCTIONS_OVERRIDE", patch)

    def test_non_text_parts_are_marked_not_silently_flattened(self):
        text = codex_acp_runtime._plain_content(
            [
                {"type": "text", "text": "caption"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]
        )
        self.assertIn("caption", text)
        self.assertIn("non-text attachment omitted", text)

    def test_permission_requests_are_denied(self):
        client = codex_acp_runtime._MorrowCodexClient(
            queue.Queue(),
            threading.Event(),
        )
        response = asyncio.run(client.request_permission())
        self.assertIsInstance(response.outcome, DeniedOutcome)
        self.assertEqual(response.outcome.outcome, "cancelled")

    def test_default_codex_home_is_project_isolated(self):
        with mock.patch.dict(
            "os.environ",
            {"MORROW_CODEX_HOME": ""},
            clear=False,
        ):
            _, _, codex_home, _ = codex_acp_runtime._runtime_paths()
        self.assertEqual(codex_home, codex_acp_runtime._DEFAULT_CODEX_HOME)
        self.assertIn("morrow-experiment-b/data/codex-home", str(codex_home))
        self.assertTrue((codex_home / ".codex").is_dir())

    def test_device_login_parser_accepts_official_codex_output(self):
        text = (
            "\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n"
            "Enter this one-time code\n"
            "\x1b[94m44I2-MJKZR\x1b[0m"
        )
        url, code = codex_acp_runtime._parse_device_login_output(text)
        self.assertEqual(url, "https://auth.openai.com/codex/device")
        self.assertEqual(code, "44I2-MJKZR")

    def test_model_and_think_controls_become_codex_session_options(self):
        request = provider_runtime.ChatRuntimeRequest(
            model="gpt-6.2-astra",
            system="",
            system_sections={},
            history=[],
            think=True,
            session_id=1,
            current_user_message_id=None,
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
        options = {
            option.config_id: option.value
            for option in codex_acp_runtime._session_config_options(request)
        }
        self.assertEqual(options["model"], "gpt-6.2-astra")
        self.assertEqual(options["reasoning_effort"], "high")
        self.assertEqual(options["mode"], "read-only")

    def test_turn_prompt_keeps_volatile_context_on_current_turn_only(self):
        request = self._request(
            current_user_message_id=3,
            stable="IDENTITY",
            volatile="现在的时间是：2026-11-27 00:00（北京时间）",
        )
        self.assertEqual(codex_acp_runtime._stable_prompt_text(request), "IDENTITY")
        prompt = codex_acp_runtime._turn_prompt(request, "hello")
        self.assertIn("2026-11-27 00:00", prompt)
        self.assertIn("hello", prompt)
        self.assertNotIn("2026-11-27 00:00", codex_acp_runtime._stable_prompt_text(request))

    def test_binding_reuses_only_append_only_morrow_history(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "chat.sqlite3")
            self._make_state_db(db_path)
            with mock.patch.dict("os.environ", {"DB_PATH": db_path}, clear=False):
                first = self._request(current_user_message_id=1)
                stable_hash = codex_acp_runtime._stable_prompt_hash(first)
                codex_acp_runtime._set_pending_binding(
                    session_id=1,
                    thread_id="thread-1",
                    stable_hash=stable_hash,
                    current_user_message_id=1,
                    reset_checkpoint=True,
                )
                self.assertTrue(codex_acp_runtime.mark_thread_synced(
                    session_id=1,
                    thread_id="thread-1",
                    through_message_id=2,
                    stable_prompt_hash=stable_hash,
                ))

                next_turn = self._request(current_user_message_id=3)
                reusable = codex_acp_runtime._binding_reusable(next_turn, stable_hash)
                self.assertIsNotNone(reusable)
                self.assertEqual(reusable["remote_session_id"], "thread-1")

                conn = sqlite3.connect(db_path)
                conn.execute(
                    "UPDATE messages SET content='u1-edited',versions='[{\"c\":\"u1-edited\"}]',active=0 "
                    "WHERE id=1"
                )
                conn.commit()
                conn.close()
                self.assertIsNone(
                    codex_acp_runtime._binding_reusable(next_turn, stable_hash)
                )

    def test_pending_turn_blocks_hidden_thread_reuse(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = str(Path(tempdir) / "chat.sqlite3")
            self._make_state_db(db_path)
            with mock.patch.dict("os.environ", {"DB_PATH": db_path}, clear=False):
                first = self._request(current_user_message_id=1)
                stable_hash = codex_acp_runtime._stable_prompt_hash(first)
                codex_acp_runtime._set_pending_binding(
                    session_id=1, thread_id="thread-1", stable_hash=stable_hash,
                    current_user_message_id=1, reset_checkpoint=True,
                )
                codex_acp_runtime.mark_thread_synced(
                    session_id=1, thread_id="thread-1", through_message_id=2,
                    stable_prompt_hash=stable_hash,
                )
                codex_acp_runtime._set_pending_binding(
                    session_id=1, thread_id="thread-1", stable_hash=stable_hash,
                    current_user_message_id=3, reset_checkpoint=False,
                )
                self.assertIsNone(
                    codex_acp_runtime._binding_reusable(
                        self._request(current_user_message_id=3), stable_hash
                    )
                )


if __name__ == "__main__":
    unittest.main()
