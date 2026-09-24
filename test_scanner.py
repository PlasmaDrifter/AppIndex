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

    def test_parse_desktop_file(self):
        parsed = parse_desktop_file("/usr/share/applications/smplayer.desktop")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["name"], "SMPlayer")
        self.assertIn("smplayer", parsed["exec_cmd"])

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
        response = self.client.get("/api/icon?name=smplayer")
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

    def test_favicon_endpoint(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/", response.headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()
