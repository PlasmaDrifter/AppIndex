"""
AppIndex - Application Scanner Module
Discovers all desktop menu entries and installed applications.
Classifies installation source (Repo/RPM, Flatpak, AppImage, Steam, Local scripts, Web Apps)
and extracts package names and uninstall commands.
"""

import datetime
import glob
import os
import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Optional, Any, Set, Tuple

try:
    import rpm
    HAS_RPM_LIB = True
except ImportError:
    HAS_RPM_LIB = False

from usage_tracker import get_app_usage_metrics


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


def format_timestamp(epoch: Optional[float]) -> str:
    """Format Unix epoch timestamp into human-readable string YYYY-MM-DD HH:MM."""
    if not epoch or epoch <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def get_flatpak_deploy_times() -> Dict[str, float]:
    """Retrieve mtime of active deployment symlinks for installed Flatpaks."""
    times = {}
    patterns = [
        "/var/lib/flatpak/app/*/*/*/active",
        os.path.expanduser("~/.local/share/flatpak/app/*/*/*/active"),
    ]
    for pat in patterns:
        for p in glob.glob(pat):
            try:
                parts = p.split(os.sep)
                app_idx = parts.index("app") + 1
                app_id = parts[app_idx]
                times[app_id] = os.lstat(p).st_mtime
            except Exception:
                pass
    return times


def get_steam_update_time(steam_id: str, default_mtime: float = 0.0) -> float:
    """Retrieve LastUpdated timestamp from Steam appmanifest file if available."""
    steam_roots = [
        os.path.expanduser("~/.local/share/Steam/steamapps"),
        os.path.expanduser("~/.steam/steam/steamapps"),
        os.path.expanduser("~/.steam/root/steamapps"),
    ]
    for root in steam_roots:
        acf_path = os.path.join(root, f"appmanifest_{steam_id}.acf")
        if os.path.exists(acf_path):
            try:
                with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if '"LastUpdated"' in line:
                            match = re.search(r'"LastUpdated"\s+"(\d+)"', line)
                            if match:
                                return float(match.group(1))
            except Exception:
                pass
            return os.path.getmtime(acf_path)
    return default_mtime


def parse_nevra(nevra_str: str):
    """Extract package name and version-release from an RPM NEVRA string."""
    m = re.match(r"^(.+?)-(?:(\d+):)?([^-]+)-([^-]+)\.([^.]+)$", nevra_str)
    if m:
        name, epoch, version, release, arch = m.groups()
        return name, f"{version}-{release}"
    return nevra_str, ""


def _parse_dnf_timestamp(date_str: str, time_str: str, is_utc: bool) -> float:
    """Parse DNF transaction date/time into a Unix epoch timestamp."""
    iso = f"{date_str}T{time_str}"
    if is_utc:
        dt = datetime.datetime.fromisoformat(iso).replace(tzinfo=datetime.timezone.utc)
    else:
        dt = datetime.datetime.fromisoformat(iso)
    return dt.timestamp()


def get_recent_dnf_transitions(cutoff_epoch: float) -> Dict[str, Dict[str, str]]:
    """Query recent DNF/DNF5 transaction history for upgraded, downgraded, reinstalled, and installed packages."""
    transitions: Dict[str, Dict[str, str]] = {}
    cmd = None
    if shutil.which("dnf"):
        cmd = "dnf"
    elif shutil.which("dnf5"):
        cmd = "dnf5"
    if not cmd:
        return transitions

    cmd_path = shutil.which(cmd) or cmd
    is_dnf5_binary = "dnf5" in os.path.basename(os.path.realpath(cmd_path)).lower()

    try:
        proc = subprocess.run([cmd, "history", "list"], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return transitions

        target_tids: List[str] = []
        for line in proc.stdout.splitlines()[1:]:
            if not line.strip():
                continue
            # DNF4 uses pipe-delimited local timestamps; DNF5 outputs whitespace columns with UTC timestamps
            if "|" in line:
                cols = [c.strip() for c in line.split("|")]
                if len(cols) < 5 or not cols[0].isdigit():
                    continue
                tid = cols[0]
                dt_parts = cols[2].split()
                if len(dt_parts) != 2:
                    continue
                date_str, time_str = dt_parts
                altered_m = re.match(r"(\d+)", cols[4])
                if not altered_m:
                    continue
                altered = int(altered_m.group(1))
                is_utc = False
            else:
                m = re.match(
                    r"^\s*(\d+)\s+.*?(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)\s+.*?\b(\d+)\s*$",
                    line,
                )
                if not m:
                    continue
                tid, date_str, time_str, altered_str = m.groups()
                altered = int(altered_str)
                is_utc = is_dnf5_binary or len(time_str) == 8

            try:
                tx_epoch = _parse_dnf_timestamp(date_str, time_str, is_utc=is_utc)
                if tx_epoch < cutoff_epoch:
                    break
                if altered > 0:
                    target_tids.append(tid)
            except Exception:
                continue

        if not target_tids:
            return transitions

        # Fetch transaction details in batches so large histories stay bounded
        info_lines: List[str] = []
        for i in range(0, len(target_tids), 25):
            chunk = target_tids[i : i + 25]
            info_proc = subprocess.run([cmd, "history", "info"] + chunk, capture_output=True, text=True, check=False)
            if info_proc.returncode == 0 and info_proc.stdout:
                info_lines.extend(info_proc.stdout.splitlines())

        if not info_lines:
            return transitions

        def _flush_transaction(
            upgraded: Dict[str, str],
            downgraded: Dict[str, str],
            reinstalled: Dict[str, str],
            installed: Dict[str, str],
            replaced: Dict[str, str],
        ) -> None:
            # Keep the newest transaction result per package (history list is newest-first)
            for name, new_ver in upgraded.items():
                if name not in transitions:
                    old_ver = replaced.get(name, "")
                    transitions[name] = {
                        "old_version": old_ver,
                        "new_version": new_ver,
                        "version_transition": f"{old_ver} -> {new_ver}" if old_ver else new_ver,
                        "update_action": "Upgrade",
                    }
            for name, new_ver in downgraded.items():
                if name not in transitions:
                    old_ver = replaced.get(name, "")
                    transitions[name] = {
                        "old_version": old_ver,
                        "new_version": new_ver,
                        "version_transition": f"{old_ver} -> {new_ver}" if old_ver else new_ver,
                        "update_action": "Downgrade",
                    }
            for name, new_ver in reinstalled.items():
                if name not in transitions:
                    transitions[name] = {
                        "old_version": replaced.get(name, ""),
                        "new_version": new_ver,
                        "version_transition": f"Reinstalled ({new_ver})" if new_ver else "Reinstalled",
                        "update_action": "Reinstall",
                    }
            for name, new_ver in installed.items():
                if name not in transitions:
                    transitions[name] = {
                        "old_version": "",
                        "new_version": new_ver,
                        "version_transition": f"New install ({new_ver})" if new_ver else "Installed",
                        "update_action": "Install",
                    }

        in_pkgs = False
        upgraded: Dict[str, str] = {}
        downgraded: Dict[str, str] = {}
        reinstalled: Dict[str, str] = {}
        installed: Dict[str, str] = {}
        replaced: Dict[str, str] = {}

        for line in info_lines:
            if line.startswith("Transaction ID"):
                _flush_transaction(upgraded, downgraded, reinstalled, installed, replaced)
                upgraded.clear()
                downgraded.clear()
                reinstalled.clear()
                installed.clear()
                replaced.clear()
                in_pkgs = False
                continue
            if "Packages altered:" in line:
                in_pkgs = True
                continue
            if in_pkgs and line.startswith("  ") and not line.startswith("    "):
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] != "Action":
                    action, nevra = parts[0], parts[1]
                    if action == "Replaced" and len(parts) >= 3 and parts[1].lower() == "by":
                        nevra = parts[2]
                    name, ver = parse_nevra(nevra)
                    if action in ("Upgrade", "Upgraded"):
                        upgraded[name] = ver
                    elif action in ("Downgrade", "Downgraded"):
                        downgraded[name] = ver
                    elif action in ("Reinstall", "Reinstalled"):
                        reinstalled[name] = ver
                    elif action == "Replaced":
                        if name not in replaced:
                            replaced[name] = ver
                    elif action in ("Install", "Installed"):
                        installed[name] = ver
            elif in_pkgs and line and not line.startswith(" "):
                in_pkgs = False

        _flush_transaction(upgraded, downgraded, reinstalled, installed, replaced)
    except Exception:
        pass

    return transitions


def get_flatpak_installed() -> Dict[str, Dict[str, Any]]:
    """Query flatpak CLI for all installed applications with metadata and deploy timestamps."""
    flatpaks = {}
    deploy_times = get_flatpak_deploy_times()
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
                    inst_time = deploy_times.get(app_id, 0.0)
                    flatpaks[app_id] = {
                        "app_id": app_id,
                        "name": parts[1].strip() if len(parts) > 1 else "",
                        "version": parts[2].strip() if len(parts) > 2 else "",
                        "branch": parts[3].strip() if len(parts) > 3 else "stable",
                        "installation": parts[4].strip() if len(parts) > 4 else "system",
                        "size": parts[5].strip() if len(parts) > 5 else "",
                        "install_time": inst_time,
                        "install_date": format_timestamp(inst_time),
                    }
    except Exception:
        pass
    return flatpaks


def _parse_rpm_qf_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one tab-separated line from rpm -qf --queryformat."""
    if "\t" not in line:
        return None
    parts = line.split("\t")
    sz_str = ""
    try:
        sz_str = format_bytes(int(parts[3].strip()))
    except Exception:
        pass
    inst_time = 0.0
    try:
        inst_time = float(parts[4].strip())
    except Exception:
        pass
    return {
        "name": parts[0].strip(),
        "version": parts[1].strip(),
        "arch": parts[2].strip() if len(parts) > 2 else "",
        "size": sz_str,
        "summary": parts[5].strip() if len(parts) > 5 else "",
        "install_time": inst_time,
        "install_date": format_timestamp(inst_time),
        "vendor": parts[6].strip() if len(parts) > 6 else "",
        "packager": parts[7].strip() if len(parts) > 7 else "",
        "buildhost": "",
        "release": parts[8].strip() if len(parts) > 8 else "",
    }


def _query_rpm_cli_batch(chunk: List[str], results: Dict[str, Dict[str, Any]]) -> None:
    """Run batched rpm -qf queries, bisecting only chunks that contain unowned paths."""
    if not chunk:
        return
    qfmt = "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{SIZE}\t%{INSTALLTIME}\t%{SUMMARY}\t%{VENDOR}\t%{PACKAGER}\t%{RELEASE}\n"
    try:
        res = subprocess.run(["rpm", "-qf", "--queryformat", qfmt] + chunk, capture_output=True, text=True, check=False)
        lines = [ln for ln in res.stdout.splitlines() if "\t" in ln]
        if res.returncode == 0 and len(lines) == len(chunk):
            for f, line in zip(chunk, lines):
                parsed = _parse_rpm_qf_line(line)
                if parsed:
                    results[f] = parsed
            return
        if len(chunk) == 1:
            if res.returncode == 0 and lines:
                parsed = _parse_rpm_qf_line(lines[0])
                if parsed:
                    results[chunk[0]] = parsed
            return
        mid = len(chunk) // 2
        _query_rpm_cli_batch(chunk[:mid], results)
        _query_rpm_cli_batch(chunk[mid:], results)
    except Exception:
        pass


def query_rpm_database(file_paths: List[str]) -> Dict[str, Dict[str, Any]]:
    """Query RPM database for file paths using native python rpm bindings or rpm CLI."""
    results: Dict[str, Dict[str, Any]] = {}
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
                    inst_time = float(hdr["installtime"]) if "installtime" in hdr else 0.0
                    vendor = str(hdr["vendor"]) if "vendor" in hdr and hdr["vendor"] else ""
                    packager = str(hdr["packager"]) if "packager" in hdr and hdr["packager"] else ""
                    buildhost = str(hdr["buildhost"]) if "buildhost" in hdr and hdr["buildhost"] else ""
                    release = str(hdr["release"]) if "release" in hdr and hdr["release"] else ""
                    results[filepath] = {
                        "name": pkg_name,
                        "version": version,
                        "arch": arch,
                        "size": format_bytes(sz) if isinstance(sz, int) else "",
                        "summary": summary,
                        "install_time": inst_time,
                        "install_date": format_timestamp(inst_time),
                        "vendor": vendor,
                        "packager": packager,
                        "buildhost": buildhost,
                        "release": release,
                    }
                    break
            return results
        except Exception:
            pass

    batch_size = 80
    for i in range(0, len(file_paths), batch_size):
        _query_rpm_cli_batch(file_paths[i : i + batch_size], results)

    return results


def resolve_rpm_origin(
    vendor: str,
    packager: str,
    buildhost: str = "",
    release: str = "",
    distro_name: str = "System",
) -> Dict[str, str]:
    """Resolve specific repository origin and badge label from RPM vendor, packager, and release metadata."""
    v_lower = (vendor or "").lower()
    p_lower = (packager or "").lower()
    combined = f"{v_lower} {p_lower} {(buildhost or '').lower()} {(release or '').lower()}"

    # 1. Terra Repository (Fyra Labs)
    if "terra" in combined or "fyralabs" in combined:
        packager_info = packager if packager else "Fyra Labs"
        return {
            "origin": "Terra",
            "badge_label": "Terra (RPM)",
            "packager_detail": f"Terra ({packager_info})",
        }

    # 2. Fedora COPR
    if "copr" in v_lower or "copr" in p_lower:
        # Check if Nobara's core COPR repository (built by gloriouseggroll)
        if "gloriouseggroll" in combined:
            return {
                "origin": "Nobara",
                "badge_label": "Nobara (RPM)",
                "packager_detail": "Nobara Official (COPR gloriouseggroll)",
            }
        # Other third-party COPR
        copr_match = re.search(r"user\s+([a-zA-Z0-9_\-\.]+)", vendor, re.IGNORECASE)
        copr_user = copr_match.group(1) if copr_match else ""
        if copr_user:
            return {
                "origin": f"COPR ({copr_user})",
                "badge_label": f"COPR ({copr_user}) (RPM)",
                "packager_detail": f"Fedora COPR (user: {copr_user})",
            }
        return {
            "origin": "COPR",
            "badge_label": "COPR (RPM)",
            "packager_detail": vendor or "Fedora COPR",
        }

    # 3. RPM Fusion
    if "rpmfusion" in combined or "rpm fusion" in combined:
        return {
            "origin": "RPM Fusion",
            "badge_label": "RPM Fusion (RPM)",
            "packager_detail": vendor or packager or "RPM Fusion",
        }

    # 4. Nobara official packages (non-COPR or release tags)
    if "nobara" in combined:
        return {
            "origin": "Nobara",
            "badge_label": "Nobara (RPM)",
            "packager_detail": vendor or packager or "Nobara Project",
        }

    # 5. Fedora Project upstream
    if "fedora project" in v_lower or "red hat" in v_lower or "fedora project" in p_lower:
        return {
            "origin": "Fedora",
            "badge_label": "Fedora (RPM)",
            "packager_detail": "Fedora Project",
        }

    # 6. Fallback
    fallback_name = vendor if vendor else (distro_name if distro_name else "Repo")
    detail = []
    if vendor:
        detail.append(vendor)
    if packager and packager != vendor:
        detail.append(packager)
    packager_detail = " — ".join(detail) if detail else f"{distro_name} Package"

    return {
        "origin": fallback_name,
        "badge_label": f"{fallback_name} (RPM)",
        "packager_detail": packager_detail,
    }


def get_distribution_info(os_release_path: Optional[str] = None) -> Dict[str, Any]:
    """Parse /etc/os-release to detect distribution name, ID, and primary package manager."""
    info = {
        "name": "Linux",
        "id": "linux",
        "id_like": [],
        "pretty_name": "Linux",
        "pkg_manager": "unknown",
        "pkg_manager_type": "unknown",
        "pkg_manager_label": "Repo",
        "uninstall_prefix": "sudo rm -f",
    }

    paths = [os_release_path] if os_release_path else ["/etc/os-release", "/usr/lib/os-release"]
    for p in paths:
        if p and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("\"'")
                        if k == "NAME":
                            info["name"] = v
                        elif k == "ID":
                            info["id"] = v.lower()
                        elif k == "ID_LIKE":
                            info["id_like"] = [x.lower() for x in v.split()]
                        elif k == "PRETTY_NAME":
                            info["pretty_name"] = v
                break
            except Exception:
                pass

    all_ids = [info["id"]] + info["id_like"]
    clean_name = re.sub(r"\b(?:GNU/)?Linux\b", "", info["name"], flags=re.IGNORECASE).strip()
    clean_name = re.sub(r"\s+", " ", clean_name)
    info["display_name"] = clean_name if clean_name else info["name"]

    # Detect package manager by prioritizing distribution family, then available database
    if any(x in ("fedora", "nobara", "rhel", "centos") for x in all_ids) or (HAS_RPM_LIB and (shutil.which("dnf") or shutil.which("rpm"))):
        info["pkg_manager"] = "dnf"
        info["pkg_manager_type"] = "rpm"
        info["pkg_manager_label"] = "RPM"
        info["uninstall_prefix"] = "sudo dnf remove"
    elif any(x in ("suse", "opensuse") for x in all_ids) or (shutil.which("zypper") and shutil.which("rpm")):
        info["pkg_manager"] = "zypper"
        info["pkg_manager_type"] = "rpm"
        info["pkg_manager_label"] = "RPM"
        info["uninstall_prefix"] = "sudo zypper rm"
    elif any(x in ("arch", "manjaro", "endeavouros") for x in all_ids) or (shutil.which("pacman") and os.path.isdir("/var/lib/pacman")):
        info["pkg_manager"] = "pacman"
        info["pkg_manager_type"] = "pacman"
        info["pkg_manager_label"] = "Pacman"
        info["uninstall_prefix"] = "sudo pacman -R"
    elif any(x in ("debian", "ubuntu", "linuxmint", "pop") for x in all_ids) or (shutil.which("dpkg") and os.path.isdir("/var/lib/dpkg")):
        info["pkg_manager"] = "apt"
        info["pkg_manager_type"] = "dpkg"
        info["pkg_manager_label"] = "APT"
        info["uninstall_prefix"] = "sudo apt remove"

    return info


def get_active_desktop_environments() -> set:
    """Detect current active desktop environments from XDG session variables."""
    current = os.environ.get("XDG_CURRENT_DESKTOP", "").strip()
    if current:
        return {d.strip().upper() for d in current.split(":") if d.strip()}

    session = os.environ.get("DESKTOP_SESSION", "").upper()
    if "PLASMA" in session or "KDE" in session:
        return {"KDE", "PLASMA"}
    elif "GNOME" in session:
        return {"GNOME"}
    elif "XFCE" in session:
        return {"XFCE"}
    elif "CINNAMON" in session:
        return {"CINNAMON", "X-CINNAMON"}
    elif "MATE" in session:
        return {"MATE"}

    # Fallback if unset/headless: universal match so nothing is erroneously hidden
    return {"KDE", "PLASMA", "GNOME", "XFCE", "CINNAMON", "MATE", "LXQT", "PANTHEON"}


def query_pacman_database(file_paths: List[str]) -> Dict[str, Dict[str, str]]:
    """Query pacman database for file ownership using pacman -Qo."""
    results: Dict[str, Dict[str, str]] = {}
    if not file_paths or not shutil.which("pacman"):
        return results

    batch_size = 80
    for i in range(0, len(file_paths), batch_size):
        chunk = file_paths[i : i + batch_size]
        try:
            cmd = ["pacman", "-Qo"] + chunk
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                # Line format: "/path/to/file is owned by pkgname version"
                m = re.match(r"^(.+?)\s+is owned by\s+(\S+)\s+(\S+)", line)
                if m:
                    filepath = m.group(1).strip()
                    pkg_name = m.group(2).strip()
                    pkg_ver = m.group(3).strip()
                    results[filepath] = {
                        "name": pkg_name,
                        "version": pkg_ver,
                        "arch": "",
                        "size": "",
                        "summary": "",
                    }
        except Exception:
            pass

    return results


def query_dpkg_database(file_paths: List[str]) -> Dict[str, Dict[str, str]]:
    """Query dpkg database for file ownership using dpkg -S and dpkg-query."""
    results: Dict[str, Dict[str, str]] = {}
    if not file_paths or not shutil.which("dpkg"):
        return results

    file_to_pkg: Dict[str, str] = {}
    batch_size = 80
    for i in range(0, len(file_paths), batch_size):
        chunk = file_paths[i : i + batch_size]
        try:
            cmd = ["dpkg", "-S"] + chunk
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                if ": " in line:
                    pkg_part, path_part = line.split(": ", 1)
                    pkg_name = pkg_part.split(":")[0].strip()
                    path = path_part.strip()
                    file_to_pkg[path] = pkg_name
        except Exception:
            pass

    if not file_to_pkg:
        return results

    unique_pkgs = list(set(file_to_pkg.values()))
    pkg_meta: Dict[str, Dict[str, str]] = {}
    for i in range(0, len(unique_pkgs), batch_size):
        chunk = unique_pkgs[i : i + batch_size]
        try:
            cmd = [
                "dpkg-query",
                "-W",
                "-f=${Package}\t${Version}\t${Architecture}\t${Installed-Size}\t${binary:Summary}\n",
            ] + chunk
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            for line in res.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    p_name = parts[0].strip()
                    p_ver = parts[1].strip()
                    p_arch = parts[2].strip() if len(parts) > 2 else ""
                    p_sz_str = ""
                    if len(parts) > 3:
                        try:
                            p_sz_str = format_bytes(int(parts[3].strip()) * 1024)
                        except Exception:
                            pass
                    p_sum = parts[4].strip() if len(parts) > 4 else ""
                    pkg_meta[p_name] = {
                        "name": p_name,
                        "version": p_ver,
                        "arch": p_arch,
                        "size": p_sz_str,
                        "summary": p_sum,
                    }
        except Exception:
            pass

    for path, pkg in file_to_pkg.items():
        if pkg in pkg_meta:
            results[path] = pkg_meta[pkg]
        else:
            results[path] = {
                "name": pkg,
                "version": "",
                "arch": "",
                "size": "",
                "summary": "",
            }

    return results


def query_system_package_database(file_paths: List[str], distro_info: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Query the host package manager (RPM, Pacman, or DPKG) based on distro information."""
    pkg_type = distro_info.get("pkg_manager_type")
    if pkg_type == "pacman":
        return query_pacman_database(file_paths)
    elif pkg_type == "dpkg":
        return query_dpkg_database(file_paths)
    elif pkg_type == "rpm":
        return query_rpm_database(file_paths)

    # If unknown, try native RPM then Pacman then DPKG
    if HAS_RPM_LIB or shutil.which("rpm"):
        res = query_rpm_database(file_paths)
        if res:
            return res
    if shutil.which("pacman"):
        res = query_pacman_database(file_paths)
        if res:
            return res
    if shutil.which("dpkg"):
        res = query_dpkg_database(file_paths)
        if res:
            return res
    return {}


def parse_desktop_file(filepath: str, active_desktops: Optional[set] = None) -> Optional[Dict[str, Any]]:
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

    # Check desktop environment restrictions dynamically
    desktops = active_desktops if active_desktops is not None else get_active_desktop_environments()
    if not_show_in:
        blocked_envs = {env.strip().upper() for env in not_show_in.split(";") if env.strip()}
        if desktops & blocked_envs:
            in_menu = False

    if only_show_in:
        allowed_envs = {env.strip().upper() for env in only_show_in.split(";") if env.strip()}
        if not (desktops & allowed_envs):
            in_menu = False

    primary_category = "Other"
    cat_set = {c.lower() for c in categories}
    if cat_set & {"game", "games", "emulator"}:
        primary_category = "Games"
    elif cat_set & {"audiovideo", "audio", "video", "multimedia", "player", "tv"}:
        primary_category = "Audio & Video"
    elif cat_set & {"development", "ide", "programming", "debugger"}:
        primary_category = "Development"
    elif cat_set & {"graphics", "rastergraphics", "vectorgraphics", "photography", "viewer", "scanning"}:
        primary_category = "Graphics"
    elif cat_set & {"network", "webbrowser", "chat", "email", "telephony", "filetransfer", "p2p", "instantmessaging"}:
        primary_category = "Internet"
    elif cat_set & {"office", "wordprocessor", "spreadsheet", "presentation"}:
        primary_category = "Office"
    elif cat_set & {"system", "terminalemulator", "monitor", "packagemanager", "filesystem"}:
        primary_category = "System"
    elif cat_set & {"utility", "utilities", "archiving", "calculator", "texteditor", "accessibility", "documentation"}:
        primary_category = "Utilities"
    elif cat_set & {"settings", "desktopsettings", "hardwaresettings"}:
        primary_category = "Settings"
    elif cat_set & {"education", "science", "math", "astronomy", "biology", "chemistry", "physics"}:
        primary_category = "Education"

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
        if token == "env" or os.path.basename(token) == "env" or "=" in token:
            idx += 1
            continue
        return token
    return None


def _extract_appimage_target(exec_cmd: str, primary_exe: str) -> str:
    """Resolve actual AppImage / ~/Applications path even when wrapped in sh -c or env."""
    candidates: List[str] = []
    if primary_exe and os.path.basename(primary_exe) not in ("sh", "bash", "zsh", "dash", "env"):
        candidates.append(primary_exe)

    # Split tokens and unpack shell -c payloads without keeping the raw -c script string
    try:
        tokens = shlex.split(exec_cmd)
    except Exception:
        tokens = exec_cmd.split()

    expanded_tokens: List[str] = []
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in ("-c", "-lc") and idx + 1 < len(tokens):
            payload = tokens[idx + 1]
            try:
                expanded_tokens.extend(shlex.split(payload))
            except Exception:
                expanded_tokens.extend(payload.split())
            idx += 2
            continue
        expanded_tokens.append(tok)
        idx += 1

    for tok in expanded_tokens:
        if not tok:
            continue
        if "/Applications/" in tok or tok.lower().endswith(".appimage"):
            candidates.append(tok)

    home_dir = os.path.expanduser("~")
    normalized: List[str] = []
    for raw in candidates:
        expanded = raw.strip("\"'")
        expanded = expanded.replace("\\$HOME", home_dir).replace("$HOME", home_dir).replace("${HOME}", home_dir)
        expanded = os.path.expanduser(expanded)
        normalized.append(expanded)

    # Prefer paths that actually exist on disk before falling back to heuristic strings
    for expanded in normalized:
        if os.path.isfile(expanded):
            return expanded
    for expanded in normalized:
        if "/Applications/" in expanded or expanded.lower().endswith(".appimage"):
            return expanded

    return os.path.expanduser(primary_exe.strip("\"'")) if primary_exe else ""


def _extract_flatpak_id_from_exec(exec_cmd: str) -> Optional[str]:
    """Extract Flatpak application ID from a flatpak run command line."""
    try:
        tokens = shlex.split(exec_cmd)
    except Exception:
        tokens = exec_cmd.split()

    saw_flatpak = False
    skip_next = False
    value_flags = {"--branch", "--arch", "--command", "--cwd", "--env", "--filesystem", "--nofilesystem", "--socket", "--nosocket", "--device", "--nodevice", "--share", "--unshare", "--talk-name", "--own-name", "--system-talk-name", "--system-own-name", "--persist", "--runtime", "--runtime-version", "--parent-pid", "--parent-share-pids", "--installation"}
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        base = os.path.basename(tok)
        if not saw_flatpak:
            if base == "flatpak":
                saw_flatpak = True
            continue
        if tok == "run":
            continue
        if tok.startswith("-"):
            if "=" not in tok and tok in value_flags:
                skip_next = True
            continue
        if re.match(r"^[a-zA-Z0-9_.-]+\.[a-zA-Z0-9_.-]+$", tok):
            return tok
        return None
    return None


def _eval_menu_rule(elem: ET.Element, fname: str, cat_set: Set[str]) -> bool:
    """Evaluate a Freedesktop XDG menu XML condition element against a desktop entry."""
    tag = elem.tag
    if tag == "Filename":
        return (elem.text or "").strip() == fname
    if tag == "Category":
        return (elem.text or "").strip() in cat_set
    if tag == "All":
        return True
    if tag == "And":
        children = list(elem)
        return bool(children) and all(_eval_menu_rule(c, fname, cat_set) for c in children)
    if tag == "Or":
        return any(_eval_menu_rule(c, fname, cat_set) for c in elem)
    if tag == "Not":
        children = list(elem)
        return not any(_eval_menu_rule(c, fname, cat_set) for c in children)
    if tag in ("Include", "Exclude"):
        return any(_eval_menu_rule(c, fname, cat_set) for c in elem)
    return False


def _standardize_menu_category(cat_name: str) -> str:
    """Normalize desktop menu directory names to AppIndex category labels."""
    if cat_name == "Multimedia":
        return "Audio & Video"
    return cat_name


def _collect_menu_tree(
    menu_elem: ET.Element,
    top_cat: Optional[str],
    menu_map: Dict[str, Dict[str, Any]],
) -> None:
    """Recursively collect merged XDG menu definitions keyed by top-level category."""
    name_el = menu_elem.find("Name")
    cat_name = name_el.text.strip() if (name_el is not None and name_el.text) else ""
    current_top = top_cat if top_cat is not None else cat_name

    if current_top:
        node = menu_map.setdefault(
            current_top,
            {
                "deleted": False,
                "only_unallocated": False,
                "include_rules": [],
                "exclude_rules": [],
                "layout_files": [],
                "has_layout": False,
            },
        )
        if menu_elem.find("Deleted") is not None and top_cat is None:
            node["deleted"] = True
        elif menu_elem.find("NotDeleted") is not None and top_cat is None:
            node["deleted"] = False

        if menu_elem.find("OnlyUnallocated") is not None:
            node["only_unallocated"] = True

        for inc in menu_elem.findall("Include"):
            node["include_rules"].append(inc)
        for exc in menu_elem.findall("Exclude"):
            node["exclude_rules"].append(exc)

        layout_el = menu_elem.find("Layout")
        if layout_el is not None:
            node["has_layout"] = True
            for fn in layout_el.findall("Filename"):
                if fn.text and fn.text.strip():
                    fname = fn.text.strip()
                    if fname not in node["layout_files"]:
                        node["layout_files"].append(fname)

    for sub in menu_elem.findall("Menu"):
        _collect_menu_tree(sub, current_top, menu_map)


def get_desktop_menu_layout(
    desktop_entries: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[Dict[str, List[str]], Set[str]]:
    """Parse active desktop environment application menu layout XML (base + user overrides).
    Returns:
        tuple: (active_menu_categories: Dict[desktop_filename, List[category_names]],
                hidden_desktop_filenames: Set[desktop_filename])
    """
    active_menu_categories: Dict[str, List[str]] = defaultdict(list)
    hidden_desktop_filenames: Set[str] = set()

    # Select base XDG menu plus optional user overrides (e.g. kmenuedit delta)
    base_candidates = [
        os.path.expanduser("~/.config/menus/plasma-applications.menu"),
        os.path.expanduser("~/.config/menus/applications.menu"),
        "/etc/xdg/menus/plasma-applications.menu",
        "/etc/xdg/menus/kf5-applications.menu",
        "/etc/xdg/menus/applications.menu",
    ]
    override_candidates = [
        os.path.expanduser("~/.config/menus/applications-kmenuedit.menu"),
    ]

    base_menu = next((c for c in base_candidates if os.path.isfile(c)), None)
    override_menus = [c for c in override_candidates if os.path.isfile(c)]

    if not base_menu and not override_menus:
        return active_menu_categories, hidden_desktop_filenames

    menu_map: Dict[str, Dict[str, Any]] = {}
    for menu_file in [m for m in [base_menu] + override_menus if m]:
        try:
            tree = ET.parse(menu_file)
            root = tree.getroot()
            for menu in root.findall("Menu"):
                _collect_menu_tree(menu, None, menu_map)
        except Exception:
            continue

    # Evaluate .hidden first
    hidden_node = menu_map.get(".hidden")
    if hidden_node and not hidden_node["deleted"]:
        for inc in hidden_node["include_rules"]:
            for fn in inc.findall("Filename"):
                if fn.text and fn.text.strip():
                    hidden_desktop_filenames.add(fn.text.strip())
        for exc in hidden_node["exclude_rules"]:
            for fn in exc.findall("Filename"):
                if fn.text and fn.text.strip():
                    hidden_desktop_filenames.discard(fn.text.strip())

    ignored_menu_categories = {".hidden", "chrome-apps", "webapps-webapps", "Applications"}
    entries_map = desktop_entries or {}

    def _is_excluded(node: Dict[str, Any], fname: str, cats: Set[str]) -> bool:
        return any(_eval_menu_rule(exc, fname, cats) for exc in node["exclude_rules"])

    # Evaluate normal menus first, then OnlyUnallocated buckets (like Applications)
    for cat_name, node in menu_map.items():
        if cat_name in ignored_menu_categories or node["deleted"] or node["only_unallocated"]:
            continue

        display_cat = _standardize_menu_category(cat_name)

        # 1. Explicit Filenames in Layout (respecting Exclude and .hidden)
        for fname in node["layout_files"]:
            if fname in hidden_desktop_filenames:
                continue
            cats = entries_map.get(fname, set())
            if _is_excluded(node, fname, cats):
                continue
            if display_cat not in active_menu_categories[fname]:
                active_menu_categories[fname].append(display_cat)

        # 2. Explicit <Filename> entries in <Include> (not Excluded or .hidden)
        for inc in node["include_rules"]:
            for fn in inc.findall("Filename"):
                if not fn.text or not fn.text.strip():
                    continue
                fname = fn.text.strip()
                if fname in hidden_desktop_filenames:
                    continue
                cats = entries_map.get(fname, set())
                if _is_excluded(node, fname, cats):
                    continue
                if display_cat not in active_menu_categories[fname]:
                    active_menu_categories[fname].append(display_cat)

        # 3. Rule-based <Include> evaluation against desktop entry categories (when desktop_entries supplied)
        if entries_map and node["include_rules"]:
            for fname, cats in entries_map.items():
                if fname in hidden_desktop_filenames:
                    continue
                if _is_excluded(node, fname, cats):
                    continue
                if any(_eval_menu_rule(inc, fname, cats) for inc in node["include_rules"]):
                    if display_cat not in active_menu_categories[fname]:
                        active_menu_categories[fname].append(display_cat)

    return active_menu_categories, hidden_desktop_filenames


def scan_all_applications(recent_hours: int = 48) -> Dict[str, Any]:
    """Scan all desktop directories and standalone apps, cross-referencing package managers."""
    distro_info = get_distribution_info()
    active_desktops = get_active_desktop_environments()
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

                parsed = parse_desktop_file(full_path, active_desktops)
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

    desktop_category_map = {
        rec["filename"]: set(rec["data"].get("categories", []))
        for rec in desktop_records
    }
    active_menu_cats, hidden_menu_files = get_desktop_menu_layout(desktop_entries=desktop_category_map)

    # Collect files to query against native system package manager
    query_files = list(seen_system_files) + shadowed_system_files
    local_binaries_map = {}
    for rec in desktop_records:
        if rec["scope"] == "local_user":
            exe = extract_executable(rec["data"]["exec_cmd"])
            resolved = None
            if exe and not os.path.isabs(exe):
                resolved = shutil.which(exe)
            elif exe and os.path.isabs(exe):
                resolved = exe
            if resolved:
                real_res = os.path.realpath(resolved)
                target_path = real_res if real_res.startswith("/usr/") else (resolved if resolved.startswith("/usr/") else None)
                if target_path:
                    query_files.append(target_path)
                    local_binaries_map[rec["path"]] = target_path

    file_to_pkg = query_system_package_database(list(dict.fromkeys(query_files)), distro_info)

    distro_raw = distro_info.get("display_name") or distro_info.get("name", "System")
    distro_name = re.sub(r"\b(?:GNU/)?Linux\b", "", distro_raw, flags=re.IGNORECASE).strip()
    distro_name = re.sub(r"\s+", " ", distro_name)
    if not distro_name:
        distro_name = "System"
    pkg_label = distro_info.get("pkg_manager_label", "Repo")
    pkg_type = distro_info.get("pkg_manager_type", "rpm")
    uninstall_prefix = distro_info.get("uninstall_prefix", "sudo rm -f")

    repo_source_type = "repo_apt" if pkg_type == "dpkg" else (f"repo_{pkg_type}" if pkg_type in ("rpm", "pacman") else "repo_system")
    repo_source_label = f"{distro_name} Repo ({pkg_label})"

    now_epoch = time.time()
    recent_cutoff = now_epoch - (max(1, int(recent_hours)) * 3600)
    dnf_transitions = get_recent_dnf_transitions(recent_cutoff) if pkg_type == "rpm" else {}

    applications = []
    processed_flatpak_ids = set()
    processed_appimage_paths = set()

    for rec in desktop_records:
        path = rec["path"]
        fname = rec["filename"]
        base_desktop_id = fname.removesuffix(".desktop")
        scope = rec["scope"]
        data = rec["data"]
        shadowed = rec.get("shadowed_paths", [])

        exec_cmd = data["exec_cmd"]
        primary_exe = extract_executable(exec_cmd) or ""

        source_type = "unmanaged"
        source_label = "Unmanaged"
        package_name = base_desktop_id
        package_version = ""
        package_arch = ""
        package_vendor = ""
        package_packager = ""
        repo_origin = ""
        installed_size = ""
        uninstall_command = ""
        uninstall_note = ""
        is_override = rec.get("is_override", False)
        install_time = 0.0
        install_date = ""
        version_transition = ""
        update_action = ""
        steam_id = ""

        # Check if Flatpak (prioritize X-Flatpak header so secondary desktop files map to the owning Flatpak ID)
        flatpak_id = None
        has_flatpak_shadow = any("/flatpak/exports/" in sp for sp in shadowed)
        if data.get("x_flatpak"):
            flatpak_id = data["x_flatpak"]
        elif scope in ("flatpak_system", "flatpak_user") or has_flatpak_shadow:
            flatpak_id = base_desktop_id
        elif "flatpak" in exec_cmd:
            flatpak_id = _extract_flatpak_id_from_exec(exec_cmd)

        if flatpak_id:
            source_type = "flatpak"
            processed_flatpak_ids.add(flatpak_id)
            is_user_install = (
                scope == "flatpak_user"
                or (flatpak_id in flatpak_apps and flatpak_apps[flatpak_id]["installation"] == "user")
            )
            source_label = "Flatpak (User)" if is_user_install else "Flatpak (System)"
            repo_origin = f"Flatpak ({'User' if is_user_install else 'System'})"
            package_name = flatpak_id

            if flatpak_id in flatpak_apps:
                package_version = flatpak_apps[flatpak_id].get("version", "")
                installed_size = flatpak_apps[flatpak_id].get("size", "")
                install_time = flatpak_apps[flatpak_id].get("install_time", 0.0)
                install_date = flatpak_apps[flatpak_id].get("install_date", "")

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
            repo_origin = "Steam Store"
            match = re.search(r"steam://rungameid/(\d+)", exec_cmd)
            steam_id = match.group(1) if match else "unknown"
            package_name = f"steam_{steam_id}"
            uninstall_command = f"steam steam://uninstall/{steam_id}"
            uninstall_note = "Opens Steam client to uninstall game"
            if not data["primary_category"] or data["primary_category"] == "Other":
                data["primary_category"] = "Games"
            def_time = os.path.getmtime(path) if os.path.exists(path) else 0.0
            install_time = get_steam_update_time(steam_id, def_time)
            install_date = format_timestamp(install_time)

        # Check if AppImage / ~/Applications executable
        elif (
            "/Applications/" in exec_cmd
            or ".appimage" in exec_cmd.lower()
            or (primary_exe and os.path.exists(os.path.expanduser(primary_exe)) and "/Applications/" in primary_exe)
        ):
            source_type = "appimage"
            source_label = "AppImage"
            repo_origin = "Standalone AppImage"
            clean_exe = _extract_appimage_target(exec_cmd, primary_exe)
            if clean_exe and os.path.isfile(clean_exe):
                processed_appimage_paths.add(os.path.realpath(clean_exe))
                install_time = os.path.getmtime(clean_exe)
                try:
                    installed_size = format_bytes(os.path.getsize(clean_exe))
                except OSError:
                    pass
            elif os.path.exists(path):
                install_time = os.path.getmtime(path)
            install_date = format_timestamp(install_time)
            package_name = os.path.basename(clean_exe) if clean_exe else fname
            uninstall_command = f'rm -f "{clean_exe}" "{path}"' if clean_exe else f'rm -f "{path}"'
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
            repo_origin = "Web Application (PWA)"
            package_name = base_desktop_id
            uninstall_command = f'rm -f "{path}"'
            uninstall_note = "Removes the web application desktop shortcut"
            if os.path.exists(path):
                install_time = os.path.getmtime(path)
                install_date = format_timestamp(install_time)

        # Check if Local User Script / Tool or Local Override of System Package
        elif scope == "local_user":
            resolved_sys_file = local_binaries_map.get(path)
            shadowed_sys_file = next((sp for sp in shadowed if sp in file_to_pkg), None)
            pkg_target = resolved_sys_file if resolved_sys_file in file_to_pkg else shadowed_sys_file
            pkg_info = file_to_pkg.get(pkg_target) if pkg_target else None

            if pkg_info:
                source_type = repo_source_type
                package_name = pkg_info["name"]
                package_version = pkg_info["version"]
                package_arch = pkg_info["arch"]
                package_vendor = pkg_info.get("vendor", "")
                package_packager = pkg_info.get("packager", "")
                package_release = pkg_info.get("release", "")
                package_buildhost = pkg_info.get("buildhost", "")
                installed_size = pkg_info.get("size", "")
                install_time = pkg_info.get("install_time", 0.0)
                install_date = pkg_info.get("install_date", "")
                uninstall_command = f"{uninstall_prefix} {package_name}"
                uninstall_note = f"Customized local menu entry overriding system package {package_name}"
                is_override = True

                if pkg_type == "rpm":
                    origin_info = resolve_rpm_origin(
                        package_vendor,
                        package_packager,
                        package_buildhost,
                        package_release,
                        distro_name,
                    )
                    source_label = origin_info["badge_label"]
                    repo_origin = origin_info["packager_detail"]
                else:
                    source_label = repo_source_label
                    repo_origin = package_vendor or package_packager or repo_source_label
            else:
                source_type = "local_tool"
                source_label = "Local App"
                repo_origin = "Local Application (~/.local/share/applications)"
                package_name = base_desktop_id
                uninstall_command = f'rm -f "{path}"'
                uninstall_note = "Removes the local desktop shortcut"
                data["primary_category"] = "Local Apps"
                if os.path.exists(path):
                    install_time = os.path.getmtime(path)
                    install_date = format_timestamp(install_time)

        # Check System desktop file owned by system package manager
        elif path in file_to_pkg:
            pkg_info = file_to_pkg[path]
            source_type = repo_source_type
            package_name = pkg_info["name"]
            package_version = pkg_info["version"]
            package_arch = pkg_info["arch"]
            package_vendor = pkg_info.get("vendor", "")
            package_packager = pkg_info.get("packager", "")
            package_release = pkg_info.get("release", "")
            package_buildhost = pkg_info.get("buildhost", "")
            installed_size = pkg_info.get("size", "")
            install_time = pkg_info.get("install_time", 0.0)
            install_date = pkg_info.get("install_date", "")
            uninstall_command = f"{uninstall_prefix} {package_name}"
            uninstall_note = f"Standard system package via {pkg_label}"

            if pkg_type == "rpm":
                origin_info = resolve_rpm_origin(
                    package_vendor,
                    package_packager,
                    package_buildhost,
                    package_release,
                    distro_name,
                )
                source_label = origin_info["badge_label"]
                repo_origin = origin_info["packager_detail"]
            else:
                source_label = repo_source_label
                repo_origin = package_vendor or package_packager or repo_source_label

        else:
            source_type = "unmanaged"
            source_label = "Unmanaged"
            repo_origin = "Unmanaged System Launcher"
            package_name = base_desktop_id
            uninstall_command = f'sudo rm -f "{path}"'
            uninstall_note = "Manually created system desktop entry"
            if os.path.exists(path):
                install_time = os.path.getmtime(path)
                install_date = format_timestamp(install_time)

        if package_name and package_name in dnf_transitions:
            trans = dnf_transitions[package_name]
            version_transition = trans.get("version_transition", "")
            update_action = trans.get("update_action", "")
            if not package_version and trans.get("new_version"):
                package_version = trans["new_version"]

        is_recent_update = bool(install_time and install_time >= recent_cutoff)
        if is_recent_update and not update_action:
            update_action = "Updated"

        last_used_time, last_used_date, launch_count = get_app_usage_metrics(
            package_name=package_name,
            desktop_filename=fname,
            exec_cmd=exec_cmd,
            steam_id=steam_id if source_type == "steam" else "",
        )

        app_in_menu = data["in_menu"]
        is_helper = not data["in_menu"]
        if fname in hidden_menu_files:
            app_in_menu = False

        # Never force NoDisplay=true or Hidden=true desktop entries back into visible menu
        app_menu_categories = list(active_menu_cats.get(fname, [])) if app_in_menu else []
        app_primary_cat = data["primary_category"]
        if app_menu_categories:
            app_primary_cat = app_menu_categories[0]

        app_id = f"{scope}:{fname}"
        applications.append(
            {
                "id": app_id,
                "name": data["name"],
                "generic_name": data["generic_name"],
                "comment": data["comment"],
                "icon": data["icon"],
                "categories": data["categories"],
                "primary_category": app_primary_cat,
                "menu_categories": app_menu_categories,
                "in_menu": app_in_menu,
                "is_helper": is_helper,
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
                "package_vendor": package_vendor,
                "package_packager": package_packager,
                "repo_origin": repo_origin,
                "installed_size": installed_size,
                "uninstall_command": uninstall_command,
                "uninstall_note": uninstall_note,
                "is_override": is_override,
                "install_time": install_time,
                "install_date": install_date,
                "version_transition": version_transition,
                "update_action": update_action,
                "is_recent_update": is_recent_update,
                "last_used_time": last_used_time,
                "last_used_date": last_used_date,
                "launch_count": launch_count,
            }
        )

    # Add standalone AppImages in ~/Applications not having desktop files
    if os.path.exists(APPLICATIONS_DIR):
        non_app_exts = (".bak", ".txt", ".py", ".sh", ".json", ".log", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".md", ".yaml", ".yml", ".desktop", ".zip", ".tar", ".gz", ".xz")
        try:
            for item in sorted(os.listdir(APPLICATIONS_DIR)):
                app_path = os.path.join(APPLICATIONS_DIR, item)
                if not os.path.isfile(app_path):
                    continue
                if item.lower().endswith(non_app_exts):
                    continue
                real_app = os.path.realpath(app_path)
                if real_app in processed_appimage_paths:
                    continue

                if os.access(app_path, os.X_OK):
                    sz = os.path.getsize(app_path)
                    app_mtime = os.path.getmtime(app_path)
                    is_recent = bool(app_mtime and app_mtime >= recent_cutoff)
                    last_used_time, last_used_date, launch_count = get_app_usage_metrics(
                        package_name=item,
                        desktop_filename=item,
                        exec_cmd=app_path,
                    )
                    applications.append(
                        {
                            "id": f"standalone:{item}",
                            "name": item,
                            "generic_name": "Portable Application",
                            "comment": f"Standalone executable in {APPLICATIONS_DIR}",
                            "icon": "application-x-executable",
                            "categories": ["Utility"],
                            "primary_category": "Utilities",
                            "menu_categories": [],
                            "in_menu": False,
                            "is_helper": False,
                            "no_display": False,
                            "hidden": False,
                            "scope": "standalone",
                            "desktop_path": "",
                            "desktop_filename": "",
                            "exec_command": app_path,
                            "source_type": "appimage",
                            "source_label": "AppImage (Standalone)",
                            "package_name": item,
                            "package_version": "",
                            "package_arch": "",
                            "package_vendor": "",
                            "package_packager": "",
                            "repo_origin": "Standalone Binary",
                            "installed_size": format_bytes(sz),
                            "uninstall_command": f'rm -f "{app_path}"',
                            "uninstall_note": "Deletes the standalone application binary",
                            "is_override": False,
                            "install_time": app_mtime,
                            "install_date": format_timestamp(app_mtime),
                            "version_transition": "",
                            "update_action": "Updated" if is_recent else "",
                            "is_recent_update": is_recent,
                            "last_used_time": last_used_time,
                            "last_used_date": last_used_date,
                            "launch_count": launch_count,
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
        "helpers": sum(1 for a in applications if a.get("is_helper")),
        "repo_system": sum(1 for a in applications if a["source_type"].startswith("repo_")),
        "repo_rpm": sum(1 for a in applications if a["source_type"] == "repo_rpm"),
        "repo_pacman": sum(1 for a in applications if a["source_type"] == "repo_pacman"),
        "repo_apt": sum(1 for a in applications if a["source_type"] in ("repo_apt", "repo_dpkg")),
        "flatpak": sum(1 for a in applications if a["source_type"] == "flatpak"),
        "appimage": sum(1 for a in applications if a["source_type"] == "appimage"),
        "steam": sum(1 for a in applications if a["source_type"] == "steam"),
        "local_tool": sum(1 for a in applications if a["source_type"] == "local_tool"),
        "web_app": sum(1 for a in applications if a["source_type"] == "web_app"),
        "unmanaged": sum(1 for a in applications if a["source_type"] == "unmanaged"),
        "recently_updated": sum(1 for a in applications if a.get("is_recent_update")),
        "recent_hours": max(1, int(recent_hours)),
        "distro": distro_info,
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
