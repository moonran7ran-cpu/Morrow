import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import app
import chat_engine


class CompanionProfileApiTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_profile_requires_auth_and_persists_three_stable_fields(self):
        client = app.app.test_client()
        with mock.patch.object(app, "AUTH_PASSWORD", "synthetic-password"), mock.patch.object(app, "get_db", return_value=self.db):
            self.assertEqual(client.get("/api/companion-profile").status_code, 401)
            with client.session_transaction() as session:
                session["authed"] = True
            payload = {
                "user_profile": "染染的稳定资料",
                "relationship_core": "阿砚与我们的关系核心",
                "user_prompt": "长期明确指令",
            }
            response = client.put("/api/companion-profile", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, payload)
            self.assertEqual(client.get("/api/companion-profile").json, payload)
            self.assertEqual(client.put("/api/companion-profile", json={"unknown": "x"}).status_code, 400)


class CompanionStableContextTest(unittest.TestCase):
    def test_three_fields_render_as_separate_stable_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "profile.db")
            with closing(sqlite3.connect(db_path)) as db:
                db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '')")
                db.executemany(
                    "INSERT INTO settings(key,value) VALUES (?,?)",
                    [
                        ("companion_user_profile", "染染资料"),
                        ("companion_relationship_core", "关系核心"),
                        ("companion_user_prompt", "用户 Prompt"),
                    ],
                )
                db.commit()
            with mock.patch.object(chat_engine, "DB_PATH", db_path):
                sections = chat_engine.get_companion_stable_sections()
            self.assertEqual(
                sections,
                [
                    "【染染｜稳定 Profile】\n染染资料",
                    "【阿砚与我们｜稳定 Identity / Relationship】\n关系核心",
                    "【用户 Prompt｜明确指令】\n用户 Prompt",
                ],
            )

    def test_frontend_uses_explicit_stable_profile_fields(self):
        page = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="userProfileBox"', page)
        self.assertIn('id="relationshipCoreBox"', page)
        self.assertIn('id="userPromptBox"', page)
        self.assertIn('/api/companion-profile', page)
        self.assertNotIn('id="personaBox"', page)


if __name__ == "__main__":
    unittest.main()
