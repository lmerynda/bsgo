"""Supervised approach seam tests; no native window or game calls."""
from threading import Event, Lock, Thread
import inspect
import json
import math
import time
import unittest
from unittest.mock import patch

from PIL import Image

from bsgo_offline.approach_probe import run_approach_probe
from bsgo_offline.native_capture import WindowGeometry
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_trace import scratch_directory


PROFILE = "largehud-20261003"


class Ship:
    def __init__(self, brake_decay_seconds=0.):
        self.lock = Lock()
        self.speed = 0.
        self.distance = 1155.
        self.brake_decay_seconds = brake_decay_seconds
        self.brake_due = None

    def sample(self):
        with self.lock:
            if self.brake_due is not None and time.monotonic() >= self.brake_due:
                self.speed = 0.
                self.brake_due = None
            if self.speed > 0:
                self.distance -= 5.
            return self.speed, self.distance

    def set_speed(self, value):
        with self.lock:
            if value == 0 and self.speed > 0 and self.brake_decay_seconds:
                self.brake_due = time.monotonic() + self.brake_decay_seconds
            else:
                self.speed = value


class PixelSample:
    def __init__(self, sequence, at, speed, distance, *, selected=True, epoch=1):
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
        self.speed, self.distance, self.selected = speed, distance, selected

    def to_image(self):
        return Image.frombytes("RGB", (4, 3), self.rgb)


class PixelSource:
    def __init__(self, ship, *, stall_after=None, change_at=None, invalid_first=False,
                 cancel_at=None, cancel_path=None):
        self.ship, self.stall_after = ship, stall_after
        self.change_at, self.invalid_first = change_at, invalid_first
        self.cancel_at, self.cancel_path = cancel_at, cancel_path
        self.sequence = 0
        self.closed = False

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(.025):
            return None
        self.sequence += 1
        if self.cancel_at == self.sequence:
            self.cancel_path.touch()
        speed, distance = self.ship.sample()
        if self.invalid_first and self.sequence == 1:
            speed = None
        return PixelSample(self.sequence, time.monotonic(), speed, distance,
            selected=self.change_at is None or self.sequence < self.change_at)

    def close(self):
        self.closed = True


def recognize(sample):
    values = {"speed": sample.speed, "distance": sample.distance,
              "asteroid_selected": sample.selected,
              "target_identity": "asteroid:hp:3900",
              "target_portrait": tuple([3] * 144), "target_max_hp": 3900}
    return RecognitionResult((4, 3), {name: FieldValue(value, sample.acquired_at,
        valid=value is not None, reason="fixture_rejection" if value is None else None)
        for name, value in values.items()})


class FakeDispatcher:
    def __init__(self, backend, ship, *, ignore_acceleration=False,
                 cancel_on_acceleration=None):
        self.backend, self.ship = backend, ship
        self.ignore_acceleration = ignore_acceleration
        self.cancel_on_acceleration = cancel_on_acceleration
        self.stop_event, self.lock = Event(), Lock()
        self.thread = None
        self.rows = []
        self.held = set()
        self.error = None

    def start(self):
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            record = self.backend.get_action(timeout=.01)
            if record is None:
                continue
            permitted = (record.kind == "key_up" and record.key in self.held)
            permitted = permitted or self.backend.delivery_allowed(record)
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and record.kind == "key_down":
                self.held.add(record.key)
                if record.key == "PageUp" and not self.ignore_acceleration:
                    self.ship.set_speed(20.)
                    if self.cancel_on_acceleration:
                        self.cancel_on_acceleration.touch()
                elif record.key == "PageDown":
                    self.ship.set_speed(0.)
            elif permitted and record.kind == "key_up":
                self.held.discard(record.key)
            row = DeliveryRecord(record.sequence, time.monotonic(), record.kind,
                outcome, 1, 1 if permitted else 0, 0)
            with self.lock:
                self.rows.append(row)
            self.backend.report_delivery(record, outcome)

    def deliveries(self):
        with self.lock:
            return tuple(self.rows)

    def stop(self, timeout):
        self.stop_event.set()
        self.backend.cancel(reason="fixture_stop")
        self.thread.join(timeout)
        return not self.thread.is_alive() and not self.held


class ApproachProbeTests(unittest.TestCase):
    def run_fixture(self, directory, *, source_options=None, ignore_acceleration=False,
                    max_thrust_seconds=3., stop_seconds=.8,
                    brake_decay_seconds=0., cancel_file=None,
                    cancel_on_acceleration=False):
        ship = Ship(brake_decay_seconds)
        source = PixelSource(ship, **(source_options or {}))
        dispatchers = []
        def dispatcher_factory(backend, journal):
            dispatcher = FakeDispatcher(backend, ship,
                ignore_acceleration=ignore_acceleration,
                cancel_on_acceleration=cancel_file if cancel_on_acceleration else None)
            dispatchers.append(dispatcher)
            return dispatcher
        result = run_approach_probe(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognize,
            dispatcher_factory=dispatcher_factory,
            configuration={"fixture": True}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3),
            clear_corridor_assessed=True, aligned_heading_assessed=True,
            safe_stopped_scene_assessed=True, scope="synthetic",
            baseline_seconds=.3, max_thrust_seconds=max_thrust_seconds,
            stop_seconds=stop_seconds, decision_interval=.1,
            cancel_file=cancel_file)
        return result, ship, source, dispatchers

    def test_threshold_approach_requires_image_closure_and_fresh_stop(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory)
            self.assertEqual(result["state"], "proved", result)
            self.assertTrue(result["final_stop_observed"])
            self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
            self.assertEqual(len(result["baseline"]), 2)
            self.assertGreater(len(result["motion"]), 2)
            self.assertLess(min(row["distance"] for row in result["motion"]), 1155)
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 4)
            self.assertFalse(result["mining_ready"])
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()))
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

    def test_invalid_baseline_never_requests_acceleration(self):
        with scratch_directory() as directory:
            result, _, source, dispatchers = self.run_fixture(directory,
                source_options={"invalid_first": True})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["acceleration_requests"], [])
            self.assertTrue(source.closed)
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)

    def test_target_loss_requests_brake_and_blocks_proof(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                source_options={"change_at": 5})
            self.assertEqual(result["state"], "blocked")
            self.assertTrue(result["brake_requests"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            self.assertEqual(len(dispatchers), 1)

    def test_thrust_limit_stops_even_if_input_never_moves_ship(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                ignore_acceleration=True, max_thrust_seconds=.3)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["brake_reason"], "thrust_limit")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

    def test_recognition_stall_gets_independent_brake_and_no_false_stop(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                source_options={"stall_after": 2}, max_thrust_seconds=.3,
                stop_seconds=.4)
            self.assertEqual(result["state"], "blocked")
            self.assertTrue(result["brake_requests"])
            self.assertFalse(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            self.assertTrue(all(result["shutdown"].values()))

    def test_delayed_coastdown_is_observed_with_sufficient_stop_budget(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                max_thrust_seconds=.3, stop_seconds=.9, brake_decay_seconds=.55)
            self.assertEqual(result["brake_reason"], "thrust_limit")
            self.assertTrue(result["final_stop_observed"], result)
            self.assertEqual(len(result["stop_observation_sequences"]), 2)
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
        with scratch_directory() as directory:
            result, _, source, _ = self.run_fixture(directory,
                max_thrust_seconds=.3, stop_seconds=.2, brake_decay_seconds=.55)
            self.assertFalse(result["final_stop_observed"])
            self.assertIn("stop_not_freshly_observed", result["reason"])
            self.assertTrue(source.closed)

    def test_native_stop_budget_defaults_and_bounds(self):
        signature = inspect.signature(run_approach_probe)
        self.assertEqual(signature.parameters["stop_seconds"].default, 35.)
        self.assertEqual(signature.parameters["max_thrust_seconds"].default, 12.)
        with scratch_directory() as directory:
            with self.assertRaisesRegex(ValueError, "max_thrust_seconds"):
                run_approach_probe(directory / "unused", source_factory=lambda: None,
                    recognizer_factory=lambda: None,
                    dispatcher_factory=lambda backend, journal: None,
                    configuration={}, profile=PROFILE, expected_window_id="42:17",
                    frame_size=(4, 3), clear_corridor_assessed=True,
                    aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
                    max_thrust_seconds=180.1)
        with scratch_directory() as directory:
            with self.assertRaisesRegex(ValueError, "stop_seconds"):
                run_approach_probe(directory / "unused", source_factory=lambda: None,
                    recognizer_factory=lambda: None,
                    dispatcher_factory=lambda backend, journal: None,
                    configuration={}, profile=PROFILE, expected_window_id="42:17",
                    frame_size=(4, 3), clear_corridor_assessed=True,
                    aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
                    stop_seconds=45.1)

    def test_long_transit_still_brakes_promptly_on_target_loss(self):
        with scratch_directory() as directory:
            started = time.monotonic()
            result, ship, source, _ = self.run_fixture(directory,
                max_thrust_seconds=180., source_options={"change_at": 5})
            self.assertLess(time.monotonic() - started, 2.)
            self.assertEqual(result["state"], "blocked")
            self.assertTrue(result["brake_requests"])
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            report = json.loads((directory / "run" / "report.json").read_text())
            self.assertGreaterEqual(report["frames"]["max_frames"],
                math.ceil((.3 + 180 + .8 + 8) / .4))

    def test_existing_cancel_marker_prevents_all_input(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            result, ship, source, dispatchers = self.run_fixture(directory,
                cancel_file=marker)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertTrue(result["cancel_requested"])
            self.assertEqual(result["acceleration_requests"], [])
            self.assertEqual(result["brake_requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(marker.exists())  # A stop request persists.
            self.assertTrue(all(result["shutdown"].values()))

    def test_cancel_marker_during_baseline_prevents_acceleration(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, ship, _, dispatchers = self.run_fixture(directory,
                cancel_file=marker,
                source_options={"cancel_at": 2, "cancel_path": marker})
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["acceleration_requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(marker.exists())

    def test_cancel_during_long_thrust_brakes_and_observes_stop_without_proof(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            started = time.monotonic()
            result, ship, source, dispatchers = self.run_fixture(directory,
                max_thrust_seconds=180., cancel_file=marker,
                cancel_on_acceleration=True)
            self.assertLess(time.monotonic() - started, 1.)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["brake_reason"], "cancel_requested")
            self.assertTrue(result["final_stop_observed"])
            self.assertGreaterEqual(len(result["stop_observation_sequences"]), 2)
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()],
                             ["injected"] * 4)
            self.assertEqual(ship.speed, 0)
            self.assertTrue(marker.exists())
            self.assertTrue(source.closed)
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()))

    def test_dispatcher_shutdown_failure_still_joins_capture_worker(self):
        def fail_stop(dispatcher, timeout):
            raise OSError("fixture stop failure")
        with scratch_directory() as directory:
            with patch.object(FakeDispatcher, "stop", fail_stop):
                result, _, source, _ = self.run_fixture(directory,
                    ignore_acceleration=True, max_thrust_seconds=.3)
            self.assertEqual(result["state"], "blocked")
            self.assertFalse(result["shutdown"]["dispatcher"])
            self.assertTrue(result["shutdown"]["recognition"])
            self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
