from dataclasses import replace
import unittest

from bsgo_offline.selected_scan_toggle import SelectedScanToggle
from bsgo_offline.state import FieldValue, Snapshot, ObservationContext
from bsgo_offline.tracking import SelectionTrack


CONTEXT = ObservationContext(1, "fixture", (2560, 1440), "42:17", 1)


def observation(at=10., sequence=1, *, values=None, context=CONTEXT, invalid=()):
    fields = {"asteroid_selected": True, "speed": 0., "distance": 1904,
              "target_identity": "asteroid:hp:3344", "target_portrait": (2,)*144,
              "hull_current": 5005, "hull_max": 5005}
    fields.update(values or {})
    return Snapshot(sequence, context.session_epoch, sequence, at, at, context.layout,
        context.frame_size, {key: FieldValue(value, at, key not in invalid,
            "fixture_unknown" if key in invalid else None) for key, value in fields.items()},
        window_id=context.window_id, ownership_generation=context.ownership_generation)


def track(snapshot, track_id=1, continuity="confirmed"):
    return SelectionTrack(track_id, snapshot.context, 9., snapshot.acquired_at,
        snapshot.fields["target_identity"].value, snapshot.fields["target_portrait"].value,
        None, continuity)


class SelectedScanToggleTests(unittest.TestCase):
    def setUp(self):
        self.toggle = SelectedScanToggle()
        self.toggle.start()

    def decide(self, snapshot=None, track_id=1, state="unknown", **changes):
        snapshot = snapshot or observation()
        selection = track(snapshot, track_id)
        selection = replace(selection, **changes)
        return self.toggle.evaluate(snapshot, selection, now=snapshot.acquired_at,
                                    marker_scan_state=state)

    def complete(self, step, delivered=True):
        self.toggle.record_result(step.request_id, delivered=delivered)

    def test_unknown_state_produces_only_one_explicitly_unknown_scan_intention(self):
        first = self.decide()
        self.assertEqual(first.action, "scan")
        self.assertEqual(first.scan_state, "unknown")
        self.assertIn("unknown_state", first.reason)
        self.assertEqual(self.decide().action, "wait")
        self.complete(first)
        self.assertEqual(self.decide(observation(10.5, 2)).action, "wait")
        self.assertFalse(self.toggle.status()["last_result"]["scan_effect_verified"])

    def test_delivery_or_caller_effect_boolean_never_becomes_scan_proof(self):
        first = self.decide()
        self.toggle.record_result(first.request_id, delivered=True, scan_effect_verified=True)
        self.assertFalse(self.toggle.status()["last_result"]["scan_effect_verified"])

    def test_failed_delivery_still_suppresses_unchanged_track(self):
        first = self.decide()
        self.complete(first, delivered=False)
        self.assertEqual(self.decide(observation(10.5, 2)).action, "wait")

    def test_known_scanned_marker_skipped_even_if_next_image_classification_unknown(self):
        self.assertEqual(self.decide(state="scanned").reason, "selected_marker_image_known_scanned")
        self.assertEqual(self.decide(observation(10.5, 2)).action, "wait")
        self.assertIsNone(self.toggle.status()["pending"])

    def test_moving_injured_or_far_selected_ship_waits_without_input(self):
        for values, reason in (({"speed": 5}, "stationary_ship_required"),
                               ({"hull_current": 4999}, "full_hull_required"),
                               ({"distance": 4001}, "selected_target_outside_scanner_range")):
            with self.subTest(values=values):
                self.toggle.restart()
                self.assertEqual(self.decide(observation(values=values)).reason, reason)
                self.assertIsNone(self.toggle.status()["pending"])
                step = self.decide(observation(10.4, 2))
                self.assertEqual(step.action, "scan")
                self.complete(step)

    def test_unknown_rejected_field_latches_gap_before_any_scan(self):
        for field in ("speed", "distance", "hull_current", "target_identity", "target_portrait"):
            with self.subTest(field=field):
                self.toggle.restart()
                self.assertEqual(self.decide(observation(invalid=(field,))).action, "blocked")
                self.assertEqual(self.decide(observation(10.4, 2)).action, "blocked")

    def test_stale_frame_then_fresh_same_track_cannot_rearm(self):
        row = observation()
        self.assertEqual(self.toggle.evaluate(row, track(row), now=12.).action, "blocked")
        self.assertEqual(self.decide(observation(12.1, 2)).action, "blocked")
        self.toggle.restart()
        self.assertEqual(self.decide(observation(12.1, 2)).action, "scan")

    def test_gap_new_tracker_id_alone_does_not_rearm_same_image_target(self):
        first = self.decide()
        self.complete(first)
        row = observation(10.5, 2)
        self.assertEqual(self.decide(row, continuity="uncertain").action, "blocked")
        self.assertEqual(self.decide(observation(11., 3), track_id=2).action, "blocked")

    def test_missing_interval_blocks_even_if_caller_reuses_confirmed_track(self):
        self.decide(state="scanned")
        self.assertEqual(self.decide(observation(13., 2)).action, "blocked")
        self.assertEqual(self.decide(observation(13.2, 3)).action, "blocked")
        changed = observation(13.4, 4, values={"target_portrait": (6,)*144})
        self.assertEqual(self.decide(changed, track_id=2).action, "scan")

    def test_pending_action_does_not_hide_observation_gap_or_discard_cleanup(self):
        first = self.decide()
        self.assertEqual(self.decide(observation(13., 2)).action, "blocked")
        self.assertEqual(self.toggle.status()["pending"]["request_id"], first.request_id)
        self.complete(first)
        self.assertEqual(self.decide(observation(13.2, 3)).action, "blocked")

    def test_continuously_observed_long_scan_keeps_one_pending_reservation(self):
        first = self.decide()
        for sequence in range(2, 22):
            step = self.decide(observation(10. + sequence * .5, sequence))
            self.assertEqual(step.action, "wait")
            self.assertEqual(step.request_id, first.request_id)
        self.complete(first)
        self.assertEqual(self.decide(observation(21., 22)).action, "wait")

    def test_observed_target_loss_then_reacquisition_allows_new_local_track(self):
        first = self.decide()
        self.complete(first)
        self.decide(observation(10.2, 2), continuity="uncertain")
        lost = observation(10.4, 3, values={"asteroid_selected": False})
        self.assertEqual(self.decide(lost).reason, "observed_selected_target_loss")
        self.assertEqual(self.decide(observation(10.6, 4), track_id=2).action, "scan")

    def test_observed_target_image_change_resolves_gap_without_world_identity_claim(self):
        first = self.decide()
        self.complete(first)
        self.decide(observation(10.2, 2), continuity="uncertain")
        different = observation(10.4, 3, values={"target_identity": "asteroid:hp:3783"})
        self.assertEqual(self.decide(different, track_id=2).action, "scan")

    def test_window_epoch_geometry_ownership_change_needs_explicit_restart(self):
        for context in (replace(CONTEXT, session_epoch=2), replace(CONTEXT, window_id="43:18"),
                        replace(CONTEXT, ownership_generation=2), replace(CONTEXT, layout="other"),
                        replace(CONTEXT, frame_size=(3840, 2160))):
            with self.subTest(context=context):
                self.toggle.restart()
                self.decide(state="scanned")
                row = observation(10.4, 2, context=context)
                self.assertEqual(self.decide(row, track_id=2).action, "blocked")
                loss = observation(10.6, 3, context=context, values={"asteroid_selected": False})
                self.assertEqual(self.decide(loss, track_id=2).action, "blocked")
                self.toggle.restart()
                self.assertEqual(self.decide(row, track_id=2).action, "scan")
                self.complete(self.decide(row, track_id=2))

    def test_track_pin_timestamp_and_shape_must_match_current_image(self):
        for change in (dict(last_evidence_at=9.), dict(context=replace(CONTEXT, session_epoch=2)),
                       dict(signature="other"), dict(portrait=(6,)*144), dict(track_id=0),
                       dict(portrait=(2.,)*144), dict(established_at=float("nan"))):
            with self.subTest(change=change):
                self.toggle.restart()
                self.assertEqual(self.decide(**change).action, "blocked")

    def test_stop_keeps_pending_cleanup_and_restart_cannot_overlap_lease(self):
        first = self.decide()
        self.toggle.stop()
        self.assertEqual(self.decide().action, "disabled")
        self.assertIsNotNone(self.toggle.status()["pending"])
        with self.assertRaisesRegex(RuntimeError, "pending_cleanup"):
            self.toggle.restart()
        self.complete(first)
        self.toggle.start()
        second = self.decide(observation(10.5, 2))
        self.assertEqual(second.action, "scan")
        self.assertNotEqual(first.request_id, second.request_id)
        with self.assertRaises(ValueError):
            self.complete(first)

    def test_duplicate_start_is_not_an_implicit_restart(self):
        first = self.decide()
        self.complete(first)
        self.toggle.start()
        self.assertEqual(self.decide(observation(10.5, 2)).action, "wait")


if __name__ == "__main__":
    unittest.main()
