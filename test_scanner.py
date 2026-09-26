"""
Unit and integration tests for AppIndex
"""

import os
import unittest
from scanner import scan_all_applications, parse_desktop_file, extract_executable
from fastapi.testclient import TestClient
from server import app


class TestScanner(unittest.TestCase):
    def test_scan_returns_results(self):
        result = scan_all_applications()
        self.assertIn("stats", result)
        self.assertIn("applications", result)
        stats = result["stats"]
        apps = result["applications"]

        self.assertGreater(stats["total"], 0)
        self.assertGreater(stats["in_menu"], 0)
        self.assertGreater(stats["repo_rpm"], 0)
        self.assertGreater(stats["repo_system"], 0)
        self.assertIn("distro", stats)
        self.assertGreater(stats["flatpak"], 0)
        self.assertGreater(stats["local_tool"], 0)

        # Check fields of applications
        sample = apps[0]
        required_fields = [
            "id", "name", "source_type", "source_label",
            "in_menu", "package_name", "uninstall_command"
        ]
        for field in required_fields:
            self.assertIn(field, sample)

    def test_distribution_info(self):
        from scanner import get_distribution_info
        info = get_distribution_info()
        self.assertIn("name", info)
        self.assertIn("pkg_manager", info)
        self.assertIn("pkg_manager_type", info)
        self.assertIn("uninstall_prefix", info)

    def test_active_desktop_environments(self):
        from scanner import get_active_desktop_environments
        desktops = get_active_desktop_environments()
        self.assertIsInstance(desktops, set)
        self.assertGreater(len(desktops), 0)

    def test_parse_desktop_file(self):
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".desktop", delete=False) as f:
            f.write("[Desktop Entry]\nType=Application\nName=SampleApp\nExec=sample-app %u\nCategories=Utility;\n")
            temp_path = f.name
        try:
            parsed = parse_desktop_file(temp_path)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed["name"], "SampleApp")
            self.assertIn("sample-app", parsed["exec_cmd"])
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_extract_executable(self):
        self.assertEqual(extract_executable("smplayer %U"), "smplayer")
        self.assertEqual(extract_executable('env FOO=bar "/usr/bin/test" arg'), "/usr/bin/test")
        self.assertEqual(extract_executable(""), None)


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_get_apps_endpoint(self):
        response = self.client.get("/api/apps")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("stats", data)
        self.assertIn("applications", data)

    def test_get_icon_endpoint(self):
        response = self.client.get("/api/icon?name=system-search")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/", response.headers.get("content-type", ""))

    def test_get_fallback_icon(self):
        response = self.client.get("/api/icon?name=non_existent_icon_xyz_123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type"), "image/svg+xml")

    def test_icon_path_traversal_blocked(self):
        # Attempt to access sensitive system files via absolute or relative path
        for attack_path in ["/etc/passwd", "/etc/shadow", "../../../../etc/passwd", "../../../etc/shadow", "system-search/../../../../etc/passwd"]:
            response = self.client.get(f"/api/icon?path={attack_path}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("content-type"), "image/svg+xml")
            self.assertNotIn("root:", response.text)

            response_name = self.client.get(f"/api/icon?name={attack_path}")
            self.assertEqual(response_name.status_code, 200)
            self.assertEqual(response_name.headers.get("content-type"), "image/svg+xml")
            self.assertNotIn("root:", response_name.text)

    def test_desktop_content_path_traversal_blocked(self):
        # Attempt to read non-desktop files or files outside allowed application directories
        for attack_path in ["/etc/passwd", "/usr/share/applications/../../../../etc/passwd", "/var/log/syslog"]:
            response = self.client.get(f"/api/desktop-content?path={attack_path}")
            self.assertEqual(response.status_code, 403)

        # Non-existent desktop file inside authorized directory returns 404
        response_missing = self.client.get("/api/desktop-content?path=/usr/share/applications/non_existent_app_12345.desktop")
        self.assertEqual(response_missing.status_code, 404)

    def test_flatpak_icons_resolve(self):
        # Verify Flatpak application icon lookup
        from server import resolve_icon_path
        for icon_name in ["org.qbittorrent.qBittorrent", "com.github.tchx84.Flatseal", "io.github.kolunmi.Bazaar"]:
            path = resolve_icon_path(icon_name)
            self.assertIsNotNone(path, f"Failed to resolve Flatpak icon {icon_name}")
            self.assertTrue(os.path.isfile(path))

    def test_export_endpoint(self):
        response = self.client.get("/api/export?format=csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers.get("content-type", ""))
        self.assertIn("package_name", response.text)

    def test_export_csv_custom_fields(self):
        response = self.client.get("/api/export?format=csv&fields=name,package_name")
        self.assertEqual(response.status_code, 200)
        first_line = response.text.splitlines()[0]
        self.assertEqual(first_line.strip(), "name,package_name")

    def test_export_json_custom_fields(self):
        response = self.client.get("/api/export?format=json&fields=name,installed_size")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("applications", data)
        if len(data["applications"]) > 0:
            app_keys = set(data["applications"][0].keys())
            self.assertEqual(app_keys, {"name", "installed_size"})

    def test_export_source_filter(self):
        response = self.client.get("/api/export?format=csv&sources=flatpak")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers.get("content-type", ""))

    def test_export_visibility_filter(self):
        response = self.client.get("/api/export?format=csv&visibility=in_menu")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers.get("content-type", ""))

    def test_favicon_endpoint(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/", response.headers.get("content-type", ""))

    def test_status_endpoint(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertIn("version", data)

    def test_check_update_endpoint(self):
        response = self.client.get("/api/check-update")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("has_update", data)
        self.assertIn("latest_version", data)
        self.assertIn("release_url", data)
        self.assertIn("current_version", data)
        self.assertIn("check_enabled", data)

    def test_apply_update_endpoint(self):
        from unittest.mock import patch
        with patch("server.apply_self_update") as mock_apply, \
             patch("server.trigger_server_restart") as mock_restart:
            mock_apply.return_value = {"mode": "git", "message": "Updated via git pull", "tag": "v0.4.0"}
            response = self.client.post("/api/apply-update")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data.get("status"), "restarting")
            self.assertEqual(data.get("mode"), "git")
            mock_apply.assert_called_once()
            mock_restart.assert_called_once()


class TestSelfUpdater(unittest.TestCase):
    def test_safe_archive_extraction(self):
        import io
        import os
        import tarfile
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            tar_path = os.path.join(temp_dir, "sample.tar.gz")
            with tarfile.open(tar_path, "w:gz") as tar:
                content = b"print('updated')\n"
                info = tarfile.TarInfo(name="AppIndex-0.4.0/test_file.py")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))

            dest_dir = os.path.join(temp_dir, "extracted")
            os.makedirs(dest_dir, exist_ok=True)
            with tarfile.open(tar_path, "r:gz") as tar:
                if hasattr(tarfile, "data_filter"):
                    tar.extractall(path=dest_dir, filter="data")
                else:
                    tar.extractall(path=dest_dir)

            extracted_file = os.path.join(dest_dir, "AppIndex-0.4.0", "test_file.py")
            self.assertTrue(os.path.isfile(extracted_file))
            with open(extracted_file, "r") as f:
                self.assertIn("updated", f.read())


class TestVersionComparison(unittest.TestCase):
    def test_version_parsing(self):
        from server import parse_version_tuple, is_newer_version
        self.assertEqual(parse_version_tuple("0.2.4"), (0, 2, 4))
        self.assertEqual(parse_version_tuple("v0.2.5"), (0, 2, 5))
        self.assertEqual(parse_version_tuple("v1.0.0-rc1"), (1, 0, 0))

        self.assertTrue(is_newer_version("0.2.5", "0.2.4"))
        self.assertTrue(is_newer_version("v1.0.0", "0.9.9"))
        self.assertFalse(is_newer_version("0.2.4", "0.2.4"))
        self.assertFalse(is_newer_version("0.2.3", "0.2.4"))


if __name__ == "__main__":
    unittest.main()
