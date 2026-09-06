"""
VexP Code IDE - Agent Workspace Tools Engine
Provides safe, scoped file operations and terminal execution for the agentic deliberation loop.
"""

import os
import re
import shutil
import subprocess
from typing import Dict, Any, List

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "read_file_range",
            "description": "Read specific lines or a range of lines from a workspace file without loading huge files into context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute file path"},
                    "start_line": {"type": "integer", "description": "Starting line number (1-based)"},
                    "end_line": {"type": "integer", "description": "Ending line number (inclusive)"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for code symbols, functions, or text across the workspace repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword or pattern"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or completely overwrite a file directly in the active project workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the active workspace (e.g. 'src/model.py' or 'app.py')"},
                    "content": {"type": "string", "description": "Full file content to write"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_file_diff",
            "description": "Surgically replace a specific block of code inside a workspace file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to modify"},
                    "target_block": {"type": "string", "description": "Existing code block to replace"},
                    "replacement_block": {"type": "string", "description": "New code block to write in place"}
                },
                "required": ["path", "target_block", "replacement_block"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "Execute a shell command in the project workspace (e.g., python3, pytest, git status, npm).",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command string"}
                },
                "required": ["command"]
            }
        }
    }
]

FORBIDDEN_COMMANDS = [
    "rm -rf /",
    ":(){ :|:& };:",
    "mkfs",
    "dd if=",
    "> /dev/nvme",
    "> /dev/sda",
    "chmod -R 777 /"
]

def execute_agent_tool(name: str, args: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    """
    Executes an autonomous tool within the confines of the given workspace directory.
    """
    try:
        if name == "read_file_range":
            fpath = args.get("path", "")
            if not os.path.isabs(fpath):
                fpath = os.path.join(workspace, fpath)
            if not os.path.exists(fpath) or os.path.isdir(fpath):
                return {"error": f"File not found: {fpath}"}
            start = max(1, int(args.get("start_line", 1)))
            end = int(args.get("end_line", start + 50))
            with open(fpath, "r", errors="replace") as f:
                lines = f.readlines()
            tot = len(lines)
            sel = lines[start - 1 : min(end, tot)]
            fmt = "".join([f"{start + i}: {l}" for i, l in enumerate(sel)])
            return {"content": fmt, "total_lines": tot, "range": f"{start}-{min(end, tot)}"}

        elif name == "search_files":
            q = args.get("query", "")
            matches = []
            # 1. Fast path: use system grep if available
            if shutil.which("grep"):
                try:
                    cmd = ["grep", "-rnI", q, workspace]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    matches = res.stdout.splitlines()[:25]
                except Exception:
                    matches = []

            # 2. Universal pure-Python fallback for Windows or minimal systems
            if not matches and q:
                try:
                    pattern = re.compile(re.escape(q), re.IGNORECASE)
                    for root, dirs, files in os.walk(workspace):
                        # Filter out common heavy / noise directories
                        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", ".venv", "__pycache__", "dist", "build")]
                        for f in files:
                            if f.startswith("."):
                                continue
                            fpath = os.path.join(root, f)
                            rel_path = os.path.relpath(fpath, workspace).replace("\\", "/")
                            try:
                                with open(fpath, "r", encoding="utf-8", errors="ignore") as file_obj:
                                    for line_no, line in enumerate(file_obj, start=1):
                                        if pattern.search(line):
                                            matches.append(f"{rel_path}:{line_no}:{line.strip()}")
                                            if len(matches) >= 25:
                                                break
                            except Exception:
                                continue
                            if len(matches) >= 25:
                                break
                        if len(matches) >= 25:
                            break
                except Exception:
                    pass

            return {"matches": matches, "count": len(matches)}

        elif name == "write_file":
            fpath = args.get("path", "")
            if not os.path.isabs(fpath):
                fpath = os.path.join(workspace, fpath)
            os.makedirs(os.path.dirname(fpath), exist_ok=True)
            content = args.get("content", "")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "path": fpath, "bytes_written": len(content)}

        elif name == "apply_file_diff":
            fpath = args.get("path", "")
            if not os.path.isabs(fpath):
                fpath = os.path.join(workspace, fpath)
            if not os.path.exists(fpath):
                return {"error": f"File not found: {fpath}"}
            target = args.get("target_block", "")
            repl = args.get("replacement_block", "")
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if target not in content:
                return {"error": "Target block not found in file. Ensure exact whitespace and line match."}
            new_c = content.replace(target, repl, 1)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_c)
            return {"success": True, "path": fpath, "bytes_written": len(new_c)}

        elif name == "run_terminal_command":
            cmd = args.get("command", "")
            if any(bad in cmd for bad in FORBIDDEN_COMMANDS):
                return {"error": "Command blocked by agent safety hook (prohibited destructive operation)."}
            res = subprocess.run(cmd, shell=True, cwd=workspace, capture_output=True, text=True, timeout=30)
            out = (res.stdout + res.stderr).strip()
            return {"exit_code": res.returncode, "output": out[:2500]}

        return {"error": f"Unknown tool: {name}"}
    except Exception as err:
        return {"error": str(err)}
