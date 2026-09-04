#!/usr/bin/env bash
# ==============================================================================
# Claude Code IDE - 1-Click Installer
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=================================================="
echo "  VexP Code IDE - Autonomous AI Coding Harness    "
echo "=================================================="
echo ""

# 1. Check Python version
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is required but not installed."
    echo "Please install Python 3.9+ and try again."
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[+] Detected Python ${PY_VERSION}"

# 2. Setup Virtual Environment
VENV_DIR="${ROOT_DIR}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
    echo "[+] Creating virtual environment in ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
fi

echo "[+] Activating virtual environment..."
source "${VENV_DIR}/bin/activate"

# 3. Install Requirements
echo "[+] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "${ROOT_DIR}/requirements.txt" -q

# 4. Check Ollama (Local AI Engine)
echo ""
echo "[+] Checking Local Ollama AI Engine..."
if command -v ollama &> /dev/null; then
    echo "    [✓] Ollama CLI detected."
    if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
        echo "    [✓] Ollama daemon is active and running on http://127.0.0.1:11434."
    else
        echo "    [!] Ollama daemon is not currently running."
        echo "        You can start it in a separate terminal with: 'ollama serve'"
    fi
else
    echo "    [!] Ollama is not installed on this system."
    echo "        For local offline GPU inference, install Ollama from: https://ollama.com"
    echo "        (You can also use OpenRouter, OpenAI, or Anthropic cloud keys without Ollama)"
fi

echo ""
echo "=================================================="
echo "  Installation Successful!                        "
echo "=================================================="
echo "To launch the IDE, run:"
echo "    ./scripts/start.sh"
echo "Or open http://127.0.0.1:7860 in your browser."
echo "=================================================="
