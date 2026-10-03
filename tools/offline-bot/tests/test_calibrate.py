"""Active-calibration orchestration against fake pixels and fake delivery only."""
from threading import Event, Lock, Thread
import time
import unittest
from unittest.mock import patch

from PIL import Image

from bsgo_offline.calibrate import run_calibration
from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.native_capture import WindowGeometry
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_trace import scratch_directory


PROFILE = "restart-20261001"


class MotionModel:
    def __init__(self):
        self._lock = Lock()
        self._speed = 0.

    def read(self):
        with self._lock:
            return self._speed

    def set(self, value):
        with self._lock:
            self._speed = value


class PixelSample:
    def __init__(self, sequence, at, speed):
        self.acquisition_sequence = sequence
        self.session_epoch = 1
        self.layout = PROFILE
        self.acquired_at = at
        self.capture_started_at = at
        self.capture_completed_at = at + .001
        self.frame_produced_at = at + .001
        self.geometry = WindowGeometry(17, 42, "fixture", (4, 3), (0, 0))
        self.width, self.height = (4, 3)
        self.rgb = bytes([17] * 36)
        self.speed = speed

    def to_image(self):
        return Image.frombytes("RGB", (4, 3), self.rgb)


class PixelSource:
    def __init__(self, model, *, stall_after=None, interval=.05):
        self.model, self.stall_after, self.interval = model, stall_after, interval
        self.sequence = 0
        self.closed = False

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(self.interval):
            return None
        self.sequence += 1
        return PixelSample(self.sequence, time.monotonic(), self.model.read())

    def close(self):
        self.closed = True


def recognize(sample):
    return RecognitionResult((4, 3), {"speed": FieldValue(sample.speed, sample.acquired_at)})


class FakeDispatcher:
    def __init__(self, backend, model, *, ignore_acceleration=False, ignore_brake=False):
        self.backend, self.model = backend, model
        self.ignore_acceleration, self.ignore_brake = ignore_acceleration, ignore_brake
        self._lock, self._stop = Lock(), Event()
        self._thread = None
        self._deliveries = []
        self._held = set()
        self.error = None

    def start(self):
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            request = self.backend.get_action(timeout=.01)
            if request is None:
                continue
            # Matching key releases are allowed after a delivered down, as in
            # NativeInputDispatcher, even if the live speed guard has changed.
            permitted = (request.kind == "key_up" and request.key in self._held)
            permitted = permitted or self.backend.delivery_allowed(request)
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and request.kind == "key_down":
                self._held.add(request.key)
                if request.key == "PageUp" and not self.ignore_acceleration:
                    self.model.set(3.)
                if request.key == "PageDown" and not self.ignore_brake:
                    self.model.set(0.)
            elif permitted and request.kind == "key_up":
                self._held.discard(request.key)
            delivery = DeliveryRecord(request.sequence, time.monotonic(), request.kind,
                outcome, 1, 1 if permitted else 0, 0)
            with self._lock:
                self._deliveries.append(delivery)
            self.backend.report_delivery(request, outcome)

    def deliveries(self):
        with self._lock:
            return tuple(self._deliveries)

    def stop(self, timeout):
        self._stop.set()
        self.backend.cancel(reason="fake_dispatcher_stop")
        self._thread.join(timeout)
        return not self._thread.is_alive() and not self._held


class CalibrateTests(unittest.TestCase):
    def run_fixture(self, directory, *, ignore_acceleration=False, ignore_brake=False,
                    stall_after=None):
        model = MotionModel()
        source = PixelSource(model, stall_after=stall_after)
        dispatchers = []
        def dispatcher_factory(backend, journal):
            instance = FakeDispatcher(backend, model,
                ignore_acceleration=ignore_acceleration, ignore_brake=ignore_brake)
            dispatchers.append(instance)
            return instance
        result = run_calibration(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognize, dispatcher_factory=dispatcher_factory,
            configuration={"fixture": True, "profile": PROFILE}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3), clear_heading=True,
            pulse_seconds=.2, baseline_seconds=.8, stop_seconds=.8)
        return result, source, dispatchers

    def test_motion_and_brake_are_proved_only_after_image_feedback(self):
        with scratch_directory() as directory:
            result, source, dispatchers = self.run_fixture(directory)
            self.assertEqual(result["state"], "proved", result)
            self.assertTrue(result["final_stop_observed"])
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()), result["shutdown"])
            self.assertTrue(source.closed)
            self.assertEqual(result["certificate"]["baseline_observation_sequences"], [1, 2])
            self.assertGreater(result["certificate"]["moving_speed"], 0)
            self.assertEqual(result["certificate"]["stopped_speed"], [0., 0.])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 4)

    def test_inserted_input_without_motion_does_not_prove_control(self):
        with scratch_directory() as directory:
            result, source, dispatchers = self.run_fixture(directory, ignore_acceleration=True)
            self.assertEqual(result["state"], "blocked", result)
            self.assertIsNone(result["certificate"])
            self.assertTrue(result["final_stop_observed"])
            self.assertTrue(source.closed)
            self.assertTrue(any(d.outcome == "injected" for d in dispatchers[0].deliveries()))
            self.assertEqual(result["shutdown"]["dispatcher"], True)

    def test_watchdog_sends_brake_when_recognition_stalls_after_acceleration(self):
        with scratch_directory() as directory:
            result, source, dispatchers = self.run_fixture(directory, stall_after=2)
            self.assertNotEqual(result["state"], "proved")
            self.assertIsNone(result["certificate"])
            self.assertFalse(result["final_stop_observed"])
            self.assertTrue(source.closed)
            self.assertTrue(any(d.kind == "key_down" and d.outcome == "injected"
                                for d in dispatchers[0].deliveries()[2:]), result)
            self.assertEqual(result["shutdown"]["watchdog"], True)
            self.assertEqual(result["shutdown"]["dispatcher"], True)

    def test_ignored_brake_never_creates_a_stop_certificate(self):
        with scratch_directory() as directory:
            result, source, dispatchers = self.run_fixture(directory, ignore_brake=True)
            self.assertNotEqual(result["state"], "proved")
            self.assertIsNone(result["certificate"])
            self.assertFalse(result["final_stop_observed"])
            self.assertTrue(source.closed)
            self.assertTrue(any(d.request_sequence == 4 and d.outcome == "injected"
                                for d in dispatchers[0].deliveries()))

    def test_cleanup_enqueue_failure_retries_without_claiming_proof(self):
        original = RecordingInputBackend.request_cleanup_stop
        attempts = []
        def fail_once(backend, *args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("fixture cleanup enqueue failure")
            return original(backend, *args, **kwargs)
        with scratch_directory() as directory:
            with patch.object(RecordingInputBackend, "request_cleanup_stop", fail_once):
                result, source, dispatchers = self.run_fixture(directory)
            self.assertGreaterEqual(len(attempts), 2)
            self.assertEqual(result["state"], "blocked")
            self.assertIsNone(result["certificate"])
            self.assertIn("cleanup_request_failure", result["cleanup_failure"])
            self.assertTrue(source.closed)
            self.assertTrue(result["shutdown"]["dispatcher"])
            self.assertTrue(any(d.request_sequence == 4 for d in dispatchers[0].deliveries()))


if __name__ == "__main__":
    unittest.main()
