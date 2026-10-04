"""Passive pipeline acceptance: pixel reads are independent of any controller."""
import ast
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from bsgo_offline import passive_observer
from bsgo_offline.diagnostics import DiagnosticRun
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_trace import scratch_directory


class Source:
    def __init__(self, count=4, *, cancel_at=None, cancel_path=None):
        self.count, self.sequence = count, 0
        self.cancel_at, self.cancel_path = cancel_at, cancel_path
        self.closed = False

    def next_sample(self, stop):
        if stop.wait(.025) or self.count is not None and self.sequence >= self.count:
            return None
        self.sequence += 1
        if self.sequence == self.cancel_at:
            self.cancel_path.touch()
        return NativeFrame(self.sequence, 1, "synthetic-passive-fixture", time.monotonic(),
            4, 3, bytes([self.sequence % 256] * 36),
            WindowGeometry(17, 42, "synthetic passive fixture", (4, 3), (0, 0)),
            source_id="synthetic-passive-fixture")

    def close(self):
        self.closed = True


def recognize(sample):
    return RecognitionResult((4, 3), {"speed": FieldValue(sample.acquisition_sequence, sample.acquired_at),
        "hull_current": FieldValue(5005, sample.acquired_at),
        "scan_result": FieldValue(None, sample.acquired_at, False,
                                  "mining_recognizer_not_validated:scan_result")})


class PassiveObserverTests(unittest.TestCase):
    def run_observer(self, directory, source, **options):
        recognizer = options.pop("recognizer", recognize)
        return passive_observer.run_passive_observer(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognizer,
            configuration={"fixture": True}, interval=.1, synthetic=True, **options)

    def test_reads_and_finalizes_without_control_or_decision_components(self):
        source = Source()
        updates = []
        original = DiagnosticRun.update_execution
        def record(run, **values):
            original(run, **values)
            updates.append(run.read_status())
        with scratch_directory() as directory, patch.object(DiagnosticRun, "update_execution", record):
            report = self.run_observer(directory, source)
            self.assertEqual(report["result"]["observations_processed"], 4)
            self.assertEqual(report["result"]["reason"], "source_exhausted")
            self.assertTrue(all(report["shutdown"].values()))
            self.assertTrue(report["complete_diagnostics"])
            self.assertTrue(source.closed)
            for state in updates:
                self.assertIsNone(state["runtime"])
                self.assertIsNone(state["input_history"])
                self.assertIsNone(state["actions"])
                self.assertIsNone(state["execution"]["last_input_intent"])
                self.assertIsNone(state["execution"]["last_input_delivery"])
                self.assertIsNone(state["execution"]["last_confirmed_effect"])
            observing = [row for row in updates if row["execution"]["phase"] == "observing"]
            self.assertTrue(observing)
            self.assertIn("SYNTHETIC DEMO", observing[-1]["execution"]["processing"])
            self.assertEqual(report["control"]["actual_mode"], "manual")
            self.assertFalse(report["control"]["app_control_enabled"])
            self.assertFalse(report["control"]["decisions_enabled"])
            self.assertIsNone(report["control"]["attached_controller"])
            self.assertEqual(report["control"]["reason"], "controller_not_attached")
            self.assertEqual(report["input_requests"], [])
            manifest = json.loads((directory / "run" / "manifest.json").read_text())
            self.assertEqual(manifest["mode"], "synthetic_passive_observer")
            self.assertTrue(manifest["synthetic"])
            self.assertIsNone(manifest["configuration"]["total_time_limit"])
            self.assertEqual(report["observation"]["fields"]["speed"]["value"], 4)
            self.assertFalse(report["observation"]["fields"]["scan_result"]["valid"])

    def test_continuous_source_stops_only_on_its_independent_cancel_file(self):
        with scratch_directory() as directory:
            mining_stop = directory / "mining-stop.request"
            mining_stop.write_text("keep mining paused")
            passive_stop = directory / "passive-stop.request"
            source = Source(None, cancel_at=8, cancel_path=passive_stop)
            report = self.run_observer(directory, source, cancel_file=passive_stop)
            self.assertGreaterEqual(report["result"]["observations_processed"], 7)
            self.assertTrue(report["result"]["cancel_requested"])
            self.assertEqual(report["execution"]["phase"], "canceled")
            self.assertTrue(source.closed)
            self.assertEqual(mining_stop.read_text(), "keep mining paused")
            self.assertTrue(passive_stop.exists())

    def test_preexisting_cancel_never_constructs_a_source_or_recognizer(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            def forbidden():
                raise AssertionError("factory must not run")
            report = passive_observer.run_passive_observer(directory / "run", source_factory=forbidden,
                recognizer_factory=forbidden, configuration={}, cancel_file=marker, synthetic=True)
            self.assertTrue(report["result"]["cancel_requested"])
            self.assertEqual(report["input_requests"], [])
            self.assertTrue(all(report["shutdown"].values()))

    def test_failed_images_survive_bounded_routine_retention(self):
        def bad(sample):
            result = recognize(sample)
            if sample.acquisition_sequence >= 3:
                return RecognitionResult((4, 3), {"speed": FieldValue(None, sample.acquired_at, False,
                                                                      "incomplete_speed_glyph")})
            return result
        with scratch_directory() as directory:
            report = self.run_observer(directory, Source(8), recognizer=bad, routine_frames=1)
            self.assertEqual(report["frames"]["budget_skipped"], 1)
            self.assertEqual(report["frames"]["failed_recognition"]["written"], 6)
            self.assertTrue(report["frames"]["failed_recognition"]["complete"])
            self.assertFalse(report["complete_diagnostics"])
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.png"))), 6)
            self.assertEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 6)

    def test_recognizer_failure_is_unknown_and_retains_its_failed_pixels(self):
        def fail(sample):
            raise ValueError("fixture recognizer failure")
        with scratch_directory() as directory:
            report = self.run_observer(directory, Source(1), recognizer=fail)
            self.assertEqual(report["execution"]["phase"], "blocked")
            self.assertIn("fixture recognizer failure", report["error"])
            self.assertFalse(report["observation"]["healthy"])
            self.assertFalse(report["observation"]["fields"]["speed"]["valid"])
            self.assertEqual(report["frames"]["failed_recognition"]["written"], 1)
            self.assertEqual(report["input_requests"], [])

    def test_blocked_capture_does_not_turn_shutdown_into_an_unbounded_close(self):
        with scratch_directory() as directory:
            entered, release = Event(), Event()
            marker = directory / "stop.request"
            class Blocked(Source):
                def next_sample(self, stop):
                    entered.set()
                    release.wait(2)
                    return None
            source = Blocked()
            def cancel():
                entered.wait(1)
                marker.touch()
            thread = Thread(target=cancel)
            thread.start()
            try:
                started = time.monotonic()
                report = self.run_observer(directory, source, cancel_file=marker, shutdown_timeout=.05)
                self.assertLess(time.monotonic() - started, 1)
                self.assertFalse(report["shutdown"]["recognition"])
                self.assertFalse(report["shutdown"]["source"])
                self.assertFalse(source.closed)
                self.assertFalse(report["complete_diagnostics"])
            finally:
                release.set()
                thread.join(1)

    def test_module_has_no_controller_planner_dispatcher_or_input_import(self):
        tree = ast.parse(Path(passive_observer.__file__).read_text())
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(set(imports) & {"input", "native_input", "mission", "execution", "planner"})
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertFalse(names & {"MiningCoordinator", "RecordingInputBackend", "NativeInputDispatcher"})

    def test_cli_pins_visible_client_capture_without_foreground_or_input(self):
        geometry = WindowGeometry(17, 42, "Battlestar Galactica Online", (2560, 1440), (0, 0))
        def runner(output, **options):
            options["source_factory"]()
            self.assertFalse(options["configuration"]["foreground_required"])
            return {"control": {"actual_mode": "manual"}, "result": {},
                    "shutdown": {"recognition": True}, "complete_diagnostics": True, "error": None}
        with patch("sys.argv", ["passive_observer", "--hwnd", "17", "--pid", "42", "--output", "unused"]), \
                patch("bsgo_offline.native_capture.inspect_window", return_value=geometry), \
                patch("bsgo_offline.native_capture.NativeCapture") as capture, \
                patch("bsgo_offline.native_capture.NativeSource"), \
                patch.object(passive_observer, "run_passive_observer", side_effect=runner), redirect_stdout(io.StringIO()):
            self.assertEqual(passive_observer.main(), 0)
        capture.assert_called_once_with(17, 42, layout="largehud-20261003", require_foreground=False)

    def test_invalid_bounds_are_rejected_before_source_start(self):
        with scratch_directory() as directory:
            for name, value in (("interval", 0), ("interval", float("nan")),
                                ("routine_frames", True), ("routine_frames", 0),
                                ("shutdown_timeout", 0), ("synthetic", "yes")):
                with self.assertRaises(ValueError):
                    passive_observer.run_passive_observer(directory / "unused", source_factory=lambda: None,
                        recognizer_factory=lambda: None, configuration={}, **{name: value})
            self.assertFalse((directory / "unused").exists())
