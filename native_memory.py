"""Experiment-B gate for Morrow's legacy native memory authority.

The data structures stay available for rollback/read-only inspection, but B does
not let them become a second long-term memory source unless explicitly opted in.
"""

import os

WRITE_TOOL_NAMES = frozenset({
    "write_memory",
    "update_memory",
    "delete_memory",
    "add_dynamic_memory",
    "update_dynamic_memory",
    "delete_dynamic_memory",
})


def enabled():
    value = os.environ.get("MORROW_NATIVE_MEMORY_ENABLED", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def blocked_message():
    return "Morrow 原生 Forever / Fragment 已在实验 B 退役；正式记忆将由 Companion Context Adapter 接管。"
