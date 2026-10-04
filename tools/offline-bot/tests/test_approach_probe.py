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
    def __init__(self, brake_decay_seconds=0., distance=1155.):
        self.lock = Lock()
        self.speed = 0.
        self.distance = distance
        self.brake_decay_seconds = brake_decay_seconds
        self.brake_due = None
        self.brake_requested = False

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
            if value == 0 and self.speed > 0:
                self.brake_requested = True
            if value == 0 and self.speed > 0 and self.brake_decay_seconds:
                self.brake_due = time.monotonic() + self.brake_decay_seconds
            else:
                self.speed = value


class PixelSample:
    def __init__(self, sequence, at, speed, distance, *, selected=True, epoch=1, hull=5005):
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
        self.hull = hull

    def to_image(self):
        return Image.frombytes("RGB", (4, 3), self.rgb)


class PixelSource:
    def __init__(self, ship, *, stall_after=None, change_at=None, invalid_first=False,
                 cancel_at=None, cancel_path=None, pause_after_baseline=0.,
                 damage_at=None, initial_hull=5005, damage_hull=4500,
                 damage_after_brake=False, target_loss_after_brake=False):
        self.ship, self.stall_after = ship, stall_after
        self.change_at, self.invalid_first = change_at, invalid_first
        self.cancel_at, self.cancel_path = cancel_at, cancel_path
        self.pause_after_baseline = pause_after_baseline
        self.damage_at, self.initial_hull = damage_at, initial_hull
        self.damage_hull = damage_hull
        self.damage_after_brake = damage_after_brake
        self.target_loss_after_brake = target_loss_after_brake
        self.sequence = 0
        self.closed = False

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(self.pause_after_baseline if self.sequence == 2 and
                     self.pause_after_baseline else .025):
            return None
        self.sequence += 1
        if self.cancel_at == self.sequence:
            self.cancel_path.touch()
        speed, distance = self.ship.sample()
        if self.invalid_first and self.sequence == 1:
            speed = None
        return PixelSample(self.sequence, time.monotonic(), speed, distance,
            selected=(self.change_at is None or self.sequence < self.change_at)
                and not (self.target_loss_after_brake and self.ship.brake_requested),
            hull=self.damage_hull if (self.damage_at and self.sequence >= self.damage_at
                or self.damage_after_brake and self.ship.brake_requested) else self.initial_hull)

    def close(self):
        self.closed = True


def recognize(sample):
    values = {"speed": sample.speed, "distance": sample.distance,
              "asteroid_selected": sample.selected,
              "target_identity": "asteroid:hp:3900",
              "target_portrait": tuple([3] * 144), "target_max_hp": 3900,
              "hull_current": sample.hull, "hull_max": 5005}
    return RecognitionResult((4, 3), {name: FieldValue(value, sample.acquired_at,
        valid=value is not None, reason="fixture_rejection" if value is None else None)
        for name, value in values.items()})


class FakeDispatcher:
    def __init__(self, backend, ship, *, ignore_acceleration=False,
                 cancel_on_acceleration=None, reduced_wheel_speed=5.,
                 ignore_transition_down=False, cancel_on_transition_down=None):
        self.backend, self.ship = backend, ship
        self.ignore_acceleration = ignore_acceleration
        self.cancel_on_acceleration = cancel_on_acceleration
        self.reduced_wheel_speed = reduced_wheel_speed
        self.ignore_transition_down = ignore_transition_down
        self.cancel_on_transition_down = cancel_on_transition_down
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
            if (self.ignore_transition_down and record.kind == "key_down"
                    and record.key == "PageDown"
                    and not dict(record.metadata).get("cleanup")):
                permitted = False
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and record.kind == "key_down":
                self.held.add(record.key)
                if record.key == "PageUp" and not self.ignore_acceleration:
                    self.ship.set_speed(20.)
                    if self.cancel_on_acceleration:
                        self.cancel_on_acceleration.touch()
                elif record.key == "PageDown":
                    self.ship.set_speed(0.)
                    if self.cancel_on_transition_down and not dict(record.metadata).get("cleanup"):
                        self.cancel_on_transition_down.touch()
            elif permitted and record.kind == "mouse_wheel" and self.reduced_wheel_speed is not None:
                self.ship.set_speed(self.reduced_wheel_speed)
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
                    cancel_on_acceleration=False, sample_interval=.4,
                    distance=1155., brake_at=900., launch_clearance=250.,
                    reduced_transition_at=None, reduced_wheel_speed=5.,
                    ignore_transition_down=False, cancel_on_transition_down=False,
                    source_factory=None):
        ship = Ship(brake_decay_seconds, distance)
        source = (source_factory(ship) if source_factory else
                  PixelSource(ship, **(source_options or {})))
        dispatchers = []
        def dispatcher_factory(backend, journal):
            dispatcher = FakeDispatcher(backend, ship,
                ignore_acceleration=ignore_acceleration,
                cancel_on_acceleration=cancel_file if cancel_on_acceleration else None,
                reduced_wheel_speed=reduced_wheel_speed,
                ignore_transition_down=ignore_transition_down,
                cancel_on_transition_down=(cancel_file if cancel_on_transition_down else None))
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
            cancel_file=cancel_file, sample_interval=sample_interval,
            brake_at=brake_at, launch_clearance=launch_clearance,
            reduced_transition_at=reduced_transition_at,
            wheel_point=(1, 1) if reduced_transition_at is not None else None,
            flight_point_assessed=reduced_transition_at is not None)
        return result, ship, source, dispatchers

    def test_short_approach_requires_explicit_launch_clearance(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                distance=880., brake_at=800., max_thrust_seconds=None)
            self.assertEqual(result["state"], "blocked", result)
            self.assertIn("Initial target too close", result["reason"])
            self.assertEqual(result["acceleration_requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                distance=880., brake_at=800., launch_clearance=75.,
                max_thrust_seconds=None, sample_interval=5.)
            self.assertEqual(result["state"], "proved", result)
            self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 4)
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            report = json.loads((directory / "run" / "report.json").read_text())
            manifest = json.loads((directory / "run" / "manifest.json").read_text())
            self.assertEqual(manifest["configuration"]["launch_clearance"], 75.)
            self.assertEqual(manifest["configuration"]["brake_at"], 800.)
            self.assertIsNone(report["frames"]["max_frames"])

    def test_short_approach_still_blocks_below_clearance_and_on_hull_loss(self):
        with scratch_directory() as directory:
            result, _, _, dispatchers = self.run_fixture(directory,
                distance=874., brake_at=800., launch_clearance=75.)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["acceleration_requests"], [])
            self.assertEqual(dispatchers, [])
        with scratch_directory() as directory:
            result, ship, _, _ = self.run_fixture(directory,
                distance=880., brake_at=800., launch_clearance=75.,
                max_thrust_seconds=None, source_options={"damage_at": 5})
            self.assertEqual(result["state"], "blocked", result)
            self.assertEqual(result["brake_reason"], "hull_guard")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_launch_clearance_rejects_invalid_configuration_before_capture(self):
        for value in (74.9, 250.1, True, math.nan, math.inf):
            with self.subTest(value=value), scratch_directory() as directory:
                with self.assertRaisesRegex(ValueError, "launch_clearance"):
                    self.run_fixture(directory, launch_clearance=value)

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
            self.assertEqual(result["final_stop_distance"], ship.distance)
            self.assertEqual(result["brake_start_observation"]["speed"], 20.)
            self.assertEqual(result["observed_braking_distance"],
                result["brake_start_observation"]["distance"] - ship.distance)
            self.assertTrue(result["braking_observations"])

    def test_invalid_baseline_never_requests_acceleration(self):
        with scratch_directory() as directory:
            result, _, source, dispatchers = self.run_fixture(directory,
                source_options={"invalid_first": True})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["acceleration_requests"], [])
            self.assertTrue(source.closed)
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)

    def test_damaged_or_unreadable_hull_cannot_authorize_acceleration(self):
        for hull in (4500, None):
            with self.subTest(hull=hull), scratch_directory() as directory:
                result, ship, source, dispatchers = self.run_fixture(directory,
                    max_thrust_seconds=None, source_options={"initial_hull": hull})
                self.assertEqual(result["state"], "blocked")
                self.assertIn("hull", result["reason"])
                self.assertEqual(result["acceleration_requests"], [])
                self.assertEqual(dispatchers, [])
                self.assertEqual(ship.speed, 0)
                self.assertTrue(source.closed)

    def test_flight_hull_loss_brakes_and_observes_stop_without_approach_proof(self):
        for hull, reason in ((4500, "Hull loss observed"),
                             (None, "Missing, invalid or stale hull_current")):
            with self.subTest(hull=hull), scratch_directory() as directory:
                result, ship, source, _ = self.run_fixture(directory,
                    max_thrust_seconds=None,
                    source_options={"damage_at": 5, "damage_hull": hull})
                self.assertEqual(result["state"], "blocked", result)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["brake_reason"], "hull_guard")
                self.assertTrue(result["final_stop_observed"])
                self.assertEqual(ship.speed, 0)
                self.assertTrue(result["complete_diagnostics"])
                self.assertTrue(all(result["shutdown"].values()))
                self.assertTrue(source.closed)

    def test_hull_loss_after_page_down_blocks_proof_but_still_verifies_stop(self):
        for hull, reason in ((4500, "Hull loss observed"),
                             (None, "Missing, invalid or stale hull_current")):
            with self.subTest(hull=hull), scratch_directory() as directory:
                result, ship, source, _ = self.run_fixture(directory,
                    distance=880., brake_at=800., launch_clearance=75.,
                    max_thrust_seconds=None, brake_decay_seconds=.15,
                    source_options={"damage_after_brake": True, "damage_hull": hull})
                self.assertEqual(result["state"], "blocked", result)
                self.assertEqual(result["reason"], reason)
                self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
                self.assertTrue(result["final_stop_observed"])
                self.assertEqual(ship.speed, 0)
                self.assertTrue(result["complete_diagnostics"])
                self.assertTrue(all(result["shutdown"].values()))
                self.assertTrue(source.closed)

    def test_target_loss_after_page_down_blocks_proof_but_keeps_speed_cleanup(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                distance=880., brake_at=800., launch_clearance=75.,
                max_thrust_seconds=None, brake_decay_seconds=.15,
                source_options={"target_loss_after_brake": True})
            self.assertEqual(result["state"], "blocked", result)
            self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
            self.assertTrue(result["final_stop_observed"])
            self.assertIsNone(result["final_stop_distance"])
            self.assertIsNone(result["observed_braking_distance"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            self.assertTrue(result["complete_diagnostics"])

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
                ignore_acceleration=True, max_thrust_seconds=.1,
                source_options={"stall_after": 2})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["brake_reason"], "thrust_limit")
            self.assertFalse(result["final_stop_observed"])
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
        self.assertIsNone(signature.parameters["max_thrust_seconds"].default)
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

    def test_uncapped_approach_finishes_by_distance_and_retains_all_frames(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory, max_thrust_seconds=None)
            self.assertEqual(result["state"], "proved", result)
            self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            report = json.loads((directory / "run" / "report.json").read_text())
            self.assertIsNone(report["frames"]["max_frames"])

    def test_uncapped_approach_detects_ignored_acceleration_from_images(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                max_thrust_seconds=None, ignore_acceleration=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("Acceleration not observed", result["reason"])
            self.assertEqual(result["brake_reason"], "image_evidence_rejected")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

    def test_five_second_sample_does_not_trigger_old_two_second_watchdog(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                max_thrust_seconds=None, sample_interval=5.,
                source_options={"pause_after_baseline": 5.})
            self.assertEqual(result["state"], "proved", result)
            self.assertIn(result["brake_reason"], ("threshold", "threshold_watchdog"))
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

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
                max_thrust_seconds=None, cancel_file=marker,
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

    def test_two_regime_transition_delivers_idle_then_one_wheel_and_stops_in_range(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                distance=1555., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.)
            self.assertEqual(result["state"], "proved", result["reason"])
            self.assertTrue(result["deceleration_observed"])
            self.assertGreaterEqual(len(result["reduced_speed_sequences"]), 3)
            self.assertTrue(700 <= result["final_stop_distance"] <= 800)
            self.assertEqual(result["brake_reason"], "threshold")
            self.assertEqual([d.kind for d in dispatchers[0].deliveries()],
                ["key_down", "key_up", "key_down", "key_up", "mouse_wheel",
                 "key_down", "key_up"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            self.assertTrue(result["complete_diagnostics"])

    def test_two_regime_rejects_near_launch_without_input(self):
        with scratch_directory() as directory:
            result, ship, _, dispatchers = self.run_fixture(directory,
                distance=1400., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("1550", result["reason"])
            self.assertEqual(dispatchers, [])
            self.assertEqual(ship.speed, 0)

    def test_two_regime_rejected_transition_down_never_delivers_wheel(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                distance=1555., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.,
                ignore_transition_down=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertNotIn("injected", [d.outcome for d in dispatchers[0].deliveries()
                if d.kind == "mouse_wheel"])
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_two_regime_cancel_between_idle_and_wheel_blocks_wheel(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, ship, source, dispatchers = self.run_fixture(directory,
                distance=1555., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.,
                cancel_file=marker, cancel_on_transition_down=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertTrue(marker.exists())
            self.assertNotIn("injected", [d.outcome for d in dispatchers[0].deliveries()
                if d.kind == "mouse_wheel"])
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_two_regime_ignored_wheel_or_full_speed_cannot_prove(self):
        for speed, reason in ((None, "Reduced throttle did not sustain motion"),
                              (33., "Full speed persisted after wheel delivery")):
            with self.subTest(speed=speed), scratch_directory() as directory:
                result, ship, source, dispatchers = self.run_fixture(directory,
                    distance=1555., brake_at=800., max_thrust_seconds=None,
                    sample_interval=5., reduced_transition_at=1300.,
                    reduced_wheel_speed=speed)
                self.assertEqual(result["state"], "blocked")
                self.assertIn(reason, result["reason"])
                self.assertEqual(len(result["wheel_requests"]), 1)
                self.assertTrue(result["final_stop_observed"])
                self.assertEqual(ship.speed, 0)

    def test_two_regime_late_target_or_hull_change_requests_cleanup(self):
        for source_options in ({"change_at": 65}, {"damage_at": 65}):
            with self.subTest(source_options=source_options), scratch_directory() as directory:
                result, ship, source, _ = self.run_fixture(directory,
                    distance=1555., brake_at=800., max_thrust_seconds=None,
                    sample_interval=5., reduced_transition_at=1300.,
                    source_options=source_options)
                self.assertEqual(result["state"], "blocked")
                self.assertTrue(result["final_stop_observed"])
                self.assertEqual(ship.speed, 0)
                self.assertTrue(source.closed)

    def test_two_regime_post_wheel_speed_increase_brakes(self):
        class RisingSource(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample and sample.acquisition_sequence == 54:
                    sample.speed = 15.
                if sample and sample.acquisition_sequence == 55:
                    sample.speed = 23.
                return sample
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                distance=1555., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.,
                source_factory=lambda ship: RisingSource(ship))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("Speed increased after reduced-throttle", result["reason"])
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_two_regime_after_proved_five_speed_change_brakes(self):
        class DriftSource(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample and sample.acquisition_sequence == 58:
                    sample.speed = 4.
                return sample
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                distance=1555., brake_at=800., max_thrust_seconds=None,
                sample_interval=5., reduced_transition_at=1300.,
                source_factory=lambda ship: DriftSource(ship))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("Observed reduced-speed regime changed", result["reason"])
            self.assertGreaterEqual(len(result["reduced_speed_sequences"]), 3)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)


if __name__ == "__main__":
    unittest.main()
