#!/usr/bin/env bash
# ==============================================================================
# VexP Code IDE - Desktop Launcher Helper
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

# Ensure nvm/node is available if installed
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck source=/dev/null
  . "$NVM_DIR/nvm.sh"
fi

export PATH="$PATH:$HOME/.local/bin:/usr/local/bin"

exec npm start
