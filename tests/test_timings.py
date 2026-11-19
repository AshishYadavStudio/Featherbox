"""Durations measured on this PC, and the render time-left estimate built from them."""
import unittest

from tests import _env  # noqa: F401

from app import timings
from app.recipes.topic_to_short import remaining_time


class Timings(unittest.TestCase):
    def setUp(self):
        timings._FILE.unlink(missing_ok=True)

    def test_unknown_until_measured(self):
        self.assertIsNone(timings.typical("image:test"))

    def test_median_of_recent_runs(self):
        for s in (40, 10, 12, 11):
            timings.record("image:test", s)
        self.assertEqual(timings.typical("image:test"), 11.5)   # the slow cold start doesn't dominate

    def test_keeps_only_recent_runs(self):
        for s in range(1, 25):
            timings.record("k", s)
        self.assertEqual(len(timings._load()["k"]), timings.KEEP)
        self.assertEqual(timings.typical("k"), 19.5)            # median of 15..24

    def test_ignores_bad_values_and_bad_file(self):
        timings.record("k", 0)
        timings.record("k", -3)
        self.assertIsNone(timings.typical("k"))
        timings._FILE.write_text("not json", "utf-8")
        self.assertIsNone(timings.typical("k"))
        timings.record("k", 5)                                   # recovers from a broken file
        self.assertEqual(timings.typical("k"), 5)


class RemainingTime(unittest.TestCase):
    STILLS = [False] * 4

    def test_needs_every_part_measured(self):
        self.assertIsNone(remaining_time(self.STILLS, None, None, 2, None, 5))     # no picture time yet
        self.assertIsNone(remaining_time(self.STILLS, 10, None, None, None, 5))    # no edit time yet
        self.assertIsNone(remaining_time(self.STILLS, 10, None, 2, None, None))    # no assembly time yet

    def test_still_pictures(self):
        # 4 pictures x 10 s + 4 edits x 2 s + 5 s assembly
        self.assertEqual(remaining_time(self.STILLS, 10, None, 2, None, 5), 53)
        self.assertEqual(remaining_time(self.STILLS, 10, None, 2, None, 5, painted=3), 23)
        self.assertEqual(remaining_time(self.STILLS, 10, None, 2, None, 5, painted=4, edited=4), 5)

    def test_ai_clips(self):
        moving = [True, False, False]
        self.assertIsNone(remaining_time(moving, 10, None, 2, 3, 5))               # clip never timed
        # 3 x 10 + 1 clip x 150 + edits 3 + 2 + 2 + 5
        self.assertEqual(remaining_time(moving, 10, 150, 2, 3, 5), 192)
        # clip done: clip time no longer needed
        self.assertEqual(remaining_time(moving, 10, None, 2, 3, 5, painted=3, animated=1), 12)


if __name__ == "__main__":
    unittest.main()
