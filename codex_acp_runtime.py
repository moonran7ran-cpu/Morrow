"""Codex subscription helpers backed by Agent Client Protocol (ACP).

ACP remains useful for login recovery, model discovery, and a legacy fresh-turn
adapter. Persistent Companion chat lives in ``codex_app_server_runtime`` because
codex-acp 2.12 cannot reliably reopen a session across adapter processes.

This module also owns the small Morrow-side thread binding helpers shared by the
native app-server transport. Morrow SQLite remains canonical; provider threads
are disposable runtime rooms.

- no MCP servers are exposed to Codex by the ACP fallback;
- permission requests are always denied;
- the Codex session runs in read-only mode inside a dedicated empty workspace.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import queue
import signal
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from acp import Client, PROTOCOL_VERSION, connect_to_agent
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    InitialSessionConfigOption2,
    RequestPermissionResponse,
    SystemPrompt,
    TextContentBlock,
)


_ROOT = Path(__file__).resolve().parent
_DEFAULT_ACP_BIN = _ROOT / "runtime" / "codex-acp" / "node_modules" / ".bin" / "codex-acp"
_DEFAULT_CODEX_HOME = _ROOT / "data" / "codex-home"
_DEFAULT_WORKDIR = _ROOT / "data" / "codex-workspace"
_DEFAULT_DB_PATH = _ROOT / "data" / "nook.db"
_DONE = object()
_SESSION_LOCKS: dict[int, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()

_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS provider_conversation_state (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    provider_kind TEXT NOT NULL,
    remote_session_id TEXT NOT NULL DEFAULT '',
    stable_prompt_hash TEXT NOT NULL DEFAULT '',
    synced_through_message_id INTEGER,
    history_fingerprint TEXT NOT NULL DEFAULT '',
    pending_user_message_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', '+8 hours')),
    PRIMARY KEY(session_id, provider_kind)
)
"""

_HISTORY_COLUMNS = (
    "id", "role", "content", "attachments", "reasoning", "reasoning_ms",
    "versions", "active", "hidden", "proactive", "tool_log", "created_at",
)


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", str(_DEFAULT_DB_PATH))).expanduser()


def _state_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(_STATE_TABLE_SQL)
    return conn


def _conversation_lock(session_id: Any) -> threading.Lock:
    key = int(session_id or 0)
    with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


def _history_fingerprint(
    conn: sqlite3.Connection,
    session_id: int,
    through_message_id: int | None,
) -> str:
    if not through_message_id:
        rows: list[sqlite3.Row] = []
    else:
        columns = ",".join(_HISTORY_COLUMNS)
        rows = conn.execute(
            f"SELECT {columns} FROM messages "
            "WHERE session_id=? AND id<=? ORDER BY id ASC",
            (session_id, int(through_message_id)),
        ).fetchall()
    payload = [
        [row[column] for column in _HISTORY_COLUMNS]
        for row in rows
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_prompt_text(request: Any) -> str:
    sections = getattr(request, "system_sections", None) or {}
    return str(
        sections.get("thread_stable")
        or sections.get("stable")
        or getattr(request, "system", "")
        or ""
    ).strip()


def _stable_prompt_hash(request: Any) -> str:
    return hashlib.sha256(_stable_prompt_text(request).encode("utf-8")).hexdigest()


def _binding_row(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM provider_conversation_state "
        "WHERE session_id=? AND provider_kind='codex'",
        (int(session_id),),
    ).fetchone()


def _binding_reusable(request: Any, stable_hash: str) -> sqlite3.Row | None:
    current_user_id = getattr(request, "current_user_message_id", None)
    if not current_user_id:
        return None
    conn = _state_connection()
    try:
        row = _binding_row(conn, int(request.session_id))
        if row is None or not str(row["remote_session_id"] or "").strip():
            return None
        if row["pending_user_message_id"] is not None:
            return None
        if str(row["stable_prompt_hash"] or "") != stable_hash:
            return None
        synced = row["synced_through_message_id"]
        if synced is None or int(current_user_id) <= int(synced):
            return None
        current = conn.execute(
            "SELECT role FROM messages WHERE session_id=? AND id=?",
            (int(request.session_id), int(current_user_id)),
        ).fetchone()
        if current is None or current["role"] != "user":
            return None
        gap = conn.execute(
            "SELECT 1 FROM messages WHERE session_id=? AND id>? AND id<? LIMIT 1",
            (int(request.session_id), int(synced), int(current_user_id)),
        ).fetchone()
        if gap is not None:
            return None
        fingerprint = _history_fingerprint(conn, int(request.session_id), int(synced))
        if fingerprint != str(row["history_fingerprint"] or ""):
            return None
        return row
    finally:
        conn.close()


def _set_pending_binding(
    *,
    session_id: int,
    thread_id: str,
    stable_hash: str,
    current_user_message_id: int | None,
    reset_checkpoint: bool,
) -> None:
    conn = _state_connection()
    try:
        pending_id = int(current_user_message_id) if current_user_message_id else -1
        if reset_checkpoint:
            previous = None
            if current_user_message_id:
                row = conn.execute(
                    "SELECT MAX(id) AS id FROM messages WHERE session_id=? AND id<?",
                    (int(session_id), int(current_user_message_id)),
                ).fetchone()
                previous = int(row["id"]) if row and row["id"] is not None else None
            fingerprint = _history_fingerprint(conn, int(session_id), previous)
            conn.execute(
                "INSERT INTO provider_conversation_state "
                "(session_id,provider_kind,remote_session_id,stable_prompt_hash,"
                "synced_through_message_id,history_fingerprint,pending_user_message_id) "
                "VALUES (?,'codex',?,?,?,?,?) "
                "ON CONFLICT(session_id,provider_kind) DO UPDATE SET "
                "remote_session_id=excluded.remote_session_id,"
                "stable_prompt_hash=excluded.stable_prompt_hash,"
                "synced_through_message_id=excluded.synced_through_message_id,"
                "history_fingerprint=excluded.history_fingerprint,"
                "pending_user_message_id=excluded.pending_user_message_id,"
                "updated_at=datetime('now','+8 hours')",
                (int(session_id), str(thread_id), stable_hash, previous, fingerprint, pending_id),
            )
        else:
            updated = conn.execute(
                "UPDATE provider_conversation_state SET pending_user_message_id=?,"
                "updated_at=datetime('now','+8 hours') "
                "WHERE session_id=? AND provider_kind='codex' AND remote_session_id=?",
                (pending_id, int(session_id), str(thread_id)),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Codex thread binding changed before prompt")
        conn.commit()
    finally:
        conn.close()


def mark_thread_synced(
    *, session_id: int, thread_id: str | None, through_message_id: int,
    stable_prompt_hash: str = "",
) -> bool:
    if not thread_id or not through_message_id:
        return False
    conn = _state_connection()
    try:
        row = _binding_row(conn, int(session_id))
        if row is None or str(row["remote_session_id"] or "") != str(thread_id):
            return False
        fingerprint = _history_fingerprint(conn, int(session_id), int(through_message_id))
        conn.execute(
            "UPDATE provider_conversation_state SET synced_through_message_id=?,"
            "history_fingerprint=?,pending_user_message_id=NULL,stable_prompt_hash=?,"
            "updated_at=datetime('now','+8 hours') "
            "WHERE session_id=? AND provider_kind='codex' AND remote_session_id=?",
            (int(through_message_id), fingerprint,
             stable_prompt_hash or str(row["stable_prompt_hash"] or ""),
             int(session_id), str(thread_id)),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def invalidate_thread_binding(session_id: int, thread_id: str | None = None) -> None:
    conn = _state_connection()
    try:
        if thread_id:
            conn.execute(
                "DELETE FROM provider_conversation_state WHERE session_id=? "
                "AND provider_kind='codex' AND remote_session_id=?",
                (int(session_id), str(thread_id)),
            )
        else:
            conn.execute(
                "DELETE FROM provider_conversation_state "
                "WHERE session_id=? AND provider_kind='codex'",
                (int(session_id),),
            )
        conn.commit()
    finally:
        conn.close()


def _local_image_inputs(content: Any) -> list[dict[str, Any]]:
    """Return only validated Morrow-upload images as Codex localImage inputs."""
    items = content if isinstance(content, list) else [content]
    root = (_db_path().parent / "uploads").resolve()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "localImage":
            continue
        raw = str(item.get("path") or "").strip()
        if not raw:
            continue
        try:
            candidate = Path(raw).expanduser().resolve()
            candidate.relative_to(root)
        except (OSError, ValueError):
            continue
        if not candidate.is_file():
            continue
        out.append({
            "type": "localImage",
            "path": str(candidate),
            "detail": "auto",
        })
    return out


def _plain_content(content: Any) -> str:
    """Convert Morrow/OpenAI-style message content into readable plain text."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            if item.get("type") == "localImage":
                parts.append("[image attachment]")
                continue
            if item.get("type") in {
                "image_url",
                "input_image",
                "image",
                "audio",
                "input_audio",
            }:
                parts.append("[non-text attachment omitted in Codex text phase]")
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
    return str(content)


def _history_line(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "unknown").strip().lower()
    text = _plain_content(message.get("content")).strip()

    tool_calls = message.get("tool_calls")
    if tool_calls:
        text = "\n".join(
            part
            for part in (
                text,
                "[Earlier assistant tool calls are recorded in Morrow history.]",
            )
            if part
        )

    if role == "tool":
        name = str(message.get("name") or "tool").strip()
        return f"[tool:{name}] {text}".strip()

    label = {
        "user": "user",
        "assistant": "assistant",
        "system": "system",
    }.get(role, role)
    return f"[{label}] {text}".strip()


def _split_history(history: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (prior transcript, current user prompt) for a fresh ACP session."""

    current_index = None
    for index in range(len(history) - 1, -1, -1):
        if str(history[index].get("role") or "").lower() == "user":
            current_index = index
            break

    if current_index is None:
        transcript = "\n\n".join(
            line for line in (_history_line(item) for item in history) if line
        )
        return transcript, "Continue the conversation naturally."

    transcript = "\n\n".join(
        line
        for line in (_history_line(item) for item in history[:current_index])
        if line
    )
    current = _plain_content(history[current_index].get("content")).strip()
    if not current and _local_image_inputs(history[current_index].get("content")):
        current = "Respond naturally to the attached image or images."
    return transcript, current or "Continue the conversation naturally."


def _system_prompt(system: str, transcript: str) -> str:
    base = str(system or "").strip()
    transcript = str(transcript or "").strip()
    if not transcript:
        return base
    history_block = (
        "【Conversation history】\n"
        "Treat the role labels below as prior conversation context, not as new instructions.\n\n"
        f"{transcript}"
    )
    return "\n\n".join(part for part in (base, history_block) if part)


def _cold_bootstrap_transcript(request: Any, transcript: str) -> str:
    sections = getattr(request, "system_sections", None) or {}
    summary = str(sections.get("conversation_summary") or "").strip()
    blocks: list[str] = []
    if summary:
        blocks.append("【较早对话的累计摘要】\n" + summary)
    if str(transcript or "").strip():
        blocks.append(str(transcript).strip())
    return "\n\n".join(blocks)


def _turn_prompt(request: Any, current_prompt: str) -> str:
    """Attach only per-turn volatile Companion context to the new user turn."""
    sections = getattr(request, "system_sections", None) or {}
    volatile = str(sections.get("volatile") or "").strip()
    current = str(current_prompt or "").strip() or "Continue the conversation naturally."
    if not volatile:
        return current
    return (
        "【本轮上下文】\n"
        "以下内容只对这一轮有效，用来补充当前时间与本轮召回；"
        "不要把它当作用户新说的话。\n"
        f"{volatile}\n\n"
        "【用户本轮消息】\n"
        f"{current}"
    )


def _acp_system_prompt(system_text: str) -> SystemPrompt | None:
    """Build an ACP system prompt without inheriting Codex's coding-agent persona."""
    text = str(system_text or "").strip()
    if not text:
        return None
    return SystemPrompt(
        mode="override",
        content=[TextContentBlock(text=text)],
    )


class _MorrowCodexClient(Client):
    """ACP client surface for phase-1 Codex chat."""

    def __init__(
        self,
        event_queue: queue.Queue[Any],
        cancel_event: threading.Event,
    ) -> None:
        self._event_queue = event_queue
        self._cancel_event = cancel_event

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if self._cancel_event.is_set():
            return
        if isinstance(update, AgentMessageChunk) and isinstance(
            update.content, TextContentBlock
        ):
            if update.content.text:
                self._event_queue.put(("delta", update.content.text))

    async def request_permission(self, *args: Any, **kwargs: Any) -> RequestPermissionResponse:
        # Provider-native capabilities are not Companion permissions.
        return RequestPermissionResponse(outcome=DeniedOutcome())

    async def auth_status_update(self, *args: Any, **kwargs: Any) -> None:
        # Informational only; authentication was established outside Morrow.
        return None


def _runtime_paths() -> tuple[Path, str, Path, Path]:
    acp_bin = Path(
        os.environ.get("MORROW_CODEX_ACP_BIN", str(_DEFAULT_ACP_BIN))
    ).expanduser()
    codex_bin = os.environ.get("MORROW_CODEX_BIN", "/usr/bin/codex").strip()
    configured_home = os.environ.get("MORROW_CODEX_HOME", "").strip()
    codex_home = (
        Path(configured_home).expanduser()
        if configured_home
        else _DEFAULT_CODEX_HOME
    )
    workdir = Path(
        os.environ.get("MORROW_CODEX_WORKDIR", str(_DEFAULT_WORKDIR))
    ).expanduser()

    if not acp_bin.is_file():
        raise RuntimeError(f"codex-acp binary not found: {acp_bin}")
    if not Path(codex_bin).is_file():
        raise RuntimeError(f"Codex binary not found: {codex_bin}")
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / ".codex").mkdir(parents=True, exist_ok=True)
    workdir.mkdir(parents=True, exist_ok=True)
    return acp_bin, codex_bin, codex_home, workdir


def _session_config_options(request: Any) -> list[InitialSessionConfigOption2]:
    """Translate Morrow model controls into Codex ACP session options."""
    options = [
        InitialSessionConfigOption2(config_id="mode", value="read-only"),
        InitialSessionConfigOption2(
            config_id="reasoning_effort",
            value="high" if bool(getattr(request, "think", False)) else "low",
        ),
    ]
    model = str(getattr(request, "model", "") or "").strip()
    if model:
        options.append(InitialSessionConfigOption2(config_id="model", value=model))
    return options


async def _run_turn(
    request: Any,
    event_queue: queue.Queue[Any],
    cancel_event: threading.Event,
) -> None:
    acp_bin, codex_bin, codex_home, workdir = _runtime_paths()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(codex_home),
            "CODEX_PATH": codex_bin,
            "NO_BROWSER": "1",
            "INITIAL_AGENT_MODE": "read-only",
        }
    )

    proc = await asyncio.create_subprocess_exec(
        str(acp_bin),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workdir),
        env=env,
        start_new_session=True,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("codex-acp did not expose stdio")

    client = _MorrowCodexClient(event_queue, cancel_event)
    conn = connect_to_agent(
        client,
        proc.stdin,
        proc.stdout,
        use_unstable_protocol=True,
    )
    session_id: str | None = None

    try:
        await asyncio.wait_for(
            conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(
                    name="morrow",
                    title="Morrow",
                    version="codex-acp-phase1",
                ),
            ),
            timeout=20,
        )

        transcript, current_prompt = _split_history(request.history)
        system_text = _system_prompt(request.system, transcript)
        system_prompt = _acp_system_prompt(system_text)

        session = await asyncio.wait_for(
            conn.new_session(
                cwd=str(workdir),
                mcp_servers=[],
                system_prompt=system_prompt,
                config_options=_session_config_options(request),
            ),
            timeout=30,
        )
        session_id = session.session_id

        prompt_task = asyncio.create_task(
            conn.prompt(
                session_id=session_id,
                prompt=[TextContentBlock(text=current_prompt)],
            )
        )
        while not prompt_task.done():
            if cancel_event.is_set():
                with contextlib.suppress(Exception):
                    await conn.cancel(session_id=session_id)
                prompt_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await prompt_task
                return
            await asyncio.sleep(0.05)

        await asyncio.wait_for(prompt_task, timeout=1)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()

        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)

        # Drain stderr so a failed child cannot leave a pipe-backed task behind.
        if proc.stderr is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.stderr.read(), timeout=1)



async def _query_available_models() -> list[str]:
    """Read the model choices advertised by the authenticated Codex ACP session."""
    acp_bin, codex_bin, codex_home, workdir = _runtime_paths()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(codex_home),
            "CODEX_PATH": codex_bin,
            "NO_BROWSER": "1",
            "INITIAL_AGENT_MODE": "read-only",
        }
    )
    proc = await asyncio.create_subprocess_exec(
        str(acp_bin),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(workdir),
        env=env,
        start_new_session=True,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("codex-acp did not expose stdio")

    client = _MorrowCodexClient(queue.Queue(), threading.Event())
    conn = connect_to_agent(
        client,
        proc.stdin,
        proc.stdout,
        use_unstable_protocol=True,
    )
    try:
        await asyncio.wait_for(
            conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(
                    name="morrow-models",
                    title="Morrow",
                    version="codex-acp-phase1",
                ),
            ),
            timeout=20,
        )
        session = await asyncio.wait_for(
            conn.new_session(
                cwd=str(workdir),
                mcp_servers=[],
                config_options=[
                    InitialSessionConfigOption2(
                        config_id="mode",
                        value="read-only",
                    )
                ],
            ),
            timeout=30,
        )
        for option in session.config_options or []:
            if getattr(option, "id", "") != "model":
                continue
            return [
                str(getattr(item, "value", "") or "").strip()
                for item in (getattr(option, "options", None) or [])
                if str(getattr(item, "value", "") or "").strip()
            ]
        return []
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
        if proc.stderr is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.stderr.read(), timeout=1)


def available_models() -> list[str]:
    """Return authenticated Codex model IDs for the Settings model picker."""
    return asyncio.run(_query_available_models())

def _worker(
    request: Any,
    event_queue: queue.Queue[Any],
    cancel_event: threading.Event,
) -> None:
    try:
        # A single Morrow conversation must never prompt the same persisted
        # provider thread concurrently.
        with _conversation_lock(getattr(request, "session_id", 0)):
            asyncio.run(_run_turn(request, event_queue, cancel_event))
    except Exception as exc:
        event_queue.put(
            (
                "error",
                {
                    "status": None,
                    "message": str(exc) or exc.__class__.__name__,
                },
            )
        )
    finally:
        event_queue.put(_DONE)


def stream_codex_acp(request: Any) -> Iterable[tuple[str, Any]]:
    """Yield Morrow-normalized streaming events from a Codex ACP turn."""

    event_queue: queue.Queue[Any] = queue.Queue()
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_worker,
        args=(request, event_queue, cancel_event),
        name=f"morrow-codex-acp-{getattr(request, 'session_id', 'turn')}",
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


# ---------------------------------------------------------------------------
# User-facing Codex subscription login recovery
# ---------------------------------------------------------------------------

_LOGIN_LOCK = threading.Lock()
_LOGIN_READY = threading.Event()
_LOGIN_STATE: dict[str, Any] = {
    "process": None,
    "verification_url": "",
    "user_code": "",
    "started_at": 0.0,
    "completed": None,
    "error": "",
}


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))


def _parse_device_login_output(text: str) -> tuple[str, str]:
    import re

    clean = _strip_ansi(text)
    url_match = re.search(r"https://auth\.openai\.com/codex/device\b", clean)
    code_match = re.search(r"\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b", clean)
    return (
        url_match.group(0) if url_match else "",
        code_match.group(0) if code_match else "",
    )


def _login_status_raw() -> dict[str, Any]:
    import subprocess

    _, codex_bin, codex_home, _ = _runtime_paths()
    env = os.environ.copy()
    env["HOME"] = str(codex_home)
    try:
        result = subprocess.run(
            [codex_bin, "login", "status"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=8,
            check=False,
        )
        detail = _strip_ansi(result.stdout).strip()
    except Exception as exc:  # noqa: BLE001
        return {
            "authenticated": False,
            "method": None,
            "detail": "",
            "error": str(exc) or type(exc).__name__,
        }

    authenticated = result.returncode == 0 and "Logged in" in detail
    method = None
    if authenticated:
        if "ChatGPT" in detail:
            method = "chatgpt"
        elif "API key" in detail:
            method = "api_key"
        else:
            method = "other"
    return {
        "authenticated": authenticated,
        "method": method,
        "detail": detail,
        "error": "" if authenticated else detail,
    }


def login_status() -> dict[str, Any]:
    status = _login_status_raw()
    with _LOGIN_LOCK:
        proc = _LOGIN_STATE.get("process")
        active = bool(proc is not None and proc.poll() is None)
        status.update(
            {
                "login_active": active,
                "verification_url": _LOGIN_STATE.get("verification_url") or "",
                "user_code": _LOGIN_STATE.get("user_code") or "",
                "login_completed": _LOGIN_STATE.get("completed"),
                "login_error": _LOGIN_STATE.get("error") or "",
            }
        )
    return status


def _watch_device_login(proc: Any) -> None:
    lines: list[str] = []
    try:
        if proc.stdout is not None:
            for raw in proc.stdout:
                clean = _strip_ansi(raw).rstrip()
                if clean:
                    lines.append(clean)
                    if len(lines) > 40:
                        lines = lines[-40:]
                url, code = _parse_device_login_output("\n".join(lines))
                if url and code:
                    with _LOGIN_LOCK:
                        _LOGIN_STATE["verification_url"] = url
                        _LOGIN_STATE["user_code"] = code
                    _LOGIN_READY.set()
        return_code = proc.wait()
        status = _login_status_raw()
        with _LOGIN_LOCK:
            _LOGIN_STATE["completed"] = bool(status.get("authenticated"))
            if return_code != 0 and not status.get("authenticated"):
                _LOGIN_STATE["error"] = (
                    "\n".join(lines[-8:]).strip()
                    or f"codex login exited with status {return_code}"
                )
    except Exception as exc:  # noqa: BLE001
        with _LOGIN_LOCK:
            _LOGIN_STATE["completed"] = False
            _LOGIN_STATE["error"] = str(exc) or type(exc).__name__
    finally:
        _LOGIN_READY.set()


def start_device_login() -> dict[str, Any]:
    import subprocess
    import time

    _, codex_bin, codex_home, workdir = _runtime_paths()
    env = os.environ.copy()
    env["HOME"] = str(codex_home)

    with _LOGIN_LOCK:
        current = _LOGIN_STATE.get("process")
        started_at = float(_LOGIN_STATE.get("started_at") or 0.0)
        if current is not None and current.poll() is None:
            if time.time() - started_at < 16 * 60:
                url = _LOGIN_STATE.get("verification_url") or ""
                code = _LOGIN_STATE.get("user_code") or ""
                if url and code:
                    return {
                        "verification_url": url,
                        "user_code": code,
                        "reused": True,
                    }
            else:
                with contextlib.suppress(Exception):
                    current.terminate()

        _LOGIN_READY.clear()
        _LOGIN_STATE.update(
            {
                "process": None,
                "verification_url": "",
                "user_code": "",
                "started_at": time.time(),
                "completed": None,
                "error": "",
            }
        )
        proc = subprocess.Popen(
            [codex_bin, "login", "--device-auth"],
            env=env,
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        _LOGIN_STATE["process"] = proc
        thread = threading.Thread(
            target=_watch_device_login,
            args=(proc,),
            name="morrow-codex-device-login",
            daemon=True,
        )
        thread.start()

    _LOGIN_READY.wait(timeout=12)
    with _LOGIN_LOCK:
        url = _LOGIN_STATE.get("verification_url") or ""
        code = _LOGIN_STATE.get("user_code") or ""
        error = _LOGIN_STATE.get("error") or ""
    if not url or not code:
        raise RuntimeError(error or "Codex did not return a device authorization code")
    return {
        "verification_url": url,
        "user_code": code,
        "reused": False,
    }
