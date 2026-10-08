"""Native resource-color regressions, plumbing, and rejection boundaries."""
from dataclasses import replace
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

from PIL import Image

from bsgo_offline.easy_launcher import _marker_reader
from bsgo_offline.manual_assist_session import SelectionEvidenceRecognizer
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.selected_scan_vision import classify_selected_marker
from bsgo_offline.state import FieldValue
from test_cockpit_contract import cockpit
from test_manual_assist import snapshot
from test_observation_api import providers, project


FIXTURES = Path(__file__).parent / "fixtures"


class SelectedScanResourceTests(unittest.TestCase):
    def test_reviewed_native_resources_and_world_label_pipeline(self):
        for folder, number, expected in (("selected_scan_markers", 36, "Water"),
                ("selected_scan_markers", 37, "Water"),
                ("selected_scan_markers", 38, "Titanium"),
                ("selected_scan_resources", 19, "Tylium"),
                ("selected_scan_resources", 25, "empty")):
            with self.subTest(number=number):
                with Image.open(FIXTURES / folder / f"scan{number}-scanned.png") as texture:
                    reading = classify_selected_marker(texture, (0, 0, *texture.size),
                        marker_location_verified=True)
                    self.assertEqual(reading.resource, expected)
                with Image.open(FIXTURES / folder / f"scan{number}-scanned-marker.png") as fragment:
                    image = Image.new("RGB", (2560, 1440))
                    image.paste(fragment, (600, 300))
                at = time.monotonic()
                sample = SimpleNamespace(acquired_at=at, acquisition_sequence=1,
                    session_epoch=1, layout="native", window_id="42:17", to_image=lambda:image)
                fields = dict(snapshot(at=at).fields)
                base = lambda _: RecognitionResult(image.size, fields, True, None)
                reader = SelectionEvidenceRecognizer(base, marker_reader=_marker_reader)
                result = reader(sample)
                self.assertEqual(result.fields["selected_scan_resource"].value, expected)
                self.assertEqual(result.fields["selected_scan_resource"].observed_at, at)
                self.assertTrue(result.fields["selected_scan_resource"].valid)
                # Losing the selected asteroid clears the resource immediately.
                fields["asteroid_selected"] = FieldValue(False, at)
                self.assertIsNone(reader(sample).fields["selected_scan_resource"].value)
                fields["asteroid_selected"] = FieldValue(True, at - 1)
                self.assertIsNone(reader(sample).fields["selected_scan_resource"].value)

    def test_cursor_gray_small_and_mixed_color_textures_remain_unknown(self):
        for number in (36, 37, 38):
            with Image.open(FIXTURES / "selected_scan_markers" / f"scan{number}-unscanned.png") as image:
                self.assertIsNone(classify_selected_marker(image, (0, 0, *image.size),
                    marker_location_verified=True).resource)
        image = Image.new("RGB", (56, 44), (30, 30, 30))
        image.paste((230, 210, 70), (0, 0, 10, 10))
        self.assertIsNone(classify_selected_marker(image, (0, 0, 56, 44),
            marker_location_verified=True).resource)
        image.paste((80, 210, 255), (0, 0, 28, 44))
        image.paste((80, 70, 255), (28, 0, 56, 44))
        self.assertIsNone(classify_selected_marker(image, (0, 0, 56, 44),
            marker_location_verified=True).resource)
        self.assertIsNone(classify_selected_marker(image, (0, 0, 56, 44)).resource)

    def test_api_requires_same_image_selected_scan_and_supported_resource(self):
        for change in (None, "stale", "other_image", "no_target", "no_scan", "bad_scan", "bad_resource", "geometry"):
            startup, mode, actions = providers()
            fields = actions["current_observation"]["fields"]
            actions["current_observation"]["frame_size"] = [2560, 1440]
            fields["selected_marker_scan_state"] = {**fields["speed"], "value": "scanned"}
            fields["selected_scan_resource"] = {**fields["speed"], "value": "Water"}
            if change == "stale":
                actions["current_observation"]["age_seconds"] = 3
            elif change == "other_image":
                fields["selected_scan_resource"]["observed_at"] -= .1
            elif change == "no_target":
                fields["asteroid_selected"]["value"] = False
            elif change == "no_scan":
                del fields["selected_marker_scan_state"]
            elif change == "bad_scan":
                fields["selected_marker_scan_state"]["valid"] = False
            elif change == "bad_resource":
                fields["selected_scan_resource"]["value"] = "Copper"
            elif change == "geometry":
                actions["current_observation"]["frame_size"] = [1920, 1080]
            row = project(startup, mode, actions)["observation"]["fields"]["selected_scan_resource"]
            self.assertEqual(row["usable"], change is None, change)
            self.assertEqual(row["value"], "Water" if change is None else None, change)

    def test_paused_http_publishes_resource_without_mining_authority(self):
        with cockpit() as (fixture, _):
            at = fixture.row.acquired_at
            fixture.row = replace(fixture.row, frame_size=(2560, 1440), fields={**fixture.row.fields,
                "selected_marker_scan_state": FieldValue("scanned", at),
                "selected_scan_resource": FieldValue("Tylium", at)})
            raw = fixture.state()["current_observation"]["fields"]
            self.assertEqual(raw["selected_scan_resource"]["value"], "Tylium")
            state = fixture.http("/api/observation")[1]
            self.assertEqual(state["observation"]["fields"]["selected_scan_resource"]["value"], "Tylium")
            self.assertEqual(state["control"]["input_owner"], "human")
            self.assertEqual(fixture.calls, [])
            self.assertIsNone(state["evidence"]["last_confirmed_effect"])
