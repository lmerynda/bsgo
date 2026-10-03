import json
import unittest

from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.startup_diagnostics import assess_startup, run_startup, reassess_saved_run
from bsgo_offline.state import FieldValue
from test_diagnostics import NativeFixtureSource
from test_trace import scratch_directory


class FixtureRecognizer:
    capabilities = {"speed": True, "distance": True, "asteroid_selected": True}

    def __call__(self, sample):
        return RecognitionResult(sample.frame_size, {
            "speed": FieldValue(0., sample.acquired_at),
            "distance": FieldValue(1400., sample.acquired_at),
            "asteroid_selected": FieldValue(False, sample.acquired_at),
            "ocr_evidence": FieldValue({"calibration_profile": "fixture", "resampling": "none"},
                                        sample.acquired_at)})


class StartupDiagnosticsTests(unittest.TestCase):
    def test_multiple_frames_have_hashed_evidence_and_control_blockers(self):
        with scratch_directory() as directory:
            output = directory / "run"
            assessment = run_startup(output, source_factory=NativeFixtureSource,
                recognizer_factory=FixtureRecognizer, configuration={"profile": "fixture"},
                duration=3, snapshot_limit=3, expected_window_id="2:1")
            self.assertEqual(assessment["capture"]["observed_frames"], 3)
            self.assertEqual(assessment["capture"]["retained_hashed_frames"], 3)
            self.assertTrue(assessment["read_checks_pass"], assessment["read_checks"])
            self.assertFalse(assessment["control_checks_pass"])
            self.assertFalse(assessment["ready_for_control_experiment"])
            blockers = {check["name"] for check in assessment["control_checks"]
                        if check["state"] != "ready"}
            self.assertTrue({"ownership", "selection", "route", "equipment", "binding:scan",
                             "recognizer:scan_result"} <= blockers)
            self.assertEqual(assessment["fields"]["speed"]["observed_values"], [0., 0., 0.])
            self.assertIn("hud-reader/restart-calibration.json",
                          assessment["source_identity"]["calibration_sha256"])
            self.assertEqual(json.loads((output / "startup-calibration.json").read_text()), assessment)
            self.assertEqual(json.loads((output / "report.json").read_text())["input_requests"], [])
            self.assertEqual(reassess_saved_run(output), assessment)

            original = (output / "startup-calibration.json").read_bytes()
            first_frame = output / "frames" / "observation-00000001.png"
            with first_frame.open("ab") as stream:
                stream.write(b"tampered")
            rechecked = reassess_saved_run(output)
            self.assertEqual(rechecked["capture"]["retained_hashed_frames"], 2)
            self.assertFalse(rechecked["read_checks_pass"])
            self.assertEqual((output / "startup-calibration.json").read_bytes(), original)

    def test_startup_capture_failure_is_reported_without_input(self):
        with scratch_directory() as directory:
            output = directory / "run"
            def fail():
                raise OSError("fixture capture unavailable")
            assessment = run_startup(output, source_factory=fail,
                recognizer_factory=FixtureRecognizer, configuration={"profile": "fixture"},
                snapshot_limit=2)
            self.assertEqual(assessment["capture"]["observed_frames"], 0)
            self.assertFalse(assessment["read_checks_pass"])
            self.assertFalse(assessment["control_checks_pass"])
            self.assertEqual(json.loads((output / "report.json").read_text())["input_requests"], [])

    def test_repeatability_does_not_override_unvalidated_control_capabilities(self):
        manifest = {"configuration": {"profile": "fixture"}, "source_sha256": {},
                    "code_revision": "test"}
        frame = {"context": {"window_id": "2:1", "frame_size": [4, 3]},
                 "state": "queued"}
        snapshot = {"healthy": True, "fields": {"speed": {"valid": True, "value": 0.},
            "distance": {"valid": True, "value": 1000.},
            "asteroid_selected": {"valid": True, "value": False},
            "ocr_evidence": {"valid": True, "value": {"calibration_profile": "fixture"}}}}
        observations = [{"snapshot": {**snapshot, "sequence": sequence},
            "frame": {**frame, "path": f"frames/observation-{sequence:08d}.png"}}
            for sequence in (1, 2)]
        report = {"preflight": {"checks": [{"name": "ownership", "state": "unknown",
            "reason": "ownership_unknown"}]}, "worker": {"error": None,
            "diagnostic_dropped": 0}, "shutdown": {"recognition": True},
            "input_pending": 0, "input_requests": [], "complete_diagnostics": True}
        assessment = assess_startup(report, manifest, observations,
            [{"observation_sequence": 1, "path": "frames/observation-00000001.png"}],
            expected_frames=2, expected_window_id="2:1")
        self.assertFalse(assessment["read_checks_pass"])
        self.assertFalse(assessment["control_checks_pass"])
        self.assertFalse(assessment["ready_for_control_experiment"])
        self.assertEqual(assessment["fields"]["speed"]["valid_frames"], 2)

    def test_hashes_must_match_the_exact_observation_and_frame_path(self):
        manifest = {"configuration": {"profile": "fixture"}, "source_sha256": {}}
        context = {"window_id": "2:1", "frame_size": [4, 3]}
        snapshot = {"sequence": 1, "healthy": True, "fields": {"ocr_evidence": {
            "valid": True, "value": {"calibration_profile": "fixture"}}}}
        observation = {"snapshot": snapshot, "frame": {"context": context,
            "state": "queued", "path": "frames/observation-00000001.png"}}
        report = {"preflight": {"checks": []}, "worker": {"error": None,
            "diagnostic_dropped": 0}, "shutdown": {"recognition": True},
            "input_pending": 0, "input_requests": [], "complete_diagnostics": True}
        wrong_frame = {"observation_sequence": 1, "path": "frames/unrelated.png"}
        result = assess_startup(report, manifest, [observation], [wrong_frame],
                                expected_frames=1, expected_window_id="2:1")
        retained = next(check for check in result["read_checks"]
                        if check["name"] == "retained_pixels")
        self.assertEqual(retained["state"], "blocked")
        self.assertEqual(result["capture"]["retained_hashed_frames"], 0)


if __name__ == "__main__":
    unittest.main()
