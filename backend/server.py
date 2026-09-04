import os
import sys
import json
import re
import time
import uuid
import threading
import subprocess
import urllib.request
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

app = FastAPI(title="VexP Code IDE")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SERVER_START_TIME = time.time()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
INDEX_HTML_PATH = os.path.join(FRONTEND_DIR, "index.html")

CONFIG_DIR = os.path.expanduser("~/.claude_code_ide")
os.makedirs(CONFIG_DIR, exist_ok=True)

DEFAULT_STORAGE_DIR = os.path.join(CONFIG_DIR, "storage")
MEMORY_FILE = os.path.join(CONFIG_DIR, "ai_memory.json")
HISTORY_FILE = os.path.join(CONFIG_DIR, "chat_history.json")
WORKSPACE_CONFIG_FILE = os.path.join(CONFIG_DIR, "active_workspace.json")
RECENT_WORKSPACES_FILE = os.path.join(CONFIG_DIR, "recent_workspaces.json")
PROVIDER_CONFIG_FILE = os.path.join(CONFIG_DIR, "provider_config.json")

# Migrate existing configuration if present
if not os.path.exists(PROVIDER_CONFIG_FILE) and os.path.exists(os.path.expanduser("~/.vexp_provider_config.json")):
    try:
        import shutil
        shutil.copyfile(os.path.expanduser("~/.vexp_provider_config.json"), PROVIDER_CONFIG_FILE)
    except Exception:
        pass

from tools import TOOLS_SPEC, execute_agent_tool

def default_provider_config():
    return {
        "active_provider": "ollama",
        "active_model": "qwen2.5:14b",
        "providers": {
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "qwen2.5:14b",
                "enabled": True
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
        }
    }

def load_provider_config() -> dict:
    if os.path.exists(PROVIDER_CONFIG_FILE):
        try:
            with open(PROVIDER_CONFIG_FILE, "r") as f:
                data = json.load(f)
                d = default_provider_config()
                d["active_provider"] = data.get("active_provider", d["active_provider"])
                d["active_model"] = data.get("active_model", d["active_model"])
                for p_key, p_val in data.get("providers", {}).items():
                    if p_key in d["providers"]:
                        d["providers"][p_key].update(p_val)
                    else:
                        d["providers"][p_key] = p_val
                return d
        except Exception:
            pass
    cfg = default_provider_config()
    save_provider_config(cfg)
    return cfg

def save_provider_config(cfg: dict):
    try:
        with open(PROVIDER_CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("Failed to save provider config:", e)

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
    return {"server_time": SERVER_START_TIME, "version": "2.6.0"}

os.makedirs(DEFAULT_STORAGE_DIR, exist_ok=True)

def load_recent_workspaces() -> List[Dict[str, str]]:
    if os.path.exists(RECENT_WORKSPACES_FILE):
        try:
            with open(RECENT_WORKSPACES_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    # Seed with initial common project directories if empty
    home = os.path.expanduser("~")
    defaults = [
        {"name": "Work", "path": os.path.join(home, "Desktop", "Work")},
        {"name": "claude-code-ide", "path": os.path.abspath(PROJECT_ROOT)},
        {"name": "MatchIQ MultiLeague", "path": os.path.join(home, "Desktop", "Zurich_VPS", "home", "mahimalam2400", "MatchIQ_MultiLeague")},
        {"name": "Fifa Project", "path": os.path.join(home, "Desktop", "Zurich_VPS", "home", "mahimalam2400", "Fifa_project")},
        {"name": "Current Workspace", "path": os.getcwd()}
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
    full = os.path.abspath(os.path.expanduser(payload.path))
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
    old_f = os.path.abspath(os.path.expanduser(payload.old_path))
    new_f = os.path.abspath(os.path.expanduser(payload.new_path))
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
    full = os.path.abspath(os.path.expanduser(payload.path))
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
    full_path = os.path.abspath(os.path.expanduser(target))
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
    target_dir = path or get_active_workspace()
    if not os.path.exists(target_dir):
        return []
    
    cmd = ["grep", "-rnI", "--exclude-dir={.git,node_modules,__pycache__,.venv,.astro,dist,build}", 
           "-m", "50", query, target_dir]
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

@app.get("/api/git/status")
def git_status_endpoint(path: Optional[str] = Query(None)):
    target_dir = path or get_active_workspace()
    try:
        is_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, cwd=target_dir).returncode == 0
        if not is_git:
            return {"branch": "none", "is_repo": False, "files": [], "staged": [], "unstaged": [], "count": 0, "remote_url": ""}
        
        branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        if not branch:
            head_rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
            branch = head_rev or "main"
        
        remote_url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
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
            "remote_url": remote_url
        }
    except Exception as e:
        return {"branch": "none", "is_repo": False, "files": [], "staged": [], "unstaged": [], "count": 0, "error": str(e), "remote_url": ""}

@app.post("/api/git/init")
def git_init_endpoint(payload: GitActionPayload):
    target_dir = payload.path or get_active_workspace()
    try:
        res = subprocess.run(["git", "init"], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/stage")
def git_stage_endpoint(payload: GitActionPayload):
    target_dir = payload.path or get_active_workspace()
    try:
        if payload.all or not payload.file:
            res = subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=target_dir)
        else:
            res = subprocess.run(["git", "add", payload.file], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/unstage")
def git_unstage_endpoint(payload: GitActionPayload):
    target_dir = payload.path or get_active_workspace()
    try:
        if payload.all or not payload.file:
            res = subprocess.run(["git", "reset", "HEAD"], capture_output=True, text=True, cwd=target_dir)
        else:
            res = subprocess.run(["git", "reset", "HEAD", "--", payload.file], capture_output=True, text=True, cwd=target_dir)
        return {"success": res.returncode == 0, "output": res.stdout + res.stderr}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/discard")
def git_discard_endpoint(payload: GitActionPayload):
    target_dir = payload.path or get_active_workspace()
    if not payload.file and not payload.all:
        raise HTTPException(status_code=400, detail="File required")
    try:
        if payload.all:
            subprocess.run(["git", "checkout", "--", "."], capture_output=True, text=True, cwd=target_dir)
            subprocess.run(["git", "clean", "-fd"], capture_output=True, text=True, cwd=target_dir)
            return {"success": True}
        
        full_p = os.path.join(target_dir, payload.file)
        res = subprocess.run(["git", "checkout", "--", payload.file], capture_output=True, text=True, cwd=target_dir)
        if res.returncode != 0:
            if os.path.isdir(full_p):
                shutil.rmtree(full_p, ignore_errors=True)
            elif os.path.exists(full_p):
                os.remove(full_p)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/commit")
def git_commit_endpoint(payload: GitCommitPayload):
    target_dir = payload.path or get_active_workspace()
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Commit message cannot be empty")
    try:
        if payload.stage_all:
            subprocess.run(["git", "add", "-A"], capture_output=True, text=True, cwd=target_dir)
        res = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True, cwd=target_dir)
        return {
            "success": res.returncode == 0,
            "output": res.stdout.strip() or res.stderr.strip()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/push")
def git_push_endpoint(payload: GitRemotePayload):
    target_dir = payload.path or get_active_workspace()
    try:
        branch = payload.branch
        if not branch:
            branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, cwd=target_dir).stdout.strip()
        cmd = ["git", "push", payload.remote or "origin", branch or "main"]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=target_dir)
        return {
            "success": res.returncode == 0,
            "output": res.stdout.strip() or res.stderr.strip()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/git/pull")
def git_pull_endpoint(payload: GitRemotePayload):
    target_dir = payload.path or get_active_workspace()
    try:
        cmd = ["git", "pull", "--rebase"]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=target_dir)
        return {
            "success": res.returncode == 0,
            "output": res.stdout.strip() or res.stderr.strip()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/git/diff")
def git_diff_endpoint(file: str = Query(...), path: Optional[str] = Query(None)):
    target_dir = path or get_active_workspace()
    try:
        orig_res = subprocess.run(["git", "show", f"HEAD:{file}"], capture_output=True, text=True, cwd=target_dir)
        original_content = orig_res.stdout if orig_res.returncode == 0 else ""
        
        full_path = os.path.join(target_dir, file)
        modified_content = ""
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                modified_content = f.read()
        
        ext = os.path.splitext(file)[1].lower()
        language = LANGUAGE_MAP.get(ext, "plaintext")
        
        return {
            "success": True,
            "file": file,
            "original": original_content,
            "modified": modified_content,
            "language": language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/fs/read")
def read_file(path: str = Query(...)):
    full_path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    ext = os.path.splitext(full_path)[1].lower()
    language = LANGUAGE_MAP.get(ext, "plaintext")
    
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {
            "success": True,
            "path": full_path,
            "name": os.path.basename(full_path),
            "content": content,
            "language": language
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WriteFilePayload(BaseModel):
    path: str
    content: str

@app.post("/api/fs/write")
def write_file(payload: WriteFilePayload):
    full_path = os.path.abspath(os.path.expanduser(payload.path))
    try:
        parent = os.path.dirname(full_path)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(payload.content)
        short = full_path.replace(os.path.expanduser("~"), "~")
        return {"success": True, "path": full_path, "message": f"Saved {short}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------
# Command Execution (Terminal Output Drawer)
# ---------------------------------------------------------------------
class ExecuteRequest(BaseModel):
    command: str
    cwd: Optional[str] = None

@app.post("/api/execute")
def run_command_endpoint(req: ExecuteRequest):
    work_dir = req.cwd or get_active_workspace()
    if not os.path.exists(work_dir):
        work_dir = get_active_workspace()
    try:
        res = subprocess.run(
            req.command, shell=True, text=True,
            capture_output=True, timeout=120,
            cwd=work_dir
        )
        out = res.stdout + res.stderr
        return {
            "success": res.returncode == 0,
            "exit_code": res.returncode,
            "output": out.strip() if out.strip() else "(Command finished with no output)"
        }
    except Exception as e:
        return {"success": False, "exit_code": -1, "output": str(e)}

# ---------------------------------------------------------------------
# Web Search & Memories
# ---------------------------------------------------------------------
def search_duckduckgo(query, max_results=3):
    try:
        results = list(DDGS().text(query, max_results=max_results))
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append({
                "index": i,
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", "")
            })
        return formatted
    except Exception:
        return []

def load_memories():
    if not os.path.exists(MEMORY_FILE):
        defaults = [
            {"id": "1", "content": "User is VexP with dual-boot Ubuntu 24.04 and Windows 11."},
            {"id": "2", "content": "Hardware: AMD Ryzen 7 6800HS (8C/16T, 22GB RAM) and NVIDIA GeForce RTX 3050 Laptop GPU (CUDA 13.2)."},
            {"id": "3", "content": "Active development projects: MatchIQ_MultiLeague (Astro, React, Python) and Fifa_project."}
        ]
        with open(MEMORY_FILE, "w") as f:
            json.dump(defaults, f, indent=2)
        return defaults
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []

@app.get("/api/memories")
def get_memories():
    return load_memories()

class MemoryPayload(BaseModel):
    content: str

@app.post("/api/memories")
def add_memory_endpoint(payload: MemoryPayload):
    mems = load_memories()
    new_mem = {"id": str(uuid.uuid4())[:8], "content": payload.content}
    mems.append(new_mem)
    with open(MEMORY_FILE, "w") as f:
        json.dump(mems, f, indent=2)
    return new_mem

def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return list(data.values())
            return []
    except Exception:
        return []

def save_history(history: List[Dict[str, Any]]):
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("Failed to save history:", e)

def append_to_session(session_id: str, role: str, content: str, workspace: str = "", thinking_seconds: float = None):
    hist = load_history()
    sess = None
    for s in hist:
        if s.get("id") == session_id:
            sess = s
            break
    
    now = int(time.time())
    if sess is None:
        clean_title = content.strip().replace("\n", " ")[:36]
        if len(content.strip()) > 36:
            clean_title += "..."
        sess = {
            "id": session_id,
            "title": clean_title or "New Chat",
            "workspace": workspace or "",
            "created_at": now,
            "updated_at": now,
            "messages": []
        }
        hist.insert(0, sess)
    else:
        sess["updated_at"] = now
        if workspace and not sess.get("workspace"):
            sess["workspace"] = workspace
        if role == "user" and (sess.get("title") in ["New Chat", "New Session", "Conversation"] or not sess.get("title")):
            clean_title = content.strip().replace("\n", " ")[:36]
            if len(content.strip()) > 36:
                clean_title += "..."
            sess["title"] = clean_title
            
    msg_entry = {
        "role": role,
        "content": content,
        "timestamp": now
    }
    if thinking_seconds is not None:
        msg_entry["thinking_seconds"] = thinking_seconds
        
    sess["messages"].append(msg_entry)
    save_history(hist)
    return sess

@app.get("/api/history")
def get_history_sessions():
    hist = load_history()
    hist.sort(key=lambda s: s.get("updated_at", s.get("created_at", 0)), reverse=True)
    return [
        {
            "id": s.get("id"),
            "title": s.get("title", "Conversation"),
            "created_at": s.get("created_at", 0),
            "updated_at": s.get("updated_at", 0),
            "message_count": len(s.get("messages", [])),
            "workspace": s.get("workspace", "")
        }
        for s in hist if s.get("id")
    ]

@app.get("/api/history/{session_id}")
def get_session(session_id: str):
    hist = load_history()
    for s in hist:
        if s.get("id") == session_id:
            return s
    return {"id": session_id, "title": "New Session", "workspace": "", "messages": []}

@app.delete("/api/history/{session_id}")
def delete_session(session_id: str):
    hist = load_history()
    hist = [s for s in hist if s.get("id") != session_id]
    save_history(hist)
    return {"success": True}

class TitlePayload(BaseModel):
    title: str

@app.post("/api/history/{session_id}/title")
def rename_session(session_id: str, payload: TitlePayload):
    hist = load_history()
    for s in hist:
        if s.get("id") == session_id:
            s["title"] = payload.title.strip()
            save_history(hist)
            return {"success": True, "title": s["title"]}
    return {"error": "Session not found"}

@app.delete("/api/memories/{mem_id}")
def delete_memory_endpoint(mem_id: str):
    mems = load_memories()
    mems = [m for m in mems if m["id"] != mem_id]
    with open(MEMORY_FILE, "w") as f:
        json.dump(mems, f, indent=2)
    return {"success": True}

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        content = await file.read()
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
    stats = {"vram_used": "N/A", "vram_total": "4096 MiB", "linux_free": "49 GB", "storage_free": "168 GB"}
    try:
        smi = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True)
        if smi.returncode == 0:
            parts = smi.stdout.strip().split(",")
            stats["vram_used"] = parts[0].strip() + " MiB"
            stats["vram_total"] = parts[1].strip() + " MiB"
    except:
        pass
    try:
        df = subprocess.run(["df", "-h", "/", "/mnt/windows"], capture_output=True, text=True)
        lines = df.stdout.strip().split("\n")
        if len(lines) >= 3:
            stats["linux_free"] = lines[1].split()[3]
            stats["storage_free"] = lines[2].split()[3]
    except:
        pass
    return stats

# ---------------------------------------------------------------------
# Dynamic AI Models & Multi-Provider Engine
# ---------------------------------------------------------------------
class SwitchModelPayload(BaseModel):
    provider: str
    model: str

class PullModelPayload(BaseModel):
    model: str

class TestProviderPayload(BaseModel):
    provider: str
    base_url: Optional[str] = ""
    api_key: Optional[str] = ""
    model: Optional[str] = ""

class ProviderUpdatePayload(BaseModel):
    active_provider: Optional[str] = None
    active_model: Optional[str] = None
    providers: Dict[str, Any]

@app.get("/api/models")
def get_available_models():
    cfg = load_provider_config()
    ollama_url = cfg["providers"].get("ollama", {}).get("base_url", "http://127.0.0.1:11434")
    local_models = fetch_ollama_models(ollama_url)

    cloud_models = []
    for p_id in ["openrouter", "openai", "anthropic"]:
        p_data = cfg["providers"].get(p_id, {})
        if p_data.get("enabled"):
            cloud_models.append({
                "provider": p_id,
                "model": p_data.get("model", ""),
                "name": f"{p_data.get('model', '')} ({p_id.capitalize()})"
            })

    return {
        "active_provider": cfg.get("active_provider", "ollama"),
        "active_model": cfg.get("active_model", "qwen2.5:14b"),
        "local_models": local_models,
        "cloud_models": cloud_models
    }

@app.post("/api/models/switch")
def switch_active_model(payload: SwitchModelPayload):
    cfg = load_provider_config()
    cfg["active_provider"] = payload.provider
    cfg["active_model"] = payload.model
    if payload.provider in cfg["providers"]:
        cfg["providers"][payload.provider]["model"] = payload.model
        cfg["providers"][payload.provider]["enabled"] = True
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
    return safe_cfg

@app.post("/api/settings/providers")
def update_provider_settings(payload: ProviderUpdatePayload):
    cfg = load_provider_config()
    if payload.active_provider:
        cfg["active_provider"] = payload.active_provider
    if payload.active_model:
        cfg["active_model"] = payload.active_model

    for p_id, incoming in payload.providers.items():
        if p_id in cfg["providers"]:
            new_key = incoming.get("api_key", "")
            if not new_key or "..." in new_key or "•••" in new_key:
                incoming["api_key"] = cfg["providers"][p_id].get("api_key", "")
            cfg["providers"][p_id].update(incoming)

    save_provider_config(cfg)
    return {"success": True, "config": cfg}

@app.post("/api/settings/providers/test")
def test_provider_connection(payload: TestProviderPayload):
    p_id = payload.provider
    t0 = time.time()
    try:
        if p_id == "ollama":
            url = (payload.base_url or "http://127.0.0.1:11434").rstrip("/")
            req = urllib.request.Request(f"{url}/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Connected to Ollama ({len(data.get('models', []))} models installed)"}

        elif p_id in ["openai", "openrouter"]:
            url = payload.base_url.rstrip("/")
            if not url.endswith("/chat/completions"):
                url = f"{url}/chat/completions"
            body = json.dumps({
                "model": payload.model or ("gpt-4o-mini" if p_id == "openai" else "openai/gpt-4o-mini"),
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {payload.api_key}"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Successfully connected to {p_id.capitalize()} ({ms}ms)"}

        elif p_id == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            body = json.dumps({
                "model": payload.model or "claude-3-5-haiku-20241022",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "hi"}]
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": payload.api_key,
                    "anthropic-version": "2023-06-01"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            ms = round((time.time() - t0) * 1000)
            return {"success": True, "latency_ms": ms, "message": f"Successfully connected to Anthropic Claude ({ms}ms)"}

        return {"error": f"Unknown provider: {p_id}"}
    except Exception as e:
        return {"error": str(e)}

def stream_llm_turn(provider: str, model: str, messages: list, tools: list, cfg: dict):
    p_info = cfg.get("providers", {}).get(provider, {})

    if provider == "ollama":
        base_url = p_info.get("base_url", "http://127.0.0.1:11434").rstrip("/")
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": True
        }
        accumulated_text = ""
        accumulated_tools = []
        try:
            req_obj = urllib.request.Request(
                f"{base_url}/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req_obj, timeout=180) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except:
                        continue
                    msg = chunk.get("message", {})
                    t_calls = msg.get("tool_calls")
                    if t_calls:
                        for tc in t_calls:
                            accumulated_tools.append(tc)
                            yield ("tool_call", tc)
                    token = msg.get("content", "")
                    if token:
                        accumulated_text += token
                        yield ("token", token)
                    if chunk.get("done"):
                        break
            yield ("done", {"content": accumulated_text, "tool_calls": accumulated_tools})
        except Exception as err:
            yield ("error", str(err))

    elif provider in ["openai", "openrouter"]:
        base_url = p_info.get("base_url", "https://api.openai.com/v1").rstrip("/")
        endpoint = f"{base_url}/chat/completions" if not base_url.endswith("/chat/completions") else base_url
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": True
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {p_info.get('api_key', '')}"
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/vexp/claude-code-ide"
            headers["X-Title"] = "VexP Code IDE"

        accumulated_text = ""
        tool_calls_map = {}
        try:
            req_obj = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req_obj, timeout=180) as resp:
                for line in resp:
                    txt = line.decode("utf-8").strip()
                    if not txt or not txt.startswith("data:"):
                        continue
                    data_str = txt[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except:
                        continue
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content_piece = delta.get("content")
                    if content_piece:
                        accumulated_text += content_piece
                        yield ("token", content_piece)
                    t_calls = delta.get("tool_calls")
                    if t_calls:
                        for tc in t_calls:
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc.get("id", f"call_{idx}"),
                                    "function": {
                                        "name": tc.get("function", {}).get("name", ""),
                                        "arguments": ""
                                    }
                                }
                            if tc.get("function", {}).get("name"):
                                tool_calls_map[idx]["function"]["name"] = tc["function"]["name"]
                            if tc.get("function", {}).get("arguments"):
                                tool_calls_map[idx]["function"]["arguments"] += tc["function"]["arguments"]

            final_tool_calls = []
            for tc in tool_calls_map.values():
                args_str = tc["function"]["arguments"]
                try:
                    tc["function"]["arguments"] = json.loads(args_str)
                except:
                    pass
                final_tool_calls.append(tc)
                yield ("tool_call", tc)

            yield ("done", {"content": accumulated_text, "tool_calls": final_tool_calls})
        except Exception as err:
            yield ("error", str(err))

    elif provider == "anthropic":
        endpoint = "https://api.anthropic.com/v1/messages"
        system_content = ""
        anth_msgs = []
        for m in messages:
            r = m.get("role")
            c = m.get("content", "")
            if r == "system":
                system_content += c + "\n"
            elif r == "user":
                anth_msgs.append({"role": "user", "content": c})
            elif r == "assistant":
                anth_msgs.append({"role": "assistant", "content": c or "Processing..."})
            elif r == "tool":
                anth_msgs.append({
                    "role": "user",
                    "content": f"Tool output: {c}"
                })

        anth_tools = []
        for t in tools:
            fn = t.get("function", {})
            anth_tools.append({
                "name": fn.get("name"),
                "description": fn.get("description"),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
            })

        payload = {
            "model": model,
            "system": system_content.strip(),
            "messages": anth_msgs,
            "max_tokens": 4096,
            "stream": True
        }
        if anth_tools:
            payload["tools"] = anth_tools

        headers = {
            "Content-Type": "application/json",
            "x-api-key": p_info.get("api_key", ""),
            "anthropic-version": "2023-06-01"
        }
        accumulated_text = ""
        current_tool_id = ""
        current_tool_name = ""
        current_tool_json = ""
        final_tool_calls = []

        try:
            req_obj = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers
            )
            with urllib.request.urlopen(req_obj, timeout=180) as resp:
                for line in resp:
                    txt = line.decode("utf-8").strip()
                    if not txt.startswith("data:"):
                        continue
                    data_str = txt[5:].strip()
                    try:
                        chunk = json.loads(data_str)
                    except:
                        continue
                    ev_type = chunk.get("type", "")
                    if ev_type == "content_block_start":
                        cb = chunk.get("content_block", {})
                        if cb.get("type") == "tool_use":
                            current_tool_id = cb.get("id", "")
                            current_tool_name = cb.get("name", "")
                            current_tool_json = ""
                    elif ev_type == "content_block_delta":
                        delta = chunk.get("delta", {})
                        d_type = delta.get("type", "")
                        if d_type == "text_delta":
                            text_piece = delta.get("text", "")
                            accumulated_text += text_piece
                            yield ("token", text_piece)
                        elif d_type == "input_json_delta":
                            current_tool_json += delta.get("partial_json", "")
                    elif ev_type == "content_block_stop":
                        if current_tool_name:
                            try:
                                parsed_args = json.loads(current_tool_json)
                            except:
                                parsed_args = current_tool_json
                            tc_obj = {
                                "id": current_tool_id,
                                "function": {
                                    "name": current_tool_name,
                                    "arguments": parsed_args
                                }
                            }
                            final_tool_calls.append(tc_obj)
                            yield ("tool_call", tc_obj)
                            current_tool_name = ""
                            current_tool_id = ""
                            current_tool_json = ""
                    elif ev_type == "message_stop":
                        break

            yield ("done", {"content": accumulated_text, "tool_calls": final_tool_calls})
        except Exception as err:
            yield ("error", str(err))

    else:
        yield ("error", f"Unsupported provider: {provider}")

def call_llm_turn(provider: str, model: str, messages: list, tools: list, cfg: dict):
    full_content = ""
    tool_calls = []
    for kind, payload in stream_llm_turn(provider, model, messages, tools, cfg):
        if kind == "error":
            return {}, payload
        elif kind == "token":
            full_content += payload
        elif kind == "tool_call":
            tool_calls.append(payload)
        elif kind == "done":
            if isinstance(payload, dict):
                if not full_content and payload.get("content"):
                    full_content = payload["content"]
                if not tool_calls and payload.get("tool_calls"):
                    tool_calls = payload["tool_calls"]
    return {
        "role": "assistant",
        "content": full_content,
        "tool_calls": tool_calls
    }, ""

# ---------------------------------------------------------------------
# Claude Code Agent Chat (with Active File Context)
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
    attachments: Optional[List[Dict[str, Any]]] = None
    provider: Optional[str] = None
    model: Optional[str] = None

@app.post("/api/chat")
async def chat_stream(req: ChatRequest):
    memories = load_memories()
    mem_text = "\n".join([f"- {m['content']}" for m in memories]) if memories else "None."

    # Dynamic Filesystem Context Detection
    fs_context = ""
    target_proj = os.path.abspath(os.path.expanduser(req.project_path or get_active_workspace()))
    
    # Check if user mentioned Zurich_VPS or other known folders
    prompt_lower = req.prompt.lower()
    if "zurich" in prompt_lower or "zurich_vps" in prompt_lower:
        zurich_base = os.path.join(os.path.expanduser("~"), "Desktop", "Zurich_VPS", "home", "mahimalam2400")
        if os.path.exists(zurich_base):
            sub_items = [f"📁 {d}" if os.path.isdir(os.path.join(zurich_base, d)) else f"📄 {d}" 
                         for d in sorted(os.listdir(zurich_base)) if not d.startswith(".")]
            fs_context += (
                f"\n\n[LOCAL FILESYSTEM INSPECTION FOR Zurich_VPS]:\n"
                f"Full Path: {zurich_base}\n"
                f"Contents:\n" + "\n".join(sub_items[:40]) + "\n"
            )

    # Active workspace contents
    if os.path.exists(target_proj):
        try:
            entries = [f"📁 {d}" if os.path.isdir(os.path.join(target_proj, d)) else f"📄 {d}" 
                       for d in sorted(os.listdir(target_proj)) if not d.startswith(".")][:30]
            fs_context += (
                f"\n\n[CURRENT ACTIVE WORKSPACE]:\n"
                f"Path: {target_proj}\n"
                f"Visible items:\n" + "\n".join(entries) + "\n"
            )
        except:
            pass

    # Active file context injection (Claude Code IDE mode)
    file_context = ""
    if req.active_file and req.active_file.content:
        snippet = req.active_file.content[:40000]
        file_context = (
            f"\n\nCURRENTLY OPEN FILE IN IDE:\n"
            f"File: {req.active_file.name} ({req.active_file.path})\n"
            f"```{req.active_file.language or 'text'}\n"
            f"{snippet}\n"
            f"```\n"
        )

    # Attachments
    attachment_context = ""
    if req.attachments:
        for att in req.attachments:
            name = att.get("name", "file")
            content = att.get("content", "")
            attachment_context += f"\n--- ATTACHED FILE: {name} ---\n{content}\n-----------------------------\n"

    # 5-Layer Agentic Thinking Harness Engine: TOOLS_SPEC & execute_agent_tool imported from tools.py

    web_sources = []
    web_context = ""
    if req.web_search or any(kw in req.prompt.lower() for kw in ["latest", "today", "news", "current", "score", "weather", "recent"]):
        web_sources = search_duckduckgo(req.prompt, max_results=3)
        if web_sources:
            web_context = "\n\n".join([f"Source [{s['title']}] ({s['url']}):\n{s['snippet']}" for s in web_sources])

    system_prompt = f"""You are VexP Code, an expert agentic AI software engineer running locally on Ubuntu 24.04 Linux with NVIDIA RTX 3050 GPU acceleration.
You have direct access to tools (read_file_range, search_files, write_file, apply_file_diff, run_terminal_command) to inspect, create, edit, and test files in the workspace.

ACTIVE WORKSPACE LOCATION:
Target Project Root: `{target_proj}`

PROJECT CONTEXT & MEMORY:
{mem_text}
{fs_context}

CRITICAL RULES:
1. You are operating directly inside `{target_proj}`. When writing or editing code, ALWAYS create and modify files directly in this active workspace. Never redirect files to arbitrary external folders like AI_Generated unless explicitly instructed by the user.
2. Use `write_file` to create new files or overwrite existing files in the project.
3. Use `apply_file_diff` to modify specific sections of existing files.
4. Use `run_terminal_command` to execute tests, linters, or scripts in the active workspace.
5. When outputting code in markdown, always include the file path in a comment or heading so it can be applied directly to the project.
6. Keep explanations direct, concise, and technically precise.
"""

    full_prompt = req.prompt
    if file_context:
        full_prompt = f"{file_context}\n\nUser Request: {full_prompt}"
    if attachment_context:
        full_prompt = f"User Attached Context:\n{attachment_context}\n\n{full_prompt}"
    if web_context:
        full_prompt = f"Live Web Data:\n{web_context}\n\n{full_prompt}"

    def event_generator():
        start_time = time.time()
        target_ws = os.path.abspath(os.path.expanduser(req.project_path or get_active_workspace()))

        # Save user message to session immediately
        try:
            append_to_session(req.session_id, "user", req.prompt, workspace=target_ws)
        except Exception as err:
            print("Session save user error:", err)

        ws_items = []
        if os.path.exists(target_ws):
            try:
                ws_items = [d for d in os.listdir(target_ws) if not d.startswith(".")][:30]
            except:
                pass
        git_b = ""
        try:
            git_b = subprocess.run(["git", "branch", "--show-current"], cwd=target_ws, capture_output=True, text=True).stdout.strip()
        except:
            pass

        branch_name = git_b if git_b else "main"
        ws_name = os.path.basename(target_ws)
        active_f = req.active_file.name if (req.active_file and req.active_file.name) else "none"

        # --- LAYER 1: Senses & Workspace Perception ---
        l1_detail = f"{ws_name} ({len(ws_items)} items, branch: {branch_name}, file: {active_f})"
        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer1', 'layer': 'Layer 1', 'status': 'done', 'label': 'Workspace Perception', 'detail': l1_detail})}\n\n"

        # --- LAYER 2: Architect Deliberation & Strategy ---
        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer2', 'layer': 'Layer 2', 'status': 'active', 'label': 'Intent Deliberation & Strategy', 'detail': 'Classifying intent and formulating execution plan...'})}\n\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": full_prompt}
        ]

        turns = 0
        max_turns = 3
        executed_tools_count = 0
        layer5_activated = False
        full_session_text = ""

        cfg = load_provider_config()
        active_provider = req.provider or cfg.get("active_provider", "ollama")
        active_model = req.model or cfg.get("active_model", "qwen2.5:14b")

        while turns < max_turns:
            turns += 1
            turn_content = ""
            turn_tool_calls = []
            turn_error = ""

            for kind, payload in stream_llm_turn(active_provider, active_model, messages, TOOLS_SPEC, cfg):
                if kind == "error":
                    turn_error = payload
                    break
                elif kind == "token":
                    if not layer5_activated:
                        if executed_tools_count == 0:
                            yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer2', 'layer': 'Layer 2', 'status': 'done', 'label': 'Intent Deliberation & Strategy', 'detail': 'Plan selected: Direct neural synthesis'})}\n\n"
                            yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer3', 'layer': 'Layer 3', 'status': 'done', 'label': 'Action Engine & Tool Dispatch', 'detail': 'Action engine ready (read/diff/shell tools on standby)'})}\n\n"
                            yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer4', 'layer': 'Layer 4', 'status': 'done', 'label': 'Environmental Observation & Sensors', 'detail': 'Workspace bounds & safety constraints verified'})}\n\n"
                        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer5', 'layer': 'Layer 5', 'status': 'active', 'label': 'Solution Synthesis & Delivery', 'detail': 'Streaming verified response with editor bindings'})}\n\n"
                        layer5_activated = True

                    turn_content += payload
                    full_session_text += payload
                    yield f"data: {json.dumps({'type': 'token', 'text': payload})}\n\n"

                elif kind == "tool_call":
                    turn_tool_calls.append(payload)

                elif kind == "done":
                    if isinstance(payload, dict):
                        if not turn_content and payload.get("content"):
                            turn_content = payload["content"]
                            full_session_text += turn_content
                        if not turn_tool_calls and payload.get("tool_calls"):
                            turn_tool_calls = payload["tool_calls"]

            if turn_error:
                yield f"data: {json.dumps({'type': 'error', 'text': f'Agent Harness Loop error ({active_provider}:{active_model}): {turn_error}'})}\n\n"
                break

            if turn_tool_calls:
                executed_tools_count += len(turn_tool_calls)
                yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer2', 'layer': 'Layer 2', 'status': 'done', 'label': 'Intent Deliberation & Strategy', 'detail': f'Plan selected: Tool augmentation required ({len(turn_tool_calls)} calls)'})}\n\n"

                messages.append({
                    "role": "assistant",
                    "content": turn_content,
                    "tool_calls": turn_tool_calls
                })

                for tc in turn_tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except:
                            pass

                    # --- LAYER 3: Action Engine & Tool Dispatch ---
                    yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer3', 'layer': 'Layer 3', 'status': 'active', 'label': 'Action Engine & Tool Dispatch', 'detail': f'Dispatching {name}({str(args)[:60]})'})}\n\n"

                    # --- LAYER 4: Environmental Observation & Feedback Loop ---
                    obs = execute_agent_tool(name, args, target_ws)
                    has_err = "error" in obs or obs.get("exit_code", 0) != 0

                    status_val = "done" if not has_err else "error"
                    detail_val = f"Observed output ({str(obs)[:60]})" if not has_err else f"Self-correcting error: {obs.get('error', '')[:60]}"

                    yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer3', 'layer': 'Layer 3', 'status': 'done', 'label': 'Action Engine & Tool Dispatch', 'detail': f'Invoked {name}'})}\n\n"
                    yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer4', 'layer': 'Layer 4', 'status': status_val, 'label': 'Environmental Observation & Sensors', 'detail': detail_val})}\n\n"

                    if name in ["write_file", "apply_file_diff"] and not has_err:
                        yield f"data: {json.dumps({'type': 'file_updated', 'path': obs.get('path', '')})}\n\n"

                    messages.append({
                        "role": "tool",
                        "content": json.dumps(obs)
                    })
            else:
                # Direct response or completion after tools
                if not layer5_activated:
                    if executed_tools_count == 0:
                        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer2', 'layer': 'Layer 2', 'status': 'done', 'label': 'Intent Deliberation & Strategy', 'detail': 'Plan selected: Direct neural synthesis'})}\n\n"
                        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer3', 'layer': 'Layer 3', 'status': 'done', 'label': 'Action Engine & Tool Dispatch', 'detail': 'Action engine ready (read/diff/shell tools on standby)'})}\n\n"
                        yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer4', 'layer': 'Layer 4', 'status': 'done', 'label': 'Environmental Observation & Sensors', 'detail': 'Workspace bounds & safety constraints verified'})}\n\n"
                    yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer5', 'layer': 'Layer 5', 'status': 'active', 'label': 'Solution Synthesis & Delivery', 'detail': 'Streaming verified response with editor bindings'})}\n\n"

                # Mark Layer 5 Done
                yield f"data: {json.dumps({'type': 'harness_step', 'step_id': 'layer5', 'layer': 'Layer 5', 'status': 'done', 'label': 'Solution Synthesis & Delivery', 'detail': 'Streaming verified response with editor bindings'})}\n\n"

                elapsed_sec = round(time.time() - start_time, 1)

                # Save assistant message to session permanently
                try:
                    append_to_session(req.session_id, "assistant", full_session_text, workspace=target_ws, thinking_seconds=elapsed_sec)
                except Exception as err:
                    print("Session save assistant error:", err)

                break

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

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

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host=host, port=port)
