import unittest

import provider_runtime


class ProviderRuntimeTest(unittest.TestCase):
    def test_saved_provider_resolves_without_chat_specific_routing(self):
        runtime = provider_runtime.resolve_provider_runtime(
            {
                "id": 7,
                "name": "Primary",
                "base_url": "https://relay.example/v1/",
                "api_key": "secret",
                "format": "openai",
                "cache_mode": "none",
            },
            fallback_base_url="https://fallback.invalid",
            fallback_api_key="fallback",
        )
        self.assertEqual(runtime.kind, "openai")
        self.assertEqual(runtime.base_url, "https://relay.example/v1")
        self.assertEqual(runtime.api_key, "secret")
        self.assertEqual(runtime.provider_meta["id"], 7)
        self.assertEqual(runtime.provider_meta["name"], "Primary")

    def test_format_override_is_resolved_at_runtime_boundary(self):
        runtime = provider_runtime.resolve_provider_runtime(
            {
                "id": 4,
                "name": "Relay",
                "base_url": "https://relay.example",
                "api_key": "secret",
                "format": "openai",
                "cache_mode": "anthropic_explicit_sticky",
            },
            fallback_base_url="https://fallback.invalid",
            fallback_api_key="fallback",
            format_override="claude",
        )
        self.assertEqual(runtime.kind, "claude")
        self.assertEqual(runtime.provider_meta["format"], "claude")
        self.assertEqual(
            runtime.provider_meta["cache_mode"],
            "anthropic_explicit_sticky",
        )

    def test_env_fallback_stays_openai_compatible(self):
        runtime = provider_runtime.resolve_provider_runtime(
            None,
            fallback_base_url="https://api.example/v1/",
            fallback_api_key="env-key",
        )
        self.assertEqual(runtime.kind, "openai")
        self.assertEqual(runtime.base_url, "https://api.example/v1")
        self.assertEqual(runtime.api_key, "env-key")
        self.assertIsNone(runtime.provider_meta["id"])
        self.assertEqual(runtime.provider_meta["name"], "env")

    def test_unknown_saved_format_keeps_current_openai_fallback_behavior(self):
        runtime = provider_runtime.resolve_provider_runtime(
            {
                "id": 9,
                "name": "Legacy",
                "base_url": "https://legacy.example",
                "api_key": "secret",
                "format": "something-old",
            },
            fallback_base_url="https://fallback.invalid",
            fallback_api_key="fallback",
        )
        self.assertEqual(runtime.kind, "openai")

    def test_dispatch_selects_only_the_runtime_adapter(self):
        runtime = provider_runtime.resolve_provider_runtime(
            {
                "id": 8,
                "name": "Claude",
                "base_url": "https://claude.example",
                "api_key": "secret",
                "format": "claude",
            },
            fallback_base_url="https://fallback.invalid",
            fallback_api_key="fallback",
        )
        request = provider_runtime.ChatRuntimeRequest(
            model="test-model",
            system="system",
            system_sections={"stable": "system", "volatile": ""},
            history=[],
            think=False,
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
        calls = []

        def openai_adapter(_runtime, _request):
            calls.append("openai")
            yield ("delta", "wrong")

        def claude_adapter(_runtime, _request):
            calls.append("claude")
            yield ("delta", "ok")

        events = list(
            provider_runtime.stream_with_runtime(
                runtime,
                request,
                adapters={
                    "openai": openai_adapter,
                    "claude": claude_adapter,
                },
            )
        )
        self.assertEqual(calls, ["claude"])
        self.assertEqual(events, [("delta", "ok")])


if __name__ == "__main__":
    unittest.main()
