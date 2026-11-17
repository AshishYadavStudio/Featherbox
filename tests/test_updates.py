"""Update check: version comparison and reading GitHub's release (no network: the request is faked)."""
import unittest
from unittest import mock

from tests import _env  # noqa: F401

from app import config, updates

RELEASE = {
    "tag_name": "v9.1.0", "name": "Featherbox 9.1.0", "body": "Notes", "published_at": "2026-11-15T19:28:21Z",
    "html_url": "https://github.com/AshishYadavStudio/Featherbox/releases/tag/v9.1.0",
    "assets": [
        {"name": "notes.txt", "browser_download_url": "https://example.invalid/notes.txt", "size": 1},
        {"name": "Featherbox-Setup-9.1.0.exe", "size": 82354688,
         "browser_download_url": "https://github.com/AshishYadavStudio/Featherbox/releases/download/v9.1.0/Featherbox-Setup-9.1.0.exe",
         "digest": "sha256:9b835613acc9fd1d7234a74b56c0eda210868062c080e4555b3429e81605691c"},
    ],
}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Versions(unittest.TestCase):
    def test_order(self):
        v = updates._vtuple
        self.assertLess(v("0.3.1"), v("0.3.2"))
        self.assertLess(v("0.3.9"), v("0.10.0"))       # numbers, not text
        self.assertLess(v("0.9.9"), v("1.0.0"))
        self.assertEqual(v("v0.3.1"), v("0.3.1"))
        self.assertEqual(v("0.3.1-beta"), v("0.3.1"))
        self.assertEqual(v("garbage"), (0,))


class Check(unittest.TestCase):
    def setUp(self):
        self.saved = dict(updates.state)

    def tearDown(self):
        updates.state.clear()
        updates.state.update(self.saved)

    def test_reads_release(self):
        with mock.patch.object(updates.httpx, "get", return_value=FakeResponse(RELEASE)) as get:
            st = updates.check(force=True)
        self.assertIn("User-Agent", get.call_args.kwargs["headers"])
        latest = updates.state["latest"]
        self.assertEqual(latest["version"], "9.1.0")
        self.assertEqual(latest["file"], "Featherbox-Setup-9.1.0.exe")         # the .exe, not the first asset
        self.assertEqual(latest["sha256"], RELEASE["assets"][1]["digest"].removeprefix("sha256:"))
        self.assertEqual(latest["size"], 82354688)
        self.assertTrue(st["available"])
        self.assertEqual(st["latest"]["version"], "9.1.0")

    def test_same_version_is_not_offered(self):
        rel = dict(RELEASE, tag_name="v" + config.APP_VERSION)
        with mock.patch.object(updates.httpx, "get", return_value=FakeResponse(rel)):
            st = updates.check(force=True)
        self.assertFalse(st["available"])
        self.assertIsNone(st["latest"])
        self.assertEqual(st["newest_seen"], config.APP_VERSION)

    def test_offline_is_quiet(self):
        with mock.patch.object(updates.httpx, "get", side_effect=updates.httpx.ConnectError("offline")):
            st = updates.check(force=True)
        self.assertEqual(st["state"], "idle")
        self.assertIn("Couldn't check", st["error"])

    def test_source_copy_cannot_install(self):
        with mock.patch.object(updates.httpx, "get", return_value=FakeResponse(RELEASE)):
            updates.check(force=True)
        with mock.patch.object(updates, "install_root", return_value=None):
            st = updates.status()
            self.assertFalse(st["can_install"])
            self.assertIn("runs from source", st["reason"])
            with self.assertRaises(RuntimeError):
                updates.start_install()


if __name__ == "__main__":
    unittest.main()
