"""Provider runtime boundary for Morrow model backends.

This module owns provider selection and backend dispatch. Companion behavior,
history, memory, tools, and UI stay above this boundary; concrete model transports
(OpenAI-compatible, Anthropic, and future subscription adapters) stay below it.
"""

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


SUPPORTED_PROVIDER_KINDS = frozenset({"openai", "claude", "codex"})


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    """Resolved transport configuration for one model turn."""

    kind: str
    base_url: str
    api_key: str
    provider_meta: dict[str, Any]


@dataclass(frozen=True)
class ChatRuntimeRequest:
    """Provider-neutral inputs prepared by the Companion layer."""

    model: str
    system: str
    system_sections: dict[str, Any]
    history: list[dict[str, Any]]
    think: bool
    session_id: int
    current_user_message_id: int | None
    cache_ttl: str
    tool_history: list[dict[str, Any]] | None
    extra_tools: Any
    extra_executor: Any
    extra_narrations: Any
    max_tool_rounds: Any
    closing_tool_names: Any
    closing_prompt: str
    round_observer: Any
    max_elapsed_seconds: Any
    base_tool_definitions: list[dict[str, Any]]
    anthropic_cache_profile: str


def _normalized_kind(value: str | None) -> str:
    value = str(value or "").strip().lower()
    return value if value in SUPPORTED_PROVIDER_KINDS else "openai"


def resolve_provider_runtime(
    provider: Mapping[str, Any] | None,
    *,
    fallback_base_url: str,
    fallback_api_key: str,
    format_override: str = "",
) -> ProviderRuntimeConfig:
    """Resolve a saved provider or env fallback without leaking routing into chat."""

    override = str(format_override or "").strip().lower()
    if provider:
        kind = _normalized_kind(
            override if override in SUPPORTED_PROVIDER_KINDS else provider.get("format")
        )
        meta = {
            "id": provider.get("id"),
            "name": provider.get("name") or "provider",
            "format": kind,
            "cache_mode": provider.get("cache_mode") or "none",
        }
        return ProviderRuntimeConfig(
            kind=kind,
            base_url=str(provider.get("base_url") or "").rstrip("/"),
            api_key=str(provider.get("api_key") or ""),
            provider_meta=meta,
        )

    kind = _normalized_kind(
        override if override in SUPPORTED_PROVIDER_KINDS else "openai"
    )
    return ProviderRuntimeConfig(
        kind=kind,
        base_url=str(fallback_base_url or "").rstrip("/"),
        api_key=str(fallback_api_key or ""),
        provider_meta={
            "id": None,
            "name": "env",
            "format": kind,
            "cache_mode": "none",
        },
    )


def stream_with_runtime(
    runtime: ProviderRuntimeConfig,
    request: ChatRuntimeRequest,
    *,
    adapters: Mapping[
        str,
        Callable[[ProviderRuntimeConfig, ChatRuntimeRequest], Iterable[Any]],
    ],
):
    """Dispatch one normalized request to the selected backend adapter."""

    adapter = adapters.get(runtime.kind)
    if adapter is None:
        raise ValueError(f"unsupported provider runtime: {runtime.kind}")
    yield from adapter(runtime, request)
