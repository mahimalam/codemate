#!/usr/bin/env bash
# ==============================================================================
# Setup Desktop Launcher for Linux
# Generates ~/.local/share/applications/codemate.desktop with the user's actual path
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="$HOME/Desktop"

mkdir -p "$APP_DIR"

cat <<EOF > "$APP_DIR/codemate.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=CodeMate
GenericName=AI Code IDE
Comment=AI coding workspace with controlled agent tools
Exec=$DIR/scripts/launch-ide.sh
Icon=$DIR/assets/icon.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=CodeMate
StartupNotify=true
Keywords=ide;ai;code;agent;harness;
EOF

chmod +x "$APP_DIR/codemate.desktop"

# Also place on Desktop if ~/Desktop exists
if [ -d "$DESKTOP_DIR" ]; then
  cp "$APP_DIR/codemate.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/codemate.desktop"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

echo "Desktop launcher installed successfully."
echo "You can now launch 'CodeMate' from your application menu or desktop."
