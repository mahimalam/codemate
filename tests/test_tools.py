import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tools import execute_agent_tool


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, name, **arguments):
        return execute_agent_tool(name, arguments, str(self.workspace))

    def test_paths_cannot_escape_workspace(self):
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        self.assertEqual(self.run_tool("read_file_range", path="../outside.txt")["error"]["code"], "path_outside_workspace")
        self.assertEqual(self.run_tool("write_file", path="../outside.txt", content="changed")["error"]["code"], "path_outside_workspace")
        self.assertEqual(outside.read_text(encoding="utf-8"), "private")

    def test_symlink_cannot_escape_workspace(self):
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        (self.workspace / "link.txt").symlink_to(outside)
        result = self.run_tool("read_file_range", path="link.txt")
        self.assertEqual(result["error"]["code"], "path_outside_workspace")

    def test_patch_requires_one_nonempty_match(self):
        path = self.workspace / "sample.txt"
        path.write_text("same\nsame\n", encoding="utf-8")
        empty = self.run_tool("apply_file_diff", path="sample.txt", target_block="", replacement_block="new")
        ambiguous = self.run_tool("apply_file_diff", path="sample.txt", target_block="same", replacement_block="new")
        self.assertEqual(empty["error"]["code"], "empty_patch_target")
        self.assertEqual(ambiguous["error"]["code"], "ambiguous_patch_target")

    def test_patch_honors_revision(self):
        path = self.workspace / "sample.txt"
        path.write_text("old", encoding="utf-8")
        read = self.run_tool("read_file_range", path="sample.txt")
        path.write_text("changed", encoding="utf-8")
        result = self.run_tool("apply_file_diff", path="sample.txt", target_block="changed", replacement_block="new", expected_sha256=read["sha256"])
        self.assertEqual(result["error"]["code"], "stale_file_revision")
        self.assertEqual(path.read_text(encoding="utf-8"), "changed")

    def test_success_result_has_observation_contract(self):
        result = self.run_tool("write_file", path="nested/file.txt", content="hello")
        self.assertEqual(result["status"], "success")
        self.assertIn("summary", result)
        self.assertIn("next_actions", result)
        self.assertEqual(result["artifacts"][0]["path"], "nested/file.txt")

    def test_search_is_bounded_and_structured(self):
        (self.workspace / "one.py").write_text("needle\nneedle\n", encoding="utf-8")
        result = self.run_tool("search_files", query="needle", max_results=1)
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["matches"][0]["path"], "one.py")

    def test_linux_command_sandbox_cannot_read_parent(self):
        outside = self.root / "outside.txt"
        outside.write_text("private", encoding="utf-8")
        result = self.run_tool("run_terminal_command", command="cat ../outside.txt", timeout_seconds=5)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("private", result.get("output", ""))

    def test_whole_device_command_can_access_outside_workspace(self):
        outside = self.root / "outside.txt"
        outside.write_text("device-visible", encoding="utf-8")
        result = self.run_tool("run_terminal_command", command="cat ../outside.txt", timeout_seconds=5, _access_scope="whole_device")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["access_scope"], "whole_device")
        self.assertFalse(result["sandboxed"])
        self.assertIn("device-visible", result["output"])

    def test_web_search_is_bounded_and_structured(self):
        class Search:
            def text(self, query, max_results):
                return [{"title": "Result", "href": "https://example.com/page", "body": "Snippet"}] * max_results

        with patch("backend.tools.DDGS", return_value=Search()):
            result = self.run_tool("web_search", query="current docs", max_results=2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["results"][0]["url"], "https://example.com/page")

    def test_web_page_blocks_private_networks(self):
        result = self.run_tool("fetch_web_page", url="http://127.0.0.1/private")
        self.assertEqual(result["error"]["code"], "private_address_blocked")


if __name__ == "__main__":
    unittest.main()
