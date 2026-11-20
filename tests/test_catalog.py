"""The "will it run on this PC?" advice for a few real machines, and other catalog helpers."""
import unittest

from tests import _env  # noqa: F401

from app import catalog, hardware

# The two PCs Featherbox has been benchmarked on, plus a plain office laptop.
RTX3050 = {"backend": "cuda", "vram_mb": 4096, "ram_gb": 30.9, "cores": 8, "threads": 16, "igpu": None}
RYZEN_AI = {"backend": "vulkan", "vram_mb": 0, "ram_gb": 23.3, "cores": 8, "threads": 16,
            "igpu": {"name": "AMD Radeon(TM) 860M Graphics", "memory_mb": 512 + 11929, "shared_mb": 11929}}
CPU_ONLY = {"backend": "cpu", "vram_mb": 0, "ram_gb": 8, "cores": 4, "threads": 8, "igpu": None}

LEVELS = {"great", "slow", "very-slow", "too-big", "recommended", "other-backend", "soon"}


def fit(cid: str, hw: dict) -> tuple[str, str]:
    return catalog.fit_label(catalog.get(cid), hw)


class FitAdvice(unittest.TestCase):
    def test_every_component_gets_advice(self):
        for hw in (RTX3050, RYZEN_AI, CPU_ONLY):
            for c in catalog.components():
                level, reason = catalog.fit_label(c, hw)
                self.assertIn(level, LEVELS, c.id)
                self.assertTrue(reason, c.id)

    def test_rtx3050(self):
        self.assertEqual(fit("llm-qwen3-4b", RTX3050)[0], "great")
        self.assertEqual(fit("img-dreamshaper8", RTX3050)[0], "great")
        self.assertIn(fit("llm-gemma3-12b", RTX3050)[0], ("slow", "very-slow"))

    def test_builtin_graphics_are_used(self):
        level, reason = fit("llm-gemma3-1b", RYZEN_AI)
        self.assertEqual(level, "great")
        self.assertIn("built-in Radeon 860M", reason)
        self.assertNotIn("No suitable graphics card", reason)
        self.assertEqual(fit("img-dreamshaper8", RYZEN_AI)[0], "slow")
        level, reason = fit("vid-wan21-1.3b", RYZEN_AI)
        self.assertEqual(level, "very-slow")
        self.assertIn("hour", reason)

    def test_builtin_graphics_too_small(self):
        level, reason = fit("llm-qwen3-30b-a3b", RYZEN_AI)
        self.assertEqual(level, "very-slow")
        self.assertIn("built-in graphics' share of memory", reason)

    def test_cpu_only(self):
        level, reason = fit("llm-qwen3-4b", CPU_ONLY)
        self.assertIn(level, ("slow", "very-slow"))
        self.assertIn("No suitable graphics card", reason)
        self.assertEqual(fit("tts-kokoro", CPU_ONLY)[0], "great")

    def test_engine_for_other_hardware(self):
        self.assertEqual(fit("llama-cuda", RYZEN_AI)[0], "other-backend")
        self.assertEqual(fit("llama-vulkan", RYZEN_AI)[0], "recommended")

    def test_recommended_set_exists(self):
        ids = {c.id for c in catalog.components()}
        for hw in (RTX3050, RYZEN_AI, CPU_ONLY):
            for cid in catalog.recommended_set(hw):
                self.assertIn(cid, ids)


class CatalogEntries(unittest.TestCase):
    """Mistakes in a model entry that would only show up when someone downloads or runs it."""

    def test_ids_unique(self):
        ids = [c.id for c in catalog.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))

    def test_image_and_video_files_have_engine_flags(self):
        from app.engines.image import _FLAGS
        for c in catalog.CATALOG:
            if c.kind == "model" and c.category in ("image", "video"):
                roles = [f.role for f in c.files]
                for r in roles:
                    self.assertIn(r, _FLAGS, f"{c.id}: no command-line flag for role {r!r}")
                self.assertTrue({"model", "diffusion"} & set(roles), f"{c.id}: nothing to load as the model")
                for key in ("width", "height", "steps", "cfg", "vram_mb"):
                    self.assertIn(key, c.meta, f"{c.id}: meta lacks {key}")
                self.assertEqual(c.meta["width"] % 32, 0, c.id)
                self.assertEqual(c.meta["height"] % 32, 0, c.id)

    def test_text_models_have_memory_data(self):
        for c in catalog.CATALOG:
            if c.kind == "model" and c.category == "llm":
                self.assertIn("ctx", c.meta, c.id)
                self.assertGreater(c.meta.get("kv_mb_per_1k", 0), 0, c.id)

    def test_shared_files_agree(self):
        """A file shared by several models (same place on disk) must be the same download everywhere."""
        seen: dict[str, tuple[str, int, str]] = {}
        for c in catalog.CATALOG:
            for f in c.files:
                if f.extract_to:
                    continue
                key = (f.url, f.size_mb)
                if f.dest in seen:
                    self.assertEqual(seen[f.dest][:2], key, f"{f.dest} differs between {seen[f.dest][2]} and {c.id}")
                else:
                    seen[f.dest] = (*key, c.id)

    def test_files_are_downloadable_links(self):
        for c in catalog.CATALOG:
            for f in c.files:
                self.assertTrue(f.url.startswith("https://"), f"{c.id}: {f.url}")
                self.assertNotIn("/blob/", f.url, f"{c.id}: a page link, not a download link")
                self.assertGreater(f.size_mb, 0, f"{c.id}: {f.dest}")

    def test_new_models_reuse_shared_files(self):
        turbo, wan = catalog.get("vid-wan22-5b-turbo"), catalog.get("vid-wan22-5b")
        self.assertEqual({f.dest for f in turbo.files} - {f.dest for f in wan.files},
                         {"models/video/Wan2_2-TI2V-5B-Turbo-Q4_K_M.gguf"}, "only the Turbo model itself is extra")
        self.assertIn("--sigmas", catalog.get("img-qwen-image21-turbo").meta["extra"])
        self.assertNotIn("layers", catalog.get("llm-gemma4-e2b").meta, "Gemma 4 is fitted by llama.cpp")


class GraphicsNames(unittest.TestCase):
    def test_short_names(self):
        self.assertEqual(catalog._gpu_short("AMD Radeon(TM) 860M Graphics"), "Radeon 860M")
        self.assertEqual(catalog._gpu_short("Intel(R) Arc(TM) 140V GPU (16GB)"), "Arc 140V")

    def test_integrated_or_card(self):
        cases = {
            ("amd", "AMD Radeon(TM) 860M Graphics"): True,
            ("amd", "AMD Radeon(TM) Graphics"): True,
            ("amd", "AMD Radeon RX 7600S"): False,
            ("intel", "Intel(R) Arc(TM) 140V GPU (16GB)"): True,
            ("intel", "Intel(R) UHD Graphics"): True,
            ("intel", "Intel(R) Arc(TM) A770 Graphics"): False,
            ("intel", "Intel(R) Arc(TM) B580 Graphics"): False,
        }
        for (vendor, name), integrated in cases.items():
            self.assertEqual(hardware._is_integrated(vendor, name), integrated, name)


class HuggingFaceLinks(unittest.TestCase):
    def test_blob_link_becomes_download(self):
        url, name = catalog.normalize_hf_url("https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/blob/main/gemma-3-1b-it-Q4_K_M.gguf")
        self.assertEqual(url, "https://huggingface.co/unsloth/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf")
        self.assertEqual(name, "gemma-3-1b-it-Q4_K_M.gguf")

    def test_rejects_other_links(self):
        for bad in ("https://huggingface.co/unsloth/gemma-3-1b-it-GGUF", "https://example.com/a/b/blob/main/x.gguf",
                    "http://huggingface.co/a/b/blob/main/x.gguf"):
            with self.assertRaises(ValueError):
                catalog.normalize_hf_url(bad)


if __name__ == "__main__":
    unittest.main()
