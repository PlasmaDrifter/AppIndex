#!/usr/bin/env bash
# AppIndex CLI & Browser Launcher
# Portable launcher script for AppIndex

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${APPINDEX_PORT:-8765}"
URL="http://localhost:${PORT}"
ACTION="${1:-launch}"

is_running() {
    curl -s --connect-timeout 1 "${URL}/api/status" >/dev/null 2>&1
}

wait_for_server() {
    local timeout=15
    local count=0
    while ! is_running; do
        sleep 0.5
        count=$((count + 1))
        if [ "$count" -ge $((timeout * 2)) ]; then
            echo "Error: Timed out waiting for AppIndex server to start." >&2
            return 1
        fi
    done
}

ensure_server() {
    if ! is_running; then
        echo "Starting AppIndex server in background on port ${PORT}..."
        (cd "${SCRIPT_DIR}" && python3 server.py --port "${PORT}" >/dev/null 2>&1 &)
        wait_for_server
    fi
}

open_browser() {
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${URL}" >/dev/null 2>&1 &
    elif command -v gio >/dev/null 2>&1; then
        gio open "${URL}" >/dev/null 2>&1 &
    else
        echo "Open your browser to: ${URL}"
    fi
}

case "${ACTION}" in
    launch)
        ensure_server
        open_browser
        ;;
    start)
        ensure_server
        echo "AppIndex server is running at ${URL}"
        ;;
    open)
        open_browser
        ;;
    status)
        if is_running; then
            echo "AppIndex is running at ${URL}"
            curl -s "${URL}/api/status"
            echo ""
        else
            echo "AppIndex server is not running."
        fi
        ;;
    scan)
        ensure_server
        echo "Triggering application scan..."
        curl -s -X POST "${URL}/api/scan"
        echo ""
        ;;
    *)
        echo "Usage: $0 [launch|start|open|status|scan]"
        echo "  launch  Ensure server is running and open browser (default)"
        echo "  start   Start server in background if not running"
        echo "  open    Open AppIndex in browser without starting server"
        echo "  status  Check server status"
        echo "  scan    Trigger a fresh application scan"
        exit 1
        ;;
esac
