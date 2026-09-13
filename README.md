# CodeMate

CodeMate is a desktop AI coding workspace for Linux and Windows. It combines a Monaco editor, terminal, Git controls, persistent agent sessions, web research, and local or cloud model providers behind a FastAPI service.

The agent is built for supervised software work. Workspace file tools remain confined to the project you open, Linux workspace commands use a network-isolated Bubblewrap sandbox, and each run has clear turn, tool, and time limits with preserved progress.

## What CodeMate includes

- React and TypeScript workbench with Monaco, xterm, Git, Explorer, search, and agent chat
- GitHub account connection for the active repository, with explicit pull and push controls
- Public web search and bounded page reading inside the agent loop
- Structured OpenAI-compatible, Anthropic, and Ollama tool-call adapters
- Iterative agent loop with stable call IDs, cancellation, repeated-action detection, and truthful run states
- SQLite sessions, messages, memories, runs, and event journal with one-time JSON migration
- Atomic writes, revision-aware patches, bounded reads/search/output, and canonical path checks
- Per-launch local API authentication, restrictive CORS and CSP, Electron sandboxing, and navigation guards
- Local Inter and JetBrains Mono fonts plus Lucide SVG icons

## How it works

```text
Electron main process
  -> authenticated loopback FastAPI service
      -> React/TypeScript renderer
      -> workspace and Git APIs
      -> SQLite run/session store
      -> provider adapters
          -> bounded agent runtime
              -> typed, workspace-confined tools
```

The renderer entry document is intentionally small. Application structure lives in typed components under `frontend/src`; generated browser assets live in `frontend/dist`. See [the architecture guide](docs/architecture/README.md) for module ownership and runtime boundaries.

## Requirements

- Python 3.10 or newer
- Node.js 22.12 or newer for the current Electron and build toolchain
- Git
- Bubblewrap on Linux for agent shell commands
- Optional: Ollama for local inference

## Install and run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm start
```

`npm start` builds the renderer before Electron starts. Electron owns the backend process and writes its server log in the platform user-data directory.

For browser development, run the backend and Vite separately:

```bash
CODEMATE_SESSION_TOKEN=codemate-dev-session python3 backend/server.py
npm run dev
```

The fixed development token is accepted only by the loopback Vite proxy. Packaged and normal browser launches generate a fresh random token.

## Validation

```bash
npm run check
```

This runs TypeScript checking, the production renderer build, and the Python unit suite.

## Model providers

Provider settings are available from the workbench. The backend supports:

- A default free cloud pool with separate **Fast reply** and **Complex work** routes
- Keyless Kilo and Pollinations models, with AI Horde as a final community fallback
- Ollama
- OpenAI
- Anthropic
- OpenRouter and other configured OpenAI-compatible endpoints
- Custom OpenAI-compatible or Anthropic-compatible endpoints

The selectable free catalog is split by task intent. Fast reply starts with low-latency models, while Complex work starts with larger reasoning and long-context models. Each route has bounded model-level failover. An automatic route resolves one concrete model at the start of a run and keeps it pinned; explicit model selection also stays pinned.

For agentic work, automatic routes only select providers that can return structured tool calls. Answer-only fallback providers remain available for normal chat, but they are never presented as capable of editing files or running tools.

API keys stay in the local provider configuration. Settings responses expose only a masked key indicator. Custom endpoints are called directly and preserve the selected provider protocol.

The model picker is searchable by model name, provider, model ID, badge, and capability. After an enabled custom API is saved with a model ID, that model appears under its provider name in both the Fast reply and Complex work pickers.

The `Local` chat control prevents cloud pool fallback. With a workspace open, the model can use coding tools when the request needs them; without a workspace, those tools are unavailable.

Web access has three persistent modes:

- **Web Auto** is the default. The agent receives live search and page-reading tools on every run and decides when current, unfamiliar, or uncertain facts need research. Questions about software availability or installation are researched before the first answer. If a model incorrectly claims that it lacks web, download, or device access, the harness retries with the actual runtime capabilities and live evidence.
- **Web Research** searches before every response and keeps the web tools available for follow-up research.
- **Web Off** disables web tools and automatic research until the user changes the mode.

Command access is user-controlled and persists on the device:

- **Ask commands** requests approval for every terminal command. Once approved, it runs with normal device and network access.
- **Workspace access** automatically allows terminal commands inside the existing workspace sandbox.
- **Whole device access** automatically allows terminal commands with the signed-in user's normal filesystem and network access. Direct file tools remain confined to the active workspace.

Streaming follows the latest output until the user scrolls away. Manual scrolling pauses auto-follow and exposes a **Latest** control to resume it.

The desktop application provides a native secondary-click context menu for selected output and editable fields, including Copy, Paste, Cut, Undo, Redo, Select All, and link actions when applicable.

## Agent execution model

A run proceeds through context preparation, model turns, tool execution, change review, and response completion. Auto routes resolve once and pin one concrete provider/model for the remainder of the run. Tool progress is checkpointed after every action, and follow-up requests receive a bounded active-task ledger when they continue the prior objective. The execution loop reserves a tool-free final turn so reaching a tool, turn, or time boundary produces a readable completion report instead of stopping mid-sentence.

Conversation history keeps the most recent turns within a conservative token estimate for the selected tier. The latest user request is authoritative, which prevents older session topics or the active workspace from silently replacing the current objective. The UI receives durable, sequenced SSE events for each stage and keeps provider, budget, and finalization errors visible inside the collapsible run card.

Available agent tools:

- bounded file reads and repository search
- atomic file creation and replacement
- exact revision-aware text patches
- sandboxed terminal commands
- read-only Git status and diff inspection
- public web search and bounded page extraction when Web is enabled

The runtime rejects unstructured prose as executable tool authority. Providers must return native structured tool calls.

## Desktop packages

```bash
npm run dist:linux
npm run dist:win
```

The package scripts build the renderer first. Platform packaging still needs validation on the target OS before distributing installers.

## Project layout

```text
backend/
  agent_runtime.py   bounded orchestration and run events
  providers.py       provider protocol adapters and routing
  server.py          authenticated FastAPI and IDE endpoints
  storage.py         SQLite persistence and migration
  tools.py           typed workspace tools and sandbox runner
electron/
  main.js            backend lifecycle and hardened desktop window
  preload.js         narrow desktop bridge
frontend/
  index.html         renderer mount document
  src/               React components, API client, and design system
docs/architecture/   implemented harness and IDE design records
scripts/             development and installer helpers
tests/                backend contract and boundary tests
```

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + S` | Save active file |
| `Ctrl/Cmd + backtick` | Toggle terminal |
| `Ctrl/Cmd + L` | Open agent panel |
| `Enter` | Send agent prompt |
| `Shift + Enter` | Add a line in the prompt |

## Security boundaries

Workspace-level agent commands fail closed when Bubblewrap is unavailable on Linux. Commands approved under **Ask commands** and commands started under **Whole device access** run with the desktop user's normal OS permissions and network access. The interactive terminal is also a full user shell. Git push and pull remain explicit user actions; push never performs an automatic rebase. The GitHub connector stores its token outside the repository in the user configuration directory with owner-only file permissions, supplies it through a temporary askpass environment, and keeps the remote URL credential-free.

## License

MIT. See [LICENSE](LICENSE).
