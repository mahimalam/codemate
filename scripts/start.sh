#!/usr/bin/env bash
# ==============================================================================
# VexP Code IDE - Launcher Script
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

echo "=================================================="
echo "  Starting VexP Code IDE...                       "
echo "  URL: http://${HOST}:${PORT}                     "
echo "=================================================="

exec python3 "${ROOT_DIR}/backend/server.py"
