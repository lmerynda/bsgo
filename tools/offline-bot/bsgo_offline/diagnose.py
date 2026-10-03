"""Capture/preflight experiment using application components, with zero native input."""
import argparse
import json
import math
from pathlib import Path
import time

from .diagnostics import DiagnosticRun
from .input import RecordingInputBackend
from .mission import MiningCoordinator
from .recognition import RecognitionWorker
from .state import SnapshotStore


def run_preflight(output, *, source_factory, recognizer_factory, configuration,
                  mode="capture_preflight", duration=6, snapshot_limit=6):
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 60:
        raise ValueError("duration")
    if type(snapshot_limit) is not int or snapshot_limit < 1:
        raise ValueError("snapshot_limit")
    diagnostics = DiagnosticRun(output, mode=mode, configuration={**configuration,
        "duration_real_seconds": duration, "snapshot_limit": snapshot_limit,
        "native_input_started": False, "mission_started": False})
    source = worker = backend = None
    error = None
    shutdown = {}
    result = {"experiment": "capture_preflight", "capture_published": False,
              "native_input_started": False, "mission_started": False}
    try:
        source = source_factory()
        recognizer = recognizer_factory()
        store = SnapshotStore()
        backend = RecordingInputBackend(session_id="diagnostic-" + diagnostics.journal.run_id,
                                        journal=diagnostics.journal)
        coordinator = MiningCoordinator(store, backend, journal=diagnostics.journal)
        worker = RecognitionWorker(source, recognizer, store, diagnostics=diagnostics)
        diagnostics.attach(worker=worker, store=store, coordinator=coordinator, backend=backend,
                           capabilities=getattr(recognizer, "capabilities", None))
        worker.start()
        deadline = time.monotonic() + duration
        sequence = 0
        while time.monotonic() < deadline and worker.processed < snapshot_limit:
            snapshot = store.wait_for_sequence(sequence, min(.1, max(0, deadline - time.monotonic())))
            if snapshot:
                sequence = snapshot.sequence
            if worker.end_reason:
                break
        latest = store.latest()
        result.update(capture_published=bool(worker.processed and latest and latest.healthy and not worker.error),
                      observations_processed=worker.processed,
                      observation_sequence=latest.sequence if latest else None)
        diagnostics.submit({"event": "capture_experiment_result", "result": result,
                            "preflight": coordinator.read_preflight(getattr(recognizer, "capabilities", None))})
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        diagnostics.submit({"event": "experiment_failure", "reason": error})
    finally:
        if worker:
            worker.stop()
            shutdown["recognition"] = worker.join(2)
        else:
            shutdown["recognition"] = True
        # A source blocked in capture may hold its lock. Never turn a bounded
        # worker join into an unbounded close waiting for that same lock.
        shutdown["source"] = shutdown["recognition"]
        if source and shutdown["recognition"] and callable(getattr(source, "close", None)):
            try:
                source.close()
            except Exception as exc:
                shutdown["source"] = False
                error = error or f"source_close_failure: {type(exc).__name__}: {exc}"
        if backend:
            backend.cancel(reason="diagnostic_shutdown")
            backend.flush_journal()
            shutdown["input_idle"] = not backend.pending_requests and not backend.held_keys
        else:
            shutdown["input_idle"] = True
    return diagnostics.close(shutdown=shutdown, result=result, error=error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--interval", type=float, default=.5)
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .mining_recognition import MiningHudRecognizer
    def source_factory():
        geometry = inspect_window(args.hwnd, expected_pid=args.pid)
        if geometry.title != "Battlestar Galactica Online":
            raise ValueError("unexpected_game_window_title")
        return NativeSource(NativeCapture(geometry.hwnd, geometry.pid, layout=args.profile), args.interval)
    report = run_preflight(args.output, source_factory=source_factory,
        recognizer_factory=lambda: MiningHudRecognizer(args.profile), mode="native_game_capture_preflight",
        configuration={"hwnd": args.hwnd, "expected_pid": args.pid, "profile": args.profile,
                       "capture_interval_real_seconds": args.interval},
        duration=args.seconds, snapshot_limit=args.frames)
    preflight = report.get("preflight") or {}
    print(json.dumps({"output": str(args.output.resolve()), "result": report["result"],
        "error": report["error"], "complete_diagnostics": report["complete_diagnostics"],
        "input_requests": len(report["input_requests"]), "shutdown": report["shutdown"],
        "blockers": [check for check in preflight.get("checks", []) if check["state"] != "ready"]}, indent=2))
    return 0 if report["result"]["capture_published"] and report["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
