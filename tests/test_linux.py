"""Linux support, checked on any system: the Linux engine catalog, unpacking engine archives, and graphics
detection from a fake /sys/class/drm tree."""
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests import _env  # noqa: F401
from tests._env import ROOT

from app import catalog, config, downloads, hardware


def safe(slot: str) -> str:
    """PCI addresses contain ':', which Windows doesn't allow in folder names."""
    return slot.replace(":", "_") if os.name == "nt" else slot


def linux_catalog() -> dict:
    """Load the catalog as it is on Linux, in a separate Python (the platform is fixed at import)."""
    code = ("import json, sys; sys.path.insert(0, 'backend'); from app import catalog, config; "
            "e = [c for c in catalog.CATALOG if c.kind == 'engine']; "
            "print(json.dumps({'platform': config.PLATFORM, 'exe': config.EXE, 'cuda_min': config.CUDA_MIN, "
            "'engines': {c.id: [f.url for f in c.files] for c in e}, "
            "'image_on_cuda': catalog.engine_for('image', 'cuda'), 'llm_on_cuda': catalog.engine_for('llm', 'cuda'), "
            "'stt_on_cuda': catalog.engine_for('stt', 'cuda'), "
            "'setup': catalog.recommended_set({'backend': 'cuda', 'vram_mb': 8192, 'ram_gb': 32}), "
            "'sd_vulkan_fit': catalog.fit_label(catalog.get('sd-vulkan'), {'backend': 'cuda'})[0]}))")
    env = dict(os.environ, FEATHERBOX_PLATFORM="linux")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


class LinuxCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lx = linux_catalog()

    def test_linux_builds_only(self):
        self.assertEqual(self.lx["platform"], "linux")
        self.assertEqual(self.lx["exe"], "")
        for cid, urls in self.lx["engines"].items():
            for url in urls:
                self.assertNotRegex(url, r"win(64)?[-.]|win-cuda|-x64\.zip$|\.exe$", f"{cid}: Windows build {url}")

    def test_same_engine_ids_minus_missing_builds(self):
        windows = {c.id for c in catalog.CATALOG if c.kind == "engine"} if config.PLATFORM == "windows" else None
        linux = set(self.lx["engines"])
        self.assertEqual(linux, {"llama-cuda", "llama-vulkan", "llama-cpu", "sd-vulkan", "sd-cpu", "whisper-cpu", "ffmpeg"})
        if windows:
            self.assertEqual(windows - linux, {"sd-cuda", "whisper-cuda"}, "no Linux CUDA builds of these exist")

    def test_runs_on_ubuntu_24_04(self):
        """Engines built on Ubuntu 26.04 need glibc 2.43 and fail to start on 24.04 LTS (seen in testing)."""
        for cid in ("sd-vulkan", "sd-cpu"):
            for url in self.lx["engines"][cid]:
                self.assertIn("Ubuntu-24.04", url, f"{cid} must be a build made on Ubuntu 24.04")

    def test_nvidia_on_linux(self):
        self.assertEqual(self.lx["llm_on_cuda"], "llama-cuda")
        self.assertEqual(self.lx["image_on_cuda"], "sd-vulkan", "pictures fall back to Vulkan")
        self.assertEqual(self.lx["stt_on_cuda"], "whisper-cpu")
        self.assertIn("sd-vulkan", self.lx["setup"])
        self.assertNotIn("sd-cuda", self.lx["setup"])
        self.assertEqual(self.lx["sd_vulkan_fit"], "recommended")
        self.assertEqual(self.lx["cuda_min"], 12.8)


class Unpacking(unittest.TestCase):
    def test_tar_and_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tar = tmp / "engine.tar.gz"
            with tarfile.open(tar, "w:gz") as tf:
                data = b"#!/bin/sh\necho hi\n"
                info = tarfile.TarInfo("build/bin/llama-server")
                info.size, info.mode = len(data), 0o755
                tf.addfile(info, io.BytesIO(data))
            downloads.extract(tar, tmp / "a")
            self.assertTrue((tmp / "a/build/bin/llama-server").is_file())
            z = tmp / "engine.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("sd-cli", b"x")
            downloads.extract(z, tmp / "b")
            self.assertTrue((tmp / "b/sd-cli").is_file())

    def test_tar_cannot_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tar = tmp / "evil.tar.gz"
            with tarfile.open(tar, "w:gz") as tf:
                info = tarfile.TarInfo("../outside.txt")
                info.size = 1
                tf.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(Exception):
                downloads.extract(tar, tmp / "out")
            self.assertFalse((tmp / "outside.txt").exists())

    def test_exe_names(self):
        with mock.patch.object(config, "EXE", ""):
            self.assertEqual(catalog.exe_name("llama-server"), "llama-server")
        with mock.patch.object(config, "EXE", ".exe"):
            self.assertEqual(catalog.exe_name("llama-server"), "llama-server.exe")


class LinuxGraphics(unittest.TestCase):
    def fake_drm(self, root: Path, cards: dict) -> None:
        for card, (vendor, slot, vram_mb) in cards.items():
            dev = root / "devices" / safe(slot)
            dev.mkdir(parents=True)
            (dev / "vendor").write_text(vendor + "\n")
            (dev / "uevent").write_text(f"DRIVER=amdgpu\nPCI_SLOT_NAME={slot}\n")
            if vram_mb is not None:
                (dev / "mem_info_vram_total").write_text(str(vram_mb * 1024 * 1024))
            (root / "drm" / card).mkdir(parents=True)
            target = root / "drm" / card / "device"
            try:
                target.symlink_to(dev, target_is_directory=True)
            except OSError:      # Windows without symlink rights: a plain folder works except for .resolve()
                target.mkdir()
                for f in dev.iterdir():
                    (target / f.name).write_text(f.read_text())
        (root / "drm" / "card0-HDMI-A-1").mkdir(parents=True)

    def detect(self, cards: dict, lspci: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_drm(root, cards)

            def run(cmd, timeout=8):
                slot = cmd[-1]
                name = next((v for k, v in lspci.items() if safe(k) in safe(slot)), None)
                return f'c4:00.0 "VGA compatible controller" "Vendor" "{name}" -r01 "Lenovo" "Device"' if name else ""
            with mock.patch.object(hardware.shutil, "which", return_value="/usr/bin/lspci" if lspci else None), \
                    mock.patch.object(hardware, "_run", side_effect=run):
                return hardware._linux_gpus(str(root / "drm"))

    def test_ryzen_ai_laptop(self):
        gpus = self.detect({"card1": ("0x1002", "0000:c4:00.0", 512)}, {"c4:00.0": "Krackan [Radeon 860M]"})
        self.assertEqual(gpus, [{"vendor": "amd", "name": "AMD Radeon 860M", "integrated": True, "reserved_mb": 512}])

    def test_amd_card_and_nvidia(self):
        gpus = self.detect({"card0": ("0x10de", "0000:01:00.0", None), "card1": ("0x1002", "0000:03:00.0", 8176)},
                           {"03:00.0": "Navi 33 [Radeon RX 7600/7600 XT/7600M XT/7600S/7700S / PRO W7600]"})
        self.assertEqual(len(gpus), 1, "NVIDIA comes from nvidia-smi, not from here")
        self.assertFalse(gpus[0]["integrated"])
        self.assertEqual(gpus[0]["vram_mb"], 8176)

    def test_intel_without_lspci(self):
        gpus = self.detect({"card0": ("0x8086", "0000:00:02.0", None)}, {})
        self.assertEqual(gpus[0]["name"], "Intel graphics")
        self.assertTrue(gpus[0]["integrated"])


if __name__ == "__main__":
    unittest.main()
