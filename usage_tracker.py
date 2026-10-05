"""
usage_tracker.py - Application Launch & Usage History Tracker for AppIndex

Tracks application launch frequencies and last-opened timestamps by analyzing
systemd user journal scopes (app-*.scope / app-*@*.service) and Steam manifests.
Persists data incrementally in a local SQLite database for instant retrieval.
"""

import json
import contextlib
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("appindex.usage")

# Database path in dynamic XDG user data directory
DATA_DIR = Path.home() / ".local" / "share" / "appindex"
DB_PATH = DATA_DIR / "usage.db"

_LOCK = threading.Lock()
_IN_MEMORY_USAGE: Dict[str, Dict[str, Any]] = {}
_LAST_SYNC_TIME: float = 0.0


@contextlib.contextmanager
def get_db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize usage tracking tables if not already present."""
    with _LOCK:
        try:
            with get_db_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_usage (
                        app_key TEXT PRIMARY KEY,
                        launch_count INTEGER DEFAULT 0,
                        last_used_timestamp REAL DEFAULT 0.0,
                        last_used_date TEXT DEFAULT ''
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sync_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                    """
                )
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to initialize usage database: {e}")


def load_cached_usage() -> Dict[str, Dict[str, Any]]:
    """Load all cached usage statistics from SQLite into memory."""
    global _IN_MEMORY_USAGE
    with _LOCK:
        if _IN_MEMORY_USAGE:
            return _IN_MEMORY_USAGE
        data = {}
        try:
            if DB_PATH.exists():
                with get_db_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT app_key, launch_count, last_used_timestamp, last_used_date FROM app_usage;")
                    for row in cur.fetchall():
                        data[row[0].lower()] = {
                            "app_key": row[0],
                            "launch_count": int(row[1]),
                            "last_used_timestamp": float(row[2]),
                            "last_used_date": row[3],
                        }
        except Exception as e:
            logger.warning(f"Error loading usage cache: {e}")
        _IN_MEMORY_USAGE = data
        return _IN_MEMORY_USAGE


def normalize_unit_name(unit_raw: str) -> str:
    """Normalize systemd unit names to extract the canonical application identifier."""
    # Examples:
    # app-flatpak-org.remmina.Remmina-1570793699.scope -> org.remmina.Remmina
    # app-md.obsidian.Obsidian@2e99c2c416e048218e02c5f4fb066e31.service -> md.obsidian.Obsidian
    # app-smplayer@eeaec95afea7404ea3a35a3be1ce4908.service -> smplayer
    # app-org.kde.dolphin@3c4e1627e73749ee83d792c9d7f41d6d.service -> org.kde.dolphin
    # app-ca._0ldsk00l.Nestopia-2996944286.scope -> ca._0ldsk00l.Nestopia
    clean = unit_raw.replace("\\x2d", "-")
    clean = re.sub(r"^app-(?:flatpak-)?", "", clean)
    clean = re.sub(r"@[a-f0-9]+\.service$", "", clean)
    clean = re.sub(r"-\d+\.scope$", "", clean)
    clean = re.sub(r"\.service$", "", clean)
    return clean.strip()


parse_unit_name = normalize_unit_name


def sync_journal_history(force_full: bool = False):
    """
    Parse systemd user journal entries for app launches.
    Supports incremental syncing via saved journal cursors.
    """
    global _IN_MEMORY_USAGE, _LAST_SYNC_TIME
    if not shutil.which("journalctl"):
        return

    init_db()

    last_cursor = None
    if not force_full:
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT value FROM sync_meta WHERE key = 'journal_cursor';")
                row = cur.fetchone()
                if row:
                    last_cursor = row[0]
        except Exception:
            pass

    cmd = ["journalctl", "--user", "--grep=Started app-", "-o", "json"]
    if last_cursor and not force_full:
        cmd.extend(["--after-cursor", last_cursor])
    else:
        # Initial cold ingestion: look back up to 90 days for snappy first-run indexing
        cmd.extend(["--since=90 days ago"])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 and not proc.stdout:
            return
        lines = proc.stdout.splitlines()
        if not lines:
            _LAST_SYNC_TIME = time.time()
            return

        new_launches: Dict[str, Dict[str, Any]] = {}
        newest_cursor = None

        for line in lines:
            try:
                entry = json.loads(line)
                cursor = entry.get("__CURSOR")
                if cursor:
                    newest_cursor = cursor

                unit = entry.get("USER_UNIT") or entry.get("_SYSTEMD_USER_UNIT") or ""
                if not unit:
                    continue

                clean_name = parse_unit_name(unit)
                if not clean_name:
                    continue

                ts = int(entry.get("__REALTIME_TIMESTAMP", 0)) / 1000000.0
                if ts <= 0:
                    continue

                clean_key = clean_name.lower()
                if clean_key not in new_launches:
                    new_launches[clean_key] = {
                        "name": clean_name,
                        "count": 0,
                        "last_used": 0.0,
                    }
                new_launches[clean_key]["count"] += 1
                if ts > new_launches[clean_key]["last_used"]:
                    new_launches[clean_key]["last_used"] = ts

            except Exception:
                continue

        # Sync Steam games LastPlayed
        steam_launches = scan_steam_last_played()
        for k, v in steam_launches.items():
            if k not in new_launches or v["last_used"] > new_launches[k]["last_used"]:
                new_launches[k] = v

        # Commit updates into database and memory
        with _LOCK:
            with get_db_connection() as conn:
                cur = conn.cursor()
                for key, data in new_launches.items():
                    cur.execute(
                        """
                        INSERT INTO app_usage (app_key, launch_count, last_used_timestamp, last_used_date)
                        VALUES (?, ?, ?, datetime(?, 'unixepoch', 'localtime'))
                        ON CONFLICT(app_key) DO UPDATE SET
                            launch_count = app_usage.launch_count + excluded.launch_count,
                            last_used_timestamp = MAX(app_usage.last_used_timestamp, excluded.last_used_timestamp),
                            last_used_date = CASE
                                WHEN excluded.last_used_timestamp > app_usage.last_used_timestamp
                                THEN excluded.last_used_date
                                ELSE app_usage.last_used_date
                            END;
                        """,
                        (data["name"], data["count"], data["last_used"], int(data["last_used"])),
                    )

                if newest_cursor:
                    cur.execute(
                        "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('journal_cursor', ?);",
                        (newest_cursor,),
                    )
                cur.execute(
                    "INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_sync', ?);",
                    (str(time.time()),),
                )
                conn.commit()

            # Refresh in-memory cache
            _IN_MEMORY_USAGE.clear()
            _LAST_SYNC_TIME = time.time()

        load_cached_usage()

    except Exception as e:
        logger.warning(f"Error during journal launch sync: {e}")


def scan_steam_last_played() -> Dict[str, Dict[str, Any]]:
    """Scan Steam library manifests for LastPlayed timestamps."""
    results = {}
    steam_roots = [
        Path.home() / ".local" / "share" / "Steam",
        Path.home() / ".steam" / "steam",
        Path.home() / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]

    for root in steam_roots:
        if not root.is_dir():
            continue
        try:
            for acf in root.glob("**/appmanifest_*.acf"):
                try:
                    content = acf.read_text(encoding="utf-8", errors="ignore")
                    appid_m = re.search(r'"appid"\s+"(\d+)"', content)
                    name_m = re.search(r'"name"\s+"([^"]+)"', content)
                    last_played_m = re.search(r'"LastPlayed"\s+"(\d+)"', content)

                    if appid_m and last_played_m:
                        appid = appid_m.group(1)
                        last_played = int(last_played_m.group(1))
                        if last_played > 0:
                            key = f"steam_{appid}".lower()
                            results[key] = {
                                "name": f"steam_{appid}",
                                "count": 1,
                                "last_used": float(last_played),
                            }
                            if name_m:
                                results[name_m.group(1).lower()] = {
                                    "name": name_m.group(1),
                                    "count": 1,
                                    "last_used": float(last_played),
                                }
                except Exception:
                    continue
        except Exception:
            continue

    return results


def start_background_usage_sync():
    """Trigger background sync thread to keep web requests non-blocking."""
    t = threading.Thread(target=sync_journal_history, kwargs={"force_full": False}, daemon=True)
    t.start()


def get_app_usage_metrics(
    package_name: str = "",
    desktop_filename: str = "",
    exec_cmd: str = "",
    steam_id: str = "",
) -> Tuple[float, str, int]:
    """
    Lookup launch count and last used timestamp for an application.
    Returns: (last_used_timestamp, last_used_date, launch_count)
    """
    cache = load_cached_usage()
    if not cache:
        return 0.0, "", 0

    candidates = []
    if steam_id:
        candidates.append(f"steam_{steam_id}".lower())

    pkg_clean = (package_name or "").lower().strip()
    if pkg_clean:
        candidates.append(pkg_clean)

    fname_clean = (desktop_filename or "").replace(".desktop", "").lower().strip()
    if fname_clean:
        candidates.append(fname_clean)
        if "." in fname_clean:
            candidates.append(fname_clean.split(".")[-1])

    if exec_cmd:
        parts = exec_cmd.split()
        if parts:
            exe_clean = os.path.basename(parts[0]).lower().strip()
            if exe_clean:
                candidates.append(exe_clean)

    for cand in candidates:
        if cand in cache:
            entry = cache[cand]
            return (
                entry["last_used_timestamp"],
                entry["last_used_date"],
                entry["launch_count"],
            )

    return 0.0, "", 0
