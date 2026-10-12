#!/usr/bin/env bash
# AppIndex Uninstaller
# Stops and removes systemd user service, desktop menu launcher, application icon, and CLI symlink.

set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "Uninstalling AppIndex..."

# 1. Stop and disable systemd user service
if command -v systemctl >/dev/null 2>&1; then
    if systemctl --user is-active --quiet appindex.service 2>/dev/null; then
        echo "Stopping systemd service..."
        systemctl --user stop appindex.service
    fi

    if systemctl --user is-enabled --quiet appindex.service 2>/dev/null; then
        echo "Disabling systemd service..."
        systemctl --user disable appindex.service
    fi
fi

# 2. Stop any standalone background instances
pkill -f "server.py.*8765" 2>/dev/null || true

# 3. Remove installed files
rm -f "${SYSTEMD_USER_DIR}/appindex.service"
rm -f "${DESKTOP_DIR}/appindex.desktop"
rm -f "${ICON_DIR}/appindex.svg"
rm -f "${BIN_DIR}/appindex"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
fi

# 4. Optional purge of cached usage data
USAGE_DB="${XDG_DATA_HOME:-$HOME/.local/share}/appindex/app_usage.db"
if [ "${1:-}" = "--purge" ]; then
    echo "Purging local cached application database at ${USAGE_DB}..."
    rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/appindex"
else
    if [ -f "${USAGE_DB}" ]; then
        echo "Cached usage statistics preserved at ${USAGE_DB} (use './uninstall.sh --purge' to delete)."
    fi
fi

# 5. Refresh desktop and icon databases
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
echo "AppIndex has been uninstalled."
