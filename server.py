"""
AppIndex - FastAPI Server
Serves the web dashboard, REST API, icon resolver, and desktop file inspector.
"""

import csv
import io
import json
import mimetypes
import os
from typing import Optional
from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import xdg.IconTheme

from scanner import scan_all_applications

app = FastAPI(title="AppIndex", version="0.1.5")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# In-memory cache
_CACHED_DATA = None


def get_cached_or_scan(force_refresh: bool = False):
    global _CACHED_DATA
    if _CACHED_DATA is None or force_refresh:
        _CACHED_DATA = scan_all_applications()
    return _CACHED_DATA


def resolve_icon_path(icon_name_or_path: str) -> Optional[str]:
    """Resolve an icon name or path to an existing local file."""
    if not icon_name_or_path:
        return None

    # Strip potential file:// scheme
    if icon_name_or_path.startswith("file://"):
        icon_name_or_path = icon_name_or_path[7:]

    # If already an existing absolute path
    if os.path.isabs(icon_name_or_path) and os.path.isfile(icon_name_or_path):
        return icon_name_or_path

    # Try standard XDG Icon lookup
    found = xdg.IconTheme.getIconPath(icon_name_or_path)
    if found and os.path.isfile(found):
        return found

    # Try with common extensions
    for ext in [".png", ".svg", ".xpm"]:
        if not icon_name_or_path.endswith(ext):
            f = xdg.IconTheme.getIconPath(icon_name_or_path + ext)
            if f and os.path.isfile(f):
                return f
        pix = f"/usr/share/pixmaps/{icon_name_or_path}{ext}"
        if os.path.isfile(pix):
            return pix

    # Search in pixmaps without extension
    pix_direct = f"/usr/share/pixmaps/{icon_name_or_path}"
    if os.path.isfile(pix_direct):
        return pix_direct

    return None


# Default fallback SVG icon
FALLBACK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="#8892b0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="3" width="18" height="18" rx="3" ry="3"></rect>
  <line x1="9" y1="9" x2="15" y2="15"></line>
  <line x1="15" y1="9" x2="9" y2="15"></line>
</svg>"""


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h1>AppIndex</h1><p>Frontend file missing.</p>")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon():
    favicon_ico = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.isfile(favicon_ico):
        return FileResponse(favicon_ico, media_type="image/x-icon")
    favicon_svg = os.path.join(STATIC_DIR, "icon.svg")
    if os.path.isfile(favicon_svg):
        return FileResponse(favicon_svg, media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/api/apps")
async def get_apps():
    """Return all scanned applications and summary statistics."""
    return get_cached_or_scan(force_refresh=False)


@app.post("/api/refresh")
async def refresh_apps():
    """Force re-scan of the system applications."""
    return get_cached_or_scan(force_refresh=True)


@app.get("/api/icon")
async def get_icon(name: Optional[str] = Query(None), path: Optional[str] = Query(None)):
    """Serve application icon by name or absolute path."""
    target = path or name
    if not target:
        return Response(content=FALLBACK_SVG, media_type="image/svg+xml")

    # Security check: never allow directory traversal or network shares
    resolved = resolve_icon_path(target)
    if resolved and os.path.isfile(resolved):
        mime, _ = mimetypes.guess_type(resolved)
        if not mime:
            if resolved.endswith(".svg"):
                mime = "image/svg+xml"
            elif resolved.endswith(".png"):
                mime = "image/png"
            elif resolved.endswith(".xpm"):
                mime = "image/x-xpixmap"
            else:
                mime = "application/octet-stream"
        return FileResponse(resolved, media_type=mime)

    return Response(content=FALLBACK_SVG, media_type="image/svg+xml")


@app.get("/api/desktop-content")
async def get_desktop_content(path: str = Query(...)):
    """Read and return raw desktop file content for viewing in the modal."""
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Desktop file not found")

    # Safety: ensure path is within standard applications paths
    allowed_dirs = [
        "/usr/share/applications",
        "/usr/local/share/applications",
        os.path.expanduser("~/.local/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
    ]
    if not any(os.path.commonpath([path, d]) == d for d in allowed_dirs if os.path.exists(d)):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/export")
async def export_apps(format: str = Query("json")):
    """Export application inventory as JSON or CSV."""
    data = get_cached_or_scan(force_refresh=False)
    apps = data.get("applications", [])

    if format.lower() == "csv":
        output = io.StringIO()
        fieldnames = [
            "name",
            "source_label",
            "package_name",
            "package_version",
            "installed_size",
            "in_menu",
            "primary_category",
            "exec_command",
            "desktop_path",
            "uninstall_command",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        # Sort applications by source label (e.g. Flatpak, Repo, AppImage) then by application name
        sorted_apps = sorted(apps, key=lambda a: (a.get("source_label", "").lower(), a.get("name", "").lower()))
        for app_item in sorted_apps:
            writer.writerow(app_item)
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=installed_apps.csv"},
        )

    # Format JSON with 2-space indentation for human readability
    formatted_json = json.dumps(data, indent=2, ensure_ascii=False)
    return StreamingResponse(
        io.BytesIO(formatted_json.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=installed_apps.json"},
    )


# Mount static assets
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def start_server(host: str = "127.0.0.1", port: int = 8765):
    """Start the Uvicorn web server."""
    # Pre-warm cache on startup
    get_cached_or_scan()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AppIndex Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    args = parser.parse_args()

    start_server(host=args.host, port=args.port)
