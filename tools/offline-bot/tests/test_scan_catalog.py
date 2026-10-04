from dataclasses import replace
import unittest

from bsgo_offline.scan_catalog import CandidateObservation, ScanCatalog
from bsgo_offline.scan_sweep import ScanSweep
from bsgo_offline.state import ObservationContext


CONTEXT = ObservationContext(1, "fixture", (2560, 1440), "42:17", 1)


def image_observation(**changes):
    values = dict(context=CONTEXT, view_revision=0, acquisition_sequence=1,
                  acquired_at=10., image_reference="frames/one.png",
                  box=(100, 200, 160, 260), visual_signature="measured-rock-outline-a",
                  distance=1904, scan_state="unscanned")
    values.update(changes)
    return CandidateObservation(**values)


class ScanCatalogTests(unittest.TestCase):
    def test_same_frame_duplicates_share_local_id(self):
        catalog = ScanCatalog()
        row = image_observation()
        first, second = catalog.observe((row, row), 10)
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(len(catalog.entries(10)), 1)

    def test_identical_appearance_at_distinct_locations_is_not_unique_identity(self):
        catalog = ScanCatalog()
        first, second = catalog.observe((image_observation(),
            image_observation(box=(400, 200, 460, 260))), 10)
        self.assertNotEqual(first.candidate_id, second.candidate_id)

    def test_hp_signature_not_accepted_as_visual_identity(self):
        with self.assertRaisesRegex(ValueError, "invalid_candidate_image_evidence"):
            image_observation(visual_signature="asteroid:hp:3344")

    def test_fresh_continuous_geometry_keeps_id_and_records_scan_image(self):
        catalog = ScanCatalog()
        old = catalog.observe((image_observation(),), 10)[0]
        new = catalog.observe((image_observation(acquisition_sequence=2,
            acquired_at=10.4, image_reference="frames/two.png", scan_state="scanned",
            resource="Water", box=(101, 200, 161, 260)),), 10.4)[0]
        self.assertEqual(old.candidate_id, new.candidate_id)
        self.assertEqual(new.scan_image_reference, "frames/two.png")

    def test_camera_ship_and_capture_context_changes_invalidate_catalog(self):
        cases = [dict(view_revision=1), dict(context=replace(CONTEXT, session_epoch=2)),
                 dict(context=replace(CONTEXT, window_id="43:18")),
                 dict(context=replace(CONTEXT, ownership_generation=2)),
                 dict(context=replace(CONTEXT, frame_size=(3840, 2160))),
                 dict(context=replace(CONTEXT, layout="other"))]
        for changed in cases:
            with self.subTest(changed=changed):
                catalog = ScanCatalog()
                old = catalog.observe((image_observation(),), 10)[0]
                new = catalog.observe((image_observation(**changed),), 10)[0]
                self.assertNotEqual(old.candidate_id, new.candidate_id)
                self.assertIsNone(catalog.get(old.candidate_id, 10))

    def test_stale_future_and_out_of_order_images_reject(self):
        for now in (9.9, 12.):
            with self.assertRaisesRegex(ValueError, "stale_candidate_image"):
                ScanCatalog().observe((image_observation(),), now)
        catalog = ScanCatalog()
        catalog.observe((image_observation(acquisition_sequence=3),), 10)
        with self.assertRaisesRegex(ValueError, "out_of_order"):
            catalog.observe((image_observation(acquisition_sequence=2),), 10)

    def test_gap_does_not_reuse_scanned_or_respawn_identity(self):
        catalog = ScanCatalog()
        old = catalog.observe((image_observation(scan_state="scanned", resource="Water"),), 10)[0]
        self.assertEqual(catalog.entries(12), ())
        new = catalog.observe((image_observation(acquisition_sequence=2,
            acquired_at=12., image_reference="frames/new.png"),), 12)[0]
        self.assertNotEqual(old.candidate_id, new.candidate_id)
        self.assertEqual(new.scan_state, "unscanned")

    def test_ambiguous_overlap_rejected_without_creating_entries(self):
        catalog = ScanCatalog()
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            catalog.observe((image_observation(), image_observation(
                box=(101, 200, 161, 260), visual_signature="different")), 10)
        self.assertEqual(catalog.entries(10), ())

    def test_same_frame_cannot_be_relabelled_as_scan_completed(self):
        catalog = ScanCatalog()
        catalog.observe((image_observation(),), 10)
        with self.assertRaisesRegex(ValueError, "same_acquisition"):
            catalog.observe((image_observation(scan_state="scanned", resource="Water"),), 10)
        self.assertEqual(catalog.entries(10)[0].scan_state, "unscanned")

    def test_bad_boxes_ranges_resources_and_provenance_rejected(self):
        cases = [dict(box=(-1, 0, 60, 60)), dict(box=(0, 0, 3000, 60)),
                 dict(box=(1, 2, 1, 3)), dict(distance=float("nan")),
                 dict(distance=True), dict(resource="Water"),
                 dict(provenance="input_delivery"), dict(image_reference=" ")]
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                image_observation(**changed)


class ScanSweepTests(unittest.TestCase):
    def setUp(self):
        self.catalog = ScanCatalog()
        self.sweep = ScanSweep(self.catalog)

    def enable_candidate(self, **values):
        entry = self.catalog.observe((image_observation(**values),), 10)[0]
        self.sweep.set_enabled(True)
        return entry

    def test_unknown_discovery_is_honest_blocker_and_toggle_off_has_no_action(self):
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).action, "disabled")
        self.sweep.set_enabled(True)
        self.assertIn("discovery_unsupported", self.sweep.next_step(10, stationary_verified=True).reason)

    def test_supplied_catalog_select_then_scan_then_visual_completion(self):
        old = self.enable_candidate()
        step = self.sweep.next_step(10, stationary_verified=True)
        self.assertEqual((step.action, step.x, step.y), ("select", 130, 230))
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).reason, "action_in_flight")
        selected = image_observation(acquisition_sequence=2, acquired_at=10.3,
            image_reference="frames/selected.png", selected=True)
        self.sweep.record_selection(old.candidate_id, selected, now=10.3)
        self.assertEqual(self.sweep.next_step(10.3, stationary_verified=True).action, "scan")
        self.sweep.record_scan_attempt(old.candidate_id, delivery_confirmed=True, now=10.3)
        self.assertEqual(self.catalog.get(old.candidate_id, 10.3).scan_state, "unscanned")
        self.assertEqual(self.sweep.next_step(10.3, stationary_verified=True).reason, "scan_result_unverified")
        self.catalog.observe((replace(selected, acquisition_sequence=3, acquired_at=10.5,
            image_reference="frames/scanned.png", scan_state="scanned", resource="Water"),), 10.5)
        self.assertEqual(self.sweep.status(10.5)["visually_scanned"], 1)
        self.assertIn("catalog_exhausted", self.sweep.next_step(10.5, stationary_verified=True).reason)

    def test_equal_hp_or_other_selected_rock_cannot_bind_candidate(self):
        entry = self.enable_candidate()
        self.sweep.next_step(10, stationary_verified=True)
        for change in (dict(visual_signature="another-rock"), dict(box=(400, 200, 460, 260)),
                       dict(view_revision=1), dict(selected=False), dict(acquisition_sequence=1)):
            evidence = image_observation(acquisition_sequence=2, acquired_at=10.2,
                image_reference="frames/selected.png", selected=True)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.sweep.record_selection(entry.candidate_id, replace(evidence, **change), now=10.2)

    def test_scan_requires_explicit_range_and_unscanned_image_classification(self):
        self.enable_candidate(selected=True, distance=None)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).reason,
                         "selected_candidate_range_unverified")
        self.catalog.reset_view(CONTEXT, 1)
        self.catalog.observe((image_observation(view_revision=1, scan_state="unknown"),), 10)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).reason,
                         "unscanned_image_classification_unsupported")

    def test_nearest_unscanned_preferred_already_scanned_and_out_of_range_skipped(self):
        rows = [image_observation(box=(100+i*200, 200, 160+i*200, 260), **values)
                for i, values in enumerate([dict(distance=1904), dict(distance=900),
                    dict(distance=600, scan_state="scanned", resource="Titanium"), dict(distance=4001)])]
        entries = self.catalog.observe(rows, 10)
        self.sweep.set_enabled(True)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).candidate_id,
                         entries[1].candidate_id)

    def test_failed_scan_cannot_repeat_unchanged_candidate(self):
        entry = self.enable_candidate(selected=True)
        self.sweep.next_step(10, stationary_verified=True)
        self.sweep.record_scan_attempt(entry.candidate_id, delivery_confirmed=False, now=10)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).action, "blocked")

    def test_motion_cancel_and_toggle_off_never_propose_input(self):
        self.enable_candidate(selected=True)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=False).action, "blocked")
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True, cancelled=True).action, "disabled")
        self.assertFalse(self.sweep.enabled)

    def test_pending_old_location_is_invalidated_by_camera_or_staleness(self):
        self.enable_candidate()
        self.sweep.next_step(10, stationary_verified=True)
        self.catalog.reset_view(CONTEXT, 1)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).reason,
                         "pending_candidate_context_changed_or_stale")

    def test_unresolved_scan_does_not_stall_other_fresh_candidates(self):
        first, second = self.catalog.observe((image_observation(selected=True),
            image_observation(box=(400, 200, 460, 260), distance=2000)), 10)
        self.sweep.set_enabled(True)
        self.sweep.next_step(10, stationary_verified=True)
        self.sweep.record_scan_attempt(first.candidate_id, delivery_confirmed=True, now=10)
        self.assertEqual(self.sweep.next_step(10, stationary_verified=True).candidate_id, second.candidate_id)

    def test_continuous_sweep_memory_bounded_across_many_views_and_late_ack_rejected(self):
        for view in range(1000):
            now = 10. + view
            entry = self.catalog.observe((image_observation(view_revision=view,
                acquisition_sequence=view+1, acquired_at=now, selected=True),), now)[0]
            self.sweep.set_enabled(True)
            self.sweep.next_step(now, stationary_verified=True)
            self.sweep.record_scan_attempt(entry.candidate_id, delivery_confirmed=True, now=now)
            self.assertLessEqual(len(self.sweep.status(now)["attempted"]), 1)
            self.assertEqual(len(self.catalog._entries), 1)
        self.catalog.entries(now+2)
        self.assertEqual(len(self.catalog._entries), 0)
        self.assertEqual(self.sweep.status(now+2)["attempted"], {})
        entry = self.catalog.observe((image_observation(view_revision=1000, selected=True,
            acquired_at=now+3),), now+3)[0]
        self.sweep.next_step(now+3, stationary_verified=True)
        self.catalog.reset_view(CONTEXT, 1001)
        with self.assertRaisesRegex(ValueError, "acknowledgment_candidate_context"):
            self.sweep.record_scan_attempt(entry.candidate_id, delivery_confirmed=True, now=now+3)
        self.assertIsNone(self.sweep.status(now+3)["pending"])

    def test_expired_selection_acknowledgment_cannot_reacquire_old_location(self):
        entry = self.enable_candidate()
        self.sweep.next_step(10, stationary_verified=True)
        with self.assertRaisesRegex(ValueError, "context_changed_or_stale"):
            self.sweep.record_selection(entry.candidate_id, image_observation(
                acquisition_sequence=2, acquired_at=13., selected=True), now=13.)
        self.assertIsNone(self.sweep.status(13.)["pending"])

    def test_same_view_catalog_capacity_and_expiry_bound_storage(self):
        catalog = ScanCatalog(capacity=3)
        for index in range(20):
            catalog.observe((image_observation(acquisition_sequence=index+1,
                acquired_at=10.+index*.01, visual_signature=f"rock-{index}"),), 10.+index*.01)
            self.assertLessEqual(len(catalog._entries), 3)
        catalog.entries(13)
        self.assertEqual(catalog._entries, {})


if __name__ == "__main__":
    unittest.main()
