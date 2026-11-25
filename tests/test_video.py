"""Studio video assembly: smooth camera moves, forward-only AI loops and crossfades between scenes.
The maths runs everywhere; the FFmpeg checks run only where FFmpeg is installed."""
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import _env  # noqa: F401

from app.engines import video

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class Camera(unittest.TestCase):
    def test_every_move_stays_inside_the_picture(self):
        size = (round(video.W * video.ZOOM_MAX), round(video.H * video.ZOOM_MAX))
        for idx in range(len(video.MOVES)):
            for zoom, x, y in video.camera(idx, 120):
                self.assertGreaterEqual(zoom, 1.0 - 1e-9)
                self.assertLessEqual(zoom, video.ZOOM_MAX + 1e-9)
                s, left, top = video.view(size, zoom, x, y)
                self.assertGreaterEqual(left, -1e-6)
                self.assertGreaterEqual(top, -1e-6)
                self.assertLessEqual(left + video.W * s, size[0] + 1e-6)
                self.assertLessEqual(top + video.H * s, size[1] + 1e-6)

    def test_moves_glide_without_jumps(self):
        """The old zoompan moved in whole-pixel steps every other frame. Now every frame moves a little,
        and no frame moves much more than its neighbours."""
        size = (round(video.W * video.ZOOM_MAX), round(video.H * video.ZOOM_MAX))
        for idx in range(len(video.MOVES)):
            cams = video.camera(idx, 240)
            lefts = [video.view(size, *c)[1] for c in cams]
            steps = [abs(b - a) for a, b in zip(lefts, lefts[1:])]
            if max(steps) < 1e-9:
                continue                      # a move with no sideways travel (straight push in or out)
            mid = steps[40:200]
            self.assertTrue(all(s > 0 for s in mid), f"move {idx} stalls mid-way")
            jerk = max(abs(b - a) for a, b in zip(steps, steps[1:]))
            self.assertLess(jerk, 0.05 * max(steps), f"move {idx} changes speed abruptly")

    def test_starts_and_ends_where_the_move_says(self):
        for idx, (start, end) in enumerate(video.MOVES):
            cams = video.camera(idx, 90)
            for got, want in ((cams[0], start), (cams[-1], end)):
                for g, w in zip(got, want):
                    self.assertAlmostEqual(g, w, places=6)

    def test_scenes_use_different_moves(self):
        self.assertNotEqual(video.camera(0, 10), video.camera(1, 10))


class Loops(unittest.TestCase):
    def test_loop_plan(self):
        blend, offset = video.loop_plan(2.0)
        self.assertEqual(blend, 0.5)
        self.assertEqual(offset, 1.0)              # loop = 1.5 s: plays 0.5→2.0 s, the start fades in over the end
        blend, offset = video.loop_plan(1.0)
        self.assertEqual(blend, 0.25)              # short clips blend for a quarter of their length
        self.assertEqual(offset, 0.5)


class Join(unittest.TestCase):
    def test_join_plan(self):
        offsets, keep, total = video.join_plan([5.0, 4.0, 6.0], fade=0.25)
        self.assertEqual(offsets, [4.75, 8.5])         # each fade starts 0.25 s before the scene ends
        self.assertEqual(keep, [4.75, 3.75, 6.0])      # the silent tail under each fade is dropped
        self.assertAlmostEqual(sum(keep), total)       # sound and picture are the same length
        self.assertEqual(total, 14.5)

    def test_fade_fits_in_the_pause_after_each_line(self):
        self.assertLess(video.FADE, 0.35)              # _voice pads every line with 0.35 s of silence

    def test_single_scene(self):
        self.assertEqual(video.join_plan([5.0]), ([], [5.0], 5.0))


@unittest.skipUnless(HAS_FFMPEG, "FFmpeg not installed")
class WithFFmpeg(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _wav(self, path: Path, seconds: float) -> Path:
        import numpy as np
        import soundfile as sf
        sr = 24000
        t = np.arange(int(sr * seconds)) / sr
        sf.write(path, (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr)
        return path

    def test_scenes_build_and_join(self):
        from PIL import Image
        img = self.tmp / "scene.png"
        Image.new("RGB", (576, 1024), (40, 90, 160)).save(img)
        segs = []
        for i, secs in enumerate((1.5, 1.2)):
            wav = self._wav(self.tmp / f"v{i}.wav", secs)
            segs.append(video.build_segment(img, wav, secs, "", self.tmp / f"seg{i}.mp4", idx=i, captions=False))
        total = video.concat(segs, self.tmp / "joined.mp4", [1.5, 1.2])
        self.assertAlmostEqual(total, 2.45)
        self.assertAlmostEqual(video.media_duration(self.tmp / "joined.mp4"), 2.45, delta=0.08)


if __name__ == "__main__":
    unittest.main()
