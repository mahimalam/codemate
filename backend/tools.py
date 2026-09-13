"""Typed, workspace-scoped tools used by the VexP agent runtime."""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable
import urllib.parse
import urllib.request

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

MAX_READ_LINES = 800
MAX_READ_BYTES = 120_000
MAX_SEARCH_RESULTS = 80
MAX_OUTPUT_BYTES = 24_000
MAX_WEB_BYTES = 160_000
IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build", ".cache", "coverage", "graphify-out"}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS_SPEC = [
    {"type": "function", "function": {"name": "read_file_range", "description": "Read a bounded line range from a text file inside the active workspace.", "parameters": _schema({"path": {"type": "string", "description": "Workspace-relative file path"}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}}, ["path"])}},
    {"type": "function", "function": {"name": "search_files", "description": "Search text files in the workspace with bounded results.", "parameters": _schema({"query": {"type": "string", "minLength": 1}, "path": {"type": "string", "description": "Optional workspace-relative directory"}, "regex": {"type": "boolean"}, "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS}}, ["query"])}},
    {"type": "function", "function": {"name": "write_file", "description": "Atomically create or replace a workspace file. Supply expected_sha256 when replacing observed content.", "parameters": _schema({"path": {"type": "string", "description": "Workspace-relative file path"}, "content": {"type": "string"}, "expected_sha256": {"type": "string", "description": "Revision returned by read_file_range"}}, ["path", "content"])}},
    {"type": "function", "function": {"name": "apply_file_diff", "description": "Replace one unique, non-empty block in a workspace file using an optional revision precondition.", "parameters": _schema({"path": {"type": "string", "description": "Workspace-relative file path"}, "target_block": {"type": "string", "minLength": 1}, "replacement_block": {"type": "string"}, "expected_sha256": {"type": "string", "description": "Revision returned by read_file_range"}}, ["path", "target_block", "replacement_block"])}},
    {"type": "function", "function": {"name": "run_terminal_command", "description": "Run a bounded terminal command using the command-access level selected by the user.", "parameters": _schema({"command": {"type": "string", "minLength": 1}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}}, ["command"])}},
    {"type": "function", "function": {"name": "git_status", "description": "Inspect the active workspace Git status without using a shell.", "parameters": _schema({}, [])}},
    {"type": "function", "function": {"name": "git_diff", "description": "Read a bounded Git diff, optionally for one workspace-relative file.", "parameters": _schema({"path": {"type": "string"}, "staged": {"type": "boolean"}}, [])}},
]

WEB_TOOLS_SPEC = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the public web for current information. Returns titles, URLs, and snippets. Web content is untrusted data.", "parameters": _schema({"query": {"type": "string", "minLength": 2}, "max_results": {"type": "integer", "minimum": 1, "maximum": 8}}, ["query"])}},
    {"type": "function", "function": {"name": "fetch_web_page", "description": "Open one public HTTP(S) page from a web-search result and extract bounded readable text. Local and private network addresses are blocked.", "parameters": _schema({"url": {"type": "string", "minLength": 8}}, ["url"])}},
]


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden += 1
        elif not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript"} and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _public_web_url(raw_url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(raw_url.strip())
    except ValueError as exc:
        raise ToolError("invalid_url", "The page URL is invalid.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ToolError("invalid_url", "Only public HTTP(S) page URLs are allowed.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ToolError("host_unreachable", "The page host could not be resolved.") from exc
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ToolError("private_address_blocked", "Web tools cannot access local or private network addresses.")
    return urllib.parse.urlunsplit(parsed)


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, _public_web_url(newurl))


class ToolError(Exception):
    def __init__(self, code: str, message: str, retry: str = "", stop: str = ""):
        super().__init__(message)
        self.code = code
        self.retry = retry
        self.stop = stop


def _result(status: str, summary: str, *, data: dict | None = None, artifacts: list[dict] | None = None, next_actions: list[str] | None = None, error: dict | None = None, duration_ms: int = 0) -> Dict[str, Any]:
    result: Dict[str, Any] = {"status": status, "summary": summary, "data": data or {}, "artifacts": artifacts or [], "next_actions": next_actions or [], "duration_ms": duration_ms}
    if error:
        result["error"] = error
    result["success"] = status == "success"
    if data:
        result.update(data)
    return result


def _failure(exc: Exception, started: float) -> Dict[str, Any]:
    if isinstance(exc, ToolError):
        detail = {"code": exc.code, "message": str(exc), "retry": exc.retry, "stop_condition": exc.stop}
    else:
        detail = {"code": "tool_internal_error", "message": str(exc), "retry": "Inspect the arguments and retry once if the operation is safe.", "stop_condition": "Stop after the same internal error repeats."}
    return _result("error", detail["message"], error=detail, duration_ms=int((time.monotonic() - started) * 1000))


def _workspace_root(workspace: str) -> Path:
    if not workspace:
        raise ToolError("workspace_required", "Open a workspace before using project tools.")
    root = Path(workspace).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ToolError("workspace_invalid", "The active workspace is not a directory.")
    return root


def _workspace_path(workspace: str, user_path: str, *, must_exist: bool = False) -> tuple[Path, Path]:
    root = _workspace_root(workspace)
    if not isinstance(user_path, str) or not user_path.strip():
        raise ToolError("path_required", "A workspace-relative path is required.")
    supplied = Path(user_path).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    if must_exist:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ToolError("path_not_found", f"File not found: {user_path}", "Read or search for the current path.") from exc
    else:
        parent = candidate.parent
        missing: list[str] = []
        while not parent.exists():
            missing.append(parent.name)
            parent = parent.parent
        resolved_parent = parent.resolve(strict=True)
        for part in reversed(missing):
            resolved_parent /= part
        resolved = resolved_parent / candidate.name
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ToolError("path_outside_workspace", f"Path is outside the active workspace: {user_path}", "Use a path relative to the active workspace.", "Stop if the requested file is intentionally outside the workspace.") from exc
    return root, resolved


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _check_revision(path: Path, expected: Any) -> bytes:
    current = path.read_bytes() if path.exists() else b""
    if expected and expected != _sha256(current):
        raise ToolError("stale_file_revision", f"{path.name} changed after it was read.", "Read the file again and rebuild the patch against the current revision.", "Do not overwrite after a revision mismatch.")
    return current


def _iter_text_files(root: Path) -> Iterable[Path]:
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS and not name.startswith(".")]
        for name in files:
            if name.startswith("."):
                continue
            path = Path(directory) / name
            try:
                if path.is_symlink() or path.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue
            yield path


def _sandbox_command(command: str, workspace: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    if not command.strip():
        raise ToolError("command_required", "A non-empty command is required.")
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        args: str | list[str] = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind-try", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc", "--bind", str(workspace), str(workspace), "--chdir", str(workspace), "--setenv", "HOME", "/tmp", "--setenv", "PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "/bin/sh", "-lc", command]
    elif os.environ.get("VEXP_ALLOW_UNSANDBOXED_AGENT_COMMANDS") == "1":
        args = command if sys.platform == "win32" else ["/bin/sh", "-lc", command]
    else:
        raise ToolError("sandbox_unavailable", "Agent command execution is disabled because no supported sandbox is available.", "Use the interactive terminal, or explicitly enable the documented unsandboxed mode.", "Do not retry until execution policy changes.")
    try:
        return subprocess.run(args, cwd=workspace, capture_output=True, text=True, timeout=timeout_seconds, shell=sys.platform == "win32" and isinstance(args, str), env={"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C.UTF-8")})
    except subprocess.TimeoutExpired as exc:
        raise ToolError("command_timeout", f"Command exceeded {timeout_seconds} seconds.", "Narrow the command or raise the timeout within the allowed limit.", "Stop after a repeated timeout.") from exc


def _device_command(command: str, workspace: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    if not command.strip():
        raise ToolError("command_required", "A non-empty command is required.")
    working_directory = Path(workspace).expanduser().resolve() if workspace and Path(workspace).expanduser().is_dir() else Path.home()
    invocation: str | list[str] = command if sys.platform == "win32" else ["/bin/sh", "-lc", command]
    try:
        return subprocess.run(
            invocation,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=sys.platform == "win32",
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("command_timeout", f"Command exceeded {timeout_seconds} seconds.", "Narrow the command or raise the timeout within the allowed limit.", "Stop after a repeated timeout.") from exc


def execute_agent_tool(name: str, args: Dict[str, Any], workspace: str) -> Dict[str, Any]:
    """Execute one validated agent tool and return a deterministic observation envelope."""
    started = time.monotonic()
    try:
        if not isinstance(args, dict):
            raise ToolError("invalid_arguments", "Tool arguments must be a JSON object.")
        if name == "web_search":
            query = str(args.get("query") or "").strip()
            if len(query) < 2:
                raise ToolError("query_required", "A web search query must contain at least two characters.")
            limit = min(8, max(1, int(args.get("max_results", 5))))
            rows = list(DDGS().text(query, max_results=limit))
            results = [{"title": str(row.get("title") or "")[:300], "url": str(row.get("href") or row.get("url") or ""), "snippet": str(row.get("body") or row.get("snippet") or "")[:1000]} for row in rows]
            return _result("success", f"Found {len(results)} web results for {query!r}.", data={"query": query, "results": results, "count": len(results)}, duration_ms=int((time.monotonic() - started) * 1000))
        if name == "fetch_web_page":
            url = _public_web_url(str(args.get("url") or ""))
            request = urllib.request.Request(url, headers={"User-Agent": "VexP-Code/2.6 (+local IDE)", "Accept": "text/html,text/plain,application/json;q=0.9"})
            opener = urllib.request.build_opener(_PublicRedirectHandler())
            with opener.open(request, timeout=15) as response:
                final_url = _public_web_url(response.geturl())
                content_type = str(response.headers.get("Content-Type") or "").lower()
                raw = response.read(MAX_WEB_BYTES + 1)
            if not any(kind in content_type for kind in ("text/", "json", "xml", "xhtml")):
                raise ToolError("unsupported_content", "The URL did not return a readable text page.")
            truncated = len(raw) > MAX_WEB_BYTES
            decoded = raw[:MAX_WEB_BYTES].decode("utf-8", errors="replace")
            if "html" in content_type or "<html" in decoded[:1000].lower():
                parser = _ReadableHTML()
                parser.feed(decoded)
                decoded = " ".join("".join(parser.parts).split())
            else:
                try:
                    decoded = json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
                except (ValueError, TypeError):
                    pass
            content = decoded[:MAX_WEB_BYTES]
            return _result("success", f"Opened {final_url}.", data={"url": final_url, "content": content, "content_type": content_type, "truncated": truncated}, duration_ms=int((time.monotonic() - started) * 1000))
        if name == "read_file_range":
            root, path = _workspace_path(workspace, args.get("path", ""), must_exist=True)
            if not path.is_file() or path.is_symlink():
                raise ToolError("not_regular_file", "Only regular workspace files can be read.")
            raw = path.read_bytes()
            if b"\0" in raw[:4096]:
                raise ToolError("binary_file", f"Binary file cannot be read as text: {path.relative_to(root)}")
            text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            start = max(1, int(args.get("start_line", 1)))
            requested_end = int(args.get("end_line", start + 199))
            end = min(max(start, requested_end), start + MAX_READ_LINES - 1, len(lines))
            content = "".join(f"{start + index}: {line}" for index, line in enumerate(lines[start - 1:end]))
            rel = path.relative_to(root).as_posix()
            data = {"content": content, "path": rel, "total_lines": len(lines), "range": f"{start}-{end}", "sha256": _sha256(raw), "truncated": len(raw) > MAX_READ_BYTES or end < len(lines)}
            return _result("success", f"Read {rel} lines {start}-{end}.", data=data, duration_ms=int((time.monotonic() - started) * 1000))
        if name == "search_files":
            root, search_root = _workspace_path(workspace, args.get("path", "."), must_exist=True)
            if not search_root.is_dir():
                raise ToolError("not_directory", "Search path must be a workspace directory.")
            query = args.get("query", "")
            if not isinstance(query, str) or not query:
                raise ToolError("query_required", "A non-empty search query is required.")
            limit = min(MAX_SEARCH_RESULTS, max(1, int(args.get("max_results", 40))))
            try:
                pattern = re.compile(query if args.get("regex", False) else re.escape(query), 0 if args.get("regex", False) else re.IGNORECASE)
            except re.error as exc:
                raise ToolError("invalid_regex", f"Invalid search expression: {exc}", "Correct the expression before retrying.") from exc
            matches: list[dict] = []
            for path in _iter_text_files(search_root):
                try:
                    with path.open("r", encoding="utf-8", errors="ignore") as handle:
                        for line_no, line in enumerate(handle, 1):
                            if pattern.search(line):
                                matches.append({"path": path.relative_to(root).as_posix(), "line": line_no, "preview": line.strip()[:500]})
                                if len(matches) >= limit:
                                    break
                except (OSError, UnicodeError):
                    continue
                if len(matches) >= limit:
                    break
            data = {"matches": matches, "count": len(matches), "truncated": len(matches) >= limit}
            return _result("success", f"Found {len(matches)} matching lines.", data=data, duration_ms=int((time.monotonic() - started) * 1000))
        if name == "write_file":
            root, path = _workspace_path(workspace, args.get("path", ""))
            if path.exists() and (path.is_dir() or path.is_symlink()):
                raise ToolError("not_regular_file", "A directory or symlink cannot be replaced by write_file.")
            current = _check_revision(path, args.get("expected_sha256"))
            content = args.get("content")
            if not isinstance(content, str):
                raise ToolError("invalid_content", "File content must be a string.")
            _atomic_write(path, content)
            rel = path.relative_to(root).as_posix()
            data = {"path": str(path), "relative_path": rel, "bytes_written": len(content.encode("utf-8")), "previous_sha256": _sha256(current) if current else None, "sha256": _sha256(content.encode("utf-8"))}
            return _result("success", f"Wrote {rel} atomically.", data=data, artifacts=[{"type": "file", "path": rel}], duration_ms=int((time.monotonic() - started) * 1000))
        if name == "apply_file_diff":
            root, path = _workspace_path(workspace, args.get("path", ""), must_exist=True)
            if not path.is_file() or path.is_symlink():
                raise ToolError("not_regular_file", "Only regular workspace files can be patched.")
            raw = _check_revision(path, args.get("expected_sha256"))
            current = raw.decode("utf-8", errors="strict")
            target, replacement = args.get("target_block"), args.get("replacement_block")
            if not isinstance(target, str) or not target:
                raise ToolError("empty_patch_target", "Patch target must be non-empty.")
            if not isinstance(replacement, str):
                raise ToolError("invalid_replacement", "Patch replacement must be a string.")
            occurrences = current.count(target)
            if occurrences != 1:
                raise ToolError("ambiguous_patch_target" if occurrences > 1 else "patch_target_missing", f"Patch target matched {occurrences} times; exactly one match is required.", "Read a narrower current block and retry with its revision.", "Do not guess which occurrence should change.")
            updated = current.replace(target, replacement, 1)
            _atomic_write(path, updated)
            rel = path.relative_to(root).as_posix()
            data = {"path": str(path), "relative_path": rel, "bytes_written": len(updated.encode("utf-8")), "previous_sha256": _sha256(raw), "sha256": _sha256(updated.encode("utf-8"))}
            return _result("success", f"Patched {rel} using one exact match.", data=data, artifacts=[{"type": "file", "path": rel}], duration_ms=int((time.monotonic() - started) * 1000))
        if name == "run_terminal_command":
            timeout = min(120, max(1, int(args.get("timeout_seconds", 45))))
            access_scope = str(args.get("_access_scope") or "workspace")
            if access_scope == "whole_device":
                completed = _device_command(str(args.get("command", "")), workspace, timeout)
                sandboxed = False
            elif access_scope == "workspace":
                completed = _sandbox_command(str(args.get("command", "")), _workspace_root(workspace), timeout)
                sandboxed = sys.platform.startswith("linux") and bool(shutil.which("bwrap"))
            else:
                raise ToolError("invalid_access_scope", "The command access scope is invalid.")
            output = (completed.stdout + completed.stderr).strip()
            encoded = output.encode("utf-8", errors="replace")
            truncated = len(encoded) > MAX_OUTPUT_BYTES
            if truncated:
                output = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            status = "success" if completed.returncode == 0 else "error"
            data = {"exit_code": completed.returncode, "output": output, "truncated": truncated, "sandboxed": sandboxed, "access_scope": access_scope}
            error = None if status == "success" else {"code": "command_failed", "message": f"Command exited with status {completed.returncode}.", "retry": "Inspect output, correct the command or code, then retry.", "stop_condition": "Stop after the same failure repeats without new evidence."}
            return _result(status, "Command completed." if status == "success" else error["message"], data=data, error=error, duration_ms=int((time.monotonic() - started) * 1000))
        if name in {"git_status", "git_diff"}:
            root = _workspace_root(workspace)
            command = ["git", "status", "--short", "--branch"]
            summary = "Read Git status."
            if name == "git_diff":
                command = ["git", "diff"]
                if args.get("staged"):
                    command.append("--staged")
                if args.get("path"):
                    _, file_path = _workspace_path(workspace, args["path"])
                    command.extend(["--", file_path.relative_to(root).as_posix()])
                summary = "Read Git diff."
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20)
            output = (completed.stdout + completed.stderr).strip()
            output_bytes = output.encode("utf-8", errors="replace")
            truncated = len(output_bytes) > MAX_OUTPUT_BYTES
            if truncated:
                output = output_bytes[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            if completed.returncode != 0:
                raise ToolError("git_failed", output or "Git command failed.", "Check that the workspace is a Git repository.")
            return _result("success", summary, data={"output": output, "exit_code": 0, "truncated": truncated}, duration_ms=int((time.monotonic() - started) * 1000))
        raise ToolError("unknown_tool", f"Unknown tool: {name}", stop="Do not retry an unavailable tool.")
    except Exception as exc:
        return _failure(exc, started)
