"""Provider protocol adapters and bounded model routing."""

from __future__ import annotations

import json
from typing import Any, Iterator
import urllib.error
import urllib.request

try:
    from .model_catalog import COMPLEX_KILO_MODELS, FAST_KILO_MODELS
except ImportError:
    from model_catalog import COMPLEX_KILO_MODELS, FAST_KILO_MODELS


STANDARD_USER_AGENT = "VexP-IDE/3"
OPENAI_COMPATIBLE = {"openai", "openrouter", "gemini", "cerebras", "groq", "github_models", "aihorde", "freellmapi", "pollinations", "kilo"}


def _event(kind: str, payload: Any):
    return kind, payload


def _resolve(provider: str, cfg: dict) -> tuple[str, dict]:
    if provider.startswith("custom_"):
        provider_id = provider.removeprefix("custom_")
        profile = next((item for item in cfg.get("custom_providers", []) if item.get("id") == provider_id), None)
        if not profile:
            raise ValueError(f"Custom provider '{provider}' was not found.")
        if not profile.get("enabled", True):
            raise ValueError(f"Custom provider '{provider}' is disabled.")
        return str(profile.get("type") or "openai"), dict(profile)
    profile = cfg.get("providers", {}).get(provider)
    if not isinstance(profile, dict):
        raise ValueError(f"Provider '{provider}' was not found.")
    if not profile.get("enabled", True):
        raise ValueError(f"Provider '{provider}' is disabled.")
    return provider, dict(profile)


def _normalize_openai_messages(messages: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for message in messages:
        item = dict(message)
        if item.get("role") == "assistant" and item.get("tool_calls"):
            calls = []
            for call in item["tool_calls"]:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments", "{}")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                function["arguments"] = arguments
                calls.append({"id": call.get("id"), "type": "function", "function": function})
            item["tool_calls"] = calls
        if item.get("role") == "tool" and not item.get("tool_call_id"):
            raise ValueError("A tool result is missing tool_call_id.")
        normalized.append(item)
    return normalized


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system: list[str] = []
    output: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(str(message.get("content") or ""))
            continue
        if role == "assistant":
            blocks: list[dict] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                arguments = call.get("function", {}).get("arguments", {})
                if isinstance(arguments, str):
                    arguments = json.loads(arguments or "{}")
                blocks.append({"type": "tool_use", "id": call.get("id"), "name": call.get("function", {}).get("name"), "input": arguments})
            output.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not call_id:
                raise ValueError("An Anthropic tool result is missing tool_call_id.")
            block = {"type": "tool_result", "tool_use_id": call_id, "content": str(message.get("content") or "")}
            if message.get("is_error"):
                block["is_error"] = True
            if output and output[-1]["role"] == "user" and isinstance(output[-1]["content"], list):
                output[-1]["content"].append(block)
            else:
                output.append({"role": "user", "content": [block]})
            continue
        if role == "user":
            output.append({"role": "user", "content": str(message.get("content") or "")})
    return "\n".join(system).strip(), output


def _stream_ollama(model: str, messages: list[dict], tools: list[dict], profile: dict, cfg: dict) -> Iterator[tuple[str, Any]]:
    endpoint = f"{profile.get('base_url', 'http://127.0.0.1:11434').rstrip('/')}/api/chat"
    payload = {"model": model, "messages": _normalize_openai_messages(messages), "stream": True}
    if tools:
        payload["tools"] = tools
    accumulated, calls = "", []
    try:
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": STANDARD_USER_AGENT})
        with urllib.request.urlopen(request, timeout=180) as response:
            for raw in response:
                if not raw.strip():
                    continue
                chunk = json.loads(raw.decode("utf-8"))
                message = chunk.get("message") or {}
                text = message.get("content") or ""
                if text:
                    accumulated += text
                    yield _event("token", text)
                for call in message.get("tool_calls") or []:
                    normalized = {"id": call.get("id") or f"ollama_{len(calls)}", "type": "function", "function": dict(call.get("function") or {})}
                    calls.append(normalized)
                    yield _event("tool_call", normalized)
                if chunk.get("done"):
                    break
        yield _event("done", {"content": accumulated, "tool_calls": calls})
    except Exception as exc:
        yield _event("error", str(exc))


def _stream_openai(protocol: str, model: str, messages: list[dict], tools: list[dict], profile: dict, cfg: dict, tier: str) -> Iterator[tuple[str, Any]]:
    base = str(profile.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": _normalize_openai_messages(messages), "stream": True}
    if tools and protocol != "pollinations":
        payload["tools"] = tools
    api_key = str(profile.get("api_key") or "").strip()
    if protocol == "aihorde" and not api_key:
        api_key = "0000000000"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json", "User-Agent": STANDARD_USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        if protocol in {"gemini", "anthropic"}:
            headers["x-api-key"] = api_key
    if protocol == "openrouter":
        headers.update({"HTTP-Referer": "https://github.com/vexp/claude-code-ide", "X-Title": "VexP Code IDE"})
    accumulated = ""
    call_parts: dict[int, dict] = {}
    timeout = 30 if tier == "fast" else 120
    try:
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                if text.startswith("data:"):
                    text = text[5:].strip()
                if text == "[DONE]":
                    break
                try:
                    chunk = json.loads(text)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content") or ""
                if piece:
                    accumulated += piece
                    yield _event("token", piece)
                for fragment in delta.get("tool_calls") or []:
                    index = int(fragment.get("index", 0))
                    part = call_parts.setdefault(index, {"id": fragment.get("id") or f"call_{index}", "type": "function", "function": {"name": "", "arguments": ""}})
                    if fragment.get("id"):
                        part["id"] = fragment["id"]
                    function = fragment.get("function") or {}
                    part["function"]["name"] += function.get("name") or ""
                    part["function"]["arguments"] += function.get("arguments") or ""
        calls: list[dict] = []
        for index in sorted(call_parts):
            call = call_parts[index]
            try:
                parsed = json.loads(call["function"]["arguments"] or "{}")
            except ValueError as exc:
                yield _event("error", f"Provider returned invalid arguments for {call['function']['name']}: {exc}")
                return
            call["function"]["arguments"] = parsed
            calls.append(call)
            yield _event("tool_call", call)
        yield _event("done", {"content": accumulated, "tool_calls": calls})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        exc.close()
        yield _event("error", f"Provider HTTP {exc.code}: {detail or exc.reason}")
    except Exception as exc:
        yield _event("error", str(exc))


def _stream_anthropic(model: str, messages: list[dict], tools: list[dict], profile: dict) -> Iterator[tuple[str, Any]]:
    base = str(profile.get("base_url") or "https://api.anthropic.com/v1").rstrip("/")
    endpoint = base if base.endswith("/messages") else f"{base}/messages"
    system, converted = _anthropic_messages(messages)
    payload: dict[str, Any] = {"model": model, "system": system, "messages": converted, "max_tokens": 8192, "stream": True}
    if tools:
        payload["tools"] = [{"name": item["function"]["name"], "description": item["function"].get("description", ""), "input_schema": item["function"]["parameters"]} for item in tools]
    key = str(profile.get("api_key") or "")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": STANDARD_USER_AGENT, "x-api-key": key, "anthropic-version": "2023-06-01"}
    accumulated, calls = "", []
    current: dict | None = None
    try:
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=180) as response:
            for raw in response:
                text = raw.decode("utf-8", errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                try:
                    event = json.loads(text[5:].strip())
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use":
                    block = event["content_block"]
                    current = {"id": block.get("id"), "type": "function", "function": {"name": block.get("name"), "arguments": ""}}
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        piece = delta.get("text") or ""
                        accumulated += piece
                        yield _event("token", piece)
                    elif delta.get("type") == "input_json_delta" and current:
                        current["function"]["arguments"] += delta.get("partial_json") or ""
                elif kind == "content_block_stop" and current:
                    try:
                        current["function"]["arguments"] = json.loads(current["function"]["arguments"] or "{}")
                    except ValueError as exc:
                        yield _event("error", f"Anthropic returned invalid tool arguments: {exc}")
                        return
                    calls.append(current)
                    yield _event("tool_call", current)
                    current = None
                elif kind == "error":
                    yield _event("error", str(event.get("error", {}).get("message") or "Anthropic stream error"))
                    return
                elif kind == "message_stop":
                    break
        yield _event("done", {"content": accumulated, "tool_calls": calls})
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        exc.close()
        yield _event("error", f"Provider HTTP {exc.code}: {detail or exc.reason}")
    except Exception as exc:
        yield _event("error", str(exc))


def _candidates(cfg: dict, tier: str, local_only: bool, requires_tools: bool = False) -> list[tuple[str, str]]:
    providers = cfg.get("providers", {})
    order = ["cerebras", "groq", "gemini", "github_models", "openrouter"] if tier == "fast" else ["gemini", "openrouter", "github_models", "cerebras", "groq"]
    candidates: list[tuple[str, str]] = []
    for provider in order:
        profile = providers.get(provider, {})
        if not local_only and profile.get("enabled") and profile.get("api_key") and profile.get("model"):
            candidates.append((provider, profile["model"]))
    if not local_only:
        gateway = providers.get("freellmapi", {})
        if gateway.get("enabled"):
            candidates.append(("freellmapi", "auto:fast" if tier == "fast" else "auto:smart"))
        kilo = providers.get("kilo", {})
        kilo_models = FAST_KILO_MODELS if tier == "fast" else COMPLEX_KILO_MODELS
        if kilo.get("enabled"):
            candidates.extend(("kilo", model) for model in kilo_models[:2])
        pollinations = providers.get("pollinations", {})
        if pollinations.get("enabled") and not requires_tools:
            candidates.append(("pollinations", pollinations.get("model") or "openai-fast"))
        if kilo.get("enabled"):
            candidates.extend(("kilo", model) for model in kilo_models[2:5])
    ollama = providers.get("ollama", {})
    if ollama.get("enabled") and ollama.get("model"):
        candidates.append(("ollama", ollama["model"]))
    if not local_only:
        if kilo.get("enabled"):
            candidates.extend(("kilo", model) for model in kilo_models[5:7])
        horde = providers.get("aihorde", {})
        if horde.get("enabled") and horde.get("model") and not requires_tools:
            candidates.append(("aihorde", horde["model"]))
    unique: list[tuple[str, str]] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique[:10]


def stream_llm_turn(provider: str, model: str, messages: list[dict], tools: list[dict], cfg: dict, tier: str = "fast", local_only: bool = False) -> Iterator[tuple[str, Any]]:
    """Stream one model turn. Only an explicit pool selection performs failover."""
    if provider == "free_pool":
        attempts = _candidates(cfg, tier, local_only, requires_tools=bool(tools))
        if not attempts:
            yield _event("error", "No eligible provider is configured for this routing policy.")
            return
        errors: list[str] = []
        for candidate, candidate_model in attempts:
            yield _event("route_selected", {"provider": candidate, "model": candidate_model})
            emitted = False
            for kind, payload in stream_llm_turn(candidate, candidate_model, messages, tools, cfg, tier, local_only):
                if kind == "error":
                    errors.append(f"{candidate}: {payload}")
                    if emitted:
                        yield kind, payload
                        return
                    break
                emitted = True
                yield kind, payload
            if emitted:
                return
        yield _event("error", "All eligible providers failed: " + " | ".join(errors[-3:]))
        return
    try:
        protocol, profile = _resolve(provider, cfg)
    except ValueError as exc:
        yield _event("error", str(exc))
        return
    if local_only and protocol != "ollama":
        yield _event("error", "Local-only mode blocked a non-local provider.")
        return
    if tools and protocol == "pollinations":
        yield _event("error", "The selected Pollinations route does not support structured agent tools. Choose an agent-capable model or use the automatic pool.")
        return
    if protocol == "ollama":
        yield from _stream_ollama(model, messages, tools, profile, cfg)
    elif protocol == "anthropic":
        yield from _stream_anthropic(model, messages, tools, profile)
    elif protocol in OPENAI_COMPATIBLE or protocol == "openai":
        yield from _stream_openai(protocol, model, messages, tools, profile, cfg, tier)
    else:
        yield _event("error", f"Unsupported provider protocol: {protocol}")
