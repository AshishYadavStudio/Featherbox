"""The benchmark tool's log parsing, accuracy score and report."""
import json
import unittest

from tests import _env  # noqa: F401
from tests._env import ROOT

import featherbox_bench as bench

# Lines as llama-server writes them (from the Ryzen AI 7 350 run).
LOG = """\
load_tensors: offloaded 27/27 layers to GPU
ggml_vulkan: 0 = AMD Radeon(TM) 860M Graphics (AMD proprietary driver) | uma: 1 | fp16: 1
slot print_timing: id  1 | task 340 | prompt eval time =     120.10 ms /    40 tokens (    3.00 ms per token,   333.06 tokens per second)
slot print_timing: id  1 | task 340 |        eval time =    3853.20 ms /   162 tokens (   23.79 ms per token,    42.04 tokens per second)
slot print_timing: id  1 | task 1587 | prompt eval time =     387.56 ms /   423 tokens (    0.92 ms per token,  1091.44 tokens per second)
slot print_timing: id  1 | task 1587 |        eval time =   20383.67 ms /   781 tokens (   26.13 ms per token,    38.27 tokens per second)
"""


class LlamaLog(unittest.TestCase):
    def test_last_request_wins(self):
        out = bench.parse_llama_log(LOG)
        self.assertEqual(out["gen_tokens"], 781)
        self.assertEqual(out["gen_tok_s"], 38.27)               # generation, not prompt speed
        self.assertEqual(out["prompt_tok_s"], 1091.44)
        self.assertEqual(out["gpu_layers"], "27/27")
        self.assertEqual(out["device"], "AMD Radeon(TM) 860M Graphics")

    def test_cuda_device(self):
        out = bench.parse_llama_log("llama_model_load_from_file_impl: using device CUDA0 (NVIDIA GeForce RTX 3050 Laptop GPU) - 3726 MiB free\n")
        self.assertEqual(out["device"], "CUDA0 NVIDIA GeForce RTX 3050 Laptop GPU")

    def test_newer_log_device_id(self):  # what the current llama.cpp prints at normal verbosity
        line = ("resolve_fused_ops: layer 0 is assigned to device Vulkan0 but Flash Attention over K/V rows is "
                "assigned to device CPU (usually due to missing support)\n")
        self.assertEqual(bench.parse_llama_log(line)["device"], "Vulkan0")

    def test_empty_log(self):
        self.assertEqual(bench.parse_llama_log(""), {})


class WordAccuracy(unittest.TestCase):
    def test_scores(self):
        self.assertEqual(bench.word_accuracy("the sky is blue", "The sky is blue."), 100.0)
        self.assertEqual(bench.word_accuracy("the sky is blue", "the sky was blue"), 75.0)
        self.assertEqual(bench.word_accuracy("the sky", ""), 0.0)
        self.assertEqual(bench.word_accuracy("", "anything"), 0.0)
        self.assertEqual(bench.word_accuracy("a b", "x y z w v"), 0.0)       # never below zero


class Report(unittest.TestCase):
    def test_reference_report_renders(self):
        ref = json.loads((ROOT / "tools/benchmark/reference-rtx3050.json").read_text("utf-8"))
        md = bench.render_markdown(ref, ref)
        self.assertIn("## Results", md)
        for r in ref["results"]:
            self.assertIn(r["name"], md)


if __name__ == "__main__":
    unittest.main()
