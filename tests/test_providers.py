import io
import json
import unittest
from unittest.mock import patch
import urllib.error

from backend.providers import _anthropic_messages, _candidates, _normalize_openai_messages, stream_llm_turn
from backend.model_catalog import build_model_catalog


class Response:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def __iter__(self):
        return iter(self.lines)


class ProviderTests(unittest.TestCase):
    def test_enabled_custom_model_is_visible_in_both_picker_tiers(self):
        config = {
            "providers": {},
            "custom_providers": [{"id": "acme", "name": "Acme API", "enabled": True, "model": "acme-code-1"}],
        }
        fast, complex_models = build_model_catalog(config, [])
        expected = ("custom_acme", "acme-code-1", "Acme API · acme-code-1")
        for catalog in (fast, complex_models):
            self.assertIn(expected, [(item["provider"], item["model"], item["name"]) for item in catalog])

    def test_openai_tool_roundtrip_keeps_ids_and_json_arguments(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "read_file_range", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        normalized = _normalize_openai_messages(messages)
        self.assertEqual(normalized[0]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(json.loads(normalized[0]["tool_calls"][0]["function"]["arguments"]), {"path": "a.py"})
        self.assertEqual(normalized[1]["tool_call_id"], "call_1")

    def test_anthropic_roundtrip_uses_tool_blocks(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "function": {"name": "read_file_range", "arguments": {"path": "a.py"}}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "result"},
        ]
        system, converted = _anthropic_messages(messages)
        self.assertEqual(system, "system")
        self.assertEqual(converted[0]["content"][0]["type"], "tool_use")
        self.assertEqual(converted[1]["content"][0]["tool_use_id"], "call_1")

    def test_custom_provider_uses_its_own_endpoint(self):
        captured = []

        def fake_urlopen(request, timeout):
            captured.append(request.full_url)
            return Response([b"data: [DONE]\n"])

        config = {
            "providers": {"openai": {"enabled": True, "base_url": "https://built-in.invalid/v1"}},
            "custom_providers": [{"id": "custom", "enabled": True, "type": "openai", "base_url": "https://custom.invalid/v1", "api_key": "fixture"}],
        }
        with patch("backend.providers.urllib.request.urlopen", fake_urlopen):
            list(stream_llm_turn("custom_custom", "fixture", [], [], config))
        self.assertEqual(captured, ["https://custom.invalid/v1/chat/completions"])

    def test_answer_json_is_not_rescued_by_default(self):
        fenced = 'data: {"choices":[{"delta":{"content":"```json\\n{\\\"name\\\":\\\"write_file\\\",\\\"arguments\\\":{}}\\n```"}}]}\n'
        config = {"providers": {"openai": {"enabled": True, "base_url": "https://fixture.invalid/v1", "api_key": "fixture"}}}
        tool = {"type": "function", "function": {"name": "write_file", "description": "write", "parameters": {"type": "object", "properties": {}}}}
        with patch("backend.providers.urllib.request.urlopen", lambda request, timeout: Response([fenced.encode(), b"data: [DONE]\n"])):
            events = list(stream_llm_turn("openai", "fixture", [], [tool], config))
        self.assertFalse(any(kind == "tool_call" for kind, _ in events))

    def test_pool_tries_distinct_tier_models_with_a_bound(self):
        calls = []

        def unavailable(request, timeout):
            payload = json.loads(request.data)
            calls.append((request.full_url, payload["model"]))
            raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO(b"{}"))

        config = {"providers": {
            "kilo": {"enabled": True, "base_url": "https://kilo.invalid/v1", "model": "kilo"},
            "pollinations": {"enabled": True, "base_url": "https://poll.invalid/v1", "model": "poll"},
            "ollama": {"enabled": False},
        }}
        with patch("backend.providers.urllib.request.urlopen", unavailable):
            events = list(stream_llm_turn("free_pool", "auto", [], [], config))
        self.assertGreater(len(calls), 2)
        self.assertLessEqual(len(calls), 10)
        self.assertEqual(len(set(calls)), len(calls))
        self.assertEqual(calls[0][1], "kilo-auto/free")
        self.assertEqual(calls[2][0], "https://poll.invalid/v1/chat/completions")
        self.assertEqual(events[-1][0], "error")

    def test_fast_and_complex_pools_start_with_different_models(self):
        config = {"providers": {
            "kilo": {"enabled": True},
            "ollama": {"enabled": False},
        }}
        self.assertEqual(_candidates(config, "fast", False)[0], ("kilo", "kilo-auto/free"))
        self.assertEqual(_candidates(config, "complex", False)[0], ("kilo", "nvidia/nemotron-3-super-120b-a12b:free"))

    def test_tool_routes_exclude_non_tool_providers(self):
        config = {"providers": {
            "kilo": {"enabled": True},
            "pollinations": {"enabled": True, "model": "openai-fast"},
            "aihorde": {"enabled": True, "model": "community"},
            "ollama": {"enabled": False},
        }}
        candidates = _candidates(config, "complex", False, requires_tools=True)
        self.assertTrue(candidates)
        self.assertNotIn("pollinations", {provider for provider, _ in candidates})
        self.assertNotIn("aihorde", {provider for provider, _ in candidates})

    def test_explicit_non_tool_provider_fails_instead_of_pretending_to_act(self):
        config = {"providers": {"pollinations": {"enabled": True, "base_url": "https://poll.invalid/v1", "model": "openai-fast"}}}
        tool = {"type": "function", "function": {"name": "read_file_range", "description": "read", "parameters": {"type": "object", "properties": {}}}}
        events = list(stream_llm_turn("pollinations", "openai-fast", [], [tool], config))
        self.assertEqual(events[-1][0], "error")
        self.assertIn("does not support structured agent tools", events[-1][1])


if __name__ == "__main__":
    unittest.main()
