"""Synthetic image and delivery seams for one supervised G experiment."""
from threading import Event, Lock, Thread
import time
import unittest

from bsgo_offline.firing_probe import run_firing_probe
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_scan_probe import PixelSample
from test_trace import scratch_directory


PROFILE = "largehud-20261003"


class Source:
    def __init__(self, *, effect=True, baseline_damage=False, invalid_after=False,
                 change_target=False, stall_after=None, cancel_at=None,
                 cancel_path=None, effect_delay=0, gone_after=None,
                 unknown_after=None, invalid_after_step=None,
                 cancel_after_delivery=None, unknown_baseline=False,
                 valid_none_baseline=False, post_gone_speed=0.,
                 hp_reader_fault_after=None, hp_reader_fault_until=None,
                 motion_after=None, initial_hp=4500,
                 low_hp_after=None, low_hp_descend=False,
                 low_hp_on_down=False, zero_after=None, zero_until=None,
                 zero_on_down=False, missing_current_hp_baseline=False,
                 distance=677.):
        self.sequence = 0
        self.closed = False
        self.effect = effect
        self.baseline_damage = baseline_damage
        self.invalid_after = invalid_after
        self.change_target = change_target
        self.stall_after = stall_after
        self.cancel_at, self.cancel_path = cancel_at, cancel_path
        self.effect_delay, self.gone_after = effect_delay, gone_after
        self.unknown_after, self.invalid_after_step = unknown_after, invalid_after_step
        self.cancel_after_delivery = cancel_after_delivery
        self.unknown_baseline = unknown_baseline
        self.valid_none_baseline = valid_none_baseline
        self.post_gone_speed = post_gone_speed
        self.hp_reader_fault_after = hp_reader_fault_after
        self.hp_reader_fault_until = hp_reader_fault_until
        self.motion_after = motion_after
        self.initial_hp, self.low_hp_after = initial_hp, low_hp_after
        self.low_hp_descend = low_hp_descend
        self.low_hp_on_down = low_hp_on_down
        self.zero_after, self.zero_until = zero_after, zero_until
        self.zero_on_down = zero_on_down
        self.missing_current_hp_baseline = missing_current_hp_baseline
        self.distance = distance
        self.down_sequence = None
        self.delivery_sequence = None

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(.025):
            return None
        self.sequence += 1
        if self.sequence == self.cancel_at:
            self.cancel_path.touch()
        after = (self.delivery_sequence is not None
                 and self.sequence > self.delivery_sequence)
        step = self.sequence - self.delivery_sequence if after else 0
        if self.cancel_after_delivery == step:
            self.cancel_path.touch()
        hp = self.initial_hp
        if self.baseline_damage and self.sequence >= 3:
            hp = 4490
        if (self.unknown_baseline or self.valid_none_baseline) and self.sequence == 1:
            hp = None
        if after and self.effect and step > self.effect_delay:
            hp = 4500 - 10 * (step - self.effect_delay)
        if after and self.low_hp_after is not None and step >= self.low_hp_after:
            hp = 351 - (101 * (step - self.low_hp_after) if self.low_hp_descend else 0)
        if (after and self.zero_after is not None and step >= self.zero_after
                and (self.zero_until is None or step < self.zero_until)):
            hp = 0
        if (self.low_hp_on_down and self.down_sequence is not None
                and self.delivery_sequence is None and self.sequence > self.down_sequence):
            hp = 351
        if (self.zero_on_down and self.down_sequence is not None
                and self.delivery_sequence is None and self.sequence > self.down_sequence):
            hp = 0
        values = {"speed": 0., "asteroid_selected": True, "distance": self.distance,
            "target_identity": "asteroid:hp:4500", "target_portrait": tuple([1] * 144),
            "target_max_hp": 4500, "target_current_hp": hp}
        if self.missing_current_hp_baseline and self.sequence == 1:
            del values["target_current_hp"]
        if after and self.invalid_after:
            values["target_current_hp"] = None
        if after and self.invalid_after_step is not None and step >= self.invalid_after_step:
            values["target_current_hp"] = None
        if after and self.unknown_after is not None and step >= self.unknown_after:
            values["target_current_hp"] = None
        if after and self.change_target:
            values["target_max_hp"] = 4600
        hp_reader_fault = (after and self.hp_reader_fault_after is not None
            and step >= self.hp_reader_fault_after
            and (self.hp_reader_fault_until is None or step < self.hp_reader_fault_until))
        if hp_reader_fault:
            values.update(target_identity=None, target_max_hp=None,
                          target_current_hp=None)
        if after and self.motion_after is not None and step >= self.motion_after:
            values["speed"] = 1.
        if after and self.gone_after is not None and step >= self.gone_after:
            values.update(asteroid_selected=False, distance=None, target_identity=None,
                          target_portrait=None, target_max_hp=None, target_current_hp=None)
            if step >= self.gone_after + 2:
                values["speed"] = self.post_gone_speed
        sample = PixelSample(self.sequence, time.monotonic(), values)
        sample.layout = PROFILE
        sample.unknown_hp = ((after and self.unknown_after is not None
                             and step >= self.unknown_after)
                            or (self.unknown_baseline and self.sequence == 1))
        sample.valid_none_hp = self.valid_none_baseline and self.sequence == 1
        sample.hp_reader_fault = (hp_reader_fault
                                  and not (self.gone_after is not None and step >= self.gone_after))
        return sample

    def close(self):
        self.closed = True


def recognize(sample):
    return RecognitionResult((4, 3), {name: FieldValue(value, sample.acquired_at,
        valid=value is not None or (name == "target_current_hp" and sample.valid_none_hp),
        reason=({"target_identity": "unreadable_or_absent",
                 "target_max_hp": "invalid_target_hp_separator",
                 "target_current_hp": "maximum_hp_context_unverified"}[name]
                if sample.hp_reader_fault and name in ("target_identity", "target_max_hp",
                                                       "target_current_hp") else
                "unvalidated_current_hp_digit_count" if name == "target_current_hp"
                and sample.unknown_hp else "fixture_rejection") if value is None else None)
        for name, value in sample.values.items()})


class Dispatcher:
    def __init__(self, backend, source, *, ignore=False, cancel_on_G=None,
                 delay_up=0.):
        self.backend, self.source, self.ignore = backend, source, ignore
        self.cancel_on_G = cancel_on_G
        self.delay_up = delay_up
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
            request = self.backend.get_action(timeout=.005)
            if request is None:
                continue
            if request.kind == "key_up" and self.delay_up:
                time.sleep(self.delay_up)
            permitted = (request.kind == "key_up" and request.key in self._held)
            permitted = (permitted or self.backend.delivery_allowed(request)) and not self.ignore
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and request.kind == "key_down":
                self._held.add(request.key)
                self.source.down_sequence = self.source.sequence
            elif permitted and request.kind == "key_up":
                self._held.discard(request.key)
                self.source.delivery_sequence = self.source.sequence
                if self.cancel_on_G:
                    self.cancel_on_G.touch()
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


class FiringProbeTests(unittest.TestCase):
    def test_continuous_mode_requires_external_cancel_channel(self):
        with scratch_directory() as directory:
            with self.assertRaisesRegex(ValueError, "continuous_observation_requires_cancel_file"):
                run_firing_probe(directory / "unused", source_factory=lambda: None,
                    recognizer_factory=lambda: None,
                    dispatcher_factory=lambda backend, journal: None,
                    configuration={}, profile=PROFILE, expected_window_id="42:17",
                    frame_size=(4, 3), safe_stopped_scene_assessed=True,
                    wanted_resource_assessed="Tylium",
                    no_preexisting_firing_assessed=True,
                    scope="synthetic", observe_until_target_gone=True)

    def run_probe(self, directory, source, *, ignore=False, after_seconds=.3,
                  cancel_file=None, cancel_on_G=False, observe_until_target_gone=False,
                  delay_up=0., min_distance=480.):
        if observe_until_target_gone and cancel_file is None:
            cancel_file = directory / "stop.request"
        dispatchers = []
        def factory(backend, journal):
            dispatcher = Dispatcher(backend, source, ignore=ignore,
                cancel_on_G=cancel_file if cancel_on_G else None,
                delay_up=delay_up)
            dispatchers.append(dispatcher)
            return dispatcher
        result = run_firing_probe(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognize, dispatcher_factory=factory,
            configuration={"fixture": True}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3),
            safe_stopped_scene_assessed=True, wanted_resource_assessed="Tylium",
            no_preexisting_firing_assessed=True, scope="synthetic",
            baseline_seconds=.5, stable_seconds=.075,
            after_seconds=after_seconds, min_distance=min_distance, cancel_file=cancel_file,
            observe_until_target_gone=observe_until_target_gone)
        return result, dispatchers

    def test_two_distinct_post_delivery_declines_prove_narrow_effect(self):
        with scratch_directory() as directory:
            source = Source()
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "proved", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertFalse(result["mining_ready"])
            self.assertEqual([r["key"] for r in result["requests"]], ["G", "G"])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 2)
            self.assertEqual(len(result["falling"]), 2)
            self.assertGreater(result["falling"][0]["target_current_hp"],
                               result["falling"][1]["target_current_hp"])
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()))
            self.assertTrue(source.closed)
            self.assertIn("autofire may continue", result["warning"])

    def test_default_lower_range_guard_blocks_close_stationary_target(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(distance=240.))
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_explicit_close_range_calibration_preserves_image_and_delivery_proof(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(distance=240.),
                                                 min_distance=200.)
            self.assertEqual(result["state"], "proved", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertTrue(result["complete_diagnostics"])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()],
                             ["injected", "injected"])
            self.assertEqual(len(result["falling"]), 2)
            self.assertTrue(all(row["distance"] == 240. for row in result["baseline"]))

    def test_no_G_if_hp_changes_during_baseline(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(baseline_damage=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("preexisting_target_hp_change", result["reason"])
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["requests"], [])

    def test_ignored_G_does_not_prove_firing(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(), ignore=True)
            self.assertEqual(result["state"], "blocked")
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(all(d.outcome != "injected" for d in dispatchers[0].deliveries()))

    def test_no_hp_effect_does_not_prove_firing(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(effect=False), after_seconds=.18)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("two_post_delivery_hp_declines_not_observed", result["reason"])

    def test_invalid_hp_after_G_blocks_and_retains_failed_image(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(invalid_after=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("invalid_or_stale_target_current_hp", result["reason"])
            self.assertGreaterEqual(len(list((directory / "run" / "failed-recognition").glob("*.png"))), 1)
            self.assertGreaterEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)

    def test_selected_target_change_blocks(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(change_target=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("selected_target_changed", result["reason"])

    def test_stalled_image_stream_cannot_prove_effect(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(stall_after=5), after_seconds=.15)
            self.assertEqual(result["state"], "blocked")
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(all(result["shutdown"].values()))

    def test_existing_cancel_marker_prevents_G(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            result, dispatchers = self.run_probe(directory, Source(), cancel_file=marker)
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertTrue(result["cancel_requested"])
            self.assertTrue(marker.exists())

    def test_cancel_during_baseline_prevents_G(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, dispatchers = self.run_probe(directory,
                Source(cancel_at=2, cancel_path=marker), cancel_file=marker)
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_cancel_after_G_does_not_claim_firing_or_ceasefire(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, dispatchers = self.run_probe(directory, Source(),
                cancel_file=marker, cancel_on_G=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(result["firing_state_after_probe"],
                             "unverified_supervisor_retains_control")
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()],
                             ["injected"] * 2)
            self.assertTrue(all(result["shutdown"].values()))

    def test_continuous_mode_waits_past_finite_deadline_and_records_ambiguous_absence(self):
        with scratch_directory() as directory:
            source = Source(effect_delay=6, gone_after=12)
            result, dispatchers = self.run_probe(directory, source,
                after_seconds=.1, observe_until_target_gone=True)
            self.assertEqual(result["state"], "recorded", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(result["reason"], "selected_target_absent_cause_unverified")
            self.assertEqual(len(result["absent_target_images"]), 2)
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertFalse(result["mining_ready"])
            self.assertIn("resource_reward", result["unverified"])
            self.assertTrue(result["complete_diagnostics"])
            from json import loads
            report = loads((directory / "run" / "report.json").read_text())
            self.assertIsNone(report["frames"]["max_frames"])
            self.assertEqual(report["frames"]["budget_skipped"], 0)
            self.assertEqual(report["frames"]["written"],
                             len(list((directory / "run" / "frames").glob("*.png")))
                             + len(list((directory / "run" / "failed-recognition").glob("*.png"))))
            from bsgo_offline.trace import iter_trace_events
            events = list(iter_trace_events(directory / "run" / "trace", result["run_id"]))
            terminal = [event["payload"] for event in events
                        if event["payload"]["event"] == "run_shutdown"]
            self.assertEqual(len(terminal), 1)
            self.assertNotIn("after", terminal[0])
            self.assertEqual(terminal[0]["full_result"]["json_pointer"], "/result")

    def test_continuous_mode_tolerates_only_declared_unknown_current_hp_after_proof(self):
        with scratch_directory() as directory:
            source = Source(unknown_after=5, gone_after=8)
            result, _ = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "recorded", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertGreater(result["unknown_current_hp_count"], 0)
            self.assertTrue(any(row["target_current_hp"] is None for row in result["after"]))
            self.assertGreater(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 0)
            self.assertTrue(result["complete_diagnostics"])

    def test_valid_three_digit_hp_after_proof_continues_to_absence_and_follow_up(self):
        with scratch_directory() as directory:
            source = Source(low_hp_after=5, gone_after=8)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "recorded", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertTrue(any(row.get("target_current_hp") == 351
                                for row in result["after"]))
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertTrue(result["complete_diagnostics"])

    def test_valid_three_digit_hp_after_G_can_be_first_then_second_decline(self):
        with scratch_directory() as directory:
            source = Source(low_hp_after=1, low_hp_descend=True, gone_after=4)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "recorded", result)
            self.assertEqual([row["target_current_hp"] for row in result["falling"]],
                             [351, 250])
            self.assertTrue(result["firing_effect_verified"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertEqual(len(result["post_disappearance_images"]), 2)

    def test_one_post_G_three_digit_read_does_not_prove_firing_alone(self):
        with scratch_directory() as directory:
            source = Source(low_hp_after=1, gone_after=4)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "selected_target_absent_without_firing_proof")
            self.assertEqual([row["target_current_hp"] for row in result["falling"]],
                             [351])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_three_digit_hp_before_G_up_cannot_be_accepted(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(low_hp_on_down=True), observe_until_target_gone=True,
                delay_up=.12)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("four_digit_hp_required", result["reason"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(result["requests"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_valid_three_digit_hp_after_prior_ocr_fault_keeps_first_blocker(self):
        with scratch_directory() as directory:
            source = Source(hp_reader_fault_after=3, hp_reader_fault_until=5,
                            low_hp_after=5, gone_after=8)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertTrue(result["firing_effect_verified"])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertTrue(any(row.get("target_current_hp") == 351
                                for row in result["after"]))
            rejection = result["first_rejection"]
            self.assertEqual(rejection["read_error"], result["first_blocker"])
            self.assertEqual(rejection["fields"]["target_max_hp"]["reason"],
                             "invalid_target_hp_separator")
            self.assertEqual(rejection["fields"]["target_identity"]["reason"],
                             "unreadable_or_absent")
            self.assertTrue((directory / "run" / rejection["image_reference"]).exists())
            self.assertTrue((directory / "run" / rejection["sidecar_reference"]).exists())
            from json import loads
            from bsgo_offline.trace import iter_trace_events
            report = loads((directory / "run" / "report.json").read_text())
            self.assertEqual(report["result"]["first_rejection"], rejection)
            events = list(iter_trace_events(directory / "run" / "trace", result["run_id"]))
            terminal = [event["payload"] for event in events
                        if event["payload"]["event"] == "run_shutdown"]
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0]["result_summary"]["first_rejection"], rejection)
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_valid_three_digit_hp_after_first_blocker_stays_read_only(self):
        with scratch_directory() as directory:
            source = Source(hp_reader_fault_after=1, hp_reader_fault_until=2,
                            low_hp_after=2, gone_after=5)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(any(row.get("target_current_hp") == 351
                                for row in result["after"]))
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_continuous_mode_rejects_unknown_current_hp_before_G(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(unknown_baseline=True), observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("invalid_or_stale_target_current_hp", result["reason"])
            rejection = result["first_rejection"]
            self.assertEqual(rejection["observation_sequence"], 1)
            self.assertEqual(rejection["fields"]["target_current_hp"],
                             {"present": True, "valid": False, "fresh": True,
                              "reason": "unvalidated_current_hp_digit_count"})
            self.assertTrue((directory / "run" / rejection["image_reference"]).exists())
            self.assertTrue((directory / "run" / rejection["sidecar_reference"]).exists())
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["requests"], [])

    def test_missing_baseline_field_records_reason_without_input(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(missing_current_hp_baseline=True),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_current_hp")
            rejection = result["first_rejection"]
            self.assertEqual(rejection["observation_sequence"], 1)
            self.assertEqual(rejection["fields"]["target_current_hp"],
                             {"present": False, "valid": False, "fresh": False,
                              "reason": "missing_field"})
            self.assertTrue((directory / "run" / rejection["image_reference"]).exists())
            self.assertIsNone(rejection["sidecar_reference"])
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_valid_none_current_hp_cannot_authorize_G(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(valid_none_baseline=True), observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("four_digit_hp_required", result["reason"])
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_valid_three_digit_hp_baseline_cannot_authorize_G(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(initial_hp=351), observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("four_digit_hp_required", result["reason"])
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_zero_hp_baseline_cannot_authorize_G(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(initial_hp=0), observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("four_digit_hp_required", result["reason"])
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_first_post_G_zero_is_observed_but_cannot_prove_effect(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(effect=False, zero_after=1, gone_after=4),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "selected_target_absent_without_firing_proof")
            self.assertTrue(result["terminal_zero_observed"])
            self.assertEqual(result["falling"], [])
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_zero_cannot_be_second_distinct_decline(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory,
                Source(zero_after=2, gone_after=5),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual([r["target_current_hp"] for r in result["falling"]],
                             [4490])
            self.assertTrue(result["terminal_zero_observed"])
            self.assertFalse(result["firing_effect_verified"])

    def test_proof_chain_cannot_resume_after_terminal_zero(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory,
                Source(zero_after=2, zero_until=3, gone_after=5),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual([r["target_current_hp"] for r in result["falling"]],
                             [4490])
            self.assertTrue(result["terminal_zero_observed"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(result["target_disappearance_observed"])

    def test_zero_after_positive_proof_retains_absence_and_follow_up(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(zero_after=3, gone_after=5),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "recorded", result)
            self.assertEqual([r["target_current_hp"] for r in result["falling"]],
                             [4490, 4480])
            self.assertTrue(result["firing_effect_verified"])
            self.assertTrue(result["terminal_zero_observed"])
            self.assertTrue(any(r["target_current_hp"] == 0 for r in result["after"]))
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertEqual(result["target_disappearance_interpretation"],
                             "selection_absent_cause_unverified")
            self.assertIn("resource_reward", result["unverified"])
            self.assertTrue(result["complete_diagnostics"])

    def test_zero_before_G_up_cannot_be_accepted(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(zero_on_down=True), observe_until_target_gone=True,
                delay_up=.12)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("four_digit_hp_required", result["reason"])
            self.assertFalse(result["terminal_zero_observed"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_zero_after_first_blocker_retains_cause_and_no_proof(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory,
                Source(hp_reader_fault_after=1, hp_reader_fault_until=2,
                       zero_after=2, gone_after=5),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertTrue(result["terminal_zero_observed"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(result["falling"], [])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)

    def test_continuous_mode_bad_hp_blocks_without_target_loss_inference(self):
        with scratch_directory() as directory:
            source = Source(invalid_after_step=5)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("unclassified_hp_or_target_reading_fault", result["reason"])
            self.assertTrue(result["firing_effect_verified"])
            self.assertFalse(result["target_disappearance_observed"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertGreater(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 0)

    def test_continuous_mode_absence_without_hp_proof_is_ambiguous_and_blocked(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(effect=False, gone_after=4),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "selected_target_absent_without_firing_proof")
            self.assertTrue(result["target_disappearance_observed"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)

    def test_post_disappearance_motion_blocks_recorded_completion(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory,
                Source(gone_after=4, post_gone_speed=1.),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("fresh_stopped_speed_required_after_disappearance", result["reason"])
            self.assertTrue(result["target_disappearance_observed"])
            self.assertTrue(result["firing_effect_verified"])
            self.assertEqual(result["post_disappearance_images"], [])

    def test_continuous_mode_cancel_after_proof_preserves_proof_but_not_ceasefire_claim(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            source = Source(cancel_path=marker, cancel_after_delivery=5)
            result, dispatchers = self.run_probe(directory, source,
                cancel_file=marker, observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertTrue(result["firing_effect_verified"])
            self.assertFalse(result["target_disappearance_observed"])
            self.assertEqual(result["firing_state_after_probe"],
                             "unverified_supervisor_retains_control")
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertTrue(marker.exists())

    def test_continuous_mode_has_stale_image_watchdog_without_total_elapsed_cap(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(effect=False, stall_after=5),
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("stale_image_stream", result["reason"])
            self.assertFalse(result["target_disappearance_observed"])

    def test_declared_hp_reader_fault_continues_read_only_to_ambiguous_absence(self):
        with scratch_directory() as directory:
            source = Source(hp_reader_fault_after=2, gone_after=7)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["first_blocker"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertEqual(result["reason"], result["first_blocker"])
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(result["falling"]), 1)
            self.assertGreaterEqual(result["hp_fault_image_count"], 1)
            self.assertTrue(result["target_disappearance_observed"])
            self.assertEqual(len(result["post_disappearance_images"]), 2)
            self.assertEqual(len(result["requests"]), 2)
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertTrue(result["complete_diagnostics"])
            self.assertGreaterEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)
            self.assertIn("resource_reward", result["unverified"])

    def test_finite_mode_still_stops_on_declared_hp_reader_fault(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory,
                Source(hp_reader_fault_after=2, gone_after=7))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("invalid_or_stale_target_identity", result["reason"])
            self.assertIsNone(result["first_blocker"])
            self.assertFalse(result["target_disappearance_observed"])

    def test_cancel_after_hp_fault_preserves_first_cause_and_no_second_G(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            source = Source(hp_reader_fault_after=2, cancel_path=marker,
                            cancel_after_delivery=4)
            result, dispatchers = self.run_probe(directory, source,
                cancel_file=marker, observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertTrue(result["cancel_requested"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)
            self.assertTrue(marker.exists())

    def test_motion_during_hp_fault_ends_observation_with_first_cause_preserved(self):
        with scratch_directory() as directory:
            source = Source(hp_reader_fault_after=2, motion_after=3)
            result, dispatchers = self.run_probe(directory, source,
                observe_until_target_gone=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"],
                             "RuntimeError: invalid_or_stale_target_identity")
            self.assertIn("invalid_hp_fault_non_hp_guard", result["observation_end_reason"])
            self.assertFalse(result["target_disappearance_observed"])
            self.assertEqual(len(dispatchers[0].deliveries()), 2)


if __name__ == "__main__":
    unittest.main()
