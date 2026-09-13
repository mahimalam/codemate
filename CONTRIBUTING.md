# Contributing to VexP Code

## Local setup

Use Python 3.10+ and Node.js 22.12+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm install
npm start
```

## Before opening a pull request

```bash
npm run check
```

Keep changes focused. Add or update tests when behavior changes, preserve the workspace and command-access boundaries, and do not commit provider keys, user history, session data, or generated build output.

For user-facing changes, explain the visible behavior and include a screenshot when it helps reviewers assess the result.
