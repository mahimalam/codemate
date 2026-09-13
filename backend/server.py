import os
import sys
import json
import re
import time
import uuid
import threading
import subprocess
import shutil
import urllib.request
import struct
import signal
import asyncio
import secrets
import tempfile
import hashlib
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any

# Cross-platform PTY detection (Linux/macOS vs Windows)
HAS_PTY = False
if sys.platform != "win32":
    try:
        import pty
        import fcntl
        import termios
        HAS_PTY = True
    except ImportError:
        pass
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="VexP Code IDE")

SERVER_START_TIME = time.time()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
FRONTEND_DIST_DIR = os.path.join(FRONTEND_DIR, "dist")
INDEX_HTML_PATH = os.path.join(FRONTEND_DIST_DIR, "index.html")
FRONTEND_ASSETS_DIR = os.path.join(FRONTEND_DIST_DIR, "assets")
if os.path.isdir(FRONTEND_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS_DIR), name="assets")

# ── User config directory (never committed to git) ─────────────────────────
CONFIG_DIR = os.path.abspath(os.path.expanduser(os.environ.get("VEXP_CONFIG_DIR", "~/.claude_code_ide")))
os.makedirs(CONFIG_DIR, exist_ok=True)

DEFAULT_STORAGE_DIR = os.path.join(CONFIG_DIR, "storage")
MEMORY_FILE = os.path.join(CONFIG_DIR, "ai_memory.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "chat_history.json")
WORKSPACE_CONFIG_FILE = os.path.join(CONFIG_DIR, "active_workspace.json")
RECENT_WORKSPACES_FILE = os.path.join(CONFIG_DIR, "recent_workspaces.json")
PROVIDER_CONFIG_FILE = os.path.join(CONFIG_DIR, "provider_config.json")
GITHUB_CONFIG_FILE = os.path.join(CONFIG_DIR, "github.json")
DATABASE_FILE = os.path.join(CONFIG_DIR, "vexp.db")
APP_SESSION_TOKEN = os.environ.get("VEXP_SESSION_TOKEN") or secrets.token_urlsafe(32)
try:
    os.chmod(CONFIG_DIR, 0o700)
except OSError:
    pass

# ── Branding config (config/branding.json) ─────────────────────────────────
_BRANDING_FILE = os.path.join(PROJECT_ROOT, "config", "branding.json")
try:
    with open(_BRANDING_FILE, "r") as _bf:
        BRANDING = json.load(_bf)
except Exception:
    BRANDING = {}

APP_NAME    = BRANDING.get("app", {}).get("name", "AI Code IDE")
AGENT_NAME  = BRANDING.get("agent", {}).get("name", "AI Agent")
SYS_INTRO   = BRANDING.get("defaults", {}).get("system_prompt_intro", "You are an expert agentic AI software engineer.")
TERM_PROMPT = BRANDING.get("agent", {}).get("terminal_prompt", "user $")

try:
    from .tools import TOOLS_SPEC, WEB_TOOLS_SPEC, execute_agent_tool
    from .storage import Storage
    from .providers import stream_llm_turn as provider_stream_llm_turn
    from .agent_runtime import AgentRuntime
    from .model_catalog import build_model_catalog
except ImportError:
    from tools import TOOLS_SPEC, WEB_TOOLS_SPEC, execute_agent_tool
    from storage import Storage
    from providers import stream_llm_turn as provider_stream_llm_turn
    from agent_runtime import AgentRuntime
    from model_catalog import build_model_catalog

storage = Storage(DATABASE_FILE, HISTORY_FILE, MEMORY_FILE)


@app.middleware("http")
async def protect_local_api(request: Request, call_next):
    """Reject cross-origin/local API calls that do not belong to this app launch."""
    host = request.headers.get("host", "").split(":", 1)[0].strip("[]")
    if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
        return JSONResponse(status_code=400, content={"error": "invalid_host"})
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
        origin_host = urlparse(origin).netloc
        if origin_host != request.headers.get("host"):
            return JSONResponse(status_code=403, content={"error": "invalid_origin"})
    public_path = request.url.path in {"/", "/vexp.svg"} or request.url.path.startswith("/assets/")
    supplied = request.headers.get("x-vexp-token") or request.cookies.get("vexp_session")
    if not public_path and not secrets.compare_digest(supplied or "", APP_SESSION_TOKEN):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    response = await call_next(request)
    if request.url.path == "/":
        response.set_cookie("vexp_session", APP_SESSION_TOKEN, httponly=True, samesite="strict", secure=False, path="/")
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; img-src 'self' data:; connect-src 'self' ws: wss:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

STANDARD_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

def default_provider_config():
    return {
        "config_version": 3,
        "active_provider": "free_pool",
        "active_model": "fast-auto",
        "active_tier": "fast",
        "providers": {
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "qwen2.5:14b",
                "enabled": True
            },
            "gemini": {
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key": "",
                "model": "gemini-2.0-flash",
                "enabled": True,
                "is_free": True
            },
            "cerebras": {
                "base_url": "https://api.cerebras.ai/v1",
                "api_key": "",
                "model": "llama-3.3-70b",
                "enabled": True,
                "is_free": True
            },
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "",
                "model": "llama-3.3-70b-versatile",
                "enabled": True,
                "is_free": True
            },
            "github_models": {
                "base_url": "https://models.inference.ai.azure.com",
                "api_key": "",
                "model": "gpt-4o",
                "enabled": False,
                "is_free": True
            },
            "pollinations": {
                "base_url": "https://text.pollinations.ai/openai",
                "api_key": "",
                "model": "openai-fast",
                "enabled": True,
                "is_free": True,
                "tier_info": "100% Free Public Streaming Engine (Keyless)"
            },
            "kilo": {
                "base_url": "https://api.kilo.ai/api/gateway/v1",
                "api_key": "",
                "model": "kilo-auto/free",
                "enabled": True,
                "is_free": True,
                "tier_info": "100% Free Public Gateway (Keyless, 200 req/hr)"
            },
            "aihorde": {
                "base_url": "https://oai.aihorde.net/v1",
                "api_key": "0000000000",
                "model": "koboldcpp/Mistral-Nemo-12B-Instruct",
                "enabled": True,
                "is_free": True,
                "tier_info": "100% Free Anonymous Community Grid (Keyless)"
            },
            "freellmapi": {
                "base_url": "http://localhost:3001/v1",
                "api_key": "freellmapi-local",
                "model": "auto",
                "enabled": False,
                "is_free": True,
                "tier_info": "34 Free Providers / 635 Endpoints via Local Gateway"
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": "",
                "model": "anthropic/claude-3.5-sonnet",
                "enabled": False
            },
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "",
                "model": "gpt-4o",
                "enabled": False
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com/v1",
                "api_key": "",
                "model": "claude-3-5-sonnet-20241022",
                "enabled": False
            }
        },
        "custom_providers": []
    }

def sanitize_provider_config(d: dict) -> bool:
    changed = False
    github_models = d.get("providers", {}).get("github_models", {})
    if github_models.get("enabled"):
        github_models["enabled"] = False
        changed = True
    if int(d.get("config_version", 0)) < 3:
        for provider_id in ("kilo", "pollinations", "aihorde"):
            if provider_id in d.get("providers", {}):
                d["providers"][provider_id]["enabled"] = True
        d["active_provider"] = "free_pool"
        d["active_model"] = "fast-auto"
        d["active_tier"] = "fast"
        d["config_version"] = 3
        changed = True
    kilo = d.get("providers", {}).get("kilo", {})
    if kilo:
        if kilo.get("model") in ["moonshotai/kimi-k2.6:free", "google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free", "nousresearch/hermes-3-llama-3.1-405b:free", "meta-llama/llama-3.3-70b-instruct:free"]:
            kilo["model"] = "kilo-auto/free"
            changed = True
        if kilo.get("api_key") == "kilo-free":
            kilo["api_key"] = ""
            changed = True
    poll = d.get("providers", {}).get("pollinations", {})
    if poll:
        if poll.get("api_key") == "pollinations-free":
            poll["api_key"] = ""
            changed = True
    if d.get("active_model") in ["moonshotai/kimi-k2.6:free", "google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free", "nousresearch/hermes-3-llama-3.1-405b:free"]:
        d["active_model"] = "complex-auto"
        changed = True
    return changed

def load_provider_config() -> dict:
    if os.path.exists(PROVIDER_CONFIG_FILE):
        try:
            with open(PROVIDER_CONFIG_FILE, "r") as f:
                data = json.load(f)
                d = default_provider_config()
                d["config_version"] = data.get("config_version", 0)
                d["active_provider"] = data.get("active_provider", d["active_provider"])
                d["active_model"] = data.get("active_model", d["active_model"])
                d["active_tier"] = data.get("active_tier", d["active_tier"])
                for p_key, p_val in data.get("providers", {}).items():
                    if p_key in d["providers"]:
                        d["providers"][p_key].update(p_val)
                    else:
                        d["providers"][p_key] = p_val
                d["custom_providers"] = data.get("custom_providers", [])
                sanitized = sanitize_provider_config(d)
                if sanitized:
                    save_provider_config(d)
                return d
        except Exception:
            pass
    cfg = default_provider_config()
    sanitize_provider_config(cfg)
    save_provider_config(cfg)
    return cfg

def save_provider_config(cfg: dict):
    try:
        descriptor, temporary_path = tempfile.mkstemp(prefix=".provider-", dir=CONFIG_DIR)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, PROVIDER_CONFIG_FILE)
            os.chmod(PROVIDER_CONFIG_FILE, 0o600)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
    except Exception as e:
        print("Failed to save provider config:", e)


def load_github_config() -> dict:
    try:
        with open(GITHUB_CONFIG_FILE, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_github_config(value: dict) -> None:
    descriptor, temporary_path = tempfile.mkstemp(prefix=".github-", dir=CONFIG_DIR)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, GITHUB_CONFIG_FILE)
        os.chmod(GITHUB_CONFIG_FILE, 0o600)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def github_git_environment() -> dict:
    env = os.environ.copy()
    token = str(load_github_config().get("token") or "")
    if not token:
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env
    if sys.platform == "win32":
        helper = os.path.join(CONFIG_DIR, "github-askpass.cmd")
        content = '@echo off\r\necho %1 | findstr /I "Username" >nul\r\nif %errorlevel%==0 (echo x-access-token) else (echo %VEXP_GITHUB_TOKEN%)\r\n'
    else:
        helper = os.path.join(CONFIG_DIR, "github-askpass.sh")
        content = '#!/bin/sh\ncase "$1" in *Username*) printf "%s" "x-access-token" ;; *) printf "%s" "$VEXP_GITHUB_TOKEN" ;; esac\n'
    current = ""
    try:
        with open(helper, "r", encoding="utf-8") as handle:
            current = handle.read()
    except OSError:
        pass
    if current != content:
        with open(helper, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
    os.chmod(helper, 0o700)
    env.update({"GIT_ASKPASS": helper, "GIT_ASKPASS_REQUIRE": "force", "GIT_TERMINAL_PROMPT": "0", "VEXP_GITHUB_TOKEN": token})
    return env


def clean_git_output(value: str) -> str:
    value = re.sub(r"https://[^/@\s]+(?::[^@\s]*)?@github\.com", "https://github.com", value)
    return re.sub(r"\b(?:ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|gh[ousr]_[A-Za-z0-9]+)\b", "[redacted-token]", value)

def fetch_ollama_models(base_url: str = "http://127.0.0.1:11434") -> List[Dict[str, Any]]:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/tags", headers={"User-Agent": "VexP-IDE"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = []
            for m in data.get("models", []):
                size_gb = round(m.get("size", 0) / (1024**3), 1)
                details = m.get("details", {})
                param = details.get("parameter_size", "")
                models.append({
                    "name": m.get("name", ""),
                    "size": f"{size_gb} GB" if size_gb > 0 else "",
                    "params": param,
                    "quant": details.get("quantization_level", "")
                })
            return models if models else [{"name": "qwen2.5:14b", "size": "9.0 GB", "params": "14.7B"}]
    except Exception:
        return [{"name": "qwen2.5:14b", "size": "9.0 GB", "params": "14.7B"}]

def warmup_ollama_model(model_name: str, base_url: str = "http://127.0.0.1:11434"):
    def _warm():
        try:
            payload = json.dumps({"model": model_name, "keep_alive": "15m"}).encode("utf-8")
            req = urllib.request.Request(f"{base_url.rstrip('/')}/api/generate", data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except Exception as e:
            print(f"Warmup notice for {model_name}:", e)
    threading.Thread(target=_warm, daemon=True).start()

@app.get("/api/version")
def api_version():
    return {"server_time": SERVER_START_TIME, "version": "2.6.0", "session_fingerprint": hashlib.sha256(APP_SESSION_TOKEN.encode()).hexdigest()[:16]}

os.makedirs(DEFAULT_STORAGE_DIR, exist_ok=True)

def load_recent_workspaces() -> List[Dict[str, str]]:
    if os.path.exists(RECENT_WORKSPACES_FILE):
        try:
            with open(RECENT_WORKSPACES_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    # Seed with sensible defaults - no personal paths
    home = os.path.expanduser("~")
    defaults = [
        {"name": os.path.basename(PROJECT_ROOT), "path": os.path.abspath(PROJECT_ROOT)},
        {"name": "Home",    "path": home},
        {"name": "Desktop", "path": os.path.join(home, "Desktop")},
        {"name": "Documents", "path": os.path.join(home, "Documents")},
    ]
    found = [d for d in defaults if os.path.exists(d["path"])]
    return found if found else [{"name": "Current Directory", "path": os.getcwd()}]

def save_recent_workspaces(recents: List[Dict[str, str]]):
    try:
        with open(RECENT_WORKSPACES_FILE, "w") as f:
            json.dump(recents, f, indent=2)
    except:
        pass

def add_recent_workspace(path: str):
    recents = load_recent_workspaces()
    name = os.path.basename(path.rstrip("/")) or path
    # Remove existing if present
    recents = [r for r in recents if os.path.abspath(r["path"]) != os.path.abspath(path)]
    recents.insert(0, {"name": name, "path": os.path.abspath(path)})
    recents = recents[:10]  # keep top 10
    save_recent_workspaces(recents)

def get_active_workspace() -> Optional[str]:
    default_dir = os.getcwd()
    if os.path.exists(WORKSPACE_CONFIG_FILE):
        try:
            with open(WORKSPACE_CONFIG_FILE, "r") as f:
                data = json.load(f)
                p = data.get("path")
                if p is None:
                    return None
                if os.path.exists(p) and os.path.isdir(p):
                    return os.path.abspath(p)
        except:
            pass
    return default_dir

def set_active_workspace(path: Optional[str]):
    if not path:
        with open(WORKSPACE_CONFIG_FILE, "w") as f:
            json.dump({"path": None}, f, indent=2)
        return None
    full = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(full) or not os.path.isdir(full):
        raise HTTPException(status_code=400, detail="Invalid directory path")
    with open(WORKSPACE_CONFIG_FILE, "w") as f:
        json.dump({"path": full}, f, indent=2)
    add_recent_workspace(full)
    return full


def resolve_workspace_target(path: Optional[str], *, must_exist: bool = True) -> str:
    """Resolve a UI path inside the active workspace, including through symlinks."""
    workspace = get_active_workspace()
    if not workspace:
        raise HTTPException(status_code=400, detail="Open a workspace first")
    root = os.path.realpath(workspace)
    raw = path or root
    candidate = os.path.abspath(os.path.expanduser(raw if os.path.isabs(raw) else os.path.join(root, raw)))
    resolved = os.path.realpath(candidate if os.path.exists(candidate) else os.path.dirname(candidate))
    if os.path.commonpath([root, resolved]) != root:
        raise HTTPException(status_code=403, detail="Path is outside the active workspace")
    if must_exist and not os.path.exists(candidate):
        raise HTTPException(status_code=404, detail="Path not found")
    return candidate

@app.get("/api/workspace")
def get_workspace_info():
    curr = get_active_workspace()
    recent = load_recent_workspaces()
    if not curr:
        return {
            "has_workspace": False,
            "current_path": None,
            "name": "No Folder Open",
            "short_path": "",
            "recent": recent
        }
    name = os.path.basename(curr.rstrip("/")) or curr
    return {
        "has_workspace": True,
        "current_path": curr,
        "name": name,
        "short_path": curr.replace(os.path.expanduser("~"), "~"),
        "recent": recent
    }

class SetWorkspacePayload(BaseModel):
    path: Optional[str] = None

@app.post("/api/workspace")
def update_workspace(payload: SetWorkspacePayload):
    new_path = set_active_workspace(payload.path)
    return get_workspace_info()

@app.post("/api/workspace/close")
def close_workspace():
    set_active_workspace(None)
    return get_workspace_info()

@app.post("/api/workspace/pick-native")
def pick_native_directory():
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":1"
    if not env.get("WAYLAND_DISPLAY"):
        env["WAYLAND_DISPLAY"] = "wayland-0"
    if not env.get("XDG_CURRENT_DESKTOP"):
        env["XDG_CURRENT_DESKTOP"] = "KDE"
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    
    # Try kdialog first (since system is KDE), then zenity
    curr = get_active_workspace() or os.path.expanduser("~")
    cmd = []
    if shutil.which("kdialog"):
        cmd = ["kdialog", "--getexistingdirectory", curr, "--title", "Open Workspace Folder"]
    elif shutil.which("zenity"):
        cmd = ["zenity", "--file-selection", "--directory", f"--filename={curr}/", "--title=Open Workspace Folder"]
    else:
        raise HTTPException(status_code=500, detail="No native dialog tool (kdialog/zenity) found")
    
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        selected = proc.stdout.strip()
        if selected and os.path.exists(selected) and os.path.isdir(selected):
            set_active_workspace(selected)
            return get_workspace_info()
        else:
            return {"cancelled": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class MkdirPayload(BaseModel):
    path: str

@app.post("/api/fs/mkdir")
def create_directory(payload: MkdirPayload):
    full = resolve_workspace_target(payload.path, must_exist=False)
    try:
        os.makedirs(full, exist_ok=True)
        return {"success": True, "path": full}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RenamePayload(BaseModel):
    old_path: str
    new_path: str

@app.post("/api/fs/rename")
def rename_item(payload: RenamePayload):
    old_f = resolve_workspace_target(payload.old_path)
    new_f = resolve_workspace_target(payload.new_path, must_exist=False)
    if not os.path.exists(old_f):
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        os.rename(old_f, new_f)
        return {"success": True, "old": old_f, "new": new_f}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class DeletePayload(BaseModel):
    path: str

@app.post("/api/fs/delete")
def delete_item(payload: DeletePayload):
    full = resolve_workspace_target(payload.path)
    if os.path.realpath(full) == os.path.realpath(get_active_workspace() or ""):
        raise HTTPException(status_code=400, detail="The workspace root cannot be deleted")
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        if os.path.isdir(full):
            shutil.rmtree(full)
        else:
            os.remove(full)
        return {"success": True, "deleted": full}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

IGNORE_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".next", ".astro", "dist", "build", ".cache"}

@app.get("/api/fs/tree")
def get_file_tree(path: Optional[str] = Query(None)):
    target = path or get_active_workspace()
    if not target:
        return []
    full_path = resolve_workspace_target(target)
    if not os.path.exists(full_path):
        return []
    
    entries = []
    try:
        with os.scandir(full_path) as it:
            for entry in it:
                if entry.name.startswith(".") and entry.name != ".env":
                    continue
                if entry.name in IGNORE_DIRS:
                    continue
                
                is_dir = entry.is_dir(follow_symlinks=False)
                entries.append({
                    "name": entry.name,
                    "path": entry.path,
                    "is_dir": is_dir,
                    "size": 0 if is_dir else entry.stat().st_size
                })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    # Sort: directories first, then alphabetical
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return entries

LANGUAGE_MAP = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".astro": "html",
    ".html": "html", ".css": "css", ".json": "json", ".md": "markdown",
    ".sh": "shell", ".bash": "shell", ".txt": "plaintext", ".sql": "sql",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".env": "shell"
}

@app.get("/api/fs/search")
def search_codebase(query: str = Query(...), path: Optional[str] = Query(None)):
    target_dir = resolve_workspace_target(path or get_active_workspace())
    query = query.strip()
    if not query or len(query) > 500:
        return []
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", "50", "--glob", "!.git/**", "--glob", "!node_modules/**", "--glob", "!.venv/**", "--glob", "!dist/**", "--glob", "!build/**", "--", query, target_dir]
    else:
        cmd = ["grep", "-rnI", "-m", "50", "--exclude-dir=.git", "--exclude-dir=node_modules", "--exclude-dir=__pycache__", "--exclude-dir=.venv", "--exclude-dir=dist", "--exclude-dir=build", "--", query, target_dir]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        results = []
        for line in res.stdout.strip().splitlines()[:50]:
            parts = line.split(":", 2)
            if len(parts) >= 3:
                rel = os.path.relpath(parts[0], target_dir)
                results.append({
                    "file": rel,
                    "full_path": parts[0],
                    "line": parts[1],
                    "preview": parts[2].strip()
                })
        return results
    except Exception as e:
        return []

class GitCommitPayload(BaseModel):
    message: str
    stage_all: bool = True
    path: Optional[str] = None

class GitActionPayload(BaseModel):
    file: Optional[str] = None
    all: bool = False
    path: Optional[str] = None

class GitRemotePayload(BaseModel):
    remote: Optional[str] = "origin"
    branch: Optional[str] = None
    path: Optional[str] = None

class GitSetRemotePayload(BaseModel):
    name: Optional[str] = "origin"
    url: str
    token: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    push_now: bool = False
    branch: Optional[str] = None
    path: Optional[str] = None

class GitTestRemotePayload(BaseModel):
    remote: Optional[str] = "origin"
    url: Optional[str] = None
    token: Optional[str] = None
    username: Optional[str] = None
    path: Optional[str] = None

class GitRemoveRemotePayload(BaseModel):
    name: Optional[str] = "origin"
    path: Optional[str] = None

class GitConfigPayload(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    path: Optional[str] = None

class GitHubConnectPayload(BaseModel):
    token: str
    remote_url: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    path: Optional[str] = None


def _clean_github_remote(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub repository URL") from exc
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Use a clean https://github.com/owner/repository URL")
    path = parsed.path.rstrip("/")
    if len([part for part in path.split("/") if part]) != 2:
        raise HTTPException(status_code=400, detail="GitHub repository URL must include owner and repository")
    return f"https://github.com{path}{'' if path.endswith('.git') else '.git'}"


@app.get("/api/github/status")
def github_connection_status(path: Optional[str] = Query(None)):
    config = load_github_config()
    remote_url = ""
    try:
        target_dir = resolve_workspace_target(path or get_active_workspace())
        raw_remote = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        remote_url = clean_git_output(raw_remote)
    except Exception:
        pass
    return {
        "connected": bool(config.get("token") and config.get("login")),
        "login": str(config.get("login") or ""),
        "name": str(config.get("name") or ""),
        "remote_url": remote_url,
        "github_models_available": False,
        "github_models_message": "GitHub retired GitHub Models on July 30, 2026. Repository access remains available.",
    }


@app.post("/api/github/connect")
def connect_github(payload: GitHubConnectPayload):
    token = payload.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="A GitHub access token is required")
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    remote_url = _clean_github_remote(payload.remote_url) if payload.remote_url else ""
    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": STANDARD_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            account = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=400, detail="GitHub rejected this token. Create a new token with repository Contents read/write permission.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {exc}") from exc
    login = str(account.get("login") or "").strip()
    if not login:
        raise HTTPException(status_code=400, detail="GitHub did not return an authenticated account")

    if payload.username is not None:
        subprocess.run(["git", "config", "user.name", payload.username.strip()], capture_output=True, text=True, cwd=target_dir)
    if payload.email is not None:
        subprocess.run(["git", "config", "user.email", payload.email.strip()], capture_output=True, text=True, cwd=target_dir)
    if remote_url:
        remotes = subprocess.run(["git", "remote"], capture_output=True, text=True, cwd=target_dir).stdout.split()
        command = ["git", "remote", "set-url" if "origin" in remotes else "add", "origin", remote_url]
        result = subprocess.run(command, capture_output=True, text=True, cwd=target_dir)
        if result.returncode != 0:
            raise HTTPException(status_code=400, detail=clean_git_output(result.stderr.strip() or "Could not set the GitHub remote"))
    save_github_config({"token": token, "login": login, "name": str(account.get("name") or "")})
    return github_connection_status(payload.path)


@app.post("/api/github/disconnect")
def disconnect_github():
    try:
        os.remove(GITHUB_CONFIG_FILE)
    except FileNotFoundError:
        pass
    return {"success": True, "connected": False}

@app.get("/api/git/status")
def git_status_endpoint(path: Optional[str] = Query(None)):
    target_dir = resolve_workspace_target(path or get_active_workspace())
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=target_dir).returncode == 0
        if not is_git:
            return {"branch": "none", "is_repo": False, "files": [], "staged": [], "unstaged": [], "count": 0, "remote_url": "", "has_remote": False}
        
        branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        if not branch:
            head_rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
            branch = head_rev or "main"
        
        raw_remote_url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        remote_url = clean_git_output(raw_remote_url)
        status_raw = subprocess.run(["git", "status", "--porcelain=v1"], capture_output=True, text=True, cwd=target_dir).stdout

        
        staged = []
        unstaged = []
        files = []
        
        for line in status_raw.splitlines():
            if len(line) >= 3:
                x = line[0]
                y = line[1]
                filename = line[3:].strip()
                if " -> " in filename:
                    filename = filename.split(" -> ")[-1]
                
                if x == '?' and y == '?':
                    item = {"status": "U", "file": filename, "staged": False}
                    unstaged.append(item)
                    files.append(item)
                else:
                    if x != ' ' and x != '?':
                        staged_item = {"status": x, "file": filename, "staged": True}
                        staged.append(staged_item)
                    if y != ' ' and y != '?':
                        unstaged_item = {"status": y, "file": filename, "staged": False}
                        unstaged.append(unstaged_item)
                        files.append(unstaged_item)
                    elif x != ' ':
                        files.append({"status": x, "file": filename, "staged": True})
        
        return {
            "branch": branch,
            "is_repo": True,
            "files": files,
            "staged": staged,
            "unstaged": unstaged,
            "count": len(files),
            "remote_url": remote_url,
            "has_remote": bool(remote_url)
        }
    except Exception as e:
        return {"branch": "none", "is_repo": False, "files": [], "staged": [], "unstaged": [], "count": 0, "error": str(e), "remote_url": "", "has_remote": False}

@app.post("/api/git/init")
def git_init_endpoint(payload: GitActionPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        res = subprocess.run(["git", "init"], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/stage")
def git_stage_endpoint(payload: GitActionPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        if payload.all or not payload.file:
            res = subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=target_dir)
        else:
            full_path = resolve_workspace_target(os.path.join(target_dir, payload.file))
            res = subprocess.run(["git", "add", "--", os.path.relpath(full_path, target_dir)], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/unstage")
def git_unstage_endpoint(payload: GitActionPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        if payload.all or not payload.file:
            res = subprocess.run(["git", "reset", "HEAD"], capture_output=True, text=True, cwd=target_dir)
        else:
            full_path = resolve_workspace_target(os.path.join(target_dir, payload.file))
            res = subprocess.run(["git", "reset", "HEAD", "--", os.path.relpath(full_path, target_dir)], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/discard")
def git_discard_endpoint(payload: GitActionPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    if payload.all:
        raise HTTPException(status_code=400, detail="Bulk discard is disabled; discard files individually")
    if not payload.file:
        raise HTTPException(status_code=400, detail="File required")
    try:
        full_path = resolve_workspace_target(os.path.join(target_dir, payload.file))
        res = subprocess.run(["git", "restore", "--worktree", "--", os.path.relpath(full_path, target_dir)], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout.strip() or res.stderr.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/commit")
def git_commit_endpoint(payload: GitCommitPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Commit message cannot be empty")
    try:
        if payload.stage_all:
            subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=target_dir)

        # Check if working tree has changes
        status_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=target_dir)
        if not status_res.stdout.strip():
            return {
                "success": False,
                "output": "Nothing to commit - working tree is clean. Edit files first before creating a commit."
            }

        res = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True, cwd=target_dir)
        return {
            "success": res.returncode == 0,
            "output": res.stdout.strip() or res.stderr.strip()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/push")
def git_push_endpoint(payload: GitRemotePayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        branch = payload.branch
        if not branch:
            branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=target_dir).stdout.strip() or "main"
        
        remote = payload.remote or "origin"
        cmd = ["git", "push", "-u", remote, branch]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=target_dir, env=github_git_environment(), timeout=120)
        
        raw_out = res.stdout.strip() or res.stderr.strip()
        clean_out = clean_git_output(raw_out)

        if res.returncode != 0:
            if ("Permission to" in clean_out and "denied" in clean_out) or "403" in clean_out:
                clean_out = (
                    "GitHub Authentication Error (HTTP 403 Forbidden):\n"
                    "Permission denied to push to this repository.\n\n"
                    "The connected token can identify the account but cannot write to this repository. "
                    "Grant the fine-grained token access to this repository with Contents read/write permission, then reconnect it in Source Control."
                )
            return {"success": False, "output": clean_out}

        return {
            "success": True,
            "output": clean_out or f"Successfully pushed branch '{branch}' to remote."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/pull")
def git_pull_endpoint(payload: GitRemotePayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        cmd = ["git", "pull", "--rebase"]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=target_dir, env=github_git_environment(), timeout=120)
        return {
            "success": res.returncode == 0,
            "output": clean_git_output(res.stdout.strip() or res.stderr.strip())
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/git/diff")
def git_diff_endpoint(file: str = Query(...), path: Optional[str] = Query(None)):
    target_dir = resolve_workspace_target(path or get_active_workspace())
    try:
        full_path = resolve_workspace_target(os.path.join(target_dir, file))
        relative_file = os.path.relpath(full_path, target_dir)
        orig_res = subprocess.run(["git", "show", f"HEAD:{relative_file}"], capture_output=True, text=True, cwd=target_dir)
        original_content = orig_res.stdout if orig_res.returncode == 0 else ""
        
        modified_content = ""
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                modified_content = f.read()
        
        ext = os.path.splitext(relative_file)[1].lower()
        language = LANGUAGE_MAP.get(ext, "plaintext")
        
        return {
            "success": True,
            "file": relative_file,
            "original": original_content,
            "modified": modified_content,
            "language": language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/remote/set")
def git_set_remote_endpoint(payload: GitSetRemotePayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=target_dir).returncode == 0
        if not is_git:
            subprocess.run(["git", "init", "-b", "main"], capture_output=True, text=True, cwd=target_dir)

        if payload.username and payload.username.strip():
            subprocess.run(["git", "config", "user.name", payload.username.strip()], capture_output=True, text=True, cwd=target_dir)
        if payload.email and payload.email.strip():
            subprocess.run(["git", "config", "user.email", payload.email.strip()], capture_output=True, text=True, cwd=target_dir)

        raw_url = payload.url.strip()
        if not raw_url:
            raise HTTPException(status_code=400, detail="Repository URL cannot be empty")

        target_url = raw_url
        if target_url.startswith("https://github.com/") and not target_url.endswith(".git"):
            target_url += ".git"

        if payload.token and payload.token.strip():
            raise HTTPException(status_code=400, detail="Tokens are not stored in Git remote URLs; use your system credential manager")

        remote_name = payload.name or "origin"

        existing_remotes = subprocess.run(["git", "remote"], capture_output=True, text=True, cwd=target_dir).stdout.split()
        if remote_name in existing_remotes:
            cmd = ["git", "remote", "set-url", remote_name, target_url]
        else:
            cmd = ["git", "remote", "add", remote_name, target_url]
        
        rem_res = subprocess.run(cmd, capture_output=True, text=True, cwd=target_dir)
        if rem_res.returncode != 0:
            return {"success": False, "output": rem_res.stderr.strip()}

        push_output = ""
        if payload.push_now:
            branch = payload.branch
            if not branch:
                branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=target_dir).stdout.strip() or "main"
            push_res = subprocess.run(["git", "push", "-u", remote_name, branch], capture_output=True, text=True, cwd=target_dir, env=github_git_environment(), timeout=120)
            push_output = push_res.stdout.strip() or push_res.stderr.strip()
            if push_res.returncode != 0:
                clean_push_out = clean_git_output(push_output)
                return {
                    "success": False,
                    "output": f"Remote saved, but push failed:\n{clean_push_out}",
                    "remote_url": re.sub(r'https://[^@]+@github\.com', 'https://github.com', target_url)
                }

        clean_url = re.sub(r'https://[^@]+@github\.com', 'https://github.com', target_url)
        return {
            "success": True,
            "output": push_output or f"Successfully connected remote '{remote_name}' to {clean_url}",
            "remote_url": clean_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/remote/test")
def git_test_remote_endpoint(payload: GitTestRemotePayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        if payload.token and payload.token.strip():
            raise HTTPException(status_code=400, detail="Use the system Git credential manager instead of passing tokens to the IDE")
        test_target = payload.url.strip() if payload.url else (payload.remote or "origin")

        res = subprocess.run(["git", "ls-remote", test_target], capture_output=True, text=True, cwd=target_dir, timeout=12, env=github_git_environment())
        if res.returncode == 0:
            lines = res.stdout.strip().splitlines()
            return {
                "success": True,
                "output": f"Connection and repository access verified! Found {len(lines)} references on remote.",
                "refs_count": len(lines)
            }
        else:
            err = res.stderr.strip() or res.stdout.strip()
            clean_err = clean_git_output(err)
            return {"success": False, "output": clean_err or "Failed to connect to remote repository."}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Connection timed out after 12 seconds."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/remote/remove")
def git_remove_remote_endpoint(payload: GitRemoveRemotePayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        name = payload.name or "origin"
        res = subprocess.run(["git", "remote", "remove", name], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout.strip() or res.stderr.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/git/config")
def git_get_config_endpoint(path: Optional[str] = Query(None)):
    target_dir = resolve_workspace_target(path or get_active_workspace())
    try:
        name = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        if not name:
            name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
        email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        if not email:
            email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()

        raw_remote = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        clean_remote = clean_git_output(raw_remote)

        return {
            "username": name,
            "email": email,
            "remote_url": clean_remote,
            "has_token": False
        }
    except Exception as e:
        return {"username": "", "email": "", "remote_url": "", "has_token": False, "saved_token": "", "error": str(e)}

@app.post("/api/git/config")
def git_set_config_endpoint(payload: GitConfigPayload):
    target_dir = resolve_workspace_target(payload.path or get_active_workspace())
    try:
        if payload.username is not None:
            subprocess.run(["git", "config", "user.name", payload.username.strip()], capture_output=True, text=True, cwd=target_dir)
        if payload.email is not None:
            subprocess.run(["git", "config", "user.email", payload.email.strip()], capture_output=True, text=True, cwd=target_dir)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/fs/read")
def read_file(path: str = Query(...)):
    full_path = resolve_workspace_target(path)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    if os.path.getsize(full_path) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The editor limit is 5 MiB per file")
    
    ext = os.path.splitext(full_path)[1].lower()
    language = LANGUAGE_MAP.get(ext, "plaintext")
    
    try:
        with open(full_path, "rb") as f:
            raw_content = f.read()
        content = raw_content.decode("utf-8", errors="replace")
        return {
            "success": True,
            "path": full_path,
            "name": os.path.basename(full_path),
            "content": content,
            "language": language,
            "sha256": hashlib.sha256(raw_content).hexdigest()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WriteFilePayload(BaseModel):
    path: str
    content: str
    expected_sha256: Optional[str] = None

@app.post("/api/fs/write")
def write_file(payload: WriteFilePayload):
    full_path = resolve_workspace_target(payload.path, must_exist=False)
    encoded = payload.content.encode("utf-8")
    if len(encoded) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The editor limit is 5 MiB per file")
    try:
        if payload.expected_sha256 is not None:
            if not os.path.exists(full_path):
                raise HTTPException(status_code=409, detail="The file was removed after it was opened")
            with open(full_path, "rb") as current_file:
                current_sha256 = hashlib.sha256(current_file.read()).hexdigest()
            if current_sha256 != payload.expected_sha256:
                raise HTTPException(status_code=409, detail="The file changed on disk; reload it before saving")
        parent = os.path.dirname(full_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(prefix=".vexp-save-", dir=parent or ".")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, full_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
        short = full_path.replace(os.path.expanduser("~"), "~")
        return {"success": True, "path": full_path, "message": f"Saved {short}", "sha256": hashlib.sha256(encoded).hexdigest()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------
# Interactive Terminal (WebSocket + PTY like VS Code / Cursor)
# ---------------------------------------------------------------------
@app.websocket("/ws/terminal")
async def terminal_websocket_endpoint(websocket: WebSocket, cwd: Optional[str] = Query(None)):
    supplied = websocket.headers.get("x-vexp-token") or websocket.cookies.get("vexp_session")
    origin = websocket.headers.get("origin", "")
    if not secrets.compare_digest(supplied or "", APP_SESSION_TOKEN) or (origin and urlparse(origin).netloc != websocket.headers.get("host")):
        await websocket.close(code=4401, reason="Unauthorized")
        return
    await websocket.accept()

    try:
        work_dir = resolve_workspace_target(cwd or get_active_workspace())
    except HTTPException:
        await websocket.close(code=4403, reason="Workspace unavailable")
        return
    if not os.path.isdir(work_dir):
        await websocket.close(code=4403, reason="Terminal path is not a directory")
        return

    if HAS_PTY:
        # Native POSIX PTY implementation for Linux and macOS
        master_fd, slave_fd = pty.openpty()
        fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

        shell = os.environ.get("SHELL", "/bin/bash")
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"

        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(slave_fd)
            try:
                os.chdir(work_dir)
            except Exception:
                pass
            try:
                os.execvpe(shell, [shell], env)
            except Exception:
                os.execvpe("/bin/sh", ["/bin/sh"], env)
            os._exit(1)

        os.close(slave_fd)

        loop = asyncio.get_running_loop()
        output_queue = asyncio.Queue()

        def on_master_readable():
            try:
                data = os.read(master_fd, 4096)
                if data:
                    output_queue.put_nowait(data)
            except Exception:
                pass

        loop.add_reader(master_fd, on_master_readable)

        async def send_to_client():
            try:
                while True:
                    data = await output_queue.get()
                    await websocket.send_bytes(data)
            except Exception:
                pass

        async def receive_from_client():
            try:
                while True:
                    msg = await websocket.receive()
                    if "bytes" in msg and msg["bytes"]:
                        os.write(master_fd, msg["bytes"])
                    elif "text" in msg and msg["text"]:
                        text = msg["text"]
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    rows = int(ctrl.get("rows", 24))
                                    cols = int(ctrl.get("cols", 80))
                                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                                    continue
                            except Exception:
                                pass
                        os.write(master_fd, text.encode("utf-8"))
            except (WebSocketDisconnect, Exception):
                pass

        sender_task = asyncio.create_task(send_to_client())
        receiver_task = asyncio.create_task(receive_from_client())

        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

        try:
            loop.remove_reader(master_fd)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(0.05)
            os.waitpid(pid, os.WNOHANG)
        except Exception:
            pass
    else:
        # Cross-platform / Windows asynchronous subprocess implementation (PowerShell / CMD)
        shell_cmd = ["powershell.exe", "-NoLogo"] if shutil.which("powershell.exe") else ["cmd.exe"]
        env = dict(os.environ)
        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=work_dir,
                env=env
            )
        except Exception as e:
            await websocket.send_text(f"\r\n[Terminal Error: Failed to launch shell: {e}]\r\n")
            return

        async def send_to_client_win():
            try:
                while True:
                    data = await proc.stdout.read(4096)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:
                pass

        async def receive_from_client_win():
            try:
                while True:
                    msg = await websocket.receive()
                    if "bytes" in msg and msg["bytes"]:
                        if proc.stdin:
                            proc.stdin.write(msg["bytes"])
                            await proc.stdin.drain()
                    elif "text" in msg and msg["text"]:
                        text = msg["text"]
                        if text.startswith("{"):
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "resize":
                                    continue
                            except Exception:
                                pass
                        if proc.stdin:
                            proc.stdin.write(text.encode("utf-8"))
                            await proc.stdin.drain()
            except (WebSocketDisconnect, Exception):
                pass

        sender_task = asyncio.create_task(send_to_client_win())
        receiver_task = asyncio.create_task(receive_from_client_win())

        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()

        try:
            proc.terminate()
        except Exception:
            pass

def load_memories():
    return storage.list_memories()

@app.get("/api/memories")
def get_memories():
    return load_memories()

class MemoryPayload(BaseModel):
    content: str
    workspace: Optional[str] = ""

@app.post("/api/memories")
def add_memory_endpoint(payload: MemoryPayload):
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Memory content cannot be empty")
    return storage.add_memory(str(uuid.uuid4())[:8], content, payload.workspace or "")

def load_history() -> List[Dict[str, Any]]:
    return [storage.get_session(item["id"]) for item in storage.list_sessions()]

def save_history(history: List[Dict[str, Any]]):
    raise RuntimeError("Direct whole-history writes are disabled; use the transactional storage API")

def append_to_session(session_id: str, role: str, content: str, workspace: str = "", thinking_seconds: float = None):
    return storage.append_message(session_id, role, content, workspace, thinking_seconds)

@app.get("/api/history")
def get_history_sessions():
    return storage.list_sessions()

@app.get("/api/history/{session_id}")
def get_session(session_id: str):
    return storage.get_session(session_id)

@app.delete("/api/history/{session_id}")
def delete_session(session_id: str):
    storage.delete_session(session_id)
    return {"success": True}

class TitlePayload(BaseModel):
    title: str

@app.post("/api/history/{session_id}/title")
def rename_session(session_id: str, payload: TitlePayload):
    try:
        if storage.rename_session(session_id, payload.title):
            return {"success": True, "title": payload.title.strip()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=404, detail="Session not found")

@app.delete("/api/memories/{mem_id}")
def delete_memory_endpoint(mem_id: str):
    storage.delete_memory(mem_id)
    return {"success": True}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Attachments are limited to 10 MiB")
        filename_lower = file.filename.lower()
        if filename_lower.endswith(".pdf"):
            import io
            import pypdf
            pdf_reader = pypdf.PdfReader(io.BytesIO(content))
            extracted_pages = []
            for i, page in enumerate(pdf_reader.pages):
                txt = page.extract_text() or ""
                extracted_pages.append(f"--- Page {i+1} ---\n{txt}")
            text_content = "\n".join(extracted_pages)
        else:
            text_content = content.decode('utf-8', errors='replace')

        return {
            "success": True,
            "filename": file.filename,
            "size": len(content),
            "content": text_content
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/stats")
def get_stats():
    stats = {"vram_used": "N/A", "vram_total": "4096 MiB", "linux_free": "N/A", "storage_free": "N/A"}
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True)
        if smi.returncode == 0:
            parts = smi.stdout.strip().split(",")
            stats["vram_used"] = parts[0].strip() + " MiB"
            stats["vram_total"] = parts[1].strip() + " MiB"
    except:
        pass
    try:
        root_path = os.path.abspath(os.sep)
        total, used, free = shutil.disk_usage(root_path)
        free_gb = f"{round(free / (1024**3), 1)} GB"
        stats["linux_free"] = free_gb
        stats["storage_free"] = free_gb
    except:
        pass
    return stats

# ---------------------------------------------------------------------
# Dynamic AI Models & Multi-Provider Engine
# ---------------------------------------------------------------------
class SwitchModelPayload(BaseModel):
    provider: str
    model: str
    tier: Optional[str] = None

class PullModelPayload(BaseModel):
    model: str

class TestProviderPayload(BaseModel):
    provider: str
    base_url: Optional[str] = ""
    api_key: Optional[str] = ""
    model: Optional[str] = ""
    provider_type: Optional[str] = "openai"

class ProbeProviderPayload(BaseModel):
    base_url: str
    api_key: Optional[str] = ""
    protocol: Optional[str] = "auto"

class ProviderUpdatePayload(BaseModel):
    active_provider: Optional[str] = None
    active_model: Optional[str] = None
    providers: Optional[Dict[str, Any]] = None
    custom_providers: Optional[List[Dict[str, Any]]] = None

@app.get("/api/models")
def get_available_models():
    cfg = load_provider_config()
    providers = cfg.get("providers", {})
    ollama = providers.get("ollama", {})
    local_models: List[Dict[str, Any]] = []
    if ollama.get("enabled"):
        local_models = fetch_ollama_models(ollama.get("base_url", "http://127.0.0.1:11434"))
    fast_models, complex_models = build_model_catalog(cfg, local_models)
    return {
        "active_provider": cfg.get("active_provider", "free_pool"),
        "active_model": cfg.get("active_model", "fast-auto"),
        "active_tier": cfg.get("active_tier", "fast"),
        "fast_models": fast_models,
        "complex_models": complex_models,
        "local_models": local_models,
    }


@app.post("/api/models/switch")
def switch_active_model(payload: SwitchModelPayload):
    cfg = load_provider_config()
    if payload.tier not in {None, "fast", "complex"}:
        raise HTTPException(status_code=400, detail="Tier must be 'fast' or 'complex'")
    cfg["active_provider"] = payload.provider
    cfg["active_model"] = payload.model
    if payload.tier:
        cfg["active_tier"] = payload.tier
    if payload.provider in cfg["providers"]:
        cfg["providers"][payload.provider]["model"] = payload.model
        cfg["providers"][payload.provider]["enabled"] = True
    elif payload.provider.startswith("custom_"):
        c_id = payload.provider[7:]
        for cp in cfg.get("custom_providers", []):
            if cp.get("id") == c_id:
                cp["model"] = payload.model
                cp["enabled"] = True
                break
    save_provider_config(cfg)

    if payload.provider == "ollama":
        base_url = cfg["providers"]["ollama"].get("base_url", "http://127.0.0.1:11434")
        warmup_ollama_model(payload.model, base_url)

    return {
        "success": True,
        "active_provider": cfg["active_provider"],
        "active_model": cfg["active_model"]
    }

@app.post("/api/models/pull")
def pull_ollama_model(payload: PullModelPayload):
    cmd = ["ollama", "pull", payload.model.strip()]
    def _run_pull():
        subprocess.run(cmd)
    threading.Thread(target=_run_pull, daemon=True).start()
    return {"success": True, "message": f"Started pulling {payload.model} in background."}

@app.get("/api/settings/providers")
def get_provider_settings():
    cfg = load_provider_config()
    safe_cfg = json.loads(json.dumps(cfg))
    for p_id, p_info in safe_cfg.get("providers", {}).items():
        key = p_info.get("api_key", "")
        if key:
            p_info["has_key"] = True
            p_info["masked_key"] = key[:4] + "..." + key[-4:] if len(key) > 8 else "••••••••"
        else:
            p_info["has_key"] = False
            p_info["masked_key"] = ""
        p_info.pop("api_key", None)

    for cp in safe_cfg.get("custom_providers", []):
        key = cp.get("api_key", "")
        if key:
            cp["has_key"] = True
            cp["masked_key"] = key[:4] + "..." + key[-4:] if len(key) > 8 else "••••••••"
        else:
            cp["has_key"] = False
            cp["masked_key"] = ""
        cp.pop("api_key", None)
    return safe_cfg

@app.post("/api/settings/providers")
def update_provider_settings(payload: ProviderUpdatePayload):
    cfg = load_provider_config()
    if payload.active_provider:
        cfg["active_provider"] = payload.active_provider
    if payload.active_model:
        cfg["active_model"] = payload.active_model

    if payload.providers:
        for p_id, incoming in payload.providers.items():
            if p_id in cfg["providers"]:
                clean = {key: value for key, value in incoming.items() if key in {"enabled", "base_url", "model", "api_key"}}
                new_key = clean.get("api_key", "")
                if not new_key or "..." in new_key or "•••" in new_key:
                    clean["api_key"] = cfg["providers"][p_id].get("api_key", "")
                if clean.get("base_url") and urlparse(str(clean["base_url"])).scheme not in {"http", "https"}:
                    raise HTTPException(status_code=400, detail=f"Invalid provider URL for {p_id}")
                cfg["providers"][p_id].update(clean)

    if payload.custom_providers is not None:
        existing_keys = {cp.get("id"): cp.get("api_key", "") for cp in cfg.get("custom_providers", [])}
        updated_custom = []
        for incoming in payload.custom_providers[:20]:
            cp = {key: value for key, value in incoming.items() if key in {"id", "name", "type", "enabled", "base_url", "model", "api_key"}}
            c_id = str(cp.get("id") or uuid.uuid4().hex[:8])[:64]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", c_id):
                raise HTTPException(status_code=400, detail="Custom provider IDs may contain letters, numbers, underscores, and hyphens")
            cp["id"] = c_id
            cp["type"] = "anthropic" if cp.get("type") == "anthropic" else "openai"
            if not cp.get("base_url") or urlparse(str(cp["base_url"])).scheme not in {"http", "https"}:
                raise HTTPException(status_code=400, detail=f"Invalid custom provider URL for {c_id}")
            new_key = cp.get("api_key", "")
            if (not new_key or "..." in new_key or "•••" in new_key) and c_id in existing_keys:
                cp["api_key"] = existing_keys[c_id]
            updated_custom.append(cp)
        cfg["custom_providers"] = updated_custom

    save_provider_config(cfg)
    return {"success": True, "config": get_provider_settings()}

@app.post("/api/settings/providers/test")
def test_provider_connection(payload: TestProviderPayload):
    p_id = payload.provider
    cfg = load_provider_config()
    if p_id.startswith("custom_"):
        custom_id = p_id[7:]
        profile = next((item for item in cfg.get("custom_providers", []) if item.get("id") == custom_id), {})
    else:
        profile = cfg.get("providers", {}).get(p_id, {})
    base_url = (payload.base_url or profile.get("base_url") or "").strip()
    api_key = payload.api_key or profile.get("api_key") or ""
    model = payload.model or profile.get("model") or ""
    provider_type = payload.provider_type or profile.get("type") or ("anthropic" if p_id == "anthropic" else "openai")
    t0 = time.time()
    try:
        if p_id == "ollama":
            url = (base_url or "http://127.0.0.1:11434").rstrip("/")
            req = urllib.request.Request(f"{url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Connected to Ollama ({len(data.get('models', []))} models installed)"}

        # Check protocol type
        p_type = provider_type.lower()
        if p_id == "anthropic" or p_type == "anthropic":
            base = (base_url or "https://api.anthropic.com/v1").rstrip("/")
            url = f"{base}/messages" if not base.endswith("/messages") else base
            body = json.dumps({
                "model": model or "claude-3-5-haiku-20241022",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "hi"}]
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "User-Agent": STANDARD_USER_AGENT,
                "Accept": "application/json"
            }
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            req = urllib.request.Request(
                url,
                data=body,
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Successfully connected to Anthropic API ({ms}ms)"}

        else:
            url = (base_url or "https://api.openai.com/v1").rstrip("/")
            if not url.endswith("/chat/completions"):
                url = f"{url}/chat/completions"
            body = json.dumps({
                "model": model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "x-api-key": api_key,
                    "User-Agent": STANDARD_USER_AGENT,
                    "Accept": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Successfully connected to {p_id.capitalize()} ({ms}ms)"}

    except urllib.error.HTTPError as he:
        try:
            raw_body = he.read().decode("utf-8", errors="ignore")
            try:
                err_data = json.loads(raw_body)
                err_msg = err_data.get("error", {}).get("message") or err_data.get("message") or raw_body[:250]
            except Exception:
                err_msg = raw_body[:250] or f"HTTP {he.code}: {he.reason}"
        except Exception:
            err_msg = f"HTTP {he.code}: {he.reason}"
        return {"error": err_msg}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/settings/providers/probe")
def probe_provider_endpoint(payload: ProbeProviderPayload):
    raw_url = (payload.base_url or "").strip().rstrip("/")
    if not raw_url:
        return {"success": False, "error": "Base URL cannot be empty."}

    api_key = (payload.api_key or "").strip()
    protocol = (payload.protocol or "auto").lower()
    t0 = time.time()

    # 1. Test Ollama
    if protocol in ["auto", "ollama"] or ":11434" in raw_url:
        try:
            req = urllib.request.Request(f"{raw_url}/api/tags", headers={"User-Agent": "VexP-IDE"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                ms = round((time.time() - t0) * 1000)
                return {
                    "success": True,
                    "protocol": "ollama",
                    "latency_ms": ms,
                    "models": models,
                    "message": f"Connected to Ollama! Found {len(models)} local models."
                }
        except Exception:
            if protocol == "ollama":
                return {"success": False, "error": f"Could not connect to Ollama on {raw_url}"}

    # 2. Test OpenAI-compatible models endpoint
    if protocol in ["auto", "openai"]:
        candidate_urls = []
        if raw_url.endswith("/v1"):
            candidate_urls.append(f"{raw_url}/models")
            candidate_urls.append(f"{raw_url.rsplit('/v1', 1)[0]}/models")
        elif raw_url.endswith("/chat/completions"):
            candidate_urls.append(f"{raw_url.rsplit('/chat/completions', 1)[0]}/models")
        else:
            candidate_urls.append(f"{raw_url}/v1/models")
            candidate_urls.append(f"{raw_url}/models")

        headers = {"User-Agent": STANDARD_USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-api-key"] = api_key

        for m_url in candidate_urls:
            try:
                req = urllib.request.Request(m_url, headers=headers)
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    models_list = []
                    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                        models_list = [item.get("id") for item in data["data"] if isinstance(item, dict) and "id" in item]
                    elif isinstance(data, list):
                        models_list = [item.get("id") or item.get("name") for item in data if isinstance(item, dict)]

                    if models_list:
                        ms = round((time.time() - t0) * 1000)
                        clean_base = m_url.rsplit("/models", 1)[0]
                        return {
                            "success": True,
                            "protocol": "openai",
                            "base_url": clean_base,
                            "latency_ms": ms,
                            "models": models_list[:150],
                            "message": f"Connected! Found {len(models_list)} models available."
                        }
            except urllib.error.HTTPError as he:
                if he.code == 401:
                    return {"success": False, "error": "Authentication failed (HTTP 401). Please check your API key."}
            except Exception:
                pass

        # Fallback test: ping /chat/completions with minimal payload
        chat_url = f"{raw_url}/chat/completions" if raw_url.endswith("/v1") else (
            f"{raw_url}/v1/chat/completions" if not raw_url.endswith("/chat/completions") else raw_url
        )
        try:
            body = json.dumps({
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1
            }).encode("utf-8")
            req = urllib.request.Request(
                chat_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "x-api-key": api_key,
                    "User-Agent": STANDARD_USER_AGENT,
                    "Accept": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {
                "success": True,
                "protocol": "openai",
                "latency_ms": ms,
                "models": [],
                "message": f"Connected to OpenAI-compatible endpoint ({ms}ms)!"
            }
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            if he.code in [400, 404] and ("model" in err_body.lower() or "not found" in err_body.lower() or "tokens" in err_body.lower()):
                ms = round((time.time() - t0) * 1000)
                return {
                    "success": True,
                    "protocol": "openai",
                    "latency_ms": ms,
                    "models": [],
                    "message": f"Endpoint verified ({ms}ms)!"
                }
            elif he.code == 401:
                return {"success": False, "error": "Authentication failed (HTTP 401). Invalid API key."}
        except Exception:
            pass

    # 3. Test Anthropic protocol
    if protocol in ["auto", "anthropic"] or "anthropic" in raw_url:
        anth_url = f"{raw_url}/messages" if raw_url.endswith("/v1") else (
            f"{raw_url}/v1/messages" if not raw_url.endswith("/messages") else raw_url
        )
        try:
            body = json.dumps({
                "model": "claude-3-5-haiku-20241022",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1
            }).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
                "User-Agent": STANDARD_USER_AGENT,
                "Accept": "application/json"
            }
            req = urllib.request.Request(anth_url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {
                "success": True,
                "protocol": "anthropic",
                "latency_ms": ms,
                "models": [
                    "claude-sonnet-4-6",
                    "claude-opus-4-6",
                    "claude-3-5-sonnet-20241022",
                    "claude-3-5-haiku-20241022",
                    "claude-3-opus-20240229"
                ],
                "message": f"Connected to Anthropic-compatible provider ({ms}ms)!"
            }
        except urllib.error.HTTPError as he:
            if he.code == 401:
                return {"success": False, "error": "Invalid Anthropic API key (HTTP 401)."}
            elif he.code in [400, 404]:
                ms = round((time.time() - t0) * 1000)
                return {
                    "success": True,
                    "protocol": "anthropic",
                    "latency_ms": ms,
                    "models": [
                        "claude-sonnet-4-6",
                        "claude-opus-4-6",
                        "claude-3-5-sonnet-20241022",
                        "claude-3-5-haiku-20241022"
                    ],
                    "message": f"Anthropic-compatible endpoint reachable ({ms}ms)."
                }
        except Exception as e:
            if protocol == "anthropic":
                return {"success": False, "error": f"Anthropic error: {str(e)}"}

    return {
        "success": False,
        "error": "Could not connect to provider. Verify the Base URL and API key."
    }

def stream_llm_turn(provider: str, model: str, messages: list, tools: list, cfg: dict, tier: str = "fast", local_only: bool = False):
    yield from provider_stream_llm_turn(provider, model, messages, tools, cfg, tier=tier, local_only=local_only)


agent_runtime = AgentRuntime(
    provider_stream=stream_llm_turn,
    tool_executor=execute_agent_tool,
    tools=TOOLS_SPEC,
    web_tools=WEB_TOOLS_SPEC,
    storage=storage,
    system_intro=SYS_INTRO,
    agent_name=AGENT_NAME,
)


# ---------------------------------------------------------------------
class ActiveFile(BaseModel):
    name: str
    path: str
    content: str
    language: Optional[str] = "plaintext"

class ChatRequest(BaseModel):
    session_id: str
    prompt: str
    project_path: Optional[str] = None
    active_file: Optional[ActiveFile] = None
    web_search: bool = False
    web_mode: Optional[str] = "auto"
    attachments: Optional[List[Dict[str, Any]]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    tier: Optional[str] = "fast"
    mode: Optional[str] = "code"
    approval_policy: Optional[str] = "ask"
    local_only: bool = False
    run_id: Optional[str] = None


def requests_web_research(prompt: str) -> bool:
    value = prompt.lower()
    return bool(
        re.search(r"\b(?:browse|google|web\s+search|search\s+(?:the\s+)?web|search\s+online|internet\s+search|look\s+up\s+online|web\s+access|internet\s+access|access\s+(?:the\s+)?(?:web|internet)|search\s+(?:the\s+)?internet|can\s+you\s+(?:browse|search))\b", value)
        or re.search(r"\b(?:latest|current|today'?s?)\s+(?:news|price|prices|release|version|schedule|score|scores|weather)\b", value)
        or re.search(r"\b(?:can|could)\s+you\s+(?:download|install)\b", value)
        or re.search(r"\b(?:download|install|available|availability|supported|support)\b.{0,80}\b(?:linux|ubuntu|debian|windows|macos|package|app|desktop|software|tool)\b", value)
    )

@app.post("/api/chat")
async def chat_stream(req: ChatRequest):
    prompt = req.prompt.strip()
    if not prompt and not req.attachments:
        raise HTTPException(status_code=400, detail="A prompt or attachment is required")
    workspace_value = req.project_path or get_active_workspace()
    workspace = ""
    if workspace_value:
        workspace = os.path.abspath(os.path.expanduser(workspace_value))
        if not os.path.isdir(workspace):
            raise HTTPException(status_code=400, detail="The selected workspace is not available")
    if req.mode not in {"ask", "code"}:
        raise HTTPException(status_code=400, detail="Mode must be 'ask' or 'code'")
    if req.approval_policy not in {"ask", "workspace", "whole_device"}:
        raise HTTPException(status_code=400, detail="Approval policy must be 'ask', 'workspace', or 'whole_device'")
    if req.web_mode not in {"off", "auto", "research"}:
        raise HTTPException(status_code=400, detail="Web mode must be 'off', 'auto', or 'research'")

    cfg = load_provider_config()
    provider = req.provider or cfg.get("active_provider", "free_pool")
    model = req.model or cfg.get("active_model", "fast-auto")
    run_id = req.run_id or f"run_{uuid.uuid4().hex}"
    active_file = req.active_file.model_dump() if req.active_file else None
    explicit_web_request = requests_web_research(prompt)
    web_enabled = req.web_search or req.web_mode != "off"
    web_search_required = req.web_search or (
        req.web_mode != "off" and (explicit_web_request or req.web_mode == "research")
    )

    def event_generator():
        for event in agent_runtime.run(
            session_id=req.session_id,
            prompt=prompt,
            workspace=workspace,
            provider=provider,
            model=model,
            tier=req.tier or "fast",
            mode=req.mode or "code",
            local_only=req.local_only,
            active_file=active_file,
            attachments=req.attachments or [],
            web_search=web_enabled,
            web_search_required=web_search_required,
            approval_policy=req.approval_policy or "ask",
            config=cfg,
            run_id=run_id,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Run-ID": run_id},
    )


@app.post("/api/runs/{run_id}/cancel")
def cancel_agent_run(run_id: str):
    agent_runtime.cancel(run_id)
    return {"success": True, "run_id": run_id, "status": "cancellation_requested"}


class ApprovalPayload(BaseModel):
    call_id: str
    approved: bool


@app.post("/api/runs/{run_id}/approval")
def resolve_agent_approval(run_id: str, payload: ApprovalPayload):
    if not agent_runtime.registry.resolve_approval(run_id, payload.call_id, payload.approved):
        raise HTTPException(status_code=409, detail="This approval is no longer pending")
    return {"success": True, "run_id": run_id, "call_id": payload.call_id, "approved": payload.approved}

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    if not os.path.exists(INDEX_HTML_PATH):
        raise HTTPException(status_code=404, detail=f"index.html not found at {INDEX_HTML_PATH}")
    with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    response = HTMLResponse(content=content)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/vexp.svg", include_in_schema=False)
def serve_favicon():
    path = os.path.join(FRONTEND_DIST_DIR, "vexp.svg")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Icon build artifact is missing")
    return FileResponse(path, media_type="image/svg+xml")

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
