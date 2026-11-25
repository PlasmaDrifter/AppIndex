"""
AppIndex - FastAPI Server
Serves the web dashboard, REST API, icon resolver, and desktop file inspector.
"""

from contextlib import asynccontextmanager
import configparser
import csv
import glob
import io
import json
import logging
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
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.request
from fastapi import Body, FastAPI, Query, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

from scanner import scan_all_applications
from usage_tracker import start_background_usage_sync
from auto_exporter import (
    get_auto_export_config,
    save_auto_export_config,
    run_export_job,
    start_auto_export_scheduler,
    stop_auto_export_scheduler,
    on_inventory_rescan,
)

logger = logging.getLogger("appindex.server")

APP_VERSION = "0.5.9"
GITHUB_REPO = "PlasmaDrifter/AppIndex"


def _warm_startup_caches() -> None:
    """Scan applications and pre-resolve/thumbnail their icons in the background."""
    data = get_cached_or_scan(force_refresh=False)
    prewarm_application_icons(data.get("applications", []))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manage background usage sync, auto-export scheduler, and non-blocking cache warm-up."""
    start_background_usage_sync()
    start_auto_export_scheduler()
    threading.Thread(target=_warm_startup_caches, daemon=True).start()
    try:
        yield
    finally:
        stop_auto_export_scheduler()


app = FastAPI(title="AppIndex", version=APP_VERSION, lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@app.middleware("http")
async def add_static_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Thread-safe in-memory caches
_CACHE_LOCK = threading.RLock()
_THEME_BUILD_LOCKS: Dict[str, threading.Lock] = {}
_CACHED_DATA: Optional[Dict[str, Any]] = None
_CACHED_RECENT_HOURS: int = 48
_CACHED_DNF_HOURS: int = 48
_CACHED_ALLOWED_ICON_DIRS: Optional[Tuple[str, ...]] = None
_CACHED_ALLOWED_DESKTOP_DIRS: Optional[Tuple[str, ...]] = None
_RESOLVED_ICON_CACHE: Dict[str, Optional[str]] = {}
_XPM_CACHE: Dict[str, bytes] = {}
_THUMB_CACHE: Dict[str, bytes] = {}
_ICON_BYTES_CACHE: Dict[str, Tuple[bytes, str]] = {}
_THEME_INDEX_CACHE: Dict[str, Dict[str, str]] = {}
_FLAT_ICON_INDEX: Optional[Dict[str, str]] = None
_APPSTREAM_ICON_INDEX: Optional[Dict[str, str]] = None
_ORDERED_THEMES_CACHE: Optional[List[str]] = None
_THUMB_THRESHOLD_BYTES = 32 * 1024
_ICON_MEM_CACHE_MAX_BYTES = 256 * 1024
_THUMB_MAX_SIZE = (96, 96)
_RASTER_THUMB_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".ico")


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
        # Check tracked modifications only so untracked local notes/files don't skip git pull
        status_check = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if status_check.stdout.strip():
            new_ver = target_tag.lstrip("v") if target_tag else APP_VERSION
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
            # If fast-forward fails (e.g. upstream branch history was squashed or rebased)
            # and local working tree is clean, fetch and reset to origin/<branch>
            fetch_res = subprocess.run(["git", "fetch", "--prune", "--tags", "origin"], cwd=BASE_DIR, capture_output=True, text=True)
            if fetch_res.returncode == 0:
                branch_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=BASE_DIR, capture_output=True, text=True)
                branch = branch_res.stdout.strip() or "main"
                reset_res = subprocess.run(["git", "reset", "--hard", f"origin/{branch}"], cwd=BASE_DIR, capture_output=True, text=True)
                if reset_res.returncode == 0:
                    return {"mode": "git-reset", "message": f"Updated via git reset to origin/{branch}", "tag": target_tag or "latest"}
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


def _recompute_recent_flags(data: Dict[str, Any], recent_hours: int) -> Dict[str, Any]:
    """Update is_recent_update and stats in memory when narrowing the recent_hours window."""
    cutoff = time.time() - (max(1, int(recent_hours)) * 3600)
    apps_out: List[Dict[str, Any]] = []
    recent_count = 0
    for app_item in data.get("applications", []):
        inst_time = float(app_item.get("install_time") or 0.0)
        is_recent = bool(inst_time and inst_time >= cutoff)
        if is_recent:
            recent_count += 1
        updated_item = dict(app_item)
        updated_item["is_recent_update"] = is_recent
        if is_recent and not updated_item.get("update_action"):
            updated_item["update_action"] = "Updated"
        elif not is_recent and updated_item.get("update_action") == "Updated":
            updated_item["update_action"] = ""
        apps_out.append(updated_item)

    stats = dict(data.get("stats", {}))
    stats["recently_updated"] = recent_count
    stats["recent_hours"] = max(1, int(recent_hours))
    return {"stats": stats, "applications": apps_out}


def _clear_icon_caches() -> None:
    """Clear icon path, directory, and theme index caches under _CACHE_LOCK."""
    global _CACHED_ALLOWED_ICON_DIRS, _FLAT_ICON_INDEX, _APPSTREAM_ICON_INDEX, _ORDERED_THEMES_CACHE
    _CACHED_ALLOWED_ICON_DIRS = None
    _RESOLVED_ICON_CACHE.clear()
    _XPM_CACHE.clear()
    _THUMB_CACHE.clear()
    _ICON_BYTES_CACHE.clear()
    _THEME_INDEX_CACHE.clear()
    _FLAT_ICON_INDEX = None
    _APPSTREAM_ICON_INDEX = None
    _ORDERED_THEMES_CACHE = None


def prewarm_application_icons(apps: List[Dict[str, Any]]) -> None:
    """Pre-resolve application icons, thumbnails, and small icon payloads in memory."""
    seen: Set[str] = set()
    for app_item in apps:
        ic = app_item.get("icon")
        if not ic or ic in seen:
            continue
        seen.add(ic)
        try:
            resolved = resolve_icon_path(ic)
            if resolved:
                _get_cached_icon_payload(resolved)
        except Exception:
            continue


def get_cached_or_scan(force_refresh: bool = False, recent_hours: Optional[int] = None) -> Dict[str, Any]:
    global _CACHED_DATA, _CACHED_RECENT_HOURS, _CACHED_DNF_HOURS
    with _CACHE_LOCK:
        target_hours = _CACHED_RECENT_HOURS if recent_hours is None else max(1, int(recent_hours))
        if _CACHED_DATA is None or force_refresh:
            _CACHED_DATA = scan_all_applications(recent_hours=target_hours)
            _CACHED_RECENT_HOURS = target_hours
            _CACHED_DNF_HOURS = target_hours
            _clear_icon_caches()
            return _CACHED_DATA

        if target_hours != _CACHED_RECENT_HOURS:
            if target_hours <= _CACHED_DNF_HOURS:
                _CACHED_DATA = _recompute_recent_flags(_CACHED_DATA, target_hours)
                _CACHED_RECENT_HOURS = target_hours
            else:
                _CACHED_DATA = scan_all_applications(recent_hours=target_hours)
                _CACHED_RECENT_HOURS = target_hours
                _CACHED_DNF_HOURS = target_hours
        return _CACHED_DATA


ALLOWED_IMAGE_EXTENSIONS: Tuple[str, ...] = (".png", ".svg", ".xpm", ".ico", ".webp", ".jpg", ".jpeg")

ICON_ROOT_DIRS: Tuple[str, ...] = (
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    os.path.expanduser("~/.local/share/flatpak/exports/share/icons"),
    "/var/lib/flatpak/exports/share/icons",
    "/usr/local/share/icons",
    "/usr/share/icons",
)

PIXMAP_ROOT_DIRS: Tuple[str, ...] = (
    os.path.expanduser("~/.local/share/pixmaps"),
    "/usr/local/share/pixmaps",
    "/usr/share/pixmaps",
)


def get_allowed_desktop_dirs() -> Tuple[str, ...]:
    global _CACHED_ALLOWED_DESKTOP_DIRS
    with _CACHE_LOCK:
        if _CACHED_ALLOWED_DESKTOP_DIRS is not None:
            return _CACHED_ALLOWED_DESKTOP_DIRS
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
        _CACHED_ALLOWED_DESKTOP_DIRS = tuple(resolved)
        return _CACHED_ALLOWED_DESKTOP_DIRS


def get_allowed_icon_dirs() -> Tuple[str, ...]:
    global _CACHED_ALLOWED_ICON_DIRS
    with _CACHE_LOCK:
        if _CACHED_ALLOWED_ICON_DIRS is not None:
            return _CACHED_ALLOWED_ICON_DIRS
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
        _CACHED_ALLOWED_ICON_DIRS = tuple(resolved)
        return _CACHED_ALLOWED_ICON_DIRS


def get_known_scanned_icon_paths() -> Set[str]:
    """Retrieve verified canonical icon paths discovered from installed desktop files."""
    data = get_cached_or_scan(force_refresh=False, recent_hours=None)
    paths = set()
    for app_item in data.get("applications", []):
        icon = app_item.get("icon")
        if icon and (os.path.isabs(icon) or "/" in icon or "\\" in icon):
            if icon.startswith("file://"):
                icon = icon[7:]
            try:
                real = os.path.realpath(icon)
                paths.add(real)
            except Exception:
                pass
    return paths


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


def _detect_active_icon_theme() -> Optional[str]:
    """Detect user's configured icon theme from KDE or GTK config."""
    env_theme = os.environ.get("XDG_ICON_THEME", "").strip()
    if env_theme:
        return env_theme

    kdeglobals = os.path.expanduser("~/.config/kdeglobals")
    if os.path.isfile(kdeglobals):
        try:
            cp = configparser.ConfigParser(interpolation=None)
            cp.read(kdeglobals, encoding="utf-8")
            theme = cp.get("Icons", "Theme", fallback="").strip()
            if theme:
                return theme
        except Exception:
            pass

    for gtk_ini in (
        os.path.expanduser("~/.config/gtk-4.0/settings.ini"),
        os.path.expanduser("~/.config/gtk-3.0/settings.ini"),
    ):
        if os.path.isfile(gtk_ini):
            try:
                cp = configparser.ConfigParser(interpolation=None)
                cp.read(gtk_ini, encoding="utf-8")
                theme = cp.get("Settings", "gtk-icon-theme-name", fallback="").strip().strip("\"'")
                if theme:
                    return theme
            except Exception:
                pass
    return None


def _read_theme_inherits(theme_name: str) -> List[str]:
    """Read Inherits list from a theme's index.theme if present."""
    for root in ICON_ROOT_DIRS:
        index_path = os.path.join(root, theme_name, "index.theme")
        if os.path.isfile(index_path):
            try:
                with open(index_path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("Inherits="):
                            raw = line.split("=", 1)[1].strip()
                            return [t.strip() for t in raw.split(",") if t.strip()]
            except Exception:
                pass
    return []


def _get_ordered_icon_themes() -> List[str]:
    """Return ordered icon themes starting with the active desktop theme and its inherits."""
    global _ORDERED_THEMES_CACHE
    with _CACHE_LOCK:
        if _ORDERED_THEMES_CACHE is not None:
            return _ORDERED_THEMES_CACHE

        ordered: List[str] = []
        seen: Set[str] = set()

        active = _detect_active_icon_theme()
        if active:
            # Expand active theme followed by its inherited themes in order
            pending = [active]
            while pending:
                th = pending.pop(0)
                if not th or th in seen:
                    continue
                seen.add(th)
                if any(os.path.isdir(os.path.join(r, th)) for r in ICON_ROOT_DIRS):
                    ordered.append(th)
                    for idx_p, parent in enumerate(_read_theme_inherits(th)):
                        if parent not in seen:
                            pending.insert(idx_p, parent)

        for th in ["hicolor"] + COMMON_ICON_THEMES:
            if th not in seen and any(os.path.isdir(os.path.join(r, th)) for r in ICON_ROOT_DIRS):
                seen.add(th)
                ordered.append(th)

        _ORDERED_THEMES_CACHE = ordered
        return _ORDERED_THEMES_CACHE


def _get_flat_icon_index() -> Dict[str, str]:
    """Index top-level icon files in user icon folders and system pixmap folders."""
    global _FLAT_ICON_INDEX
    with _CACHE_LOCK:
        if _FLAT_ICON_INDEX is not None:
            return _FLAT_ICON_INDEX

        index: Dict[str, str] = {}
        allowed_dirs = get_allowed_icon_dirs()
        flat_dirs = [
            os.path.expanduser("~/.local/share/icons"),
            os.path.expanduser("~/.icons"),
        ] + list(PIXMAP_ROOT_DIRS)

        for d in flat_dirs:
            if not os.path.isdir(d):
                continue
            try:
                for entry in os.scandir(d):
                    if not entry.is_file():
                        continue
                    stem, ext = os.path.splitext(entry.name)
                    ext_l = ext.lower()
                    if ext_l not in ALLOWED_IMAGE_EXTENSIONS:
                        continue
                    canonical = os.path.realpath(entry.path)
                    if not canonical.startswith(allowed_dirs):
                        continue
                    if entry.name not in index:
                        index[entry.name] = canonical
                    if stem not in index:
                        index[stem] = canonical
            except Exception:
                pass

        _FLAT_ICON_INDEX = index
        return _FLAT_ICON_INDEX


def _get_appstream_icon_index() -> Dict[str, str]:
    """Index Flatpak AppStream icon caches once per scan."""
    global _APPSTREAM_ICON_INDEX
    with _CACHE_LOCK:
        if _APPSTREAM_ICON_INDEX is not None:
            return _APPSTREAM_ICON_INDEX

        index: Dict[str, str] = {}
        allowed_dirs = get_allowed_icon_dirs()
        base_dirs = [
            "/var/lib/flatpak/appstream/*/*/active/icons",
            os.path.expanduser("~/.local/share/flatpak/appstream/*/*/active/icons"),
        ]
        for b in base_dirs:
            for icons_root in glob.glob(b):
                for size in ("128x128", "64x64", "flatpak", "scalable"):
                    size_dir = os.path.join(icons_root, size)
                    if not os.path.isdir(size_dir):
                        continue
                    try:
                        for entry in os.scandir(size_dir):
                            if not entry.is_file():
                                continue
                            stem, ext = os.path.splitext(entry.name)
                            if ext.lower() not in ALLOWED_IMAGE_EXTENSIONS:
                                continue
                            canonical = os.path.realpath(entry.path)
                            if not canonical.startswith(allowed_dirs):
                                continue
                            if stem not in index:
                                index[stem] = canonical
                            if entry.name not in index:
                                index[entry.name] = canonical
                    except Exception:
                        pass

        _APPSTREAM_ICON_INDEX = index
        return _APPSTREAM_ICON_INDEX


_SKIP_ICON_SUBDIRS = frozenset({"animations", "applets", "cursors", "emotes", "stock"})
_FALLBACK_SKIP_ICON_SUBDIRS = _SKIP_ICON_SUBDIRS | frozenset({"actions", "emblems", "mimetypes"})
_PRIMARY_ICON_THEMES_LIMIT = 4


def _get_theme_lock(cache_key: str) -> threading.Lock:
    """Return a single-flight lock for building one icon theme index."""
    with _CACHE_LOCK:
        lock = _THEME_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _THEME_BUILD_LOCKS[cache_key] = lock
        return lock


def _get_theme_index(theme_name: str, skip_actions: bool = False) -> Dict[str, str]:
    """Build or retrieve a compact stem -> best icon path index for a single XDG theme."""
    cache_key = f"{theme_name}:{'noact' if skip_actions else 'full'}"
    with _CACHE_LOCK:
        if cache_key in _THEME_INDEX_CACHE:
            return _THEME_INDEX_CACHE[cache_key]

    with _get_theme_lock(cache_key):
        with _CACHE_LOCK:
            if cache_key in _THEME_INDEX_CACHE:
                return _THEME_INDEX_CACHE[cache_key]

        scored: Dict[str, Tuple[int, str]] = {}
        home_prefix = os.path.expanduser("~")
        skip_dirs = _FALLBACK_SKIP_ICON_SUBDIRS if skip_actions else _SKIP_ICON_SUBDIRS

        for root in ICON_ROOT_DIRS:
            tdir = os.path.join(root, theme_name)
            if not os.path.isdir(tdir):
                continue
            is_user_override = root.startswith(home_prefix)
            for dirpath, dirnames, filenames in os.walk(tdir):
                dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs]
                if not filenames:
                    continue

                rel = dirpath[len(tdir) :].lower()
                base_score = 1000 if is_user_override else 0
                if "apps" in rel:
                    base_score += 200
                elif "categories" in rel or "preferences" in rel or "devices" in rel or "places" in rel:
                    base_score += 100

                if "48x48" in rel or "/48" in rel or "64x64" in rel or "/64" in rel:
                    base_score += 40
                elif "scalable" in rel or "128x128" in rel or "/128" in rel:
                    base_score += 35
                elif "32x32" in rel or "/32" in rel or "256x256" in rel or "/256" in rel:
                    base_score += 25
                elif "24x24" in rel or "22x22" in rel or "16x16" in rel:
                    base_score += 5

                for fn in filenames:
                    stem, ext = os.path.splitext(fn)
                    ext_l = ext.lower()
                    if ext_l not in ALLOWED_IMAGE_EXTENSIONS:
                        continue
                    score = base_score
                    if ext_l == ".svg":
                        score += 6
                    elif ext_l == ".png":
                        score += 5
                    elif ext_l == ".webp":
                        score += 4
                    elif ext_l in (".jpg", ".jpeg"):
                        score += 3
                    elif ext_l == ".ico":
                        score += 2
                    else:
                        score += 1

                    prev = scored.get(stem)
                    if prev is None or score > prev[0]:
                        scored[stem] = (score, os.path.join(dirpath, fn))

        result = {k: v[1] for k, v in scored.items()}
        with _CACHE_LOCK:
            _THEME_INDEX_CACHE[cache_key] = result
        return result


def find_flatpak_appstream_icon(clean_name: str, allowed_dirs: Tuple[str, ...]) -> Optional[str]:
    """Find Flatpak icon in Flatpak AppStream icon caches."""
    appstream = _get_appstream_icon_index()
    hit = appstream.get(clean_name)
    if hit and hit.startswith(allowed_dirs) and os.path.isfile(hit):
        return hit
    return None


def resolve_icon_path(icon_name_or_path: str) -> Optional[str]:
    """Resolve an icon name or path to an existing local file safely."""
    if not icon_name_or_path:
        return None

    with _CACHE_LOCK:
        if icon_name_or_path in _RESOLVED_ICON_CACHE:
            return _RESOLVED_ICON_CACHE[icon_name_or_path]

    resolved = _resolve_icon_path_uncached(icon_name_or_path)
    with _CACHE_LOCK:
        _RESOLVED_ICON_CACHE[icon_name_or_path] = resolved
    return resolved


def _resolve_icon_path_uncached(icon_name_or_path: str) -> Optional[str]:
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

    stem, ext = os.path.splitext(clean_name)
    lookup_keys = [clean_name, stem] if (ext and ext.lower() in ALLOWED_IMAGE_EXTENSIONS) else [clean_name]

    # 1. Check flat user icon folders & pixmaps
    flat_idx = _get_flat_icon_index()
    for key in lookup_keys:
        hit = flat_idx.get(key)
        if hit:
            canonical = os.path.realpath(hit)
            if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS) and canonical.startswith(allowed_dirs) and os.path.isfile(canonical):
                return canonical

    themes = _get_ordered_icon_themes()

    # 2. Search primary themes (active theme + inherits + hicolor)
    for idx, theme in enumerate(themes[:_PRIMARY_ICON_THEMES_LIMIT]):
        theme_idx = _get_theme_index(theme, skip_actions=False)
        for key in lookup_keys:
            hit = theme_idx.get(key)
            if hit:
                canonical = os.path.realpath(hit)
                if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS) and canonical.startswith(allowed_dirs) and os.path.isfile(canonical):
                    return canonical

    # 3. Check Flatpak AppStream icon cache before walking slower fallback themes (e.g. Papirus, oxygen)
    for key in lookup_keys:
        appstream_icon = find_flatpak_appstream_icon(key, allowed_dirs)
        if appstream_icon:
            return appstream_icon

    # 4. Search remaining fallback themes
    for theme in themes[_PRIMARY_ICON_THEMES_LIMIT:]:
        theme_idx = _get_theme_index(theme, skip_actions=True)
        for key in lookup_keys:
            hit = theme_idx.get(key)
            if hit:
                canonical = os.path.realpath(hit)
                if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS) and canonical.startswith(allowed_dirs) and os.path.isfile(canonical):
                    return canonical

    return None


def get_raster_thumbnail_bytes(canonical_path: str) -> Optional[bytes]:
    """Downscale oversized raster icons (>32 KB) to 96x96 PNG and cache in memory."""
    if Image is None:
        return None

    with _CACHE_LOCK:
        if canonical_path in _THUMB_CACHE:
            return _THUMB_CACHE[canonical_path]

    try:
        if os.path.getsize(canonical_path) <= _THUMB_THRESHOLD_BYTES:
            return None
        with Image.open(canonical_path) as im:
            if ImageOps is not None:
                im = ImageOps.exif_transpose(im)
            if im.width <= _THUMB_MAX_SIZE[0] and im.height <= _THUMB_MAX_SIZE[1] and canonical_path.endswith(".png"):
                return None
            if im.mode not in ("RGBA", "RGB"):
                im = im.convert("RGBA")
            im.thumbnail(_THUMB_MAX_SIZE, Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            thumb_bytes = buf.getvalue()
        with _CACHE_LOCK:
            _THUMB_CACHE[canonical_path] = thumb_bytes
        return thumb_bytes
    except Exception:
        return None


def convert_xpm_to_png(xpm_path: str) -> Optional[bytes]:
    """Convert an XPM file to PNG bytes for modern browser compatibility."""
    if Image is None:
        return None

    canonical = os.path.realpath(xpm_path)
    if not canonical.endswith(".xpm"):
        return None

    with _CACHE_LOCK:
        if canonical in _XPM_CACHE:
            return _XPM_CACHE[canonical]

    allowed_dirs = get_allowed_icon_dirs()
    if canonical.startswith(allowed_dirs):
        if os.path.isfile(canonical):
            # First attempt standard Pillow loader
            try:
                im = Image.open(canonical)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                png_bytes = buf.getvalue()
                with _CACHE_LOCK:
                    _XPM_CACHE[canonical] = png_bytes
                return png_bytes
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
                png_bytes = buf.getvalue()
                with _CACHE_LOCK:
                    _XPM_CACHE[canonical] = png_bytes
                return png_bytes
            except Exception:
                return None

    return None


ICON_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400"}

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
def get_apps(recent_hours: int = Query(48, ge=1, le=8760)):
    """Return all scanned applications and summary statistics."""
    return get_cached_or_scan(force_refresh=False, recent_hours=recent_hours)


@app.post("/api/refresh")
def refresh_apps(recent_hours: Optional[int] = Query(None, ge=1, le=8760)):
    """Force re-scan of the system applications."""
    start_background_usage_sync()
    result = get_cached_or_scan(force_refresh=True, recent_hours=recent_hours)
    threading.Thread(target=prewarm_application_icons, args=(result.get("applications", []),), daemon=True).start()
    on_inventory_rescan(result.get("applications", []))
    return result


@app.post("/api/scan")
def scan_apps_alias(recent_hours: Optional[int] = Query(None, ge=1, le=8760)):
    """Alias for /api/refresh for CLI compat."""
    start_background_usage_sync()
    result = get_cached_or_scan(force_refresh=True, recent_hours=recent_hours)
    threading.Thread(target=prewarm_application_icons, args=(result.get("applications", []),), daemon=True).start()
    on_inventory_rescan(result.get("applications", []))
    return result


def _guess_icon_mime(canonical: str) -> str:
    mime, _ = mimetypes.guess_type(canonical)
    if mime:
        return mime
    if canonical.endswith(".svg"):
        return "image/svg+xml"
    if canonical.endswith(".png"):
        return "image/png"
    if canonical.endswith(".xpm"):
        return "image/x-xpixmap"
    return "application/octet-stream"


def _get_cached_icon_payload(canonical: str) -> Optional[Tuple[bytes, str]]:
    """Return cached (bytes, mime_type) for thumbnails, XPMs, or small icons <=256 KB."""
    with _CACHE_LOCK:
        if canonical in _ICON_BYTES_CACHE:
            return _ICON_BYTES_CACHE[canonical]

    if not os.path.isfile(canonical):
        return None

    if canonical.endswith(".xpm"):
        png_bytes = convert_xpm_to_png(canonical)
        if png_bytes:
            payload = (png_bytes, "image/png")
            with _CACHE_LOCK:
                _ICON_BYTES_CACHE[canonical] = payload
            return payload

    if canonical.endswith(_RASTER_THUMB_EXTS):
        thumb_bytes = get_raster_thumbnail_bytes(canonical)
        if thumb_bytes:
            payload = (thumb_bytes, "image/png")
            with _CACHE_LOCK:
                _ICON_BYTES_CACHE[canonical] = payload
            return payload

    try:
        if os.path.getsize(canonical) <= _ICON_MEM_CACHE_MAX_BYTES:
            with open(canonical, "rb") as f:
                raw = f.read()
            payload = (raw, _guess_icon_mime(canonical))
            with _CACHE_LOCK:
                _ICON_BYTES_CACHE[canonical] = payload
            return payload
    except OSError:
        pass
    return None


@app.api_route("/api/icon", methods=["GET", "HEAD"])
def get_icon(name: Optional[str] = Query(None), path: Optional[str] = Query(None)):
    """Serve application icon by name or absolute path."""
    target = path or name
    if not target:
        return Response(content=FALLBACK_SVG, media_type="image/svg+xml", headers=ICON_CACHE_HEADERS)

    # Security check: resolve icon path strictly within allowed icon roots
    resolved = resolve_icon_path(target)
    if resolved:
        canonical = os.path.realpath(resolved)
        if canonical.endswith(ALLOWED_IMAGE_EXTENSIONS):
            allowed_dirs = get_allowed_icon_dirs()
            if canonical.startswith(allowed_dirs):
                cached = _get_cached_icon_payload(canonical)
                if cached:
                    raw, mime = cached
                    return Response(content=raw, media_type=mime, headers=ICON_CACHE_HEADERS)
                if os.path.isfile(canonical):
                    return FileResponse(canonical, media_type=_guess_icon_mime(canonical), headers=ICON_CACHE_HEADERS)

    return Response(content=FALLBACK_SVG, media_type="image/svg+xml", headers=ICON_CACHE_HEADERS)


@app.get("/api/desktop-content")
def get_desktop_content(path: str = Query(...)):
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
                    logger.error(f"Failed to read desktop file '{canonical_path}': {e}")
                    raise HTTPException(status_code=500, detail="Failed to read desktop file")
            raise HTTPException(status_code=404, detail="Desktop file not found")
    raise HTTPException(status_code=403, detail="Access denied")


DEFAULT_EXPORT_FIELDS = [
    "name",
    "source_label",
    "package_name",
]


@app.get("/api/export")
def export_apps(
    format: str = Query("json"),
    fields: Optional[str] = Query(None, description="Comma-separated list of field names to export"),
    sources: Optional[str] = Query(None, description="Comma-separated list of source types to export"),
    visibility: Optional[str] = Query("all", description="Filter by visibility: 'all', 'in_menu', or 'hidden'"),
    recent_hours: Optional[int] = Query(None, ge=1, le=8760),
):
    """Export application inventory as JSON or CSV with optional field, source, and visibility filtering."""
    data = get_cached_or_scan(force_refresh=False, recent_hours=recent_hours)
    apps = list(data.get("applications", []))

    # Filter by source types if specified
    if sources and sources.strip() and sources.strip().lower() != "all":
        from auto_exporter import expand_source_filters
        wanted_sources = expand_source_filters([sources])
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
            row = {}
            for field_name in selected_fields:
                val = app_item.get(field_name)
                if isinstance(val, list):
                    val = "; ".join(str(v) for v in val)
                elif val is None:
                    val = ""
                row[field_name] = val
            writer.writerow(row)
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
        logger.error(f"Failed to apply update: {exc}")
        raise HTTPException(status_code=500, detail="Failed to apply update")

    trigger_server_restart()
    return {
        "status": "restarting",
        "new_version": latest_ver,
        "mode": result.get("mode"),
        "message": result.get("message"),
    }


@app.get("/api/auto-export")
def get_auto_export_settings():
    """Return the current auto-export configuration and execution status."""
    return get_auto_export_config()


@app.post("/api/auto-export")
def update_auto_export_settings(body: Any = Body(...)):
    """Update auto-export configuration."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid configuration format")
    try:
        updated = save_auto_export_config(body)
        return {"status": "ok", "config": updated}
    except Exception as e:
        logger.error(f"Failed to update auto-export configuration: {e}")
        raise HTTPException(status_code=500, detail="Failed to update auto-export configuration")


@app.post("/api/auto-export/run-now")
def trigger_auto_export_now():
    """Manually trigger the auto-export job immediately."""
    data = get_cached_or_scan(force_refresh=False, recent_hours=None)
    apps = data.get("applications", [])
    success, message, file_path, count = run_export_job(apps_data=apps)
    return {
        "status": "ok" if success else "error",
        "success": success,
        "message": message,
        "file_path": file_path,
        "count": count,
        "config": get_auto_export_config(),
    }


# Mount static assets
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def start_server(host: str = "127.0.0.1", port: int = 8765):
    """Start the Uvicorn web server."""
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AppIndex Web Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765)")
    args = parser.parse_args()

    start_server(host=args.host, port=args.port)
