"""Persistent Codex chat transport for Morrow via `codex app-server`.

Morrow SQLite is the canonical conversation ledger. Codex threads are disposable
runtime rooms: append-only history resumes the existing thread, while any
canonical-history mismatch causes a cold rebuild from Morrow.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import signal
import subprocess
import threading
from typing import Any, Iterable

import codex_acp_runtime as shared


_DONE = object()
_TURN_LOCK = threading.Lock()


class _AppServer:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._ready = False
        self._start_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[Any]] = {}
        self._notifications: queue.Queue[Any] = queue.Queue()
        self._next_id = 0
        self._live_threads: set[str] = set()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise RuntimeError("codex app-server is not running")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            proc.stdin.write(line)
            proc.stdin.flush()

    def _reader(self, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" in message and not message.get("method"):
                    with self._pending_lock:
                        waiter = self._pending.get(str(message["id"]))
                    if waiter is not None:
                        waiter.put(message)
                    continue
                if "id" in message and message.get("method"):
                    # No provider-native tools or approvals are granted here.
                    with contextlib.suppress(Exception):
                        self._write({
                            "id": message["id"],
                            "error": {
                                "code": -32000,
                                "message": "Unsupported interactive request in Morrow chat.",
                            },
                        })
                    continue
                if message.get("method"):
                    self._notifications.put(message)
        finally:
            error = RuntimeError("codex app-server exited")
            with self._pending_lock:
                waiters = list(self._pending.values())
            for waiter in waiters:
                waiter.put({"__exception__": error})
            self._notifications.put({"method": "__process_exit__", "params": {}})
            self._live_threads.clear()
            self._ready = False

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen[str]) -> None:
        if proc.stderr is None:
            return
        for _line in proc.stderr:
            pass

    def _request_raw(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        with self._id_lock:
            self._next_id += 1
            request_id = str(self._next_id)
        waiter: queue.Queue[Any] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = waiter
        try:
            self._write({"id": int(request_id), "method": method, "params": params or {}})
            try:
                message = waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"Codex RPC timed out: {method}") from exc
            if "__exception__" in message:
                raise message["__exception__"]
            if message.get("error"):
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise RuntimeError(f"Codex RPC {method} failed: {detail}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def start(self) -> None:
        with self._start_lock:
            if self._alive() and self._ready:
                return
            self.shutdown()
            _acp_bin, codex_bin, codex_home, workdir = shared._runtime_paths()
            env = os.environ.copy()
            env.update({
                "HOME": str(codex_home),
                "CODEX_HOME": str(codex_home / ".codex"),
                "RUST_LOG": "warn",
            })
            proc = subprocess.Popen(
                [codex_bin, "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=str(workdir),
                env=env,
                start_new_session=True,
            )
            self._proc = proc
            self._live_threads.clear()
            while not self._notifications.empty():
                with contextlib.suppress(queue.Empty):
                    self._notifications.get_nowait()
            threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
            threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()
            try:
                self._request_raw(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "morrow_experiment_b",
                            "title": "Morrow Experiment B",
                            "version": "codex-app-server-1",
                        },
                        "capabilities": {
                            "experimentalApi": True,
                            "requestAttestation": False,
                        },
                    },
                    timeout=20,
                )
                self._write({"method": "initialized"})
                self._ready = True
            except Exception:
                self.shutdown()
                raise

    def shutdown(self) -> None:
        proc = self._proc
        self._proc = None
        self._ready = False
        self._live_threads.clear()
        if proc is None or proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(Exception):
            proc.wait(timeout=5)

    def has_live_thread(self, thread_id: str) -> bool:
        return self._alive() and str(thread_id) in self._live_threads

    def mark_live_thread(self, thread_id: str) -> None:
        if thread_id:
            self._live_threads.add(str(thread_id))

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 30,
    ) -> dict[str, Any]:
        self.start()
        return self._request_raw(method, params, timeout=timeout)

    def drain_notifications(self) -> None:
        while True:
            try:
                self._notifications.get_nowait()
            except queue.Empty:
                return

    def next_notification(self, timeout: float = 0.1) -> dict[str, Any] | None:
        try:
            message = self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None
        return message if isinstance(message, dict) else None


_APP_SERVER = _AppServer()
atexit.register(_APP_SERVER.shutdown)


def _run_turn(
    request: Any,
    event_queue: queue.Queue[Any],
    cancel_event: threading.Event,
) -> None:
    _acp_bin, _codex_bin, _codex_home, workdir = shared._runtime_paths()
    transcript, current_prompt = shared._split_history(request.history)
    stable_hash = shared._stable_prompt_hash(request)
    current_user_id = getattr(request, "current_user_message_id", None)
    binding = shared._binding_reusable(request, stable_hash)

    with _TURN_LOCK:
        _APP_SERVER.start()
        _APP_SERVER.drain_notifications()
        thread_id: str | None = None
        resumed = False

        if binding is not None:
            candidate = str(binding["remote_session_id"] or "").strip()
            if _APP_SERVER.has_live_thread(candidate):
                thread_id = candidate
                resumed = True
            else:
                try:
                    result = _APP_SERVER.request(
                        "thread/resume",
                        {
                            "threadId": candidate,
                            "cwd": str(workdir),
                            "sandbox": "read-only",
                            "approvalPolicy": "never",
                            "developerInstructions": shared._stable_prompt_text(request),
                            "excludeTurns": True,
                        },
                        timeout=30,
                    )
                    thread_id = str((result.get("thread") or {}).get("id") or candidate)
                    _APP_SERVER.mark_live_thread(thread_id)
                    resumed = True
                except Exception:
                    # Morrow is canonical; a missing/corrupt remote room is recoverable.
                    thread_id = None

        if thread_id is None:
            bootstrap = shared._cold_bootstrap_transcript(request, transcript)
            system_text = shared._system_prompt(shared._stable_prompt_text(request), bootstrap)
            params: dict[str, Any] = {
                "cwd": str(workdir),
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": False,
                "baseInstructions": system_text,
            }
            model = str(getattr(request, "model", "") or "").strip()
            if model:
                params["model"] = model
            result = _APP_SERVER.request("thread/start", params, timeout=30)
            thread_id = str((result.get("thread") or {}).get("id") or "").strip()
            if not thread_id:
                raise RuntimeError("Codex thread/start returned no thread id")
            _APP_SERVER.mark_live_thread(thread_id)
            shared._set_pending_binding(
                session_id=int(request.session_id),
                thread_id=thread_id,
                stable_hash=stable_hash,
                current_user_message_id=current_user_id,
                reset_checkpoint=True,
            )
        else:
            shared._set_pending_binding(
                session_id=int(request.session_id),
                thread_id=thread_id,
                stable_hash=stable_hash,
                current_user_message_id=current_user_id,
                reset_checkpoint=False,
            )

        event_queue.put((
            "provider_state",
            {
                "kind": "codex",
                "thread_id": thread_id,
                "stable_prompt_hash": stable_hash,
                "resumed": resumed,
                "transport": "app-server",
            },
        ))

        _APP_SERVER.drain_notifications()
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{
                "type": "text",
                "text": shared._turn_prompt(request, current_prompt),
                "text_elements": [],
            }],
            "cwd": str(workdir),
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
            "summary": "auto",
            "effort": "high" if bool(getattr(request, "think", False)) else "low",
        }
        model = str(getattr(request, "model", "") or "").strip()
        if model:
            turn_params["model"] = model
        turn_result = _APP_SERVER.request("turn/start", turn_params, timeout=30)
        turn_id = str((turn_result.get("turn") or {}).get("id") or "").strip()
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn id")

        while True:
            if cancel_event.is_set():
                with contextlib.suppress(Exception):
                    _APP_SERVER.request(
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": turn_id},
                        timeout=10,
                    )
                return
            message = _APP_SERVER.next_notification(timeout=0.1)
            if message is None:
                continue
            method = str(message.get("method") or "")
            params = message.get("params") or {}
            if method == "__process_exit__":
                raise RuntimeError("Codex app-server exited during turn")
            if str(params.get("threadId") or "") != thread_id:
                continue
            notification_turn_id = str(
                params.get("turnId") or (params.get("turn") or {}).get("id") or ""
            )
            if notification_turn_id and notification_turn_id != turn_id:
                continue
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta:
                    event_queue.put(("delta", delta))
                continue
            if method == "item/reasoning/summaryTextDelta":
                delta = str(params.get("delta") or "")
                if delta:
                    event_queue.put(("reasoning", delta))
                continue
            if method == "turn/completed":
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status and status != "completed":
                    error = turn.get("error") or {}
                    detail = error.get("message") if isinstance(error, dict) else str(error)
                    raise RuntimeError(detail or f"Codex turn ended with status {status}")
                return


def _worker(
    request: Any,
    event_queue: queue.Queue[Any],
    cancel_event: threading.Event,
) -> None:
    try:
        # One app-server notification stream is deliberately serialized in this
        # first persistent-thread phase. Morrow itself remains multi-threaded.
        with shared._conversation_lock(getattr(request, "session_id", 0)):
            _run_turn(request, event_queue, cancel_event)
    except Exception as exc:
        event_queue.put((
            "error",
            {"status": None, "message": str(exc) or exc.__class__.__name__},
        ))
    finally:
        event_queue.put(_DONE)


def stream_codex_app_server(request: Any) -> Iterable[tuple[str, Any]]:
    """Yield Morrow-normalized streaming events from persistent Codex threads."""
    event_queue: queue.Queue[Any] = queue.Queue()
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_worker,
        args=(request, event_queue, cancel_event),
        name=f"morrow-codex-app-server-{getattr(request, 'session_id', 'turn')}",
        daemon=True,
    )
    thread.start()
    try:
        while True:
            item = event_queue.get()
            if item is _DONE:
                break
            yield item
    finally:
        cancel_event.set()
