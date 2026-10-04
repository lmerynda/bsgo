"""Observer telemetry must describe actual probe evidence, never authorize input."""
import json
import unittest
from unittest.mock import patch

from bsgo_offline.diagnostics import DiagnosticRun
import test_approach_probe as approach
import test_firing_probe as firing
import test_scan_probe as scan
import test_selection_probe as selection
import test_throttle_probe as throttle
from test_trace import scratch_directory


class ExecutionTelemetryTests(unittest.TestCase):
    def record(self):
        updates = []
        original = DiagnosticRun.update_execution
        def observe(run, **values):
            original(run, **values)
            updates.append(run.read_status()["execution"])
        return updates, patch.object(DiagnosticRun, "update_execution", observe)

    def final(self, directory):
        return json.loads((directory / "run" / "status.json").read_text())

    def test_approach_reports_wait_motion_braking_cleanup_and_image_stop(self):
        updates, recording = self.record()
        with scratch_directory() as directory, recording:
            result, _, _, _ = approach.ApproachProbeTests().run_fixture(directory)
            self.assertEqual(result["state"], "proved", result)
            phases = [row["phase"] for row in updates]
            for phase in ("baseline", "cruising", "braking", "cleanup", "completed"):
                self.assertIn(phase, phases)
            baseline = next(row for row in updates if row["phase"] == "baseline")
            self.assertTrue(baseline["waiting_for"])
            self.assertIsNone(baseline["last_input_intent"])
            final = self.final(directory)
            self.assertEqual(final["execution"]["last_confirmed_effect"]["effect"],
                             "post_delivery_motion_and_stop_verified")
            self.assertEqual(final["execution"]["last_confirmed_effect"]["observation_sequences"],
                             result["stop_observation_sequences"][-2:])

    def test_bad_baseline_has_no_request_delivery_or_confirmed_effect(self):
        with scratch_directory() as directory:
            result, _, _, _ = approach.ApproachProbeTests().run_fixture(
                directory, source_options={"invalid_first": True})
            status = self.final(directory)
            execution = status["execution"]
            self.assertEqual(execution["phase"], "blocked")
            self.assertEqual(execution["first_blocker"], result["reason"])
            self.assertIsNone(execution["last_input_intent"])
            self.assertIsNone(execution["last_input_delivery"])
            self.assertIsNone(execution["last_confirmed_effect"])
            self.assertFalse(status["observation"]["fields"]["speed"]["valid"])
            self.assertEqual(status["last_retained_frame"]["state"], "written")

    def test_two_regime_reports_reducing_then_verified_slow_approach(self):
        updates, recording = self.record()
        with scratch_directory() as directory, recording:
            result, _, _, _ = approach.ApproachProbeTests().run_fixture(directory,
                distance=1560., brake_at=800., reduced_transition_at=1300.,
                sample_interval=5., max_thrust_seconds=None)
            self.assertEqual(result["state"], "proved", result)
            phases = [row["phase"] for row in updates]
            self.assertIn("reducing", phases)
            self.assertIn("slow_approach", phases)
            self.assertLess(phases.index("reducing"), phases.index("slow_approach"))
            transition = next(row for row in updates if row["phase"] == "reducing")
            self.assertIn("three", transition["waiting_for"])
            reduced = next(row for row in updates if row["phase"] == "slow_approach")
            self.assertEqual(reduced["last_confirmed_effect"]["effect"],
                             "post_wheel_reduced_speed_regime_verified")
            self.assertEqual(len(reduced["last_confirmed_effect"]["observation_sequences"]), 3)

    def test_canceled_probe_is_terminal_and_never_requests_firing(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            result, _ = firing.FiringProbeTests().run_probe(directory, firing.Source(), cancel_file=marker)
            self.assertTrue(result["cancel_requested"])
            execution = self.final(directory)["execution"]
            self.assertEqual(execution["phase"], "canceled")
            self.assertTrue(execution["terminal"])
            self.assertIsNone(execution["last_input_intent"])
            self.assertIsNone(execution["last_confirmed_effect"])

    def test_firing_ocr_recovery_retains_first_blocker_and_autofire_warning(self):
        updates, recording = self.record()
        with scratch_directory() as directory, recording:
            source = firing.Source(effect=False, hp_reader_fault_after=2, gone_after=8)
            result, _ = firing.FiringProbeTests().run_probe(
                directory, source, observe_until_target_gone=True)
            faults = [row for row in updates if row["phase"] == "ocr_blocked_readonly_recovery"]
            self.assertTrue(faults)
            self.assertTrue(faults[0]["waiting_for"])
            self.assertIsNone(faults[0]["last_confirmed_effect"])
            status = self.final(directory)
            self.assertEqual(status["execution"]["first_blocker"], result["first_blocker"])
            self.assertIn("autofire may continue beyond probe", status["execution"]["warnings"])
            self.assertEqual(status["execution"]["last_confirmed_effect"]["effect"],
                             "selection_absent_cause_unverified")
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(len(result["requests"]), 2)

    def test_selection_effect_and_scan_unverified_are_distinct(self):
        with scratch_directory() as directory:
            helper = selection.SelectionProbeTests()
            result = helper.run_probe(directory, helper.changed_source())
            self.assertTrue(result["selection_effect_verified"])
            self.assertEqual(self.final(directory)["execution"]["last_confirmed_effect"]["effect"],
                             "changed_target_observed_after_click")
        updates, recording = self.record()
        with scratch_directory() as directory, recording:
            result, _ = scan.ScanProbeTests().run_probe(directory, scan.PixelSource())
            self.assertFalse(result["scan_effect_verified"])
            self.assertIn("scanning", [row["phase"] for row in updates])
            self.assertIsNone(self.final(directory)["execution"]["last_confirmed_effect"])

    def test_throttle_reports_image_motion_and_stop_without_mission_claim(self):
        updates, recording = self.record()
        with scratch_directory() as directory, recording:
            result, _, _, _ = throttle.ThrottleProbeTests().run_fixture(directory)
            self.assertEqual(result["state"], "proved", result)
            phases = [row["phase"] for row in updates]
            for phase in ("baseline", "slow_approach", "braking", "cleanup", "completed"):
                self.assertIn(phase, phases)
            self.assertEqual(self.final(directory)["execution"]["last_confirmed_effect"]["effect"],
                             "post_delivery_partial_motion_and_stop_verified")
            self.assertFalse(result["mining_ready"])
