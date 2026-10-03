"""Stationary scan orchestration through fake pixels and input delivery only."""
from threading import Event, Lock, Thread
import time
import unittest

from PIL import Image

from bsgo_offline.native_capture import WindowGeometry
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.scan_probe import run_scan_probe
from bsgo_offline.state import FieldValue
from test_trace import scratch_directory


PROFILE = "restart-20261001"


class PixelSample:
    def __init__(self, sequence, at, values, *, epoch=1):
        self.acquisition_sequence = sequence
        self.session_epoch = epoch
        self.layout = PROFILE
        self.acquired_at = at
        self.capture_started_at = at
        self.capture_completed_at = at + .001
        self.frame_produced_at = at + .001
        self.geometry = WindowGeometry(17, 42, "fixture", (4, 3), (0, 0))
        self.width, self.height = (4, 3)
        self.rgb = bytes([sequence % 256] * 36)
        self.values = values

    def to_image(self):
        return Image.frombytes("RGB", (4, 3), self.rgb)


class PixelSource:
    def __init__(self, *, change_after=None, changed=None, stall_after=None,
                 invalid_first=False, epoch_after=None):
        self.sequence = 0
        self.closed = False
        self.change_after = change_after
        self.changed = changed or {}
        self.stall_after = stall_after
        self.invalid_first = invalid_first
        self.epoch_after = epoch_after
        self.values = {"speed": 0., "asteroid_selected": True, "distance": 500,
                       "target_max_hp": 3900, "target_identity": "asteroid:hp:3900"}

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(.025):
            return None
        self.sequence += 1
        values = dict(self.values)
        if self.change_after is not None and self.sequence >= self.change_after:
            values.update(self.changed)
        if self.invalid_first and self.sequence == 1:
            values["distance"] = None
        return PixelSample(self.sequence, time.monotonic(), values,
                           epoch=2 if self.epoch_after is not None
                           and self.sequence >= self.epoch_after else 1)

    def close(self):
        self.closed = True


def recognize(sample):
    return RecognitionResult((4, 3), {name: FieldValue(value, sample.acquired_at,
        valid=value is not None, reason="fixture_rejection" if value is None else None)
        for name, value in sample.values.items()})


class FakeDispatcher:
    def __init__(self, backend, *, ignore=False):
        self.backend, self.ignore = backend, ignore
        self._stop, self._lock = Event(), Lock()
        self._thread = None
        self._deliveries = []
        self._held = set()
        self.error = None

    def start(self):
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            request = self.backend.get_action(timeout=.01)
            if request is None:
                continue
            permitted = (request.kind == "key_up" and request.key in self._held)
            permitted = permitted or self.backend.delivery_allowed(request)
            permitted = permitted and not self.ignore
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and request.kind == "key_down":
                self._held.add(request.key)
            elif permitted and request.kind == "key_up":
                self._held.discard(request.key)
            record = DeliveryRecord(request.sequence, time.monotonic(), request.kind,
                outcome, 1, 1 if permitted else 0, 0)
            with self._lock:
                self._deliveries.append(record)
            self.backend.report_delivery(request, outcome)

    def deliveries(self):
        with self._lock:
            return tuple(self._deliveries)

    def stop(self, timeout):
        self._stop.set()
        self.backend.cancel(reason="fake_dispatcher_stop")
        self._thread.join(timeout)
        return not self._thread.is_alive() and not self._held


class ScanProbeTests(unittest.TestCase):
    def run_probe(self, directory, source, *, ignore=False):
        dispatchers = []
        def dispatcher_factory(backend, journal):
            dispatcher = FakeDispatcher(backend, ignore=ignore)
            dispatchers.append(dispatcher)
            return dispatcher
        result = run_scan_probe(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognize, dispatcher_factory=dispatcher_factory,
            configuration={"fixture": True}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3),
            safe_scene_assessed=True, scope="synthetic", baseline_seconds=.3,
            after_seconds=.17)
        return result, dispatchers

    def test_complete_package_records_one_scan_but_never_claims_effect(self):
        with scratch_directory() as directory:
            source = PixelSource()
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "recorded", result)
            self.assertEqual(result["reason"], "scan_effect_unverified")
            self.assertFalse(result["scan_effect_verified"])
            self.assertFalse(result["mining_ready"])
            self.assertEqual(len(result["baseline"]), 2)
            self.assertGreaterEqual(len(result["after"]), 2)
            self.assertEqual([r["key"] for r in result["scan_requests"]], ["Z", "Z"])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 2)
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()))
            self.assertTrue(source.closed)
            # The capture worker may publish one final frame while shutdown is
            # requested; the diagnostic package must retain that frame too.
            from json import loads
            report = loads((directory / "run" / "report.json").read_text())
            self.assertEqual(len(list((directory / "run" / "frames").glob("*.png"))),
                             report["frames"]["written"])
            self.assertGreaterEqual(report["frames"]["written"],
                                    len(result["baseline"]) + len(result["after"]))

    def test_invalid_baseline_never_sends_scan_and_retains_rejection(self):
        with scratch_directory() as directory:
            source = PixelSource(invalid_first=True)
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["scan_requests"], [])
            self.assertTrue(source.closed)
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.png"))), 1)
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)

    def test_target_change_after_request_blocks_result(self):
        with scratch_directory() as directory:
            source = PixelSource(change_after=3, changed={"target_max_hp": 4100})
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("selected_target_changed", result["reason"])
            self.assertEqual(len(result["scan_requests"]), 2)
            self.assertFalse(result["mining_ready"])
            self.assertTrue(source.closed)

    def test_stationary_distance_change_blocks_result(self):
        with scratch_directory() as directory:
            source = PixelSource(change_after=3, changed={"distance": 520})
            result, _ = self.run_probe(directory, source)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("stationary_distance_changed", result["reason"])
            self.assertFalse(result["scan_effect_verified"])

    def test_capture_context_change_blocks_result(self):
        with scratch_directory() as directory:
            source = PixelSource(epoch_after=3)
            result, _ = self.run_probe(directory, source)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("observation_context_changed", result["reason"])
            self.assertFalse(result["scan_effect_verified"])

    def test_ignored_input_cannot_record_scan_experiment_as_delivered(self):
        with scratch_directory() as directory:
            source = PixelSource()
            result, dispatchers = self.run_probe(directory, source, ignore=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("scan_input_not_delivered", result["reason"])
            self.assertTrue(all(d.outcome != "injected" for d in dispatchers[0].deliveries()))
            self.assertTrue(source.closed)

    def test_stalled_capture_after_baseline_is_blocked_and_shutdown(self):
        with scratch_directory() as directory:
            source = PixelSource(stall_after=2)
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("post_scan_images_missing", result["reason"])
            self.assertEqual(len(dispatchers), 1)
            self.assertTrue(all(result["shutdown"].values()))
            self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
