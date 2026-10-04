"""Continuously observe HUD evidence while a human plays; no decisions or input."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .diagnostics import DiagnosticRun, recognition_failures
from .recognition import RecognitionWorker
from .state import SnapshotStore


def run_passive_observer(output, *, source_factory, recognizer_factory,
                         configuration, interval=1., cancel_file=None,
                         routine_frames=64, shutdown_timeout=2., synthetic=False):
    """Run until an explicit stop or source termination, with no control objects."""
    if type(interval) not in (int, float) or not math.isfinite(interval) or not .1 <= interval <= 10:
        raise ValueError("interval")
    if type(routine_frames) is not int or not 1 <= routine_frames <= 512:
        raise ValueError("routine_frames")
    if (type(shutdown_timeout) not in (int, float) or not math.isfinite(shutdown_timeout)
            or not 0 < shutdown_timeout <= 5):
        raise ValueError("shutdown_timeout")
    if type(synthetic) is not bool:
        raise ValueError("synthetic")
    if cancel_file is not None and not str(cancel_file).strip():
        raise ValueError("cancel_file")
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    label = "SYNTHETIC DEMO: " if synthetic else ""
    diagnostic = DiagnosticRun(output,
        mode="synthetic_passive_observer" if synthetic else "native_passive_observer",
        configuration={**configuration, "synthetic": synthetic, "passive_observation": True,
            "capture_interval_real_seconds": interval, "maximum_image_age": max(2., interval + 2.),
            "native_input_started": False, "decisions_enabled": False, "mission_started": False,
            "cancel_file": str(cancel_path) if cancel_path else None,
            "routine_frame_retention": "first configured count; later routine images are budget-skipped",
            "routine_frames": routine_frames, "total_time_limit": None},
        frame_capacity=4, max_frames=routine_frames)
    diagnostic.set_control_status({"actual_mode": "manual", "requested_mode": "manual",
        "pending": False, "app_control_enabled": False, "decisions_enabled": False,
        "attached_controller": None, "autopilot_available": False,
        "reason": "controller_not_attached", "last_ack": {
            "actual_mode": "manual", "reason": "passive_observation_only",
            "at_monotonic": time.monotonic(), "at_utc": datetime.now(timezone.utc).isoformat()}})
    source = worker = None
    store = SnapshotStore()
    shutdown = {}
    error = None
    result = {"state": "recorded", "reason": "not_started", "cancel_requested": False,
        "native_input_started": False, "decisions_enabled": False, "mining_ready": False,
        "observations_processed": 0, "passive_observation": True}
    try:
        if cancel_path is not None and cancel_path.exists():
            result.update(state="canceled", reason="cancel_requested", cancel_requested=True)
        else:
            source = source_factory()
            worker = RecognitionWorker(source, recognizer_factory(), store, diagnostics=diagnostic)
            diagnostic.attach(worker=worker, store=store, coordinator=None, backend=None)
            diagnostic.update_execution(phase="observing", processing=label + "reading HUD evidence only",
                waiting_for=label + "the next image or an explicit observation stop",
                expected_next_step=label + "publish readings; decisions and ship controls are disabled")
            worker.start()
            sequence = 0
            while True:
                if cancel_path is not None and cancel_path.exists():
                    result.update(state="canceled", reason="cancel_requested", cancel_requested=True)
                    break
                snapshot = store.wait_for_sequence(sequence, .1)
                if snapshot is not None:
                    sequence = snapshot.sequence
                    failures = recognition_failures(snapshot)
                    reason = snapshot.reason or (
                        "; ".join(f"{name}: {why}" for name, why in failures.items()) if failures else None)
                    diagnostic.update_execution(phase="observing",
                        processing=label + "reading HUD evidence; no decisions or ship control",
                        waiting_for=label + "the next image or an explicit observation stop",
                        expected_next_step=label + "publish readings; retain failed images and the declared routine budget",
                        reason=reason, blocker=reason,
                        progress=label + f"image observation {sequence} published")
                if worker.end_reason:
                    if worker.error:
                        error = worker.error
                        result.update(state="blocked", reason=error)
                    else:
                        result.update(reason=worker.end_reason)
                    break
    except KeyboardInterrupt:
        result.update(state="canceled", reason="supervisor_interrupt", cancel_requested=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    finally:
        diagnostic.update_execution(phase="cleanup", processing=label + "stopping passive observation workers",
            waiting_for=label + "recognition and evidence writers to stop",
            expected_next_step=label + "finalize the observation package; Manual mode stays active", blocker=error)
        if worker is not None:
            worker.stop()
            shutdown["recognition"] = worker.join(shutdown_timeout)
            result["observations_processed"] = worker.processed
        else:
            shutdown["recognition"] = True
        # Do not call close on a source still blocked inside capture; it can
        # hold the same source lock and defeat the bounded worker join.
        shutdown["source"] = shutdown["recognition"]
        if source is not None and shutdown["recognition"] and callable(getattr(source, "close", None)):
            try:
                source.close()
            except Exception as exc:
                shutdown["source"] = False
                error = error or f"source_close_failure: {type(exc).__name__}: {exc}"
                result.update(state="blocked", reason=error)
    return diagnostic.close(shutdown=shutdown, result=result, error=error, timeout=shutdown_timeout)


def _positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=_positive_int, required=True)
    parser.add_argument("--pid", type=_positive_int, required=True)
    parser.add_argument("--profile", choices=["largehud-20261003", "restart-20261001", "original"],
                        default="largehud-20261003")
    parser.add_argument("--interval", type=float, default=1.)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--routine-frames", type=int, default=64)
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .recognition import HudRecognizer
    def source_factory():
        geometry = inspect_window(args.hwnd, expected_pid=args.pid)
        if geometry.title != "Battlestar Galactica Online":
            raise ValueError("unexpected_game_window_title")
        return NativeSource(NativeCapture(geometry.hwnd, geometry.pid,
            layout=args.profile, require_foreground=False), args.interval)
    report = run_passive_observer(args.output, source_factory=source_factory,
        recognizer_factory=lambda: HudRecognizer(args.profile),
        configuration={"hwnd": args.hwnd, "expected_pid": args.pid, "profile": args.profile,
            "capture_mode": "visible_client_rgb", "foreground_required": False,
            "capture_guards": "title, PID, client geometry, visibility and unoccluded visible pixels"},
        interval=args.interval, cancel_file=args.cancel_file, routine_frames=args.routine_frames)
    print(json.dumps({"output": str(args.output.resolve()), "control": report["control"],
        "result": report["result"], "shutdown": report["shutdown"],
        "complete_diagnostics": report["complete_diagnostics"]}, indent=2))
    return 1 if report["error"] or not all(report["shutdown"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
