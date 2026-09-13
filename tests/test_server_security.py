import os
import json
import tempfile
import unittest
import stat
from unittest.mock import patch

from fastapi import HTTPException

from backend import server


class ServerBoundaryTests(unittest.TestCase):
    def test_explicit_web_request_enables_live_research(self):
        self.assertTrue(server.requests_web_research("Please search the web for this project"))
        self.assertTrue(server.requests_web_research("Do you have a web access?"))
        self.assertTrue(server.requests_web_research("Can you search the internet?"))
        self.assertTrue(server.requests_web_research("What is today's weather?"))
        self.assertTrue(server.requests_web_research("Can you download Claude Code on my Linux desktop?"))
        self.assertTrue(server.requests_web_research("Is Claude Desktop available for Ubuntu?"))
        self.assertFalse(server.requests_web_research("Search files in the current workspace"))

    def test_version_two_config_restores_free_cloud_pool(self):
        with tempfile.TemporaryDirectory() as config_dir:
            config_path = os.path.join(config_dir, "providers.json")
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "config_version": 2,
                    "active_provider": "ollama",
                    "active_model": "qwen2.5:14b",
                    "providers": {
                        "kilo": {"enabled": False},
                        "pollinations": {"enabled": False},
                        "aihorde": {"enabled": False},
                    },
                }, handle)
            with patch.object(server, "CONFIG_DIR", config_dir), patch.object(server, "PROVIDER_CONFIG_FILE", config_path):
                config = server.load_provider_config()
            self.assertEqual(config["config_version"], 3)
            self.assertEqual((config["active_provider"], config["active_model"], config["active_tier"]), ("free_pool", "fast-auto", "fast"))
            self.assertTrue(all(config["providers"][name]["enabled"] for name in ("kilo", "pollinations", "aihorde")))

    def test_ui_paths_are_confined_to_active_workspace(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            inside = os.path.join(workspace, "inside.txt")
            with open(inside, "w", encoding="utf-8") as handle:
                handle.write("inside")
            with patch.object(server, "get_active_workspace", return_value=workspace):
                self.assertEqual(server.resolve_workspace_target(inside), inside)
                with self.assertRaises(HTTPException) as caught:
                    server.resolve_workspace_target(os.path.join(outside, "outside.txt"), must_exist=False)
                self.assertEqual(caught.exception.status_code, 403)

    def test_ui_paths_reject_escaping_symlinks(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as outside:
            os.symlink(outside, os.path.join(workspace, "escape"))
            with patch.object(server, "get_active_workspace", return_value=workspace):
                with self.assertRaises(HTTPException) as caught:
                    server.resolve_workspace_target(os.path.join(workspace, "escape", "new.txt"), must_exist=False)
                self.assertEqual(caught.exception.status_code, 403)

    def test_provider_settings_do_not_return_secret(self):
        config = {"providers": {"openai": {"api_key": "secret-value", "enabled": True}}, "custom_providers": []}
        with patch.object(server, "load_provider_config", return_value=config):
            response = server.get_provider_settings()
        self.assertNotIn("api_key", response["providers"]["openai"])
        self.assertTrue(response["providers"]["openai"]["has_key"])

    def test_retired_github_models_provider_is_disabled(self):
        config = {"providers": {"github_models": {"enabled": True}}}
        self.assertTrue(server.sanitize_provider_config(config))
        self.assertFalse(config["providers"]["github_models"]["enabled"])

    def test_github_remote_rejects_embedded_credentials(self):
        self.assertEqual(
            server._clean_github_remote("https://github.com/octocat/project"),
            "https://github.com/octocat/project.git",
        )
        with self.assertRaises(HTTPException) as caught:
            server._clean_github_remote("https://token@github.com/octocat/project.git")
        self.assertEqual(caught.exception.status_code, 400)

    def test_git_output_redacts_credentials(self):
        cleaned = server.clean_git_output("failed https://name:secret@github.com/o/r.git github_pat_example_123")
        self.assertNotIn("secret", cleaned)
        self.assertNotIn("github_pat_example_123", cleaned)
        self.assertIn("https://github.com/o/r.git", cleaned)

    def test_github_connect_stores_secret_locally_but_never_returns_it(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self): return b'{"login":"octocat","name":"The Octocat"}'

        with tempfile.TemporaryDirectory() as config_dir, tempfile.TemporaryDirectory() as workspace:
            config_path = os.path.join(config_dir, "github.json")
            subprocess_result = __import__("subprocess").run(["git", "init", "-q", workspace], capture_output=True)
            self.assertEqual(subprocess_result.returncode, 0)
            payload = server.GitHubConnectPayload(
                token="fixture-secret",
                remote_url="https://github.com/octocat/project",
                username="Octocat",
                email="octocat@example.test",
                path=workspace,
            )
            with (
                patch.object(server, "CONFIG_DIR", config_dir),
                patch.object(server, "GITHUB_CONFIG_FILE", config_path),
                patch.object(server, "get_active_workspace", return_value=workspace),
                patch.object(server.urllib.request, "urlopen", return_value=Response()),
            ):
                response = server.connect_github(payload)
            self.assertTrue(response["connected"])
            self.assertEqual(response["login"], "octocat")
            self.assertNotIn("token", response)
            with open(config_path, encoding="utf-8") as handle:
                stored = json.load(handle)
            self.assertEqual(stored["token"], "fixture-secret")
            self.assertEqual(stat.S_IMODE(os.stat(config_path).st_mode), 0o600)
            remote = __import__("subprocess").run(
                ["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=workspace
            ).stdout.strip()
            self.assertEqual(remote, "https://github.com/octocat/project.git")

    def test_provider_connection_uses_saved_credentials_when_key_field_is_blank(self):
        config = {
            "providers": {"openai": {"api_key": "saved-secret", "base_url": "https://provider.invalid/v1", "model": "saved-model"}},
            "custom_providers": [],
        }
        captured = {}

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def read(self): return b"{}"

        def open_request(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return Response()

        payload = server.TestProviderPayload(provider="openai", base_url="", api_key="", model="")
        with patch.object(server, "load_provider_config", return_value=config), patch.object(server.urllib.request, "urlopen", open_request):
            response = server.test_provider_connection(payload)
        self.assertTrue(response["success"])
        self.assertEqual(captured["url"], "https://provider.invalid/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer saved-secret")
        self.assertEqual(captured["body"]["model"], "saved-model")

    def test_editor_save_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = os.path.join(workspace, "file.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("first")
            with patch.object(server, "get_active_workspace", return_value=workspace):
                opened = server.read_file(path)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("external change")
                with self.assertRaises(HTTPException) as caught:
                    server.write_file(server.WriteFilePayload(path=path, content="editor change", expected_sha256=opened["sha256"]))
                self.assertEqual(caught.exception.status_code, 409)
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "external change")


if __name__ == "__main__":
    unittest.main()
