import tempfile
import unittest
from pathlib import Path

from backend.storage import Storage


class StorageTests(unittest.TestCase):
    def test_session_messages_survive_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "state.db")
            store = Storage(database)
            store.append_message("s1", "user", "Fix the bug", "/workspace")
            store.append_message("s1", "assistant", "Fixed", "/workspace", 1.2)
            reopened = Storage(database)
            session = reopened.get_session("s1")
            self.assertEqual([item["role"] for item in session["messages"]], ["user", "assistant"])
            self.assertEqual(reopened.list_sessions()[0]["message_count"], 2)

    def test_json_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.json"
            history.write_text('[{"id":"old","title":"Old","messages":[{"role":"user","content":"hello"}]}]', encoding="utf-8")
            database = str(root / "state.db")
            Storage(database, str(history))
            store = Storage(database, str(history))
            self.assertEqual(len(store.get_session("old")["messages"]), 1)


if __name__ == "__main__":
    unittest.main()
