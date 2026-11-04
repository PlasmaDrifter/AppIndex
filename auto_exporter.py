"""
auto_exporter.py - Automated Application Inventory Exporter for AppIndex

Handles scheduled and trigger-based exports of installed application records
to user-configured filesystem destinations with custom filtering and column selection.
"""

import csv
import datetime
import io
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("appindex.auto_exporter")

DATA_DIR = Path.home() / ".local" / "share" / "appindex"
CONFIG_FILE = DATA_DIR / "auto_export.json"

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

DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "frequency": "daily",  # "every_6h", "every_12h", "daily", "every_3d", "weekly", "on_rescan"
    "destination_dir": str(Path.home() / "Documents" / "AppIndex_Exports"),
    "format": "csv",  # "csv" or "json"
    "filename_pattern": "both",  # "timestamped", "latest", or "both"
    "max_retained_entries": 10,  # Keep last N timestamped export files (0 = keep all / no pruning)
    "sources": [
        "repo_rpm,repo_pacman,repo_apt,repo_dpkg",
        "flatpak",
        "appimage",
        "steam",
        "local_tool",
        "unmanaged,web_app",
    ],
    "visibility": "all",  # "all", "in_menu", or "hidden"
    "fields": DEFAULT_EXPORT_FIELDS,
    "last_run_timestamp": 0.0,
    "last_run_date": "",
    "last_run_status": "",
    "last_run_file": "",
    "last_run_count": 0,
    "retained_file_count": 0,
}

FREQUENCY_SECONDS_MAP = {
    "every_6h": 6 * 3600,
    "every_12h": 12 * 3600,
    "daily": 24 * 3600,
    "every_3d": 3 * 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "on_rescan": 0,
}

_CONFIG_LOCK = threading.Lock()
_SCHEDULER_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()


def resolve_destination_dir(dir_str: str) -> Path:
    """Safely expand and resolve destination path without hardcoded user paths."""
    expanded = os.path.expanduser(dir_str.strip())
    path = Path(expanded).resolve()
    return path


def get_retained_export_files(target_dir: Path) -> List[Path]:
    """Find and return list of timestamped export files sorted newest first."""
    if not target_dir.exists():
        return []

    timestamped_files: List[Path] = []
    try:
        for p in target_dir.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if not (name.startswith("installed_apps_") and (name.endswith(".csv") or name.endswith(".json"))):
                continue
            if name in ("installed_apps_latest.csv", "installed_apps_latest.json"):
                continue
            timestamped_files.append(p)
    except Exception as e:
        logger.warning(f"Error reading export destination files: {e}")
        return []

    # Sort newest first by mtime
    timestamped_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return timestamped_files


def prune_old_exports(target_dir: Path, max_retained: int) -> Tuple[int, int]:
    """
    Prune older timestamped export files if total exceeds max_retained.
    max_retained <= 0 means unlimited (disabled).
    Returns (pruned_count, remaining_count).
    """
    files = get_retained_export_files(target_dir)
    if max_retained <= 0 or len(files) <= max_retained:
        return 0, len(files)

    to_delete = files[max_retained:]
    pruned_count = 0
    for f in to_delete:
        try:
            f.unlink()
            pruned_count += 1
            logger.info(f"Pruned older export file: {f.name}")
        except Exception as e:
            logger.warning(f"Failed to prune old export file {f}: {e}")

    remaining = get_retained_export_files(target_dir)
    return pruned_count, len(remaining)


def get_auto_export_config() -> Dict[str, Any]:
    """Load auto-export configuration from disk or return defaults."""
    with _CONFIG_LOCK:
        if not CONFIG_FILE.exists():
            config = dict(DEFAULT_CONFIG)
        else:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                config = dict(DEFAULT_CONFIG)
                config.update(data)
            except Exception as e:
                logger.warning(f"Failed to read auto-export config: {e}")
                config = dict(DEFAULT_CONFIG)

        try:
            target_dir = resolve_destination_dir(config.get("destination_dir", ""))
            config["retained_file_count"] = len(get_retained_export_files(target_dir))
        except Exception:
            config["retained_file_count"] = config.get("retained_file_count", 0)

        return config


def save_auto_export_config(new_config: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and persist auto-export configuration to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _CONFIG_LOCK:
        current = dict(DEFAULT_CONFIG)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    current.update(json.load(f))
            except Exception:
                pass

        for k, v in new_config.items():
            if k in DEFAULT_CONFIG:
                current[k] = v

        if "max_retained_entries" in new_config:
            try:
                current["max_retained_entries"] = max(0, int(new_config["max_retained_entries"]))
            except (ValueError, TypeError):
                current["max_retained_entries"] = 10

        dest = current.get("destination_dir", "").strip()
        if not dest:
            current["destination_dir"] = str(Path.home() / "Documents" / "AppIndex_Exports")

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save auto-export config: {e}")
            raise

        try:
            target_dir = resolve_destination_dir(current.get("destination_dir", ""))
            current["retained_file_count"] = len(get_retained_export_files(target_dir))
        except Exception:
            current["retained_file_count"] = 0

        return current


def run_export_job(apps_data: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, str, str, int]:
    """
    Execute export according to current configuration.
    Returns: (success: bool, message: str, generated_file_path: str, exported_count: int)
    """
    config = get_auto_export_config()
    dest_path_str = config.get("destination_dir", "")
    target_dir = resolve_destination_dir(dest_path_str)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to create destination directory: {e}")
        msg = "Failed to create destination directory"
        _record_run_result(config, success=False, message=msg, file_path="", count=0)
        return False, msg, "", 0

    if apps_data is None:
        try:
            from scanner import scan_all_applications
            scan_result = scan_all_applications()
            apps_data = scan_result.get("applications", [])
        except Exception as e:
            logger.error(f"Failed to fetch application inventory: {e}")
            msg = "Failed to fetch application inventory"
            _record_run_result(config, success=False, message=msg, file_path="", count=0)
            return False, msg, "", 0

    # 1. Filter sources
    sources_cfg = config.get("sources") or []
    wanted_sources = set()
    for s in sources_cfg:
        for sub in s.split(","):
            sub_clean = sub.strip().lower()
            if sub_clean:
                wanted_sources.add(sub_clean)

    filtered_apps = []
    for a in apps_data:
        src_type = str(a.get("source_type", "")).lower()
        src_lbl = str(a.get("source_label", "")).lower()
        if wanted_sources and (src_type not in wanted_sources and src_lbl not in wanted_sources):
            continue

        vis = config.get("visibility", "all")
        if vis in ("in_menu", "menu", "visible") and not a.get("in_menu"):
            continue
        elif vis in ("hidden", "no_display") and a.get("in_menu"):
            continue

        filtered_apps.append(a)

    # Sort applications by source label then name
    sorted_apps = sorted(
        filtered_apps,
        key=lambda a: (str(a.get("source_label", "")).lower(), str(a.get("name", "")).lower()),
    )

    fields = config.get("fields") or DEFAULT_EXPORT_FIELDS
    if not fields:
        fields = DEFAULT_EXPORT_FIELDS

    now = datetime.datetime.now()
    timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
    export_format = str(config.get("format", "csv")).lower()
    pattern = str(config.get("filename_pattern", "both")).lower()

    base_name = "installed_apps"
    created_files = []

    try:
        if export_format == "csv":
            content = _generate_csv(sorted_apps, fields)
            ext = ".csv"
        else:
            content = _generate_json(sorted_apps, fields, now)
            ext = ".json"

        # Determine target file names
        if pattern in ("timestamped", "both"):
            ts_file = target_dir / f"{base_name}_{timestamp_str}{ext}"
            with open(ts_file, "w", encoding="utf-8") as f:
                f.write(content)
            created_files.append(str(ts_file))

        if pattern in ("latest", "both"):
            latest_file = target_dir / f"{base_name}_latest{ext}"
            with open(latest_file, "w", encoding="utf-8") as f:
                f.write(content)
            created_files.append(str(latest_file))

        main_file = created_files[0] if created_files else ""
        max_retained = int(config.get("max_retained_entries", 10))
        pruned_count, remaining_count = prune_old_exports(target_dir, max_retained)

        msg = f"Successfully exported {len(sorted_apps)} applications to {len(created_files)} file(s)"
        if pruned_count > 0:
            msg += f" (pruned {pruned_count} older export{'s' if pruned_count != 1 else ''})"

        _record_run_result(
            config,
            success=True,
            message=msg,
            file_path=main_file,
            count=len(sorted_apps),
            retained_file_count=remaining_count,
        )
        return True, msg, main_file, len(sorted_apps)

    except Exception as e:
        logger.error(f"Export generation failed: {e}")
        msg = "Export generation failed"
        _record_run_result(config, success=False, message=msg, file_path="", count=0)
        return False, msg, "", 0


def _generate_csv(apps: List[Dict[str, Any]], fields: List[str]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for item in apps:
        row = {}
        for f in fields:
            val = item.get(f)
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val)
            elif val is None:
                val = ""
            row[f] = val
        writer.writerow(row)
    return output.getvalue()


def _generate_json(apps: List[Dict[str, Any]], fields: List[str], generated_time: datetime.datetime) -> str:
    items = []
    for item in apps:
        record = {f: item.get(f) for f in fields}
        items.append(record)
    payload = {
        "generated_at": generated_time.isoformat(),
        "total_exported": len(items),
        "applications": items,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _record_run_result(
    config: Dict[str, Any],
    success: bool,
    message: str,
    file_path: str,
    count: int,
    retained_file_count: Optional[int] = None,
):
    now_epoch = time.time()
    now_str = datetime.datetime.fromtimestamp(now_epoch).strftime("%Y-%m-%d %H:%M:%S")

    config["last_run_timestamp"] = now_epoch
    config["last_run_date"] = now_str
    config["last_run_status"] = "success" if success else f"error: {message}"
    config["last_run_file"] = file_path
    config["last_run_count"] = count
    if retained_file_count is not None:
        config["retained_file_count"] = retained_file_count

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to record auto-export result: {e}")


def on_inventory_rescan(apps_data: Optional[List[Dict[str, Any]]] = None):
    """Callback triggered whenever an inventory scan or refresh occurs."""
    config = get_auto_export_config()
    if not config.get("enabled"):
        return
    if config.get("frequency") == "on_rescan":
        threading.Thread(target=run_export_job, kwargs={"apps_data": apps_data}, daemon=True).start()


def _scheduler_loop():
    logger.info("Auto-export scheduler thread started.")
    while not _STOP_EVENT.is_set():
        try:
            config = get_auto_export_config()
            if config.get("enabled"):
                freq = config.get("frequency", "daily")
                interval_secs = FREQUENCY_SECONDS_MAP.get(freq, 24 * 3600)

                if interval_secs > 0:
                    last_run = float(config.get("last_run_timestamp", 0.0) or 0.0)
                    now = time.time()
                    if (now - last_run) >= interval_secs:
                        logger.info("Auto-export scheduled interval elapsed. Triggering export job.")
                        run_export_job()
        except Exception as e:
            logger.error(f"Error in auto-export scheduler: {e}")

        # Sleep in 10-second increments for responsive shutdown
        for _ in range(6):
            if _STOP_EVENT.is_set():
                break
            time.sleep(10.0)


def start_auto_export_scheduler():
    """Start the background auto-export scheduler daemon."""
    global _SCHEDULER_THREAD
    if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
        return
    _STOP_EVENT.clear()
    _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, name="AppIndexAutoExportScheduler", daemon=True)
    _SCHEDULER_THREAD.start()


def stop_auto_export_scheduler():
    """Signal background scheduler to stop."""
    _STOP_EVENT.set()
