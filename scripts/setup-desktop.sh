#!/usr/bin/env bash
# ==============================================================================
# Setup Desktop Launcher for Linux
# Generates ~/.local/share/applications/vexp-code-ide.desktop with user's actual path
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/.local/share/applications"
DESKTOP_DIR="$HOME/Desktop"

mkdir -p "$APP_DIR"

cat <<EOF > "$APP_DIR/vexp-code-ide.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=VexP Code IDE
GenericName=AI Code IDE
Comment=Autonomous AI Coding Harness and IDE
Exec=$DIR/scripts/launch-ide.sh
Icon=$DIR/assets/icon.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=VexP Code IDE
StartupNotify=true
Keywords=ide;ai;code;claude;harness;
EOF

chmod +x "$APP_DIR/vexp-code-ide.desktop"

# Also place on Desktop if ~/Desktop exists
if [ -d "$DESKTOP_DIR" ]; then
  cp "$APP_DIR/vexp-code-ide.desktop" "$DESKTOP_DIR/"
  chmod +x "$DESKTOP_DIR/vexp-code-ide.desktop"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

echo "✅ Desktop launcher installed successfully!"
echo "You can now launch 'VexP Code IDE' from your application menu or desktop."
