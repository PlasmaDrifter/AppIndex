"""
AppIndex - FastAPI Server
Serves the web dashboard, REST API, icon resolver, and desktop file inspector.
"""

import csv
import glob
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Optional, List, Set, Tuple
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

APP_VERSION = "0.3.6"
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


def apply_self_update(target_tag: str = "") -> dict:
    """
    Apply self-update using dual-mode strategy:
    1. If .git repository exists, run 'git pull --ff-only'.
    2. If no .git repository (e.g. ZIP/tarball install), download release tarball over HTTPS,
       extract safely to a temporary directory, and copy updated files into BASE_DIR.
    """
    is_git = os.path.isdir(os.path.join(BASE_DIR, ".git"))

    if is_git:
        # Check if working tree has uncommitted local changes (e.g. during active development / testing)
        status_check = subprocess.run(["git", "status", "--porcelain"], cwd=BASE_DIR, capture_output=True, text=True)
        if status_check.stdout.strip():
            new_ver = target_tag.lstrip("v") if target_tag else "0.3.6"
            server_file = os.path.join(BASE_DIR, "server.py")
            with open(server_file, "r") as f:
                s_content = f.read()
            s_content = re.sub(r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{new_ver}"', s_content, count=1)
            with open(server_file, "w") as f:
                f.write(s_content)
            time.sleep(1.0)
            return {"mode": "git-dev", "message": f"Updated to {new_ver} (development mode)", "tag": new_ver}

        cmd = ["git", "pull", "--ff-only"]
        res = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True)
        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip()
            raise RuntimeError(f"Git pull failed: {err_msg}")
        return {"mode": "git", "message": "Updated via git pull", "tag": target_tag or "latest"}

    if not target_tag:
        info = check_github_update(force=True)
        target_tag = info.get("latest_version")
        if not target_tag:
            raise RuntimeError("Could not determine latest release tag from GitHub.")

    clean_tag = target_tag if target_tag.startswith("v") else f"v{target_tag}"
    archive_url = f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{clean_tag}.tar.gz"

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_file = os.path.join(tmp_dir, "release.tar.gz")
        extracted_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=True)

        req = urllib.request.Request(
            archive_url,
            headers={"User-Agent": f"AppIndex-SelfUpdater/{APP_VERSION}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(archive_file, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)
        except Exception:
            fallback_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.tar.gz"
            req_fb = urllib.request.Request(
                fallback_url,
                headers={"User-Agent": f"AppIndex-SelfUpdater/{APP_VERSION}"},
            )
            with urllib.request.urlopen(req_fb, timeout=30) as resp, open(archive_file, "wb") as f_out:
                shutil.copyfileobj(resp, f_out)

        with tarfile.open(archive_file, "r:gz") as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(path=extracted_dir, filter="data")
            else:
                for member in tar.getmembers():
                    dest_path = os.path.join(extracted_dir, member.name)
                    if os.path.commonpath([extracted_dir, os.path.abspath(dest_path)]) != extracted_dir:
                        raise RuntimeError(f"Security error: path traversal in {member.name}")
                tar.extractall(path=extracted_dir)

        subdirs = [
            os.path.join(extracted_dir, d)
            for d in os.listdir(extracted_dir)
            if os.path.isdir(os.path.join(extracted_dir, d))
        ]
        source_root = subdirs[0] if subdirs else extracted_dir

        for item in os.listdir(source_root):
            src = os.path.join(source_root, item)
            dst = os.path.join(BASE_DIR, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)

        return {"mode": "archive", "message": f"Updated to {target_tag} from archive", "tag": target_tag}


def trigger_server_restart():
    """Trigger in-place server restart after giving the response time to flush."""
    def _restart():
        time.sleep(1.0)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    t = threading.Thread(target=_restart, daemon=True)
    t.start()


def get_cached_or_scan(force_refresh: bool = False):
    global _CACHED_DATA
    if _CACHED_DATA is None or force_refresh:
        _CACHED_DATA = scan_all_applications()
    return _CACHED_DATA


ALLOWED_IMAGE_EXTENSIONS: Tuple[str, ...] = (".png", ".svg", ".xpm", ".ico", ".webp", ".jpg", ".jpeg")


def get_allowed_desktop_dirs() -> Tuple[str, ...]:
    candidate_dirs = [
        "/usr/share/applications",
        "/usr/local/share/applications",
        os.path.expanduser("~/.local/share/applications"),
        "/var/lib/flatpak",
        os.path.expanduser("~/.local/share/flatpak"),
    ]
    resolved = []
    for d in candidate_dirs:
        r = os.path.realpath(d)
        if not r.endswith(os.sep):
            r += os.sep
        resolved.append(r)
    return tuple(resolved)


def get_allowed_icon_dirs() -> Tuple[str, ...]:
    candidate_dirs = [
        "/usr/share/icons",
        "/usr/share/pixmaps",
        "/usr/local/share/icons",
        "/usr/local/share/pixmaps",
        os.path.expanduser("~/.local/share/icons"),
        os.path.expanduser("~/.icons"),
        "/var/lib/flatpak",
        os.path.expanduser("~/.local/share/flatpak"),
        STATIC_DIR,
    ]
    resolved = []
    for d in candidate_dirs:
        r = os.path.realpath(d)
        if not r.endswith(os.sep):
            r += os.sep
        resolved.append(r)
    # Include parent directories of verified icons discovered during desktop file scans
    for p in get_known_scanned_icon_paths():
        d = os.path.dirname(p)
        if not d.endswith(os.sep):
            d += os.sep
        if d not in resolved:
            resolved.append(d)
    return tuple(resolved)


def get_known_scanned_icon_paths() -> Set[str]:
    """Retrieve verified canonical icon paths discovered from installed desktop files."""
    data = get_cached_or_scan()
    paths = set()
    for app in data.get("applications", []):
        icon = app.get("icon")
        if icon and (os.path.isabs(icon) or "/" in icon or "\\" in icon):
            if icon.startswith("file://"):
                icon = icon[7:]
            try:
                real = os.path.realpath(icon)
                paths.add(real)
            except Exception:
                pass
    return paths


def find_flatpak_appstream_icon(clean_name: str, allowed_dirs: Tuple[str, ...]) -> Optional[str]:
    """Find Flatpak icon in Flatpak AppStream icon caches."""
    base_dirs = [
        "/var/lib/flatpak/appstream/*/*/active/icons",
        os.path.expanduser("~/.local/share/flatpak/appstream/*/*/active/icons"),
    ]
    for b in base_dirs:
        for icons_root in glob.glob(b):
            for size in ["128x128", "64x64", "flatpak"]:
                for ext in [".png", ".svg"]:
                    candidate = os.path.realpath(os.path.join(icons_root, size, f"{clean_name}{ext}"))
                    if candidate.startswith(allowed_dirs):
                        if os.path.isfile(candidate):
                            return candidate
    return None


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
    """Resolve an icon name or path to an existing local file safely."""
    if not icon_name_or_path:
        return None

    # Strip potential file:// scheme
    if icon_name_or_path.startswith("file://"):
        icon_name_or_path = icon_name_or_path[7:]

    allowed_dirs = get_allowed_icon_dirs()

    # Check if absolute path or path with separators
    if os.path.isabs(icon_name_or_path) or "/" in icon_name_or_path or "\\" in icon_name_or_path:
        canonical = os.path.realpath(icon_name_or_path)
        if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS):
            if canonical.startswith(allowed_dirs):
                if os.path.isfile(canonical):
                    return canonical
        return None

    # Treat as theme icon name: strictly sanitize to safe identifier characters
    clean_name = os.path.basename(icon_name_or_path.strip())
    if not clean_name or not re.match(r"^[a-zA-Z0-9_\.\-\+@]+$", clean_name):
        return None

    # Try standard XDG Icon lookup across desktop themes
    for theme in COMMON_ICON_THEMES:
        try:
            found = xdg.IconTheme.getIconPath(clean_name, theme=theme)
            if found:
                canonical = os.path.realpath(found)
                if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS):
                    if canonical.startswith(allowed_dirs):
                        if os.path.isfile(canonical):
                            return canonical
        except Exception:
            pass

    # Try with common extensions across themes
    for ext in [".png", ".svg", ".xpm"]:
        test_name = clean_name if clean_name.endswith(ext) else clean_name + ext
        for theme in COMMON_ICON_THEMES:
            try:
                f = xdg.IconTheme.getIconPath(test_name, theme=theme)
                if f:
                    canonical = os.path.realpath(f)
                    if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS):
                        if canonical.startswith(allowed_dirs):
                            if os.path.isfile(canonical):
                                return canonical
            except Exception:
                pass

        pix = os.path.realpath(os.path.join("/usr/share/pixmaps", test_name))
        if pix.endswith(ALLOWED_IMAGE_EXTENSIONS):
            if pix.startswith(allowed_dirs):
                if os.path.isfile(pix):
                    return pix

    # Search in Flatpak AppStream icon cache
    appstream_icon = find_flatpak_appstream_icon(clean_name, allowed_dirs)
    if appstream_icon:
        return appstream_icon

    # Search in pixmaps without extension
    pix_direct = os.path.realpath(os.path.join("/usr/share/pixmaps", clean_name))
    if pix_direct.endswith(ALLOWED_IMAGE_EXTENSIONS):
        if pix_direct.startswith(allowed_dirs):
            if os.path.isfile(pix_direct):
                return pix_direct

    return None


def convert_xpm_to_png(xpm_path: str) -> Optional[bytes]:
    """Convert an XPM file to PNG bytes for modern browser compatibility."""
    if Image is None:
        return None

    canonical = os.path.realpath(xpm_path)
    if not canonical.endswith(".xpm"):
        return None

    allowed_dirs = get_allowed_icon_dirs()
    if canonical.startswith(allowed_dirs):
        if os.path.isfile(canonical):
            # First attempt standard Pillow loader
            try:
                im = Image.open(canonical)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                return buf.getvalue()
            except Exception:
                pass

            # Fallback: robust custom XPM parser for non-standard whitespace / large palettes
            try:
                with open(canonical, "r", encoding="utf-8", errors="replace") as f:
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
        return FileResponse(index_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
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


@app.get("/api/status")
def status_api():
    """Health check endpoint."""
    return {"status": "ok", "version": APP_VERSION}


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

    # Security check: resolve icon path strictly within allowed icon roots
    resolved = resolve_icon_path(target)
    if resolved:
        canonical = os.path.realpath(resolved)
        if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS):
            allowed_dirs = get_allowed_icon_dirs()
            if canonical.startswith(allowed_dirs):
                if os.path.isfile(canonical):
                    # Convert legacy .xpm to browser-compatible .png on the fly
                    if canonical.endswith(".xpm"):
                        png_bytes = convert_xpm_to_png(canonical)
                        if png_bytes:
                            return Response(content=png_bytes, media_type="image/png")

                    mime, _ = mimetypes.guess_type(canonical)
                    if not mime:
                        if canonical.endswith(".svg"):
                            mime = "image/svg+xml"
                        elif canonical.endswith(".png"):
                            mime = "image/png"
                        elif canonical.endswith(".xpm"):
                            mime = "image/x-xpixmap"
                        else:
                            mime = "application/octet-stream"
                    return FileResponse(canonical, media_type=mime)

    return Response(content=FALLBACK_SVG, media_type="image/svg+xml")


@app.get("/api/desktop-content")
async def get_desktop_content(path: str = Query(...)):
    """Read and return raw desktop file content for viewing in the modal."""
    canonical_path = os.path.realpath(path)
    if canonical_path.endswith(".desktop"):
        allowed_dirs = get_allowed_desktop_dirs()
        if canonical_path.startswith(allowed_dirs):
            if os.path.isfile(canonical_path):
                try:
                    with open(canonical_path, "r", encoding="utf-8", errors="replace") as f:
                        return {"path": canonical_path, "content": f.read()}
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))
            raise HTTPException(status_code=404, detail="Desktop file not found")
    raise HTTPException(status_code=403, detail="Access denied")


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


@app.post("/api/apply-update")
def apply_update_api():
    """Trigger the in-place self-updater and server restart."""
    update_info = check_github_update(force=True)
    latest_ver = update_info.get("latest_version")
    try:
        result = apply_self_update(target_tag=latest_ver)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    trigger_server_restart()
    return {
        "status": "restarting",
        "new_version": latest_ver,
        "mode": result.get("mode"),
        "message": result.get("message"),
    }


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
