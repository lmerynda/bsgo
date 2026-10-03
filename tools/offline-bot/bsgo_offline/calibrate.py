"""Active, supervised pre-mining calibration: image-observed motion and stop.

The native CLI requires an explicitly assessed clear heading. A certificate is
scoped to this session's speed feedback; it does not authorize mining.
"""
import argparse
import json
import math
from pathlib import Path
from threading import Lock, Timer
import time

from .artifacts import plain
from .calibration_evidence import MotionBrakeProbe
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .recognition import RecognitionWorker
from .state import SnapshotStore


def run_calibration(output, *, source_factory, recognizer_factory, dispatcher_factory,
                    configuration, profile, expected_window_id, frame_size,
                    clear_heading, scope="native", pulse_seconds=1.5,
                    baseline_seconds=5., stop_seconds=8.):
    """Injectable component runner; native OS access occurs only in factories.

    A timer requests the reserved idle command independently of capture/OCR.
    No other controller may own this input backend during the probe.
    """
    if clear_heading is not True:
        raise ValueError("fresh_clear_heading_assessment_required")
    for name, value, limit in (("pulse_seconds", pulse_seconds, 2.),
                               ("baseline_seconds", baseline_seconds, 10.),
                               ("stop_seconds", stop_seconds, 15.)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= limit:
            raise ValueError(name)
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    diagnostic = DiagnosticRun(output, mode=scope + "_motion_brake_calibration",
        configuration={**configuration, "clear_heading_assessed": True,
            "pulse_seconds": pulse_seconds, "baseline_seconds": baseline_seconds,
            "stop_seconds": stop_seconds, "mission_started": False},
        max_frames=128, frame_capacity=8)
    source = worker = backend = dispatcher = watchdog = probe = None
    store = SnapshotStore()
    started = time.monotonic()
    deadline = started + baseline_seconds + pulse_seconds + stop_seconds
    shutdown, error = {}, None
    result = {"state": "blocked", "reason": "not_started", "certificate": None,
              "final_stop_observed": False, "mining_ready": False,
              "unverified": ["heading_control", "target_selection", "scan", "mining", "inventory"]}
    stop_lock = Lock()
    brake_at = None
    brake_reason = None
    stop_attempts = 0
    stop_failure = None
    acceleration_at = None
    acceleration_records = ()
    delivered = set()
    previous_assessment = None
    baseline = []
    stop_images = []

    def stop_ship(reason):
        nonlocal brake_at, brake_reason, stop_attempts, stop_failure
        with stop_lock:
            if (brake_at is None and backend is not None and acceleration_at is not None
                    and stop_attempts < 2):
                stop_attempts += 1
                requested_at = time.monotonic()
                try:
                    backend.request_cleanup_stop(reason="calibration_" + reason)
                    brake_at, brake_reason = requested_at, reason
                    diagnostic.submit({"event": "calibration_brake_requested", "at": brake_at,
                                       "reason": reason, "attempt": stop_attempts})
                except Exception as exc:
                    stop_failure = f"cleanup_request_failure: {type(exc).__name__}: {exc}"
                    diagnostic.submit({"event": "calibration_brake_request_failed",
                        "at": requested_at, "reason": stop_failure, "attempt": stop_attempts})

    def input_guard(record):
        # Cleanup bypasses this application guard, but never native focus/PID guards.
        snapshot = store.latest()
        now = time.monotonic()
        field = snapshot.fields.get("speed") if snapshot else None
        return bool(record.key == "PageUp" and snapshot and probe
            and snapshot.context == probe.context and snapshot.healthy
            and field and field.valid and field.value == 0
            and 0 <= now - snapshot.acquired_at < 2
            and acceleration_at is not None and now < acceleration_at + pulse_seconds
            and brake_at is None and stop_failure is None)

    def collect_deliveries():
        if not dispatcher or not probe:
            return
        requests = {r.sequence: r for r in backend.records()}
        for delivery in dispatcher.deliveries():
            if delivery.request_sequence in delivered:
                continue
            delivered.add(delivery.request_sequence)
            request = requests.get(delivery.request_sequence)
            if request and request.key in ("PageUp", "PageDown"):
                operation = "accelerate" if request.key == "PageUp" else "brake"
                probe.record_delivery(operation, diagnostic.journal.run_id + "-" + operation,
                                      request, delivery)

    try:
        source = source_factory()
        recognizer = recognizer_factory()
        backend = RecordingInputBackend(session_id="calibration-" + diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1],
            journal=diagnostic.journal)
        backend.set_delivery_contract(input_guard)
        worker = RecognitionWorker(source, recognizer, store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        worker.start()
        sequence = 0
        baseline_deadline = started + baseline_seconds
        while time.monotonic() < deadline:
            collect_deliveries()
            snapshot = store.wait_for_sequence(sequence, .025)
            now = time.monotonic()
            if snapshot:
                sequence = snapshot.sequence
                if probe is None:
                    if snapshot.window_id != expected_window_id or snapshot.frame_size != tuple(frame_size):
                        raise RuntimeError("unexpected_capture_window_or_geometry")
                    probe = MotionBrakeProbe(context=snapshot.context, session_id=backend.session_id,
                        profile=profile, source_sha256=diagnostic.manifest["source_sha256"],
                        scope=scope, started_at=started, expires_at=deadline,
                        response_timeout=stop_seconds, run_id=diagnostic.journal.run_id,
                        max_observations=128)
                probe.observe(snapshot)
                field = snapshot.fields.get("speed")
                fresh = bool(snapshot.healthy and field and field.valid
                             and snapshot.context == probe.context
                             and 0 <= now - snapshot.acquired_at < 2)
                if acceleration_at is None:
                    if fresh and field.value == 0:
                        baseline.append(snapshot.sequence)
                    else:
                        raise RuntimeError("fresh_stopped_baseline_required")
                    if len(baseline) >= 2:
                        dispatcher = dispatcher_factory(backend, diagnostic.journal)
                        dispatcher.start()
                        acceleration_at = time.monotonic()
                        watchdog = Timer(pulse_seconds, stop_ship, args=("pulse_deadline",))
                        watchdog.daemon = True
                        watchdog.start()
                        acceleration_records = backend.press_key("PageUp", reason="calibration_accelerate")
                        diagnostic.submit({"event": "calibration_acceleration_requested",
                            "at": acceleration_at, "baseline": baseline[-2:],
                            "requests": acceleration_records})
                elif brake_at is not None:
                    # Stop proof is separate from command insertion or motion proof.
                    brakes = [d for d in dispatcher.deliveries() if d.kind == "key_up"
                              and d.request_sequence in {r.sequence for r in backend.records() if r.key == "PageDown"}
                              and d.outcome == "injected"]
                    if fresh and brakes and snapshot.acquired_at > brakes[-1].at and field.value == 0:
                        stop_images.append(snapshot.sequence)
                    else:
                        stop_images.clear()
            collect_deliveries()
            if probe:
                assessment = probe.evaluate(time.monotonic())
                marker = (assessment.state, assessment.reason)
                if marker != previous_assessment:
                    diagnostic.submit({"event": "calibration_assessment", "assessment": assessment})
                    previous_assessment = marker
                if acceleration_at is not None and assessment.reason == "waiting_for_brake_delivery":
                    stop_ship("image_observed_motion")
                if assessment.state == "blocked" and acceleration_at is not None:
                    stop_ship("evidence_rejected")
                if assessment.state == "proved":
                    result.update(plain(assessment))
                    result["final_stop_observed"] = True
                    break
                if brake_at is not None and len(stop_images) >= 2:
                    result.update(plain(assessment))
                    result["final_stop_observed"] = True
                    break
                if assessment.state == "blocked" and acceleration_at is None:
                    result.update(plain(assessment))
                    break
            if acceleration_at is None and now >= baseline_deadline:
                raise RuntimeError("baseline_timeout")
            if worker.end_reason and not snapshot:
                raise RuntimeError("recognition_ended:" + worker.end_reason)
        else:
            result["reason"] = "calibration_timeout"
        if probe and result["reason"] == "not_started":
            result.update(plain(probe.evaluate(time.monotonic())))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error, certificate=None)
    finally:
        stop_ship("shutdown")
        if watchdog:
            watchdog.cancel()
            watchdog.join(1)
        shutdown["watchdog"] = not watchdog or not watchdog.is_alive()
        if dispatcher:
            # Let a reserved cleanup command be consumed before terminating dispatch.
            until = time.monotonic() + .5
            while backend.pending_requests and time.monotonic() < until:
                time.sleep(.01)
            shutdown["dispatcher"] = dispatcher.stop(2)
            result["deliveries"] = plain(dispatcher.deliveries())
            result["dispatcher_error"] = dispatcher.error
        else:
            shutdown["dispatcher"] = True
        if worker:
            worker.stop()
            shutdown["recognition"] = worker.join(2)
        else:
            shutdown["recognition"] = True
        shutdown["source"] = shutdown["recognition"]
        if source and shutdown["recognition"]:
            try:
                source.close()
            except Exception as exc:
                shutdown["source"] = False
                error = error or f"source_close_failure: {type(exc).__name__}: {exc}"
        if backend:
            backend.cancel(reason="calibration_finished")
            backend.flush_journal()
        shutdown["input_idle"] = not backend or (not backend.pending_requests and not backend.held_keys)
        result.update(brake_reason=brake_reason, stop_observation_sequences=stop_images[-2:],
                      cleanup_attempts=stop_attempts, cleanup_failure=stop_failure)
        if stop_failure:
            error = error or stop_failure
            result.update(state="blocked", reason=stop_failure, certificate=None)
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    if not report["complete_diagnostics"] or not all(shutdown.values()):
        result.update(state="blocked", reason="incomplete_diagnostic_evidence", certificate=None)
    _write_json(diagnostic.directory / "control-calibration.json", {"run_id": diagnostic.journal.run_id,
        "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result})
    return {"run_id": diagnostic.journal.run_id,
            "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clear-heading-assessed", action="store_true", required=True)
    parser.add_argument("--source-size", choices=["native-2560", "3840x2160"], default="native-2560")
    parser.add_argument("--pulse-seconds", type=float, default=1.5)
    parser.add_argument("--key-hold-ms", type=float, default=50.)
    args = parser.parse_args()
    if not math.isfinite(args.key_hold_ms) or not 0 <= args.key_hold_ms <= 500:
        parser.error("--key-hold-ms must be between zero and 500")
    if args.key_hold_ms / 1000 >= args.pulse_seconds:
        parser.error("key hold must be shorter than the maximum throttle pulse")
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd, expected_pid=args.pid)
    if geometry.title != "Battlestar Galactica Online":
        raise ValueError("unexpected_game_window_title")
    configured_size = (3840, 2160) if args.source_size == "3840x2160" else (2560, 1440)
    if geometry.client_size != configured_size:
        raise ValueError("unexpected_native_client_size")
    result = run_calibration(args.output,
        source_factory=lambda: NativeSource(NativeCapture(geometry.hwnd, geometry.pid, layout=args.profile), .4),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile,
            source_frame_size=configured_size if args.source_size == "3840x2160" else None),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend, geometry.hwnd,
            geometry.pid, expected_size=geometry.client_size, expected_origin=geometry.screen_origin,
            journal=journal, minimum_key_hold_seconds=args.key_hold_ms / 1000),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
                       "source_size": args.source_size, "minimum_key_hold_ms": args.key_hold_ms},
        profile=args.profile, expected_window_id=f"{geometry.pid}:{geometry.hwnd}",
        frame_size=geometry.client_size, clear_heading=args.clear_heading_assessed,
        pulse_seconds=args.pulse_seconds)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] == "proved" and result["final_stop_observed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
