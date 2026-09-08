"""
Tool Call Rescue Engine for Open-Source and Free LLMs.
Ported and adapted from FreeLLMAPI (https://github.com/tashfeenahmed/freellmapi).

When free or open-source models emit tool calls as plain text tags or raw JSON
rather than structured tool_calls arrays, this engine rescues them into valid
OpenAI-compatible tool_calls so agentic workflows (diffs, files, terminals)
never fail mid-task.
"""

import re
import json
import uuid
from typing import List, Dict, Any, Tuple, Optional

# Known dialect markers that signal an open-source model is emitting a tool call in text
DIALECT_MARKERS = [
    "<|tool_calls_section_begin|>",
    "<|tool_call_begin|>",
    "<tool_call>",
    "<function=",
]

KNOWN_TOOLS = {
    "read_file_range",
    "search_files",
    "write_file",
    "apply_file_diff",
    "run_terminal_command"
}

def extract_balanced_json(text: str, start_pos: int) -> Tuple[Optional[str], int]:
    """
    Extracts a balanced JSON object starting from the first '{' at or after start_pos.
    Returns (json_str, end_pos) or (None, -1) if no balanced JSON found.
    """
    brace_idx = text.find("{", start_pos)
    if brace_idx == -1:
        return None, -1

    depth = 0
    in_string = False
    escaped = False
    end_idx = -1

    for i in range(brace_idx, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

    if end_idx != -1:
        return text[brace_idx:end_idx], end_idx
    return None, -1

def rescue_tool_calls(text: str, allowed_tools: Optional[set] = None) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Scans model output text for inline tool-call dialects and extracts them
    into standard OpenAI tool_call dictionaries.

    Returns:
        (clean_text, rescued_calls)
    """
    if not text:
        return text, []

    valid_tools = allowed_tools if allowed_tools is not None else KNOWN_TOOLS
    rescued = []
    clean_text = text

    # Dialect 1: Qwen / Hermes XML tags: <tool_call> ... </tool_call>
    xml_matches = re.finditer(r"<tool_call>(.*?)</tool_call>", clean_text, re.DOTALL)
    for m in list(xml_matches):
        raw_inner = m.group(1).strip()
        try:
            parsed = json.loads(raw_inner)
            name = parsed.get("name") or parsed.get("tool") or ""
            args = parsed.get("arguments") or parsed.get("parameters") or {}
            if name in valid_tools:
                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                rescued.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args_str
                    }
                })
                clean_text = clean_text.replace(m.group(0), "")
        except Exception:
            pass

    # Dialect 2: Llama / Groq function tags: <function=NAME>{...}</function>
    func_pattern = re.compile(r"<function=([a-zA-Z0-9_\-]+)(?:>|\s*)(.*?)</function>", re.DOTALL)
    for m in list(func_pattern.finditer(clean_text)):
        fn_name = m.group(1).strip()
        payload_text = m.group(2).strip()
        if fn_name in valid_tools:
            json_blob, _ = extract_balanced_json(payload_text, 0)
            if json_blob:
                try:
                    parsed_args = json.loads(json_blob)
                    rescued.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": fn_name,
                            "arguments": json.dumps(parsed_args)
                        }
                    })
                    clean_text = clean_text.replace(m.group(0), "")
                except Exception:
                    pass

    # Dialect 3: DeepSeek / Kimi token syntax: <|tool_call_begin|>functions.NAME:0 ...
    kimi_pattern = re.compile(
        r"<\|tool_call_begin\|>functions\.([a-zA-Z0-9_\-]+)(?::\d+)?\s*<\|tool_call_argument_begin\|>(.*?)<\|tool_call_end\|>",
        re.DOTALL
    )
    for m in list(kimi_pattern.finditer(clean_text)):
        fn_name = m.group(1).strip()
        raw_args = m.group(2).strip()
        if fn_name in valid_tools:
            try:
                parsed_args = json.loads(raw_args)
                rescued.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "arguments": json.dumps(parsed_args)
                    }
                })
                clean_text = clean_text.replace(m.group(0), "")
            except Exception:
                pass

    # Dialect 4: Markdown fenced JSON blocks containing {"name": "write_file", "arguments": ...}
    fenced_blocks = re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", clean_text, re.DOTALL)
    for m in list(fenced_blocks):
        try:
            parsed = json.loads(m.group(1))
            name = parsed.get("name") or parsed.get("tool") or ""
            if name in valid_tools:
                args = parsed.get("arguments") or parsed.get("parameters") or {}
                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                rescued.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": args_str
                    }
                })
                clean_text = clean_text.replace(m.group(0), "")
        except Exception:
            pass

    # Clean residual dialect marker headers
    clean_text = re.sub(r"<\|tool_calls_section_begin\|>", "", clean_text)
    clean_text = re.sub(r"<\|tool_calls_section_end\|>", "", clean_text)
    clean_text = clean_text.strip()

    return clean_text, rescued
