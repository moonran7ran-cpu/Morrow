import asyncio
import queue
import threading
import unittest

import codex_acp_runtime
import provider_runtime
from acp.schema import DeniedOutcome


class CodexAcpRuntimeTest(unittest.TestCase):
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
        self.assertIn("Conversation history supplied by Morrow", text)
        self.assertIn("[assistant] hello", text)

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

    def test_model_and_think_controls_become_codex_session_options(self):
        request = provider_runtime.ChatRuntimeRequest(
            model="gpt-6.2-astra",
            system="",
            system_sections={},
            history=[],
            think=True,
            session_id=1,
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


if __name__ == "__main__":
    unittest.main()
