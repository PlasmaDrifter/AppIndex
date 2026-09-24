"""
App Menu Inspector - Application Scanner Module
Discovers all desktop menu entries and installed applications.
Classifies installation source (Repo/RPM, Flatpak, AppImage, Steam, Local scripts, Web Apps)
and extracts package names and uninstall commands.
"""

import os
import re
import shlex
import shutil
import subprocess
from typing import Dict, List, Optional, Any

try:
    import rpm
    HAS_RPM_LIB = True
except ImportError:
    HAS_RPM_LIB = False


# Desktop directories to inspect in order of priority
DESKTOP_DIRS = [
    ("local_user", os.path.expanduser("~/.local/share/applications")),
    ("flatpak_user", os.path.expanduser("~/.local/share/flatpak/exports/share/applications")),
    ("flatpak_system", "/var/lib/flatpak/exports/share/applications"),
    ("system_local", "/usr/local/share/applications"),
    ("system", "/usr/share/applications"),
]

APPLICATIONS_DIR = os.path.expanduser("~/Applications")


def format_bytes(sz_bytes: int) -> str:
    """Format bytes into human readable string."""
    if sz_bytes >= 1024 * 1024 * 1024:
        return f"{sz_bytes / (1024**3):.2f} GB"
    elif sz_bytes >= 1024 * 1024:
        return f"{sz_bytes / (1024**2):.1f} MB"
    elif sz_bytes >= 1024:
        return f"{sz_bytes / 1024:.0f} KB"
    return f"{sz_bytes} B"


def get_flatpak_installed() -> Dict[str, Dict[str, Any]]:
    """Query flatpak CLI for all installed applications with metadata."""
    flatpaks = {}
    try:
        cmd = [
            "flatpak",
            "list",
            "--app",
            "--columns=application,name,version,branch,installation,size",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode == 0:
            for line in res.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    app_id = parts[0].strip()
                    flatpaks[app_id] = {
                        "app_id": app_id,
                        "name": parts[1].strip() if len(parts) > 1 else "",
                        "version": parts[2].strip() if len(parts) > 2 else "",
                        "branch": parts[3].strip() if len(parts) > 3 else "stable",
                        "installation": parts[4].strip() if len(parts) > 4 else "system",
                        "size": parts[5].strip() if len(parts) > 5 else "",
                    }
    except Exception:
        pass
    return flatpaks


def query_rpm_database(file_paths: List[str]) -> Dict[str, Dict[str, str]]:
    """Query RPM database for file paths using native python rpm bindings or rpm CLI."""
    results = {}
    if not file_paths:
        return results

    if HAS_RPM_LIB:
        try:
            ts = rpm.TransactionSet()
            for filepath in file_paths:
                mi = ts.dbMatch("basenames", filepath)
                for hdr in mi:
                    pkg_name = hdr["name"]
                    version = f"{hdr['version']}-{hdr['release']}"
                    arch = hdr["arch"]
                    sz = hdr["size"]
                    summary = hdr["summary"]
                    results[filepath] = {
                        "name": pkg_name,
                        "version": version,
                        "arch": arch,
                        "size": format_bytes(sz) if isinstance(sz, int) else "",
                        "summary": summary,
                    }
                    break
            return results
        except Exception:
            pass

    # Fallback to rpm CLI in batches
    batch_size = 80
    for i in range(0, len(file_paths), batch_size):
        chunk = file_paths[i : i + batch_size]
        for f in chunk:
            try:
                cmd = ["rpm", "-qf", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{SIZE}\t%{SUMMARY}\n", f]
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                line = res.stdout.strip()
                if res.returncode == 0 and "\t" in line:
                    parts = line.split("\t")
                    sz_str = ""
                    try:
                        sz_str = format_bytes(int(parts[3].strip()))
                    except Exception:
                        pass
                    results[f] = {
                        "name": parts[0].strip(),
                        "version": parts[1].strip(),
                        "arch": parts[2].strip() if len(parts) > 2 else "",
                        "size": sz_str,
                        "summary": parts[4].strip() if len(parts) > 4 else "",
                    }
            except Exception:
                pass

    return results


def parse_desktop_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Parse a .desktop file manually to ensure fast and error-tolerant parsing."""
    if not os.path.isfile(filepath):
        return None

    entry: Dict[str, str] = {}
    in_desktop_entry = False

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    if line == "[Desktop Entry]":
                        in_desktop_entry = True
                    else:
                        in_desktop_entry = False
                    continue

                if in_desktop_entry and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if key not in entry:
                        entry[key] = val
    except Exception:
        return None

    if not entry:
        return None

    app_type = entry.get("Type", "Application")
    name = entry.get("Name", os.path.splitext(os.path.basename(filepath))[0])
    generic_name = entry.get("GenericName", "")
    comment = entry.get("Comment", "")
    exec_cmd = entry.get("Exec", "")
    icon = entry.get("Icon", "")
    terminal = entry.get("Terminal", "false").lower() == "true"
    no_display = entry.get("NoDisplay", "false").lower() == "true"
    hidden = entry.get("Hidden", "false").lower() == "true"
    only_show_in = entry.get("OnlyShowIn", "")
    not_show_in = entry.get("NotShowIn", "")
    categories_raw = entry.get("Categories", "")
    mime_type = entry.get("MimeType", "")
    x_flatpak = entry.get("X-Flatpak", "")

    categories = [c.strip() for c in categories_raw.split(";") if c.strip()]

    # Check menu visibility
    in_menu = True
    if app_type != "Application":
        in_menu = False
    if no_display or hidden:
        in_menu = False

    # Check desktop environment restrictions
    if not_show_in:
        blocked_envs = [env.strip().upper() for env in not_show_in.split(";") if env.strip()]
        if "KDE" in blocked_envs or "PLASMA" in blocked_envs:
            in_menu = False

    if only_show_in:
        allowed_envs = [env.strip().upper() for env in only_show_in.split(";") if env.strip()]
        if "KDE" not in allowed_envs and "PLASMA" not in allowed_envs:
            in_menu = False

    primary_category = "Other"
    cat_lower = [c.lower() for c in categories]
    if any(c in cat_lower for c in ["game", "games", "emulator"]):
        primary_category = "Games"
    elif any(c in cat_lower for c in ["audiovideo", "audio", "video", "multimedia", "player"]):
        primary_category = "Audio & Video"
    elif any(c in cat_lower for c in ["development", "ide", "programming", "debugger"]):
        primary_category = "Development"
    elif any(c in cat_lower for c in ["graphics", "rastergraphics", "photography", "viewer"]):
        primary_category = "Graphics"
    elif any(c in cat_lower for c in ["network", "webbrowser", "chat", "email", "telephony"]):
        primary_category = "Internet"
    elif any(c in cat_lower for c in ["office", "wordprocessor", "spreadsheet"]):
        primary_category = "Office"
    elif any(c in cat_lower for c in ["system", "terminalemulator", "monitor"]):
        primary_category = "System"
    elif any(c in cat_lower for c in ["utility", "utilities", "archiving", "calculator"]):
        primary_category = "Utilities"
    elif any(c in cat_lower for c in ["settings", "desktopsettings"]):
        primary_category = "Settings"

    return {
        "name": name,
        "generic_name": generic_name,
        "comment": comment,
        "exec_cmd": exec_cmd,
        "icon": icon,
        "terminal": terminal,
        "no_display": no_display,
        "hidden": hidden,
        "in_menu": in_menu,
        "categories": categories,
        "primary_category": primary_category,
        "mime_type": mime_type,
        "x_flatpak": x_flatpak,
        "app_type": app_type,
    }


def extract_executable(exec_cmd: str) -> Optional[str]:
    """Extract primary executable path or binary name from an Exec line."""
    if not exec_cmd:
        return None
    cmd = exec_cmd.strip()
    try:
        tokens = shlex.split(cmd)
    except Exception:
        tokens = cmd.split()

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token.startswith("env") or "=" in token:
            idx += 1
            continue
        return token
    return None


def scan_all_applications() -> Dict[str, Any]:
    """Scan all desktop directories and standalone apps, cross-referencing package managers."""
    flatpak_apps = get_flatpak_installed()

    desktop_records = []
    seen_desktop_filenames = {}
    seen_system_files = set()
    shadowed_system_files = []

    for scope, directory in DESKTOP_DIRS:
        if not os.path.exists(directory):
            continue
        try:
            for item in sorted(os.listdir(directory)):
                if not item.endswith(".desktop"):
                    continue
                full_path = os.path.join(directory, item)
                if not os.path.isfile(full_path):
                    continue

                # If this desktop file was already seen from a higher-priority directory, mark override and skip duplicate
                if item in seen_desktop_filenames:
                    existing = seen_desktop_filenames[item]
                    existing["shadowed_paths"].append(full_path)
                    existing["is_override"] = True
                    if full_path.startswith("/usr/"):
                        shadowed_system_files.append(full_path)
                    continue

                parsed = parse_desktop_file(full_path)
                if not parsed:
                    continue

                rec = {
                    "scope": scope,
                    "filename": item,
                    "path": full_path,
                    "data": parsed,
                    "shadowed_paths": [],
                    "is_override": False,
                }
                desktop_records.append(rec)
                seen_desktop_filenames[item] = rec

                if scope in ("system", "system_local"):
                    seen_system_files.add(full_path)
        except Exception:
            pass

    # Collect files to query against RPM (including shadowed system files)
    rpm_query_files = list(seen_system_files) + shadowed_system_files
    local_binaries_map = {}
    for rec in desktop_records:
        if rec["scope"] == "local_user":
            exe = extract_executable(rec["data"]["exec_cmd"])
            if exe and not os.path.isabs(exe):
                resolved = shutil.which(exe)
                if resolved and resolved.startswith("/usr/"):
                    rpm_query_files.append(resolved)
                    local_binaries_map[rec["path"]] = resolved
            elif exe and os.path.isabs(exe) and exe.startswith("/usr/"):
                rpm_query_files.append(exe)
                local_binaries_map[rec["path"]] = exe

    file_to_rpm = query_rpm_database(rpm_query_files)

    applications = []
    processed_flatpak_ids = set()
    processed_appimage_paths = set()

    for rec in desktop_records:
        path = rec["path"]
        fname = rec["filename"]
        scope = rec["scope"]
        data = rec["data"]
        shadowed = rec.get("shadowed_paths", [])

        exec_cmd = data["exec_cmd"]
        primary_exe = extract_executable(exec_cmd) or ""

        source_type = "unmanaged"
        source_label = "Unmanaged System Entry"
        package_name = fname.replace(".desktop", "")
        package_version = ""
        package_arch = ""
        installed_size = ""
        uninstall_command = ""
        uninstall_note = ""
        is_override = rec.get("is_override", False)

        # Check if Flatpak (either direct in flatpak exports or shadowing a flatpak export)
        flatpak_id = None
        has_flatpak_shadow = any("/flatpak/exports/" in sp for sp in shadowed)
        if scope in ("flatpak_system", "flatpak_user"):
            flatpak_id = fname.replace(".desktop", "")
        elif has_flatpak_shadow:
            flatpak_id = fname.replace(".desktop", "")
        elif data.get("x_flatpak"):
            flatpak_id = data["x_flatpak"]
        elif "flatpak run" in exec_cmd:
            match = re.search(r"flatpak(?:\s+run)?(?:\s+--\S+)*\s+([a-zA-Z0-9_.-]+)", exec_cmd)
            if match and not match.group(1).startswith("-"):
                flatpak_id = match.group(1)

        if flatpak_id:
            source_type = "flatpak"
            processed_flatpak_ids.add(flatpak_id)
            is_user_install = (
                scope == "flatpak_user"
                or (flatpak_id in flatpak_apps and flatpak_apps[flatpak_id]["installation"] == "user")
            )
            source_label = "Flatpak (User)" if is_user_install else "Flatpak (System)"
            package_name = flatpak_id

            if flatpak_id in flatpak_apps:
                package_version = flatpak_apps[flatpak_id].get("version", "")
                installed_size = flatpak_apps[flatpak_id].get("size", "")

            if is_user_install:
                uninstall_command = f"flatpak uninstall --user {flatpak_id}"
            else:
                uninstall_command = f"flatpak uninstall {flatpak_id}"
            uninstall_note = f"Remove flatpak app {flatpak_id}"
            if is_override:
                uninstall_note += " (Customized menu entry overrides system flatpak)"

        # Check if Steam Game
        elif "steam steam://rungameid/" in exec_cmd:
            source_type = "steam"
            source_label = "Steam Game"
            match = re.search(r"steam://rungameid/(\d+)", exec_cmd)
            steam_id = match.group(1) if match else "unknown"
            package_name = f"steam_{steam_id}"
            uninstall_command = f"steam steam://uninstall/{steam_id}"
            uninstall_note = "Opens Steam client to uninstall game"
            if not data["primary_category"] or data["primary_category"] == "Other":
                data["primary_category"] = "Games"

        # Check if AppImage / ~/Applications executable
        elif (
            "/Applications/" in exec_cmd
            or ".appimage" in exec_cmd.lower()
            or (primary_exe and os.path.exists(primary_exe) and "/Applications/" in primary_exe)
        ):
            source_type = "appimage"
            source_label = "AppImage / Portable"
            clean_exe = primary_exe.strip("\"'")
            if os.path.exists(clean_exe):
                processed_appimage_paths.add(os.path.realpath(clean_exe))
            package_name = os.path.basename(clean_exe) if clean_exe else fname
            uninstall_command = f'rm -f "{clean_exe}" "{path}"'
            uninstall_note = "Deletes the portable executable and its menu shortcut"

        # Check if Web App / PWA
        elif (
            "WebApp-" in fname
            or "--app=" in exec_cmd
            or "--app-id=" in exec_cmd
            or "flextop" in fname
        ):
            source_type = "web_app"
            source_label = "Web App (PWA)"
            package_name = fname.replace(".desktop", "")
            uninstall_command = f'rm -f "{path}"'
            uninstall_note = "Removes the web application desktop shortcut"

        # Check if Local User Script / Tool or Local Override of RPM
        elif scope == "local_user":
            resolved_rpm_file = local_binaries_map.get(path)
            # Also check if shadowed system file is owned by RPM
            shadowed_rpm_file = next((sp for sp in shadowed if sp in file_to_rpm), None)
            rpm_target = resolved_rpm_file if resolved_rpm_file in file_to_rpm else shadowed_rpm_file
            rpm_info = file_to_rpm.get(rpm_target) if rpm_target else None

            if rpm_info:
                source_type = "repo_rpm"
                source_label = "Nobara Repo (RPM)"
                package_name = rpm_info["name"]
                package_version = rpm_info["version"]
                package_arch = rpm_info["arch"]
                installed_size = rpm_info.get("size", "")
                uninstall_command = f"sudo dnf remove {package_name}"
                uninstall_note = f"Customized local menu entry overriding system package {package_name}"
                is_override = True
            else:
                source_type = "local_tool"
                source_label = "Local Tool / Script"
                package_name = fname.replace(".desktop", "")
                uninstall_command = f'rm -f "{path}"'
                uninstall_note = "Removes the local desktop shortcut"
                data["primary_category"] = "Local Tools"

        # Check System desktop file owned by RPM
        elif path in file_to_rpm:
            rpm_info = file_to_rpm[path]
            source_type = "repo_rpm"
            source_label = "Nobara Repo (RPM)"
            package_name = rpm_info["name"]
            package_version = rpm_info["version"]
            package_arch = rpm_info["arch"]
            installed_size = rpm_info.get("size", "")
            uninstall_command = f"sudo dnf remove {package_name}"
            uninstall_note = "Standard system package via DNF/RPM"

        else:
            source_type = "unmanaged"
            source_label = "Unmanaged / Manual Build"
            package_name = fname.replace(".desktop", "")
            uninstall_command = f'sudo rm -f "{path}"'
            uninstall_note = "Manually created system desktop entry"

        app_id = f"{scope}:{fname}"
        applications.append(
            {
                "id": app_id,
                "name": data["name"],
                "generic_name": data["generic_name"],
                "comment": data["comment"],
                "icon": data["icon"],
                "categories": data["categories"],
                "primary_category": data["primary_category"],
                "in_menu": data["in_menu"],
                "no_display": data["no_display"],
                "hidden": data["hidden"],
                "scope": scope,
                "desktop_path": path,
                "desktop_filename": fname,
                "exec_command": exec_cmd,
                "source_type": source_type,
                "source_label": source_label,
                "package_name": package_name,
                "package_version": package_version,
                "package_arch": package_arch,
                "installed_size": installed_size,
                "uninstall_command": uninstall_command,
                "uninstall_note": uninstall_note,
                "is_override": is_override,
            }
        )

    # Add standalone AppImages in ~/Applications not having desktop files
    if os.path.exists(APPLICATIONS_DIR):
        try:
            for item in sorted(os.listdir(APPLICATIONS_DIR)):
                app_path = os.path.join(APPLICATIONS_DIR, item)
                if not os.path.isfile(app_path):
                    continue
                if item.endswith((".bak", ".txt", ".py", ".sh", ".json", ".log", ".png", ".jpg")):
                    continue
                real_app = os.path.realpath(app_path)
                if real_app in processed_appimage_paths:
                    continue

                if os.access(app_path, os.X_OK):
                    sz = os.path.getsize(app_path)
                    applications.append(
                        {
                            "id": f"standalone:{item}",
                            "name": item,
                            "generic_name": "Portable Application",
                            "comment": f"Standalone executable in {APPLICATIONS_DIR}",
                            "icon": "application-x-executable",
                            "categories": ["Utility"],
                            "primary_category": "Utilities",
                            "in_menu": False,
                            "no_display": False,
                            "hidden": False,
                            "scope": "standalone",
                            "desktop_path": "",
                            "desktop_filename": "",
                            "exec_command": app_path,
                            "source_type": "appimage",
                            "source_label": "AppImage / Portable (Standalone)",
                            "package_name": item,
                            "package_version": "",
                            "package_arch": "",
                            "installed_size": format_bytes(sz),
                            "uninstall_command": f'rm -f "{app_path}"',
                            "uninstall_note": "Deletes the standalone application binary",
                            "is_override": False,
                        }
                    )
        except Exception:
            pass

    # Sort applications: in menu first, then alphabetical by name
    applications.sort(key=lambda a: (not a["in_menu"], a["name"].lower()))

    # Build summary stats
    stats = {
        "total": len(applications),
        "in_menu": sum(1 for a in applications if a["in_menu"]),
        "hidden": sum(1 for a in applications if not a["in_menu"]),
        "repo_rpm": sum(1 for a in applications if a["source_type"] == "repo_rpm"),
        "flatpak": sum(1 for a in applications if a["source_type"] == "flatpak"),
        "appimage": sum(1 for a in applications if a["source_type"] == "appimage"),
        "steam": sum(1 for a in applications if a["source_type"] == "steam"),
        "local_tool": sum(1 for a in applications if a["source_type"] == "local_tool"),
        "web_app": sum(1 for a in applications if a["source_type"] == "web_app"),
        "unmanaged": sum(1 for a in applications if a["source_type"] == "unmanaged"),
    }

    return {"stats": stats, "applications": applications}


if __name__ == "__main__":
    import json
    import time

    t0 = time.time()
    result = scan_all_applications()
    t1 = time.time()
    print(f"Scanned {result['stats']['total']} applications in {t1 - t0:.2f}s")
    print(json.dumps(result["stats"], indent=2))
