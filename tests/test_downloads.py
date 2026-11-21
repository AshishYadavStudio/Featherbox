"""Pausing, resuming and discarding downloads (no network: files are faked in the throwaway data folder)."""
import unittest
from unittest import mock

from tests import _env  # noqa: F401

from app import config
from app.catalog import Component, FileSpec
from app.downloads import DownloadManager


def comp(cid: str, files: list[FileSpec]) -> Component:
    return Component(cid, "model", "llm", cid, "test", files, "MIT")


def write(rel: str, size: int) -> None:
    p = config.DATA / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


class Discard(unittest.TestCase):
    def setUp(self):
        self.dm = DownloadManager()

    def test_deletes_partial_and_unfinished_files(self):
        c = comp("t-partial", [
            FileSpec("u", "models/test/a.gguf", 1),                                  # half-fetched
            FileSpec("u", "models/test/b.bin", 1),                                   # fetched, install unfinished
            FileSpec("u", "bin/_dl/e.zip", 1, extract_to="bin/test-engine"),         # half-unpacked engine
        ])
        write("models/test/a.gguf.part", 1000)
        write("models/test/b.bin", 2000)
        write("bin/test-engine/engine.exe", 3000)
        with mock.patch("app.catalog.components", return_value=[c]):
            freed = self.dm.discard(c)
        self.assertEqual(freed, 6000)
        for rel in ("models/test/a.gguf.part", "models/test/b.bin", "bin/test-engine"):
            self.assertFalse((config.DATA / rel).exists(), rel)

    def test_keeps_files_another_installed_component_uses(self):
        shared = FileSpec("u", "models/test/shared.gguf", 1)
        other = comp("t-other", [shared])
        write("models/test/shared.gguf", 500)
        other.marker.parent.mkdir(parents=True, exist_ok=True)
        other.marker.write_text("ok")
        mine = comp("t-mine", [shared, FileSpec("u", "models/test/mine.gguf", 1)])
        write("models/test/mine.gguf.part", 700)
        try:
            with mock.patch("app.catalog.components", return_value=[other, mine]):
                freed = self.dm.discard(mine)
            self.assertEqual(freed, 700)
            self.assertTrue((config.DATA / "models/test/shared.gguf").exists())
        finally:
            other.marker.unlink(missing_ok=True)

    def test_keeps_files_another_running_download_is_fetching(self):
        """Two video models share the text encoder: discarding one must not delete the shared file the other
        is still writing (on Windows that delete fails and stopped the whole clean-up)."""
        shared = FileSpec("u", "models/test/encoder.gguf", 1)
        other = comp("t-running", [shared])
        mine = comp("t-mine2", [shared, FileSpec("u", "models/test/mine2.gguf", 1)])
        write("models/test/encoder.gguf.part", 400)
        write("models/test/mine2.gguf.part", 300)
        self.dm.tasks[other.id] = {"id": other.id, "status": "downloading", "done": 400, "total": 1000, "cancel": False}
        with mock.patch("app.catalog.components", return_value=[other, mine]):
            freed = self.dm.discard(mine)
        self.assertEqual(freed, 300)
        self.assertTrue((config.DATA / "models/test/encoder.gguf.part").exists())
        self.assertFalse((config.DATA / "models/test/mine2.gguf.part").exists())

    def test_a_locked_file_does_not_stop_the_clean_up(self):
        c = comp("t-locked", [FileSpec("u", "models/test/l1.gguf", 1), FileSpec("u", "models/test/l2.gguf", 1)])
        write("models/test/l1.gguf.part", 100)
        write("models/test/l2.gguf.part", 200)
        real_unlink = type(config.DATA).unlink

        def unlink(p, *a, **k):
            if p.name == "l1.gguf.part":
                raise PermissionError("in use")
            return real_unlink(p, *a, **k)
        with mock.patch("app.catalog.components", return_value=[c]), \
                mock.patch.object(type(config.DATA), "unlink", unlink):
            freed = self.dm.discard(c)
        self.assertEqual(freed, 200)
        self.assertFalse((config.DATA / "models/test/l2.gguf.part").exists())
        (config.DATA / "models/test/l1.gguf.part").unlink()

    def test_refuses_installed_components(self):
        c = comp("t-installed", [FileSpec("u", "models/test/done.gguf", 1)])
        write("models/test/done.gguf", 10)
        c.marker.parent.mkdir(parents=True, exist_ok=True)
        c.marker.write_text("ok")
        try:
            with self.assertRaises(ValueError):
                self.dm.discard(c)
            self.assertTrue((config.DATA / "models/test/done.gguf").exists())
        finally:
            c.marker.unlink(missing_ok=True)

    def test_clears_the_download_status(self):
        c = comp("t-status", [FileSpec("u", "models/test/s.gguf", 1)])
        self.dm.tasks[c.id] = {"id": c.id, "status": "cancelled", "done": 5, "total": 10, "cancel": True}
        with mock.patch("app.catalog.components", return_value=[c]):
            self.dm.discard(c)
        self.assertIsNone(self.dm.status(c.id))


class PauseResume(unittest.TestCase):
    def test_resume_while_still_pausing_keeps_the_same_download(self):
        dm = DownloadManager()
        c = comp("t-resume", [FileSpec("u", "models/test/r.gguf", 1)])
        t = {"id": c.id, "status": "downloading", "done": 5, "total": 10, "cancel": True}
        dm.tasks[c.id] = t
        self.assertIs(dm.start(c), t)          # no second download is started
        self.assertFalse(t["cancel"])          # and the pause is called off


if __name__ == "__main__":
    unittest.main()
