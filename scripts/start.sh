#!/usr/bin/env bash
# ==============================================================================
# CodeMate - Launcher Script
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Activate virtual environment if present
if [ -d "${ROOT_DIR}/.venv" ]; then
    source "${ROOT_DIR}/.venv/bin/activate"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-7860}"

if [ ! -f "${ROOT_DIR}/frontend/dist/index.html" ]; then
    if ! command -v npm &> /dev/null; then
        echo "[ERROR] The renderer is not built and npm is unavailable."
        echo "Install Node.js, then run: npm install && npm run build"
        exit 1
    fi
    echo "[+] Building the IDE renderer..."
    (cd "${ROOT_DIR}" && npm run build)
fi

echo "=================================================="
echo "  Starting CodeMate...                            "
echo "  URL: http://${HOST}:${PORT}                     "
echo "=================================================="

exec python3 "${ROOT_DIR}/backend/server.py"
