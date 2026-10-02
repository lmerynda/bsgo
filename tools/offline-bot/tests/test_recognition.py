"""Architecture tests: no game, no native input, no inference server."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from threading import Event
import time
import unittest
from uuid import uuid4

from bsgo_offline.recognition import HudRecognizer, RecognitionResult, RecognitionWorker
from bsgo_offline.replay import FrameSample, ReplaySource
from bsgo_offline.state import FieldValue, Snapshot, SnapshotStore


@contextmanager
def scratch_directory():
    # Python's Windows mode0700 TemporaryDirectory ACL excludes the sandbox
    # identity on this machine. Default mkdir permissions inherit workspace ACL.
    base = Path(__file__).resolve().parent
    directory = base / ("recognition-scratch-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        checked = directory.resolve()
        if checked.parent != base or not checked.name.startswith("recognition-scratch-"):
            raise RuntimeError("Unexpected test cleanup path")
        shutil.rmtree(checked)


class ListSource:
    def __init__(self, count=6, interval=.005, fail=False):
        self.count, self.interval, self.fail = count, interval, fail
        self.index = 0

    def next_sample(self, stop_event):
        if self.fail:
            raise OSError("injected source failure")
        if self.index >= self.count or stop_event.wait(self.interval):
            return None
        self.index += 1
        return FrameSample(Path("unused.png"), self.index, 1, "original", time.monotonic())


def read_sample(sample):
    return RecognitionResult((2560, 1440), {
        "speed": FieldValue(sample.acquisition_sequence, sample.acquired_at)})


def snapshot(sequence=1, value=None, acquired=1., published=2.):
    return Snapshot(sequence, 1, sequence, acquired, published, "original", (2560, 1440),
                    {"sample": FieldValue(value, acquired)})


class RecognitionTests(unittest.TestCase):
    def test_snapshots_defensively_freeze_nested_containers(self):
        original = {"boxes": [[1, 2]], "tags": {"asteroid"}}
        field = FieldValue(original, 1.)
        fields = {"sample": field}
        state = Snapshot(1, 1, 1, 1., 2., "original", [2560, 1440], fields)
        original["boxes"][0][0] = 99
        original["tags"].add("changed")
        fields.clear()
        self.assertEqual(state.fields["sample"].value["boxes"], ((1, 2),))
        self.assertEqual(state.fields["sample"].value["tags"], frozenset({"asteroid"}))
        with self.assertRaises(TypeError):
            state.fields["new"] = field
        with self.assertRaises(TypeError):
            state.fields["sample"].value["boxes"] = []
        with self.assertRaises(FrozenInstanceError):
            state.sequence = 2
        with self.assertRaises(TypeError):
            FieldValue(object(), 1.)

    def test_invalid_current_field_never_retains_historical_value(self):
        store = SnapshotStore()
        store.publish(snapshot(value=44))
        rejected = FieldValue(44, 3., False, "erased_speed")
        store.publish(Snapshot(2, 1, 2, 3., 4., "original", (2560, 1440), {"sample": rejected}))
        self.assertIsNone(store.latest().fields["sample"].value)
        self.assertEqual(store.latest().fields["sample"].reason, "erased_speed")

    def test_delayed_consumer_does_not_block_snapshot_progress(self):
        store = SnapshotStore()
        worker = RecognitionWorker(ListSource(count=20, interval=.005), read_sample, store)
        worker.start()
        first = store.wait_for_sequence(0, 1.)
        self.assertIsNotNone(first)
        # Simulates a consumer holding an immutable proposal input and doing
        # work while the producer independently consumes every remaining frame.
        self.assertTrue(worker.join(2.))
        latest = store.latest()
        self.assertEqual(latest.sequence, 20)
        self.assertGreater(latest.sequence, first.sequence)
        self.assertEqual(first.fields["speed"].value, first.acquisition_sequence)
        self.assertIsNone(worker.error)
        self.assertTrue(worker.source_exhausted)
        self.assertEqual(worker.end_reason, "source_exhausted")

    def test_publishing_does_not_refresh_observation_timestamp(self):
        store = SnapshotStore()
        worker = RecognitionWorker(ListSource(count=1),
            lambda sample: RecognitionResult((1, 1), {"speed": FieldValue(0, 1.)}),
            store, clock=lambda: 900.)
        worker.start()
        self.assertTrue(worker.join())
        self.assertEqual(store.latest().published_at, 900.)
        self.assertEqual(store.latest().fields["speed"].observed_at, 1.)

    def test_source_failure_publishes_unhealthy_unknowns(self):
        store = SnapshotStore()
        store.publish(snapshot(value=10))
        worker = RecognitionWorker(ListSource(fail=True), read_sample, store)
        worker.start()
        self.assertTrue(worker.join())
        state = store.latest()
        self.assertEqual(state.sequence, 2)
        self.assertFalse(state.healthy)
        self.assertIn("source_failure", state.reason)
        self.assertTrue(all(not field.valid and field.value is None for field in state.fields.values()))

    def test_recognizer_failure_does_not_republish_previous_values(self):
        store = SnapshotStore()
        def fail(sample):
            if sample.acquisition_sequence == 2:
                raise RuntimeError("injected recognizer failure")
            return read_sample(sample)
        worker = RecognitionWorker(ListSource(count=4), fail, store)
        worker.start()
        self.assertTrue(worker.join())
        self.assertEqual(worker.processed, 1)
        self.assertEqual(store.latest().acquisition_sequence, 2)
        self.assertFalse(store.latest().fields["speed"].valid)
        self.assertIn("recognizer_failure", worker.error)

    def test_wait_and_monotonic_publication(self):
        store = SnapshotStore()
        self.assertIsNone(store.wait_for_sequence(0, .001))
        state = snapshot()
        store.publish(state)
        self.assertIs(store.wait_for_sequence(0, 0), state)
        self.assertIsNone(store.wait_for_sequence(1, .001))
        with self.assertRaises(ValueError):
            store.publish(snapshot())

    def test_bounded_join_exposes_blocked_recognizer(self):
        entered, release = Event(), Event()
        def blocked(sample):
            entered.set()
            release.wait(2.)
            return read_sample(sample)
        worker = RecognitionWorker(ListSource(count=1), blocked, SnapshotStore())
        worker.start()
        self.assertTrue(entered.wait(1.))
        try:
            worker.stop()
            self.assertFalse(worker.join(.001))
        finally:
            release.set()
        self.assertTrue(worker.join(1.))
        self.assertEqual(worker.processed, 0)
        self.assertEqual(worker.end_reason, "cancelled")

    def test_stop_interrupts_long_replay_wait(self):
        with scratch_directory() as temp:
            path = self._manifest(temp, ["frame-2-001.png", "frame-2-002.png"])
            worker = RecognitionWorker(ReplaySource(path, interval=30), read_sample, SnapshotStore())
            worker.start()
            self.assertIsNotNone(worker.store.wait_for_sequence(0, 1.))
            worker.stop()
            self.assertTrue(worker.join(.5))
            self.assertEqual(worker.processed, 1)

    def _manifest(self, directory, sources):
        rows = [{"id": str(index), "kind": "real_capture", "source": name,
                 "source_run": "run", "file": f"images/{index}.png", "expected": {}}
                for index, name in enumerate(sources)]
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps({"version": 1, "rows": rows}))
        return path

    def test_replay_uses_original_source_order_and_synthetic_times(self):
        with scratch_directory() as temp:
            path = self._manifest(temp, ["frame-1000-010.png", "frame-1000-002.png",
                                         "frame-1000-001.png"])
            source = ReplaySource(path, interval=0, clock=lambda: 123.5)
            samples = [source.next_sample(Event()) for _ in range(3)]
            self.assertEqual([sample.source_id for sample in samples],
                             ["frame-1000-001.png", "frame-1000-002.png", "frame-1000-010.png"])
            self.assertEqual([sample.acquisition_sequence for sample in samples], [1, 2, 3])
            self.assertEqual([sample.acquired_at for sample in samples], [123.5] * 3)

    def test_replay_changes_epoch_at_run_boundary(self):
        with scratch_directory() as temp:
            path = self._manifest(temp, ["frame-1.png", "frame-2.png"])
            corpus = json.loads(path.read_text())
            corpus["rows"][1]["source_run"] = "run-b"
            path.write_text(json.dumps(corpus))
            source = ReplaySource(path, interval=0, session_epoch=8)
            self.assertEqual(source.next_sample(Event()).session_epoch, 8)
            self.assertEqual(source.next_sample(Event()).session_epoch, 9)

    def test_hud_adapter_preserves_rejected_field_evidence(self):
        root = Path(__file__).resolve().parents[3]
        corpus = root / "test-data" / "vision" / "v1"
        manifest = json.loads((corpus / "manifest.json").read_text())
        row = next(row for row in manifest["rows"] if "synthetic-absent-speed" in row["source"])
        sample = FrameSample(corpus / row["file"], 1, 1, "original", 45.)
        result = HudRecognizer()(sample)
        self.assertIsNone(result.fields["speed"].value)
        self.assertFalse(result.fields["speed"].valid)
        self.assertEqual(result.fields["speed"].observed_at, 45.)
        self.assertTrue(result.fields["ocr_evidence"].value["reasons"])

    def test_hud_adapter_never_auto_switches_layout(self):
        result = HudRecognizer()(FrameSample(Path("not-read.png"), 1, 1,
                                           "restart-20261001", 1.))
        self.assertFalse(result.healthy)
        self.assertEqual(result.reason, "layout_profile_mismatch")


if __name__ == "__main__":
    unittest.main()
