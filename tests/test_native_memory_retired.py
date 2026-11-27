import os
import shutil
import sqlite3
import unittest
import uuid
from unittest import mock

import app
import chat_engine
import native_memory
import tools


class NativeMemoryRetiredTest(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.getcwd(), "data", "test-tmp", uuid.uuid4().hex)
        os.makedirs(self.root)
        self.old_app_paths = (app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR)
        self.old_chat_paths = (chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR)
        self.old_tools_db = tools.DB_PATH
        db_path = os.path.join(self.root, "nook.db")
        uploads = os.path.join(self.root, "uploads")
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = db_path, self.root, uploads
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = db_path, self.root, uploads
        tools.DB_PATH = db_path
        app.init_db()
        self.db_path = db_path

    def tearDown(self):
        app.DB_PATH, app.DATA_DIR, app.UPLOAD_DIR = self.old_app_paths
        chat_engine.DB_PATH, chat_engine.DATA_DIR, chat_engine.UPLOAD_DIR = self.old_chat_paths
        tools.DB_PATH = self.old_tools_db
        shutil.rmtree(self.root, ignore_errors=True)

    def _count(self, table):
        with sqlite3.connect(self.db_path) as db:
            return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_default_gate_hides_and_refuses_all_native_memory_writers(self):
        with mock.patch.dict(os.environ, {"MORROW_NATIVE_MEMORY_ENABLED": "0"}):
            names = {item["function"]["name"] for item in tools.openai_tools()}
            self.assertTrue(native_memory.WRITE_TOOL_NAMES.isdisjoint(names))
            result = tools.execute("write_memory", {"content": "不该写进去"})
            self.assertFalse(result["ok"])
            self.assertIn("已在实验 B 退役", result["error"])
            self.assertEqual(self._count("memory_entries"), 0)

    def test_http_write_endpoints_are_read_only_when_retired(self):
        client = app.app.test_client()
        client.post("/api/login", json={"password": app.AUTH_PASSWORD})
        with mock.patch.dict(os.environ, {"MORROW_NATIVE_MEMORY_ENABLED": "0"}):
            response = client.post("/api/memory/entries", json={"content": "x", "kind": "fixed"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("已在实验 B 退役", response.get_json()["error"])
            self.assertEqual(self._count("memory_entries"), 0)

    def test_fixed_and_dynamic_rows_do_not_enter_context_or_recall_ledger(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO memory_entries(content,kind,tags) VALUES ('旧固定','fixed','[]')")
            db.execute("INSERT INTO memory_entries(content,kind,tags) VALUES ('旧动态','dynamic','[\"草莓\"]')")
            db.commit()
        with mock.patch.dict(os.environ, {"MORROW_NATIVE_MEMORY_ENABLED": "0"}):
            sections = chat_engine.build_system_sections("草莓")
        self.assertNotIn("旧固定", sections["stable"])
        self.assertNotIn("旧动态", sections["volatile"])
        self.assertEqual(self._count("memory_recall_events"), 0)

    def test_explicit_opt_in_keeps_legacy_rollback_path_available(self):
        with mock.patch.dict(os.environ, {"MORROW_NATIVE_MEMORY_ENABLED": "1"}):
            names = {item["function"]["name"] for item in tools.openai_tools()}
        self.assertTrue(native_memory.WRITE_TOOL_NAMES.issubset(names))


if __name__ == "__main__":
    unittest.main()
