import json
from pathlib import Path
import unittest

from PIL import Image

from bsgo_offline.events import EventDetector
from bsgo_offline.reward_notice import read_reward_notice, read_reward_notice_crop
from bsgo_offline.state import Snapshot
from evaluate_reward_notices import evaluate
from test_trace import scratch_directory


CORPUS = Path(__file__).resolve().parents[3] / "test-data/vision/v3-reward-notices"


class RewardNoticeTests(unittest.TestCase):
    def crop(self, name="fire009-053"):
        with Image.open(CORPUS / (name+".png")) as image:
            return image.convert("RGB")

    def test_source_separated_evaluation_reports_accepts_and_real_rejects(self):
        report = evaluate(CORPUS / "manifest.json")
        self.assertEqual(report["counts"]["development"], dict(accepted_correct=2,
            accepted_wrong=0, rejected_present=2, rejected_absent=8, total=12))
        self.assertEqual(report["counts"]["evaluation"], dict(accepted_correct=1,
            accepted_wrong=0, rejected_present=1, rejected_absent=3, total=5))

    def test_supported_native_wrapper_requires_layout_and_native_size(self):
        full = Image.new("RGB", (2560,1440))
        full.paste(self.crop(), (1060,230))
        notice = read_reward_notice(full, profile="largehud-20261003")
        self.assertEqual((notice.resource, notice.amount), ("Water",1237))
        self.assertIsNone(read_reward_notice(full, profile="original").amount)
        self.assertIsNone(read_reward_notice(self.crop(), profile="largehud-20261003").amount)
        self.assertIsNone(read_reward_notice_crop(self.crop().resize((880,160))).amount)

    def test_missing_digit_resource_syntax_header_and_extra_ink_stay_unknown(self):
        for box in ((124,40,144,80), (242,40,275,80), (96,40,113,80),
                    (0,0,440,40)):
            damaged = self.crop()
            damaged.paste((0,0,0), box)
            self.assertIsNone(read_reward_notice_crop(damaged).amount)
        damaged = self.crop()
        damaged.paste((255,255,255), (355,45,365,70))
        self.assertIsNone(read_reward_notice_crop(damaged).amount)

    def test_unsupported_digit_is_not_nearest_template_guess(self):
        damaged = self.crop()
        # Replace the measured first digit with a different glyph raster.
        damaged.paste((0,0,0), (124,40,144,80))
        damaged.paste((255,255,255), (127,45,143,48))
        damaged.paste((255,255,255), (127,67,143,70))
        damaged.paste((255,255,255), (127,45,130,70))
        damaged.paste((255,255,255), (140,45,143,70))
        self.assertIsNone(read_reward_notice_crop(damaged).amount)

    def test_field_retains_source_time_and_duplicate_notices_never_emit_reward(self):
        notice = read_reward_notice_crop(self.crop())
        detector = EventDetector()
        for sequence in (1,2,3):
            field = notice.as_field(float(sequence))
            self.assertTrue(field.valid)
            self.assertEqual(field.observed_at,float(sequence))
            self.assertFalse(field.value["attributed"])
            snapshot = Snapshot(sequence,1,sequence,float(sequence),float(sequence),
                "largehud-20261003",(2560,1440),{"reward_notice":field})
            self.assertFalse(detector.update(snapshot))
        unknown = read_reward_notice_crop(self.crop("fire009-051")).as_field(4.)
        self.assertFalse(unknown.valid)
        self.assertIsNone(unknown.value)

    def test_corpus_hash_tampering_and_source_run_split_leakage_are_rejected(self):
        for change in ("hash", "split", "path"):
            with scratch_directory() as directory:
                manifest = json.loads((CORPUS/"manifest.json").read_text())
                for row in manifest["rows"]:
                    (directory/row["file"]).write_bytes((CORPUS/row["file"]).read_bytes())
                if change == "hash":
                    manifest["rows"][0]["sha256"] = "0"*64
                elif change == "split":
                    manifest["rows"][1]["split"] = "evaluation"
                else:
                    manifest["rows"][0]["file"] = "../escaped.png"
                path = directory/"manifest.json"
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    evaluate(path)


if __name__ == "__main__":
    unittest.main()
