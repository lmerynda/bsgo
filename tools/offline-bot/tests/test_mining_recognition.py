import fixture_data  # noqa: F401  restores packed test images first
from pathlib import Path
from threading import Event
import unittest

from bsgo_offline.events import EventDetector
from bsgo_offline.mining_recognition import MiningHudRecognizer, MINING_FIELDS
from bsgo_offline.replay import ReplaySource
from bsgo_offline.state import Snapshot


class MiningRecognitionTests(unittest.TestCase):
    def test_saved_real_pixels_never_fill_unsupported_mining_fields(self):
        root = Path(__file__).resolve().parents[3]
        source = ReplaySource(root / "test-data/vision/v1/manifest.json",
                              source_run="sol-tylium-live-2321", interval=0, limit=3)
        recognizer, detector = MiningHudRecognizer(), EventDetector()
        for name in (*MINING_FIELDS, "owned", "selection_revision"):
            self.assertIs(recognizer.capabilities[name], False)
        for index in range(1, 4):
            sample = source.next_sample(Event())
            result = recognizer(sample)
            self.assertTrue(result.fields["distance"].valid)
            for name in MINING_FIELDS:
                self.assertFalse(result.fields[name].valid)
                self.assertIsNone(result.fields[name].value)
            snapshot = Snapshot(index, sample.session_epoch, sample.acquisition_sequence,
                sample.acquired_at, sample.acquired_at, sample.layout, result.frame_size, result.fields)
            self.assertFalse(any(e.kind in ("scan_result", "resource_change") for e in detector.update(snapshot)))


if __name__ == "__main__": unittest.main()
