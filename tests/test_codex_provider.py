import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

import app
import chat_engine


class CodexProviderApiTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_paths = (
            app.DB_PATH,
            app.DATA_DIR,
            app.UPLOAD_DIR,
            chat_engine.DB_PATH,
            chat_engine.DATA_DIR,
            chat_engine.UPLOAD_DIR,
            app.AUTH_PASSWORD,
        )
        app.DB_PATH = os.path.join(self.root, "nook.db")
        app.DATA_DIR = self.root
        app.UPLOAD_DIR = os.path.join(self.root, "uploads")
        chat_engine.DB_PATH = app.DB_PATH
        chat_engine.DATA_DIR = self.root
        chat_engine.UPLOAD_DIR = app.UPLOAD_DIR
        app.AUTH_PASSWORD = "test-password"
        app.init_db()
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session["authed"] = True

    def tearDown(self):
        (
            app.DB_PATH,
            app.DATA_DIR,
            app.UPLOAD_DIR,
            chat_engine.DB_PATH,
            chat_engine.DATA_DIR,
            chat_engine.UPLOAD_DIR,
            app.AUTH_PASSWORD,
        ) = self.old_paths
        shutil.rmtree(self.root, ignore_errors=True)

    def test_codex_provider_allows_blank_api_credentials(self):
        response = self.client.post(
            "/api/providers",
            json={
                "name": "Codex Subscription",
                "format": "codex",
                "api_key": "",
                "api_url": "",
                "models": [{"id": "gpt-6.2-sol", "name": "6.2 Sol"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()
        self.assertEqual(saved["format"], "codex")
        self.assertEqual(saved["api_key"], "")
        self.assertEqual(saved["api_url"], "")

        resolved = chat_engine.get_provider(saved["id"])
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["format"], "codex")
        self.assertEqual(resolved["base_url"], "")
        self.assertEqual(resolved["api_key"], "")

    @mock.patch(
        "app.codex_acp_runtime.available_models",
        return_value=["gpt-6.2-sol", "gpt-6.2-astra"],
    )
    def test_codex_model_discovery_uses_subscription_runtime(self, models):
        response = self.client.post(
            "/api/providers/fetch-models",
            json={"format": "codex", "api_key": "", "api_url": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["models"],
            ["gpt-6.2-sol", "gpt-6.2-astra"],
        )
        models.assert_called_once_with()

    @mock.patch(
        "app.codex_acp_runtime.login_status",
        return_value={
            "authenticated": True,
            "method": "chatgpt",
            "detail": "Logged in using ChatGPT",
            "error": "",
            "login_active": False,
            "verification_url": "",
            "user_code": "",
            "login_completed": None,
            "login_error": "",
        },
    )
    def test_codex_status_reports_subscription_state_without_credentials(self, status):
        response = self.client.get("/api/codex/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["method"], "chatgpt")
        self.assertNotIn("token", body)
        self.assertNotIn("api_key", body)
        status.assert_called_once_with()

    @mock.patch(
        "app.codex_acp_runtime.start_device_login",
        return_value={
            "verification_url": "https://auth.openai.com/codex/device",
            "user_code": "ABCD-12345",
            "reused": False,
        },
    )
    def test_device_login_endpoint_returns_only_browser_handoff(self, start):
        response = self.client.post("/api/codex/login/device", json={})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["verificationUrl"], "https://auth.openai.com/codex/device")
        self.assertEqual(body["userCode"], "ABCD-12345")
        self.assertNotIn("token", body)
        self.assertNotIn("api_key", body)
        start.assert_called_once_with()


class CodexProviderFrontendContractTest(unittest.TestCase):
    def test_provider_editor_has_subscription_mode_without_api_fields(self):
        html = (Path(__file__).parents[1] / "static" / "index.html").read_text()
        self.assertIn('<option value="codex">Codex Subscription</option>', html)
        self.assertIn('value==="codex"?"Codex Subscription"', html)
        self.assertIn('$("#provKey").closest(".field").hidden=isCodex', html)
        self.assertIn('$("#provUrl").closest(".field").hidden=isCodex', html)
        self.assertIn('body.format!=="codex"&&!body.api_url', html)
        self.assertIn('label:"Codex Subscription"', html)
        self.assertIn('id="provCodexAuth"', html)
        self.assertIn('id="provCodexLogin"', html)
        self.assertIn('id="provCodexCopy"', html)
        self.assertIn('function copyCodexDeviceCode()', html)
        self.assertIn('api("/api/codex/status")', html)
        self.assertIn('api("/api/codex/login/device"', html)
        self.assertIn('url.hostname!=="auth.openai.com"', html)


if __name__ == "__main__":
    unittest.main()
