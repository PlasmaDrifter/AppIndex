"""
AppIndex - FastAPI Server
Serves the web dashboard, REST API, icon resolver, and desktop file inspector.
"""

import csv
import io
import json
import mimetypes
import os
import re
import threading
import time
from typing import Optional
import urllib.request
from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import xdg.IconTheme
try:
    from PIL import Image
except ImportError:
    Image = None

from scanner import scan_all_applications

APP_VERSION = "0.2.5"
GITHUB_REPO = "PlasmaDrifter/AppIndex"

app = FastAPI(title="AppIndex", version=APP_VERSION)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# In-memory cache
_CACHED_DATA = None


def parse_version_tuple(ver_str: str):
    clean = re.sub(r"^[^\d]*", "", str(ver_str).strip())
    parts = []
    for part in clean.split("."):
        digits = re.match(r"^\d+", part)
        if digits:
            parts.append(int(digits.group(0)))
        else:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer_version(latest: str, current: str) -> bool:
    try:
        return parse_version_tuple(latest) > parse_version_tuple(current)
    except Exception:
        return False


UPDATE_CACHE = {
    "last_checked": 0,
    "latest_version": "",
    "release_url": "",
    "has_update": False,
    "lock": threading.Lock(),
}


def check_github_update(force: bool = False, enabled: bool = True):
    if not enabled:
        return {
            "has_update": False,
            "latest_version": APP_VERSION,
            "release_url": f"https://github.com/{GITHUB_REPO}/releases",
            "current_version": APP_VERSION,
            "check_enabled": False,
        }

    now = time.time()
    # Cache for 1 hour (3600 seconds) unless forced
    with UPDATE_CACHE["lock"]:
        if not force and (now - UPDATE_CACHE["last_checked"] < 3600) and UPDATE_CACHE["last_checked"] > 0:
            return {
                "has_update": UPDATE_CACHE["has_update"],
                "latest_version": UPDATE_CACHE["latest_version"],
                "release_url": UPDATE_CACHE["release_url"],
                "current_version": APP_VERSION,
                "check_enabled": True,
            }

    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"AppIndex-UpdateChecker/{APP_VERSION}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = data.get("tag_name", "").strip()
            html_url = data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"
            has_update = bool(tag and is_newer_version(tag, APP_VERSION))

            with UPDATE_CACHE["lock"]:
                UPDATE_CACHE["last_checked"] = now
                UPDATE_CACHE["latest_version"] = tag or APP_VERSION
                UPDATE_CACHE["release_url"] = html_url
                UPDATE_CACHE["has_update"] = has_update

            return {
                "has_update": has_update,
                "latest_version": tag or APP_VERSION,
                "release_url": html_url,
                "current_version": APP_VERSION,
                "check_enabled": True,
            }
    except Exception:
        with UPDATE_CACHE["lock"]:
            # On error, wait 10 min before next attempt to avoid spamming
            UPDATE_CACHE["last_checked"] = now - 3000
            return {
                "has_update": UPDATE_CACHE["has_update"],
                "latest_version": UPDATE_CACHE["latest_version"] or APP_VERSION,
                "release_url": UPDATE_CACHE["release_url"] or f"https://github.com/{GITHUB_REPO}/releases",
                "current_version": APP_VERSION,
                "check_enabled": True,
            }


def get_cached_or_scan(force_refresh: bool = False):
    global _CACHED_DATA
    if _CACHED_DATA is None or force_refresh:
        _CACHED_DATA = scan_all_applications()
    return _CACHED_DATA


# Known common desktop icon themes to search in order of priority
COMMON_ICON_THEMES = [
    "hicolor",
    "breeze",
    "breeze-dark",
    "Adwaita",
    "Papirus",
    "Papirus-Dark",
    "Yaru",
    "elementary",
    "oxygen",
    "gnome",
]


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

    # Try standard XDG Icon lookup across desktop themes
    for theme in COMMON_ICON_THEMES:
        try:
            found = xdg.IconTheme.getIconPath(icon_name_or_path, theme=theme)
            if found and os.path.isfile(found):
                return found
        except Exception:
            pass

    # Try with common extensions across themes
    for ext in [".png", ".svg", ".xpm"]:
        if not icon_name_or_path.endswith(ext):
            for theme in COMMON_ICON_THEMES:
                try:
                    f = xdg.IconTheme.getIconPath(icon_name_or_path + ext, theme=theme)
                    if f and os.path.isfile(f):
                        return f
                except Exception:
                    pass
        pix = f"/usr/share/pixmaps/{icon_name_or_path}{ext}"
        if os.path.isfile(pix):
            return pix

    # Search in pixmaps without extension
    pix_direct = f"/usr/share/pixmaps/{icon_name_or_path}"
    if os.path.isfile(pix_direct):
        return pix_direct

    return None


def convert_xpm_to_png(xpm_path: str) -> Optional[bytes]:
    """Convert an XPM file to PNG bytes for modern browser compatibility."""
    if Image is None or not os.path.isfile(xpm_path):
        return None

    # First attempt standard Pillow loader
    try:
        im = Image.open(xpm_path)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        pass

    # Fallback: robust custom XPM parser for non-standard whitespace / large palettes
    try:
        with open(xpm_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        strings = re.findall(r"\"([^\"]*)\"", content)
        if not strings:
            return None

        parts = strings[0].split()
        if len(parts) < 4:
            return None

        width, height, ncolors, cpp = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])

        palette = {}
        for i in range(1, ncolors + 1):
            line = strings[i]
            key = line[:cpp]
            rest = line[cpp:]
            m = re.search(r"c\s+([^\s]+)", rest)
            if m:
                val = m.group(1)
                if val.lower() == "none":
                    palette[key] = (0, 0, 0, 0)
                elif val.startswith("#"):
                    hex_str = val[1:]
                    if len(hex_str) == 6:
                        r = int(hex_str[0:2], 16)
                        g = int(hex_str[2:4], 16)
                        b = int(hex_str[4:6], 16)
                        palette[key] = (r, g, b, 255)
                    elif len(hex_str) == 12:
                        r = int(hex_str[0:2], 16)
                        g = int(hex_str[4:6], 16)
                        b = int(hex_str[8:10], 16)
                        palette[key] = (r, g, b, 255)
                else:
                    palette[key] = (0, 0, 0, 255)

        pixel_lines = strings[ncolors + 1 : ncolors + 1 + height]
        img = Image.new("RGBA", (width, height))
        pixels = img.load()

        for y, line in enumerate(pixel_lines):
            for x in range(width):
                k = line[x * cpp : (x + 1) * cpp]
                pixels[x, y] = palette.get(k, (0, 0, 0, 0))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
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
        # Convert legacy .xpm to browser-compatible .png on the fly
        if resolved.endswith(".xpm"):
            png_bytes = convert_xpm_to_png(resolved)
            if png_bytes:
                return Response(content=png_bytes, media_type="image/png")

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


DEFAULT_EXPORT_FIELDS = [
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


@app.get("/api/export")
async def export_apps(
    format: str = Query("json"),
    fields: Optional[str] = Query(None, description="Comma-separated list of field names to export"),
    sources: Optional[str] = Query(None, description="Comma-separated list of source types to export"),
    visibility: Optional[str] = Query("all", description="Filter by visibility: 'all', 'in_menu', or 'hidden'"),
):
    """Export application inventory as JSON or CSV with optional field, source, and visibility filtering."""
    data = get_cached_or_scan(force_refresh=False)
    apps = list(data.get("applications", []))

    # Filter by source types if specified
    if sources and sources.strip() and sources.strip().lower() != "all":
        wanted_sources = {s.strip().lower() for s in sources.split(",") if s.strip()}
        apps = [
            a for a in apps
            if a.get("source_type", "").lower() in wanted_sources
            or a.get("source_label", "").lower() in wanted_sources
        ]

    # Filter by menu visibility if specified
    if visibility:
        vis = visibility.strip().lower()
        if vis in ("in_menu", "menu", "visible"):
            apps = [a for a in apps if a.get("in_menu") is True]
        elif vis in ("hidden", "no_display"):
            apps = [a for a in apps if a.get("in_menu") is False]

    # Sort applications by source label then by application name
    sorted_apps = sorted(
        apps,
        key=lambda a: (str(a.get("source_label", "")).lower(), str(a.get("name", "")).lower()),
    )

    # Determine export field list
    if fields and fields.strip():
        selected_fields = [f.strip() for f in fields.split(",") if f.strip()]
    else:
        selected_fields = DEFAULT_EXPORT_FIELDS

    if format.lower() == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=selected_fields, extrasaction="ignore")
        writer.writeheader()
        for app_item in sorted_apps:
            writer.writerow(app_item)
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=installed_apps.csv"},
        )

    # JSON export
    if fields and fields.strip():
        export_apps_list = [
            {k: app_item.get(k) for k in selected_fields}
            for app_item in sorted_apps
        ]
    else:
        export_apps_list = sorted_apps

    export_payload = {
        "generated_at": data.get("generated_at"),
        "total_exported": len(export_apps_list),
        "applications": export_apps_list,
    }

    formatted_json = json.dumps(export_payload, indent=2, ensure_ascii=False)
    return StreamingResponse(
        io.BytesIO(formatted_json.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=installed_apps.json"},
    )


@app.get("/api/check-update")
def check_update_api(force: int = 0):
    """Check for application updates on GitHub."""
    return check_github_update(force=bool(force))


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
