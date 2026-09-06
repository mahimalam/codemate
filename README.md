# AI Code IDE - Autonomous AI Coding Harness

> A fully open-source, self-hosted AI coding IDE you can run entirely on your own machine or server. Supports **local GPU inference** (Ollama), **cloud APIs** (OpenRouter, Anthropic, OpenAI), and **any OpenAI-compatible proxy**. Built for developers who want total control.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-green)](https://fastapi.tiangolo.com)

---

## ✨ Highlights

| Feature | Description |
|---|---|
| **5-Layer Agentic Loop** | Mirrors Claude Code & Devin - context clustering → intent planning → autonomous tool dispatch → self-correction → diff assembly |
| **Multi-Provider AI** | Ollama (local GPU), OpenRouter (100+ models), Anthropic, OpenAI, or **any custom OpenAI-compatible proxy** |
| **Monaco Editor** | VS Code-grade editor with syntax highlighting, code folding, tabbed editing |
| **Integrated Terminal** | Real shell execution from the browser |
| **Source Control** | Full Git commit / push / pull UI with GitHub PAT integration |
| **AI Memory** | Persistent per-user memory the agent reads on every request |
| **Fully Customizable** | Change name, branding, agent persona, default models - all in one config file |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────┐
│                  Browser (index.html)            │
│  Monaco Editor │ File Tree │ Terminal │ AI Chat  │
└───────────────────────┬─────────────────────────┘
                        │ HTTP / SSE
┌───────────────────────▼─────────────────────────┐
│             FastAPI Backend (server.py)          │
│  Provider Router │ Git API │ FS API │ Agent Loop │
└──────┬──────────────────────────┬───────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────┐
│ Ollama      │          │ Cloud Providers  │
│ (Local GPU) │          │ OpenRouter/OAI/  │
└─────────────┘          │ Anthropic/Proxy  │
                         └─────────────────┘
```

### 5-Layer Agent Loop
```
User Prompt
  └▶ Layer 1: Context Clustering & File Sensor Fusion
       └▶ Layer 2: Intent Classification & Deliberation
            └▶ Layer 3: Tool Dispatch (read/write/diff/bash)
                 └▶ Layer 4: Observation & Self-Correction  ──┐
                      └▶ Layer 5: Diff Assembly & UI Render   │
                                                    (loop ◀───┘)
```

---

## 🚀 Quickstart

### 1. Clone
```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

### 2. Install

**Linux / macOS:**
```bash
bash scripts/install.sh
npm install
```

**Windows:**
```cmd
scripts\install.bat
```
*(Or simply double-click `scripts\install.bat` in File Explorer).*

### 3. Launch

#### Option A: Native Desktop IDE (Recommended)
```bash
npm start
```
*On Linux, you can also launch via desktop application menu. On Windows, double-click `scripts\start.bat`.* Electron automatically manages the backend server lifecycle.

#### Option B: Web Browser Mode
- **Linux / macOS:** `bash scripts/start.sh`
- **Windows:** `python backend\server.py`
- Open **http://127.0.0.1:7860** in your browser.

---

## 📦 Building Standalone Desktop Packages

To package the IDE as standalone desktop installers:

**Linux (AppImage & DEB):**
```bash
npm run dist:linux
```
Generates portable `dist/*.AppImage` and `dist/*.deb`.

**Windows (Installer EXE & Portable):**
```bash
npm run dist:win
```
Generates `dist/*-Setup.exe` (NSIS installer) and `dist/*.exe` (portable).

---

## ⚙️ Configuration & Customization

### Branding (name, agent persona, terminal prompt)

Edit **`config/branding.json`** - this is the single source of truth for all user-facing names:

```json
{
  "app": {
    "name": "My AI IDE",
    "tagline": "Autonomous AI Coding Harness"
  },
  "agent": {
    "name": "My Agent",
    "welcome_message": "My AI IDE Ready",
    "input_placeholder": "Ask My Agent about your project...",
    "terminal_prompt": "dev $"
  },
  "defaults": {
    "system_prompt_intro": "You are an expert AI software engineer.",
    "default_ai_provider": "ollama",
    "default_ai_model": "qwen2.5:14b"
  }
}
```

### AI Providers (via the Settings UI)

Click the **⚙ Gear** icon in the IDE → **AI Providers & Models**:

| Provider | What to enter |
|---|---|
| **Ollama (local)** | Endpoint URL (default: `http://127.0.0.1:11434`) - no API key needed |
| **OpenRouter** | Your `sk-or-...` API key |
| **Anthropic** | Your `sk-ant-...` API key |
| **OpenAI** | Your `sk-...` API key |
| **Custom Proxy** | Any OpenAI-compatible base URL + key |

All keys are stored **locally** in `~/.claude_code_ide/provider_config.json` - never committed to git.

### Environment Variables (optional)

Copy `.env.example` to `.env` and set overrides:
```bash
cp .env.example .env
```

---

## 📁 File Structure

```
.
├── backend/
│   ├── server.py          # FastAPI server, agent loop, all API endpoints
│   └── tools.py           # Agent workspace tools (read, write, diff, bash)
├── config/
│   ├── branding.json      # ← EDIT THIS to customize name, agent, defaults
│   └── default_config.json # Default AI provider templates
├── frontend/
│   └── index.html         # Monaco editor, full IDE UI, SSE chat stream
├── scripts/
│   ├── install.sh         # One-click setup (venv, deps, Ollama check)
│   └── start.sh           # One-click launcher
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
└── .gitignore             # Excludes all secrets and runtime data
```

---

## 🔒 What Is NOT Committed to Git

The following are in `.gitignore` and **never** pushed:

- `~/.claude_code_ide/` - all your API keys, chat history, memories, workspaces
- `.env` - any local environment secrets
- `*.log` - server logs
- `graphify-out/` - knowledge graph cache

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl + Enter` | Send prompt to AI |
| `Ctrl + Shift + E` | Explorer |
| `Ctrl + Shift + F` | Search |
| `Ctrl + Shift + G` | Source Control |
| `` Ctrl + ` `` | Toggle Terminal |
| `Ctrl + B` | Toggle Sidebar |
| `Ctrl + ,` | Settings |

---

## 🤝 Contributing

PRs are welcome. Please:
1. Fork and create a feature branch
2. Keep personal data out of commits
3. Test with at least one provider (Ollama is free and local)

---

## 📄 License

MIT License - free for personal and commercial use. See [LICENSE](LICENSE).
