"""
Unit and integration tests for AppIndex
"""

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


if __name__ == "__main__":
    unittest.main()
