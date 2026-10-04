"""Offline seams for one native-style wheel pulse; no game or desktop calls."""
import json
import math
from threading import Event, Lock, Thread
import time
import unittest

from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.throttle_probe import run_throttle_probe
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_approach_probe import Ship, PixelSource, recognize, PROFILE
from test_trace import scratch_directory


class WheelDispatcher:
    def __init__(self, backend, ship, *, wheel_speed=20.):
        self.backend, self.ship, self.wheel_speed = backend, ship, wheel_speed
        self.end, self.lock = Event(), Lock()
        self.thread = None
        self.rows = []
        self.held = set()
        self.error = None

    def start(self):
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.end.is_set():
            record = self.backend.get_action(timeout=.005)
            if record is None:
                continue
            allowed = (record.kind == "key_up" and record.key in self.held)
            allowed = allowed or self.backend.delivery_allowed(record)
            outcome = "injected" if allowed else "skipped_dependency"
            if allowed and record.kind == "mouse_wheel" and self.wheel_speed is not None:
                self.ship.set_speed(self.wheel_speed)
            if allowed and record.kind == "key_down":
                self.held.add(record.key)
                if record.key == "PageDown":
                    self.ship.set_speed(0.)
            if allowed and record.kind == "key_up":
                self.held.discard(record.key)
            row = DeliveryRecord(record.sequence, time.monotonic(), record.kind,
                                 outcome, 1, 1 if allowed else 0, 0)
            with self.lock:
                self.rows.append(row)
            self.backend.report_delivery(record, outcome)

    def deliveries(self):
        with self.lock:
            return tuple(self.rows)

    def stop(self, timeout):
        self.end.set()
        self.backend.cancel(reason="fixture_stop")
        self.thread.join(timeout)
        return not self.thread.is_alive() and not self.held


class ThrottleProbeTests(unittest.TestCase):
    def run_fixture(self, directory, *, speed=20., distance=1200., source_options=None,
                    cancel_file=None, recognizer=recognize, ship_factory=None,
                    source_factory=None, brake_at=None):
        ship = ship_factory(distance) if ship_factory else Ship(distance=distance)
        source = (source_factory(ship) if source_factory else
                  PixelSource(ship, **(source_options or {})))
        dispatchers = []
        def factory(backend, journal):
            dispatcher = WheelDispatcher(backend, ship, wheel_speed=speed)
            dispatchers.append(dispatcher)
            return dispatcher
        result = run_throttle_probe(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognizer, dispatcher_factory=factory,
            configuration={"fixture": True}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3), wheel_point=(1, 1),
            wheel_steps=1, clear_corridor_assessed=True,
            aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
            flight_point_assessed=True, scope="synthetic", baseline_seconds=1.,
            stop_seconds=1., sample_interval=5., cancel_file=cancel_file,
            brake_at=brake_at)
        return result, ship, source, dispatchers

    def test_one_wheel_proves_partial_motion_and_observed_stop(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory)
            self.assertEqual(result["state"], "proved", result)
            self.assertEqual(result["observed_partial_speed"], 20)
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertEqual([d.kind for d in dispatchers[0].deliveries()],
                             ["mouse_wheel", "key_down", "key_up"])
            self.assertGreaterEqual(len(result["stable_motion_sequences"]), 3)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)
            self.assertTrue(result["complete_diagnostics"])
            report = json.loads((directory / "run" / "report.json").read_text())
            self.assertIsNone(report["frames"]["max_frames"])

    def test_ignored_wheel_brakes_and_never_proves(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory, speed=None)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "no_motion_after_wheel")
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

    def test_full_speed_or_target_change_brakes_and_preserves_first_cause(self):
        for options, cause in (({"speed": 33.}, "full_speed_not_partial"),
                               ({"source_options": {"change_at": 5}},
                                "invalid_target_speed_or_full_hull")):
            with self.subTest(cause=cause), scratch_directory() as directory:
                result, ship, source, dispatchers = self.run_fixture(directory, **options)
                self.assertEqual(result["state"], "blocked")
                self.assertEqual(result["reason"], cause)
                self.assertEqual(result["first_rejection"]["reason"], cause)
                self.assertTrue(result["final_stop_observed"])
                self.assertTrue(source.closed)
                self.assertEqual(ship.speed, 0)

    def test_existing_cancel_marker_blocks_before_input(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            result, ship, source, dispatchers = self.run_fixture(directory, cancel_file=marker)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["wheel_requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertEqual(ship.speed, 0)

    def test_hull_fault_after_wheel_keeps_stop_cleanup(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                source_options={"damage_at": 5})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "invalid_target_speed_or_full_hull")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)
            self.assertTrue(source.closed)

    def test_nonfinite_hull_is_rejected_and_never_proves(self):
        def malformed(sample):
            result = recognize(sample)
            if sample.acquisition_sequence >= 5:
                fields = dict(result.fields)
                fields["hull_current"] = FieldValue(math.inf, sample.acquired_at)
                fields["hull_max"] = FieldValue(math.inf, sample.acquired_at)
                return RecognitionResult((4, 3), fields)
            return result
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory, recognizer=malformed)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["first_rejection"]["reason"],
                             "invalid_target_speed_or_full_hull")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_midflight_cancel_requests_one_cleanup_stop(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, ship, source, dispatchers = self.run_fixture(directory,
                cancel_file=marker,
                source_options={"cancel_at": 4, "cancel_path": marker})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertEqual(len(result["brake_requests"]), 2)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_stale_acquisition_stops_and_retains_failed_frame(self):
        class StaleSource(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample is not None and sample.acquisition_sequence == 4:
                    sample.acquired_at -= 8
                    sample.capture_started_at -= 8
                    sample.capture_completed_at -= 8
                    sample.frame_produced_at -= 8
                return sample
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                source_factory=lambda ship: StaleSource(ship))
            self.assertEqual(result["state"], "blocked")
            self.assertIn(result["first_rejection"]["reason"],
                          ("invalid_or_stale_capture_context", "stale_image_watchdog"))
            self.assertTrue(result["final_stop_observed"])
            self.assertTrue(source.closed)
            self.assertEqual(ship.speed, 0)

    def test_travel_budget_requests_stop_before_stability_proof(self):
        class FastTravelShip(Ship):
            def sample(self):
                with self.lock:
                    if self.speed > 0:
                        self.distance -= 120
                    return self.speed, self.distance
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                ship_factory=lambda distance: FastTravelShip(distance=distance))
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["first_rejection"]["reason"], "travel_budget_reached")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_hull_fault_during_coast_preserves_blocker_but_observes_zero(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory,
                source_options={"damage_after_brake": True})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["first_rejection"]["reason"],
                             "invalid_target_speed_or_full_hull")
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_transit_keeps_one_partial_notch_past_300_until_800_then_stops(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                speed=5., distance=1200., brake_at=800.)
            self.assertEqual(result["state"], "proved", result)
            self.assertEqual(result["brake_reason"], "range_threshold")
            self.assertGreater(initial := result["baseline"][0]["distance"]
                               - result["final_stop_distance"], 300)
            self.assertLessEqual(result["final_stop_distance"], 800)
            self.assertGreaterEqual(result["final_stop_distance"], 700)
            self.assertEqual([d.kind for d in dispatchers[0].deliveries()],
                             ["mouse_wheel", "key_down", "key_up"])
            self.assertEqual(ship.speed, 0)
            manifest = json.loads((directory / "run" / "manifest.json").read_text())
            self.assertIsNone(manifest["configuration"]["travel_budget"])
            self.assertIsNone(json.loads((directory / "run" / "report.json").read_text())
                              ["frames"]["max_frames"])

    def test_transit_ignored_notch_never_repeats_input(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                speed=None, brake_at=800.)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "no_motion_after_wheel")
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertEqual(len(result["brake_requests"]), 2)
            self.assertEqual(ship.speed, 0)

    def test_transit_late_target_fault_after_300_still_brakes(self):
        with scratch_directory() as directory:
            result, ship, source, dispatchers = self.run_fixture(directory,
                speed=5., brake_at=800., source_options={"change_at": 70})
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["first_rejection"]["reason"],
                             "invalid_target_speed_or_full_hull")
            self.assertEqual(len(result["wheel_requests"]), 1)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(ship.speed, 0)

    def test_transit_final_range_below_700_blocks_proof(self):
        with scratch_directory() as directory:
            result, ship, source, _ = self.run_fixture(directory, speed=5.,
                brake_at=800., ship_factory=lambda distance: Ship(
                    brake_decay_seconds=.8, distance=distance))
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "final_range_outside_reduced_speed_acceptance_band")
            self.assertTrue(result["final_stop_observed"])
            self.assertLess(result["final_stop_distance"], 700)

    def test_transit_both_zero_ranges_must_be_in_band_and_consistent(self):
        class ContradictoryStoppedSource(PixelSource):
            def __init__(self, ship, ranges):
                super().__init__(ship)
                self.ranges = iter(ranges)

            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample is not None and self.ship.brake_requested and sample.speed == 0:
                    sample.distance = next(self.ranges, 790.)
                return sample

        for ranges, reason in (((690., 740.), "final_range_outside_reduced_speed_acceptance_band"),
                               ((750., 790.), "stopped_target_distance_changed")):
            with self.subTest(ranges=ranges), scratch_directory() as directory:
                result, ship, source, _ = self.run_fixture(directory, speed=5., brake_at=800.,
                    source_factory=lambda ship: ContradictoryStoppedSource(ship, ranges))
                self.assertEqual(result["state"], "blocked", result)
                self.assertEqual(result["reason"], reason)
                self.assertEqual(result["first_rejection"]["reason"], reason)
                self.assertEqual(result["stopped_target_ranges"], list(ranges))
                self.assertTrue(result["final_stop_observed"])
                self.assertEqual(ship.speed, 0)
                self.assertTrue(source.closed)


if __name__ == "__main__":
    unittest.main()
