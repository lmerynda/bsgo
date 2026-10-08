from dataclasses import replace
import json
from pathlib import Path
import unittest

from PIL import Image

from bsgo_offline.scan_catalog import CandidateObservation
from bsgo_offline.selected_scan_vision import (classify_selected_marker,
    verify_selected_scan_transition, locate_selected_marker)
from bsgo_offline.state import ObservationContext
from bsgo_offline.tracking import SelectionTrack


FIXTURES = Path(__file__).parent / "fixtures" / "selected_scan_markers"


class SelectedScanVisionTests(unittest.TestCase):
    def image(self, number, state):
        with Image.open(FIXTURES / f"scan{number}-{state}.png") as image:
            return image.copy()

    def pair(self, number=37):
        before_image, after_image = self.image(number, "unscanned"), self.image(number, "scanned")
        context = ObservationContext(1, "native-marker-fixture", before_image.size, "42:17", 1)
        before = CandidateObservation(context, 0, 1, 10., "native-before.png",
            (0, 0, *before_image.size), "reviewed-rock-outline", 1904, "unscanned", selected=True)
        after = replace(before, acquisition_sequence=21, acquired_at=18.,
            image_reference="native-after.png", scan_state="unknown")
        track = SelectionTrack(1, context, 9., 10., "asteroid:hp:3344", (0,)*144, 1)
        return before_image, after_image, dict(before=before, after=after,
            before_track=track, after_track=replace(track, last_evidence_at=18.))

    def test_annotated_native_baselines_and_scan_textures(self):
        metadata = json.loads((FIXTURES / "annotations.json").read_text())
        self.assertEqual(len(metadata["fixtures"]), 6)
        for number in (36, 37, 38):
            for state, expected in (("unscanned", "unknown"), ("scanned", "scanned")):
                with self.subTest(number=number, state=state):
                    image = self.image(number, state)
                    result = classify_selected_marker(image, (0, 0, *image.size), marker_location_verified=True)
                    self.assertEqual(result.scan_state, expected)

    def test_native_transition_requires_reviewed_baseline_and_continuity(self):
        for number in (36, 37, 38):
            before_image, after_image, evidence = self.pair(number)
            result = verify_selected_scan_transition(before_image, after_image, **evidence)
            self.assertEqual(result.scan_state, "scanned")
            self.assertIsNone(result.resource)

    def test_cursor_halo_and_unverified_roi_are_unknown(self):
        image = self.image(36, "unscanned")
        self.assertEqual(classify_selected_marker(image, (0, 0, *image.size),
            marker_location_verified=True).scan_state, "unknown")
        image = self.image(36, "scanned")
        self.assertEqual(classify_selected_marker(image, (0, 0, *image.size)).scan_state, "unknown")

    def test_gray_missing_white_cursor_and_small_bright_patch_remain_unknown(self):
        for color in ((30, 30, 30), (255, 255, 255), (10, 60, 110)):
            image = Image.new("RGB", (60, 40), color)
            self.assertEqual(classify_selected_marker(image, (0, 0, 60, 40),
                marker_location_verified=True).scan_state, "unknown")
        image = Image.new("RGB", (60, 40), (0, 0, 0))
        image.paste((60, 200, 255), (0, 0, 10, 10))
        self.assertEqual(classify_selected_marker(image, (0, 0, 60, 40),
            marker_location_verified=True).scan_state, "unknown")

    def test_context_camera_reselection_gap_and_bad_range_block_transition(self):
        before_image, after_image, evidence = self.pair()
        changes = [dict(after=replace(evidence["after"], view_revision=1)),
                   dict(after=replace(evidence["after"], selected=False)),
                   dict(after=replace(evidence["after"], visual_signature="other-rock")),
                   dict(after=replace(evidence["after"], distance=2000)),
                   dict(after=replace(evidence["after"], acquisition_sequence=1)),
                   dict(after=replace(evidence["after"], image_reference="native-before.png")),
                   dict(before=replace(evidence["before"], scan_state="unknown")),
                   dict(after_track=replace(evidence["after_track"], track_id=2)),
                   dict(after_track=replace(evidence["after_track"], continuity="uncertain")),
                   dict(after_track=replace(evidence["after_track"], established_at=17.))]
        for change in changes:
            with self.subTest(change=change):
                result = verify_selected_scan_transition(before_image, after_image, **(evidence | change))
                self.assertEqual(result.scan_state, "unknown")

    def test_already_scanned_baseline_and_unseen_effect_do_not_prove_new_scan(self):
        before_image, after_image, evidence = self.pair()
        self.assertEqual(verify_selected_scan_transition(after_image, after_image,
            **evidence).reason, "baseline_already_has_scan_texture")
        self.assertEqual(verify_selected_scan_transition(before_image, before_image,
            **evidence).scan_state, "unknown")

    def test_wrong_image_geometry_and_invalid_crop_stay_unknown(self):
        before_image, after_image, evidence = self.pair()
        self.assertEqual(verify_selected_scan_transition(before_image.resize((10, 10)),
            after_image, **evidence).reason, "image_context_geometry_mismatch")
        self.assertEqual(classify_selected_marker(after_image, (-1, 0, 30, 30),
            marker_location_verified=True).scan_state, "unknown")

    def test_native_marker_fragments_located_at_varied_new_coordinates(self):
        for index, (number, state) in enumerate((n, s) for n in (36, 37, 38)
                                              for s in ("unscanned", "scanned")):
            with Image.open(FIXTURES / f"scan{number}-{state}-marker.png") as fragment:
                image = Image.new("RGB", (2560, 1440))
                x, y = 300+index*250, 300+index*70
                image.paste(fragment, (x, y))
            found = locate_selected_marker(image, selected_asteroid_verified=True)
            self.assertEqual(found.label_matches, 1, (number, state, found))
            self.assertIsNotNone(found.box)
            classified = classify_selected_marker(image, found.box, marker_location_verified=True)
            self.assertEqual(classified.scan_state, "scanned" if state == "scanned" else "unknown")

    def test_zero_multiple_and_hud_only_labels_do_not_locate_target(self):
        with Image.open(FIXTURES / "scan37-scanned-marker.png") as fragment:
            image = Image.new("RGB", (2560, 1440))
            image.paste(fragment, (300, 30))
            self.assertIsNone(locate_selected_marker(image, selected_asteroid_verified=True).box)
            image.paste(fragment, (300, 300))
            image.paste(fragment, (1200, 700))
            found = locate_selected_marker(image, selected_asteroid_verified=True)
            self.assertEqual(found.label_matches, 2)
            self.assertIsNone(found.box)
        self.assertIsNone(locate_selected_marker(Image.new("RGB", (2560, 1440)),
            selected_asteroid_verified=True).box)

    def test_reviewed_dim_exrun_label_and_clipped_letters(self):
        with Image.open(FIXTURES / "exrun-dim-marker.png") as source:
            fragment = source.copy()
        image = Image.new("RGB", (2560, 1440))
        image.paste(fragment, (300, 300))
        found = locate_selected_marker(image, selected_asteroid_verified=True)
        self.assertEqual(found.label_matches, 1)
        self.assertEqual(found.box, (335, 347, 391, 391))
        # A dim partial word is still insufficient evidence.
        fragment.paste((0, 0, 0), (103, 11, 114, 27))
        image.paste(fragment, (300, 300))
        self.assertIsNone(locate_selected_marker(image, selected_asteroid_verified=True).box)

    def test_locator_requires_selected_hud_and_native_geometry(self):
        image = Image.new("RGB", (2560, 1440))
        self.assertEqual(locate_selected_marker(image).reason, "selected_asteroid_unverified")
        self.assertIsNone(locate_selected_marker(image.resize((1920, 1080)),
            selected_asteroid_verified=True).box)

    def test_clipped_label_cannot_pass_on_high_total_template_similarity(self):
        with Image.open(FIXTURES / "scan37-scanned-marker.png") as source:
            fragment = source.copy()
        # Erase the final D (only about 10% of all template ink).
        fragment.paste((0, 0, 0), (93, 2, 104, 18))
        image = Image.new("RGB", (2560, 1440))
        image.paste(fragment, (300, 300))
        self.assertIsNone(locate_selected_marker(image, selected_asteroid_verified=True).box)


if __name__ == "__main__":
    unittest.main()
