"""Voice names shown in the UI (no model needed)."""
import unittest

from tests import _env  # noqa: F401

from app.engines.tts import VOICE_LABELS, label_for


class VoiceLabels(unittest.TestCase):
    def test_curated_labels_win(self):
        for vid, label in VOICE_LABELS.items():
            self.assertEqual(label_for(vid), label)

    def test_built_from_id(self):
        self.assertEqual(label_for("af_alloy"), "Alloy — US female")
        self.assertEqual(label_for("bm_lewis"), "Lewis — UK male")
        self.assertEqual(label_for("am_santa"), "Santa — US male")
        self.assertEqual(label_for("jf_alpha"), "Alpha — Japanese female")

    def test_odd_ids_are_kept_readable(self):
        self.assertEqual(label_for("xx_custom_voice"), "Custom Voice")
        self.assertEqual(label_for("plainname"), "plainname")


if __name__ == "__main__":
    unittest.main()
