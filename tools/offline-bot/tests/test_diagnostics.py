import hashlib
import json
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch

from bsgo_offline.diagnostics import DiagnosticRun
from bsgo_offline.diagnose import run_preflight
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.recognition import RecognitionResult, RecognitionWorker
from bsgo_offline.state import FieldValue, SnapshotStore
from bsgo_offline.trace import iter_trace_events
from runtime_fixture import observation
from test_trace import scratch_directory


class NativeFixtureSource:
    def __init__(self, count=3):
        self.count, self.sequence = count, 0
        self.closed = False
    def next_sample(self, stop):
        if self.sequence >= self.count or stop.is_set():
            return None
        self.sequence += 1
        return NativeFrame(self.sequence, 1, "fixture", time.monotonic(), 4, 3, bytes([17] * 36),
            WindowGeometry(1, 2, "fixture", (4, 3), (-2000, 100)))
    def close(self):
        self.closed = True


def recognize_fixture(sample):
    return RecognitionResult(sample.frame_size, {"speed": FieldValue(0., sample.acquired_at)})


class DiagnosticTests(unittest.TestCase):
    def test_native_observation_history_pixels_and_live_status_are_retained(self):
        with scratch_directory() as directory:
            run_directory = directory / "run"
            report = run_preflight(run_directory, source_factory=NativeFixtureSource,
                recognizer_factory=lambda: recognize_fixture, configuration={"test": True}, snapshot_limit=3)
            self.assertTrue(report["result"]["capture_published"])
            self.assertTrue(report["complete_diagnostics"], report)
            self.assertEqual(report["input_requests"], [])
            self.assertIsNone(report["runtime"]["owned"])
            self.assertFalse(report["preflight"]["ready"])
            manifest = json.loads((run_directory / "manifest.json").read_text())
            self.assertIn("bsgo_offline/diagnose.py", manifest["source_sha256"])
            self.assertIn("hud-reader/restart-calibration.json", manifest["source_sha256"])
            self.assertIn("hud-reader/templates.json", manifest["source_sha256"])
            self.assertEqual(report["input_pending"], 0)
            events = list(iter_trace_events(run_directory / "trace", report["run_id"]))
            observations = [event["payload"] for event in events if event["payload"]["event"] == "observation"]
            self.assertEqual([event["snapshot"]["sequence"] for event in observations], [1, 2, 3])
            frames = [event["payload"] for event in events if event["payload"]["event"] == "frame_retained"]
            self.assertEqual(len(frames), 3)
            for frame in frames:
                path = run_directory / frame["path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frame["sha256"])
                self.assertEqual(frame["context"]["window_id"], "2:1")
            self.assertEqual(json.loads((run_directory / "status.json").read_text())["lifecycle"], "finalized")

    def test_startup_failure_preserves_manifest_report_and_zero_requests(self):
        with scratch_directory() as directory:
            def fail():
                raise OSError("fixture capture startup failure")
            report = run_preflight(directory / "run", source_factory=fail,
                recognizer_factory=lambda: recognize_fixture, configuration={})
            self.assertIn("capture startup failure", report["error"])
            self.assertEqual(report["input_requests"], [])
            self.assertTrue(all(report["shutdown"].values()))
            self.assertTrue(report["complete_diagnostics"])
            self.assertFalse(report["result"]["capture_published"])
            self.assertTrue((directory / "run" / "manifest.json").exists())
            self.assertTrue((directory / "run" / "report.json").exists())

    def test_manifest_exists_before_source_starts_and_existing_run_is_preserved(self):
        with scratch_directory() as directory:
            output = directory / "run"
            def source():
                self.assertTrue((output / "manifest.json").exists())
                return NativeFixtureSource(1)
            run_preflight(output, source_factory=source, recognizer_factory=lambda: recognize_fixture,
                          configuration={}, snapshot_limit=1)
            original = (output / "report.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "must_be_empty"):
                DiagnosticRun(output, mode="fixture", configuration={})
            self.assertEqual(original, (output / "report.json").read_bytes())

    def test_frame_backpressure_is_nonblocking_and_explicitly_incomplete(self):
        with scratch_directory() as directory:
            gate, entered = Event(), Event()
            source = NativeFixtureSource(8)
            original = NativeFrame.to_image
            def slow(frame):
                entered.set()
                gate.wait(2)
                return original(frame)
            with patch.object(NativeFrame, "to_image", slow):
                run = DiagnosticRun(directory / "run", mode="fixture", configuration={}, frame_capacity=1)
                try:
                    run.observation(observation(), (), source.next_sample(Event()), {})
                    self.assertTrue(entered.wait(1))
                    started = time.monotonic()
                    for sequence in range(2, 8):
                        run.observation(observation(sequence), (), source.next_sample(Event()), {})
                    self.assertLess(time.monotonic() - started, .5)
                    self.assertGreater(run.frame_stats()["dropped"], 0)
                finally:
                    gate.set()
                    report = run.close(shutdown={})
            self.assertFalse(report["complete_diagnostics"])

    def test_frame_write_failure_does_not_stop_recognition(self):
        with scratch_directory() as directory:
            with patch.object(NativeFrame, "to_image", side_effect=OSError("fixture disk failure")):
                report = run_preflight(directory / "run", source_factory=NativeFixtureSource,
                    recognizer_factory=lambda: recognize_fixture, configuration={}, snapshot_limit=3)
            self.assertEqual(report["result"]["observations_processed"], 3)
            self.assertIn("fixture disk failure", report["frames"]["error"])
            self.assertFalse(report["complete_diagnostics"])

    def test_observer_failure_is_counted_without_killing_worker(self):
        class FailedObserver:
            def observation(self, *args):
                raise OSError("fixture observer failure")
        worker = RecognitionWorker(NativeFixtureSource(), recognize_fixture, SnapshotStore(), diagnostics=FailedObserver())
        worker.start()
        self.assertTrue(worker.join(2))
        self.assertEqual(worker.processed, 3)
        self.assertEqual(worker.metrics()["diagnostic_dropped"], 3)
        self.assertEqual(worker.metrics()["stage"], "ended")
        self.assertGreaterEqual(worker.metrics()["last_durations_real_seconds"]["recognition"], 0)


if __name__ == "__main__":
    unittest.main()
