import hashlib
import json
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import patch

from bsgo_offline.diagnostics import DiagnosticRun, recognition_failures
from bsgo_offline.execution_status import ExecutionStatus
from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.diagnose import run_preflight
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.recognition import RecognitionResult, RecognitionWorker
from bsgo_offline.state import FieldValue, SnapshotStore
from bsgo_offline.trace import iter_trace_events
from runtime_fixture import observation
from test_trace import scratch_directory


class NativeFixtureSource:
    def __init__(self, count=3):
        self.count, self.sequence = count, 0
        self.closed = False
    def next_sample(self, stop):
        if self.sequence >= self.count or stop.is_set():
            return None
        self.sequence += 1
        return NativeFrame(self.sequence, 1, "fixture", time.monotonic(), 4, 3, bytes([17] * 36),
            WindowGeometry(1, 2, "fixture", (4, 3), (-2000, 100)))
    def close(self):
        self.closed = True


def recognize_fixture(sample):
    return RecognitionResult(sample.frame_size, {"speed": FieldValue(0., sample.acquired_at)})


class DiagnosticTests(unittest.TestCase):
    def test_execution_phase_clock_blocker_and_image_proof_are_independent(self):
        status = ExecutionStatus()
        status.update(phase="baseline", waiting_for="two fresh stopped images")
        entered = status.read()["phase_entered_monotonic"]
        status.update(phase="baseline", blocker="unreadable_speed", progress="new image")
        status.update(phase="braking", progress="brake requested")
        value = status.read(intent={"sequence": 3}, delivery={"outcome": "inserted"})
        self.assertEqual(value["first_blocker"], "unreadable_speed")
        self.assertEqual(value["last_progress"]["message"], "brake requested")
        self.assertIsNone(value["last_confirmed_effect"])
        self.assertGreaterEqual(value["phase_entered_monotonic"], entered)
        status.update(phase="completed", terminal=True,
                      confirmed_effect={"observation_sequence": 9, "effect": "two_zero_images"})
        status.update(phase="cruising")
        self.assertEqual(status.read()["phase"], "completed")
        self.assertEqual(status.read()["last_confirmed_effect"]["observation_sequence"], 9)

    def test_status_exports_stale_current_and_unsupported_fields_honestly(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture",
                                configuration={"maximum_image_age": 7})
            store = SnapshotStore()
            store.publish(observation(at=10, fields={
                "speed": FieldValue(5, 9),
                "hull_current": FieldValue(5005, 10),
                "scan_result": FieldValue(None, 10, False, "mining_recognizer_not_validated:scan_result"),
                "target_max_hp": FieldValue(4500, 10),
                "ocr_evidence": FieldValue({"speed_confidence": .98, "target_hp_confidence": .99}, 10)}))
            run._store = store
            with patch("bsgo_offline.diagnostics.time.monotonic", return_value=16.5):
                value = run.read_status()["observation"]
            self.assertTrue(value["fields"]["hull_current"]["fresh"])
            self.assertFalse(value["fields"]["speed"]["fresh"])
            self.assertFalse(value["fields"]["speed"]["observed_in_current_image"])
            self.assertEqual(value["fields"]["speed"]["confidence"], .98)
            self.assertEqual(value["fields"]["target_max_hp"]["confidence"], .99)
            self.assertIsNone(value["fields"]["hull_current"]["confidence"])
            self.assertFalse(value["fields"]["scan_result"]["valid"])
            self.assertEqual(value["maximum_image_age_seconds"], 7)
            run.close(shutdown={})

    def test_status_distinguishes_input_intent_partial_delivery_and_effect(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            backend = RecordingInputBackend(session_id="fixture")
            backend.key_down("G", reason="test_request")
            delivery = DeliveryRecord(1, time.monotonic(), "key_down", "partial", 1, 0, 0)
            class Dispatcher:
                def deliveries(self):
                    return (delivery,)
            run._backend = backend
            run.attach_dispatcher(Dispatcher())
            value = run.read_status()["execution"]
            self.assertEqual(value["last_input_intent"]["key"], "G")
            self.assertEqual(value["last_input_delivery"]["outcome"], "partial")
            self.assertIsNone(value["last_confirmed_effect"])
            backend.cancel(reason="fixture_cleanup")
            backend.drain()
            run.close(shutdown={}, result={"state": "blocked", "reason": "no_delivery",
                "warning": "autofire may continue beyond probe",
                "firing_state_after_probe": "unverified_supervisor_retains_control"})
            final = json.loads((directory / "run" / "status.json").read_text())
            self.assertEqual(final["execution"]["phase"], "blocked")
            self.assertTrue(final["execution"]["terminal"])
            self.assertIn("autofire may continue beyond probe", final["execution"]["warnings"])

    def test_broken_delivery_telemetry_is_unknown_and_cannot_break_cleanup(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            class Dispatcher:
                def deliveries(self):
                    raise OSError("fixture delivery read failure")
            run.attach_dispatcher(Dispatcher())
            status = run.read_status()
            self.assertIsNone(status["execution"]["last_input_delivery"])
            self.assertIn("fixture delivery read failure", status["input_delivery_read_error"])
            report = run.close(shutdown={}, result={"state": "proved", "reason": "fixture_proof"})
            self.assertFalse(report["complete_diagnostics"])
            self.assertEqual(report["execution"]["phase"], "blocked")

    def test_nonfinite_observation_is_unknown_and_status_remains_serializable(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            store = SnapshotStore()
            store.publish(observation(at=time.monotonic(), fields={
                "hull_current": FieldValue(float("inf"), time.monotonic()),
                "ocr_evidence": FieldValue({"speed_confidence": float("nan")}, time.monotonic())}))
            run._store = store
            status = run._publish_status()
            field = status["observation"]["fields"]["hull_current"]
            self.assertIsNone(field["value"])
            self.assertFalse(field["valid"])
            self.assertTrue(field["source_valid"])
            self.assertEqual(field["reason"], "nonfinite_observation_value")
            self.assertIsNone(status["observation"]["fields"]["speed"]["confidence"])
            run.close(shutdown={}, result={"state": "blocked", "reason": "invalid_hull"})

    def test_status_replacement_recovers_brief_reader_sharing_conflict(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            replace = Path.replace
            attempts = []
            def transient(path, target):
                attempts.append(path)
                if len(attempts) < 3:
                    raise PermissionError("reader sharing conflict")
                return replace(path, target)
            with patch.object(Path, "replace", transient):
                run._publish_status()
            self.assertEqual(len(attempts), 3)
            self.assertEqual(run.read_status()["status_write_errors"], 0)
            self.assertTrue((directory / "run" / "status.json").is_file())
            with patch.object(Path, "replace", side_effect=PermissionError("persistent denial")) as failed:
                with self.assertRaisesRegex(PermissionError, "persistent denial"):
                    run._publish_status()
                self.assertEqual(failed.call_count, 5)
            run.close(shutdown={})

    def test_explicit_unbounded_routine_frame_count_retains_all_frames(self):
        with scratch_directory() as directory:
            source = NativeFixtureSource(80)
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={},
                                max_frames=None, frame_capacity=96)
            for sequence in range(1, 81):
                run.observation(observation(sequence), (), source.next_sample(Event()), {})
            report = run.close(shutdown={}, timeout=5)
            self.assertTrue(report["complete_diagnostics"], report)
            self.assertIsNone(report["frames"]["max_frames"])
            self.assertEqual(report["frames"]["written"], 80)
            self.assertEqual(report["frames"]["budget_skipped"], 0)
            self.assertEqual(len(list((directory / "run" / "frames").glob("*.png"))), 80)

    def test_finite_frame_budget_still_requires_positive_integer(self):
        with scratch_directory() as directory:
            for value in (0, -1, True, 1.5):
                with self.assertRaisesRegex(ValueError, "max_frames"):
                    DiagnosticRun(directory / "run", mode="fixture", configuration={},
                                  max_frames=value)

    def test_every_failed_image_survives_routine_budget_with_metadata(self):
        with scratch_directory() as directory:
            source = NativeFixtureSource(21)
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={},
                                max_frames=1, frame_capacity=32)
            run.observation(observation(), (), source.next_sample(Event()), {})
            for sequence in range(2, 22):
                snapshot = observation(sequence, fields={"speed":
                    FieldValue(None, 1., False, "incomplete_speed_four")})
                run.observation(snapshot, (), source.next_sample(Event()), {})
            report = run.close(shutdown={})
            self.assertTrue(report["complete_diagnostics"], report)
            failed = report["frames"]["failed_recognition"]
            self.assertEqual((failed["observed"], failed["written"], failed["missing"]), (20, 20, 0))
            self.assertIsNone(failed["count_budget"])
            self.assertTrue(failed["complete"])
            for sequence in range(2, 22):
                stem = directory / "run" / "failed-recognition" / f"observation-{sequence:08d}"
                metadata = json.loads(stem.with_suffix(".json").read_text())
                self.assertEqual(metadata["sha256"], hashlib.sha256(stem.with_suffix(".png").read_bytes()).hexdigest())
                self.assertEqual(metadata["recognition_failures"], {"speed": "incomplete_speed_four"})
                self.assertEqual(metadata["snapshot"]["sequence"], sequence)
                self.assertEqual(metadata["run_id"], report["run_id"])
                self.assertIn("hud-reader/hud_reader.py", metadata["source_sha256"])

    def test_unimplemented_fields_and_absent_target_are_not_recognition_failures(self):
        snapshot = observation(fields={
            "scan_result": FieldValue(None, 1., False, "mining_recognizer_not_validated:scan_result"),
            "asteroid_selected": FieldValue(False, 1.),
            "target_identity": FieldValue(None, 1., False, "asteroid_title_not_verified")})
        self.assertEqual(recognition_failures(snapshot), {})

    def test_unknown_selected_scan_display_is_not_archived_but_hud_misreads_are(self):
        display = {"selected_marker_scan_state": FieldValue(None, 1., False, "selected_marker_absent_or_multiple"),
                   "selected_scan_resource": FieldValue(None, 1., False, "resource_color_unverified")}
        self.assertEqual(recognition_failures(observation(fields=display)), {})
        misread = observation(fields={**display,
            "distance": FieldValue(None, 1., False, "distance_spacing_invalid")})
        self.assertEqual(recognition_failures(misread), {"distance": "distance_spacing_invalid"})

    def test_failure_image_backpressure_is_reported_without_blocking_capture(self):
        with scratch_directory() as directory:
            gate, entered = Event(), Event()
            original = NativeFrame.to_image
            def slow(frame):
                entered.set()
                gate.wait(2)
                return original(frame)
            source = NativeFixtureSource(5)
            with patch.object(NativeFrame, "to_image", slow):
                run = DiagnosticRun(directory / "run", mode="fixture", configuration={}, frame_capacity=1)
                try:
                    bad = {"speed": FieldValue(None, 1., False, "unreadable")}
                    run.observation(observation(fields=bad), (), source.next_sample(Event()), {})
                    self.assertTrue(entered.wait(1))
                    self.assertIsNone(run.read_status()["last_retained_frame"])
                    run.update_execution(phase="ocr_blocked_readonly_recovery",
                                         blocker="unreadable", waiting_for="a readable image")
                    self.assertEqual(run.read_status()["execution"]["phase"],
                                     "ocr_blocked_readonly_recovery")
                    for sequence in range(2, 6):
                        run.observation(observation(sequence, fields=bad), (), source.next_sample(Event()), {})
                finally:
                    gate.set()
                    report = run.close(shutdown={})
            self.assertFalse(report["complete_diagnostics"])
            retained = report["last_retained_frame"]
            self.assertEqual(retained["state"], "written")
            self.assertTrue((directory / "run" / retained["path"]).is_file())
            self.assertTrue((directory / "run" / retained["sidecar"]).is_file())
            self.assertEqual(report["execution"]["first_blocker"], "unreadable")
            self.assertEqual(report["frames"]["failed_recognition"]["observed"], 5)
            self.assertEqual(report["frames"]["failed_recognition"]["dropped"], 3)
            self.assertEqual(report["frames"]["failed_recognition"]["missing"], 3)

    def test_failure_sidecar_write_error_cannot_claim_complete_archive(self):
        with scratch_directory() as directory:
            source = NativeFixtureSource(1)
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            with patch("bsgo_offline.diagnostics._write_json", side_effect=OSError("sidecar disk failure")):
                run.observation(observation(fields={"speed": FieldValue(None, 1., False, "unreadable")}),
                                (), source.next_sample(Event()), {})
                run._writer.join(1)
                self.assertFalse(run._writer.is_alive())
            report = run.close(shutdown={})
            self.assertFalse(report["complete_diagnostics"])
            self.assertEqual(report["frames"]["failed_recognition"]["missing"], 1)
            self.assertIn("sidecar disk failure", report["frames"]["error"])

    def test_recognizer_exception_retains_its_native_image(self):
        with scratch_directory() as directory:
            def fail(sample):
                raise ValueError("fixture recognizer error")
            report = run_preflight(directory / "run", source_factory=NativeFixtureSource,
                recognizer_factory=lambda: fail, configuration={}, snapshot_limit=3)
            failed = report["frames"]["failed_recognition"]
            self.assertEqual((failed["observed"], failed["written"]), (1, 1))
            self.assertTrue(failed["complete"])
            metadata = json.loads((directory / "run" / "failed-recognition" /
                                   "observation-00000001.json").read_text())
            self.assertIn("fixture recognizer error", metadata["recognition_failures"]["observation"])

    def test_failed_observation_without_pixels_has_incomplete_image_archive(self):
        with scratch_directory() as directory:
            run = DiagnosticRun(directory / "run", mode="fixture", configuration={})
            run.observation(observation(fields={"speed": FieldValue(None, 1., False, "unreadable")}),
                            (), None, {})
            report = run.close(shutdown={})
            failed = report["frames"]["failed_recognition"]
            self.assertEqual(failed["missing"], 1)
            self.assertFalse(failed["complete"])

    def test_native_observation_history_pixels_and_live_status_are_retained(self):
        with scratch_directory() as directory:
            run_directory = directory / "run"
            report = run_preflight(run_directory, source_factory=NativeFixtureSource,
                recognizer_factory=lambda: recognize_fixture, configuration={"test": True}, snapshot_limit=3)
            self.assertTrue(report["result"]["capture_published"])
            self.assertTrue(report["complete_diagnostics"], report)
            self.assertEqual(report["input_requests"], [])
            self.assertIsNone(report["runtime"]["owned"])
            self.assertFalse(report["preflight"]["ready"])
            manifest = json.loads((run_directory / "manifest.json").read_text())
            self.assertIn("bsgo_offline/diagnose.py", manifest["source_sha256"])
            self.assertIn("hud-reader/restart-calibration.json", manifest["source_sha256"])
            self.assertIn("hud-reader/templates.json", manifest["source_sha256"])
            self.assertEqual(report["input_pending"], 0)
            events = list(iter_trace_events(run_directory / "trace", report["run_id"]))
            observations = [event["payload"] for event in events if event["payload"]["event"] == "observation"]
            self.assertEqual([event["snapshot"]["sequence"] for event in observations], [1, 2, 3])
            frames = [event["payload"] for event in events if event["payload"]["event"] == "frame_retained"]
            self.assertEqual(len(frames), 3)
            for frame in frames:
                path = run_directory / frame["path"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frame["sha256"])
                self.assertEqual(frame["context"]["window_id"], "2:1")
            self.assertEqual(json.loads((run_directory / "status.json").read_text())["lifecycle"], "finalized")

    def test_large_result_is_full_in_report_but_terminal_trace_is_bounded(self):
        with scratch_directory() as directory:
            run_directory = directory / "run"
            run = DiagnosticRun(run_directory, mode="fixture", configuration={})
            motion = [{"sequence": n, "distance": 5000-n,
                       "target_portrait": list(range(144))} for n in range(205)]
            result = {"state": "blocked", "reason": "cancel_requested",
                      "cancel_requested": True, "motion": motion}
            report = run.close(shutdown={"recognition": True}, result=result)
            self.assertTrue(report["complete_diagnostics"], report)
            self.assertEqual(report["result"]["motion"], motion)
            stored = json.loads((run_directory / "report.json").read_text())
            self.assertEqual(len(stored["result"]["motion"]), 205)
            events = list(iter_trace_events(run_directory / "trace", report["run_id"]))
            terminal = [e["payload"] for e in events
                        if e["payload"]["event"] == "run_shutdown"]
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0]["full_result"],
                             {"file": "report.json", "json_pointer": "/result"})
            self.assertEqual(terminal[0]["result_summary"]["reason"], "cancel_requested")
            self.assertNotIn("motion", terminal[0])
            self.assertLess(len(json.dumps(terminal[0]).encode()), 4096)
            self.assertEqual(report["trace"]["dropped"], 0)

    def test_first_rejection_terminal_summary_is_bounded_and_report_keeps_detail(self):
        with scratch_directory() as directory:
            run_directory = directory / "run"
            run = DiagnosticRun(run_directory, mode="fixture", configuration={})
            rejection = {"observation_sequence": 7, "acquisition_sequence": 9,
                         "read_error": "bad image " * 2000,
                         "image_reference": "failed-recognition/" + "x" * 5000,
                         "sidecar_reference": "failed-recognition/" + "y" * 5000,
                         "fields": {f"field-{n}": {"present": True,
                             "valid": False, "fresh": True, "reason": "z" * 2000}
                             for n in range(20)}}
            result = {"state": "blocked", "reason": "invalid_or_stale_target_identity",
                      "first_rejection": rejection}
            report = run.close(shutdown={"recognition": True}, result=result)
            self.assertEqual(report["result"]["first_rejection"], rejection)
            events = list(iter_trace_events(run_directory / "trace", report["run_id"]))
            terminal = [e["payload"] for e in events
                        if e["payload"]["event"] == "run_shutdown"]
            self.assertEqual(len(terminal), 1)
            summary = terminal[0]["result_summary"]["first_rejection"]
            self.assertEqual(summary["observation_sequence"], 7)
            self.assertEqual(len(summary["fields"]), 8)
            self.assertEqual(len(summary["fields"]["field-0"]["reason"]), 160)
            self.assertEqual(len(summary["image_reference"]), 256)
            self.assertEqual(terminal[0]["full_result"]["json_pointer"], "/result")
            self.assertLess(len(json.dumps(terminal[0]).encode()), 4096)
            self.assertEqual(report["trace"]["dropped"], 0)

    def test_startup_failure_preserves_manifest_report_and_zero_requests(self):
        with scratch_directory() as directory:
            def fail():
                raise OSError("fixture capture startup failure")
            report = run_preflight(directory / "run", source_factory=fail,
                recognizer_factory=lambda: recognize_fixture, configuration={})
            self.assertIn("capture startup failure", report["error"])
            self.assertEqual(report["input_requests"], [])
            self.assertTrue(all(report["shutdown"].values()))
            self.assertTrue(report["complete_diagnostics"])
            self.assertFalse(report["result"]["capture_published"])
            self.assertTrue((directory / "run" / "manifest.json").exists())
            self.assertTrue((directory / "run" / "report.json").exists())

    def test_manifest_exists_before_source_starts_and_existing_run_is_preserved(self):
        with scratch_directory() as directory:
            output = directory / "run"
            def source():
                self.assertTrue((output / "manifest.json").exists())
                return NativeFixtureSource(1)
            run_preflight(output, source_factory=source, recognizer_factory=lambda: recognize_fixture,
                          configuration={}, snapshot_limit=1)
            original = (output / "report.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "must_be_empty"):
                DiagnosticRun(output, mode="fixture", configuration={})
            self.assertEqual(original, (output / "report.json").read_bytes())

    def test_frame_backpressure_is_nonblocking_and_explicitly_incomplete(self):
        with scratch_directory() as directory:
            gate, entered = Event(), Event()
            source = NativeFixtureSource(8)
            original = NativeFrame.to_image
            def slow(frame):
                entered.set()
                gate.wait(2)
                return original(frame)
            with patch.object(NativeFrame, "to_image", slow):
                run = DiagnosticRun(directory / "run", mode="fixture", configuration={}, frame_capacity=1)
                try:
                    run.observation(observation(), (), source.next_sample(Event()), {})
                    self.assertTrue(entered.wait(1))
                    started = time.monotonic()
                    for sequence in range(2, 8):
                        run.observation(observation(sequence), (), source.next_sample(Event()), {})
                    self.assertLess(time.monotonic() - started, .5)
                    self.assertGreater(run.frame_stats()["dropped"], 0)
                finally:
                    gate.set()
                    report = run.close(shutdown={})
            self.assertFalse(report["complete_diagnostics"])

    def test_frame_write_failure_does_not_stop_recognition(self):
        with scratch_directory() as directory:
            with patch.object(NativeFrame, "to_image", side_effect=OSError("fixture disk failure")):
                report = run_preflight(directory / "run", source_factory=NativeFixtureSource,
                    recognizer_factory=lambda: recognize_fixture, configuration={}, snapshot_limit=3)
            self.assertEqual(report["result"]["observations_processed"], 3)
            self.assertIn("fixture disk failure", report["frames"]["error"])
            self.assertFalse(report["complete_diagnostics"])

    def test_observer_failure_is_counted_without_killing_worker(self):
        class FailedObserver:
            def observation(self, *args):
                raise OSError("fixture observer failure")
        worker = RecognitionWorker(NativeFixtureSource(), recognize_fixture, SnapshotStore(), diagnostics=FailedObserver())
        worker.start()
        self.assertTrue(worker.join(2))
        self.assertEqual(worker.processed, 3)
        self.assertEqual(worker.metrics()["diagnostic_dropped"], 3)
        self.assertEqual(worker.metrics()["stage"], "ended")
        self.assertGreaterEqual(worker.metrics()["last_durations_real_seconds"]["recognition"], 0)


if __name__ == "__main__":
    unittest.main()
