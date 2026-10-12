#!/usr/bin/env bash
# AppIndex Installer
# Installs application icon, desktop launcher, CLI symlink, and systemd user service.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "Installing AppIndex..."

# 1. Check Python dependencies
check_dependencies() {
    local missing=()
    for mod in fastapi uvicorn xdg PIL; do
        if ! python3 -c "import ${mod}" >/dev/null 2>&1; then
            missing+=("${mod}")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo "Notice: Some Python dependencies are not installed: ${missing[*]}"
        echo "You can install them via your system package manager or pip:"
        echo "  Fedora / Nobara: sudo dnf install python3-fastapi python3-uvicorn python3-pyxdg python3-pillow"
        echo "  Ubuntu / Debian: sudo apt install python3-fastapi python3-uvicorn python3-xdg python3-pil"
        echo "  Arch Linux:      sudo pacman -S python-fastapi python-uvicorn python-pyxdg python-pillow"
        echo "  Pip:             pip install fastapi uvicorn pyxdg pillow"
        echo ""
    fi
}

check_dependencies

# 2. Create required target directories
mkdir -p "${BIN_DIR}" "${DESKTOP_DIR}" "${ICON_DIR}" "${SYSTEMD_USER_DIR}"

# 3. Ensure repository scripts are executable
chmod +x "${REPO_DIR}/appindex.sh"

# 4. Install scalable application icon
if [ -f "${REPO_DIR}/static/icon.svg" ]; then
    cp -f "${REPO_DIR}/static/icon.svg" "${ICON_DIR}/appindex.svg"
    echo "Installed application icon to ${ICON_DIR}/appindex.svg"
fi

# 5. Create CLI symlink in ~/.local/bin
ln -sf "${REPO_DIR}/appindex.sh" "${BIN_DIR}/appindex"
echo "Created CLI command link: ${BIN_DIR}/appindex"

# 6. Generate and install desktop menu entry
cat > "${DESKTOP_DIR}/appindex.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AppIndex
GenericName=Linux Application & Package Inventory
Comment=Inspect installed applications, package managers, and uninstall commands
Exec=${REPO_DIR}/appindex.sh launch
Icon=appindex
Terminal=false
Categories=Utility;System;
Keywords=package;flatpak;rpm;dnf;appimage;uninstall;applications;inventory;steam;apt;pacman;
StartupNotify=true
EOF
chmod 644 "${DESKTOP_DIR}/appindex.desktop"
echo "Installed desktop menu launcher: ${DESKTOP_DIR}/appindex.desktop"

# 7. Generate and install systemd user service
cat > "${SYSTEMD_USER_DIR}/appindex.service" <<EOF
[Unit]
Description=AppIndex Web Service
After=network.target

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/python3 ${REPO_DIR}/server.py --port 8765
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
chmod 644 "${SYSTEMD_USER_DIR}/appindex.service"
echo "Installed systemd user service: ${SYSTEMD_USER_DIR}/appindex.service"

# 8. Reload systemd and start service
if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    systemctl --user enable --now appindex.service
    echo "AppIndex background service enabled and started."
fi

# 9. Refresh desktop and icon caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${DESKTOP_DIR}" >/dev/null 2>&1 || true
fi

if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi

echo ""
echo "Installation complete."
echo "Access AppIndex at: http://localhost:8765"
echo "Run via terminal:   appindex"
echo "Menu entry:         AppIndex in Applications -> System / Utilities"
