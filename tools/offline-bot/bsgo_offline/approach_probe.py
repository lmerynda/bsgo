"""Supervised, bounded approach to one preselected asteroid and observed stop.

This is a single experiment, not mining readiness. Scene, corridor and heading
are supervisor assertions; the application does not recognize hull or combat.
"""
import argparse
import json
import math
from pathlib import Path
from threading import Event, Lock, Thread
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .policy import ApproachPolicy, PolicyError
from .recognition import RecognitionWorker
from .state import SnapshotStore


def _speed_zero(snapshot, *, now, context, window_id, frame_size):
    field = snapshot.fields.get("speed")
    return bool(snapshot.context == context and snapshot.window_id == window_id
        and snapshot.frame_size == tuple(frame_size) and snapshot.healthy
        and 0 <= now - snapshot.acquired_at < 2 and field and field.valid
        and 0 <= now - field.observed_at < 2 and field.value == 0)


def run_approach_probe(output, *, source_factory, recognizer_factory,
                       dispatcher_factory, configuration, profile,
                       expected_window_id, frame_size, clear_corridor_assessed,
                       aligned_heading_assessed, safe_stopped_scene_assessed,
                       scope="native", brake_at=900., max_thrust_seconds=12.,
                       baseline_seconds=4., stop_seconds=35.,
                       decision_interval=2., cancel_file=None):
    """Injectable supervised probe with an independent PageDown watchdog."""
    if (clear_corridor_assessed, aligned_heading_assessed,
            safe_stopped_scene_assessed) != (True, True, True):
        raise ValueError("fresh_supervisor_scene_assessments_required")
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    if cancel_file is not None and not str(cancel_file).strip():
        raise ValueError("cancel_file")
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    for name, value, low, high in (("brake_at", brake_at, 600, 1400),
        ("max_thrust_seconds", max_thrust_seconds, .1, 180),
        ("baseline_seconds", baseline_seconds, .1, 8),
        ("stop_seconds", stop_seconds, .1, 45),
        ("decision_interval", decision_interval, .01, 2)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(name)
    policy = ApproachPolicy(brake_at=brake_at)
    # Native capture runs every 0.4 seconds. Retain the complete explicitly
    # bounded transit and coast-down, with headroom for cleanup captures.
    frame_budget = max(256, math.ceil((baseline_seconds + max_thrust_seconds
                                      + stop_seconds + 8) / .4))
    diagnostic = DiagnosticRun(output, mode=scope + "_supervised_approach_probe",
        configuration={**configuration, "supervisor_assumptions": {
            "clear_corridor": True, "aligned_heading": True,
            "safe_stopped_scene": True}, "brake_at": brake_at,
            "max_thrust_seconds": max_thrust_seconds,
            "baseline_seconds": baseline_seconds, "stop_seconds": stop_seconds,
            "gun_optimal_range": 760, "gun_max_range": 800,
            "cancel_file": str(cancel_path) if cancel_path else None,
            "mission_started": False}, frame_capacity=32, max_frames=frame_budget)
    store = SnapshotStore()
    source = worker = backend = dispatcher = watchdog = None
    watchdog_stop = Event()
    cancel_seen = Event()
    stop_lock = Lock()
    started = time.monotonic()
    baseline, motion, stop_images = [], [], []
    context = initial = previous = None
    observed_sequence = 0
    previous_acquisition_sequence = -1
    acceleration_at = brake_at_time = None
    brake_reason = blocker = error = None
    acceleration_records = brake_records = ()
    stop_attempts = 0
    stop_failure = None
    shutdown = {}
    result = {"state": "blocked", "reason": "not_started", "mining_ready": False,
        "baseline": baseline, "motion": motion, "stop_observation_sequences": stop_images,
        "acceleration_requests": [], "brake_requests": [], "deliveries": [],
        "post_acceleration_motion_sequences": [],
        "final_stop_observed": False,
        "cancel_requested": False,
        "unverified": ["hull_health", "combat_detection", "exclusive_input_ownership",
                       "scan_result", "resources", "mining", "inventory"]}

    def canceled():
        if cancel_path is None:
            return False
        try:
            requested = cancel_path.exists()
        except OSError:
            requested = True  # A broken cancel channel cannot authorize thrust.
        if requested and not cancel_seen.is_set():
            cancel_seen.set()
            diagnostic.submit({"event": "approach_cancel_requested",
                               "at": time.monotonic(), "path": str(cancel_path)})
        return requested

    def stop_ship(reason):
        nonlocal brake_at_time, brake_reason, stop_attempts, stop_failure, brake_records
        with stop_lock:
            if backend is None or acceleration_at is None or brake_at_time is not None or stop_attempts >= 2:
                return
            stop_attempts += 1
            at = time.monotonic()
            try:
                brake_records = backend.request_cleanup_stop(reason="approach_" + reason)
                brake_at_time, brake_reason = at, reason
                result["brake_requests"] = plain(brake_records)
                diagnostic.submit({"event": "approach_brake_requested", "at": at,
                                   "reason": reason, "attempt": stop_attempts})
            except Exception as exc:
                stop_failure = f"cleanup_request_failure: {type(exc).__name__}: {exc}"
                diagnostic.submit({"event": "approach_brake_request_failed", "at": at,
                                   "reason": stop_failure, "attempt": stop_attempts})

    def valid_target(snapshot, now):
        if (snapshot.window_id != expected_window_id or snapshot.frame_size != tuple(frame_size)
                or context is not None and snapshot.context != context):
            raise PolicyError("Capture window or context changed")
        row = policy.validate(snapshot, now)
        hp = snapshot.fields.get("target_max_hp")
        if (not hp or not hp.valid or type(hp.value) not in (int, float)
                or not math.isfinite(hp.value) or hp.value <= 0
                or not 0 <= now - hp.observed_at < 2):
            raise PolicyError("Missing or stale target maximum HP")
        if initial is not None:
            if hp.value != initial["target_max_hp"] or row.target_identity != initial["target_identity"]:
                raise PolicyError("Selected target identity or maximum HP changed")
            difference = sum(abs(a-b) for a,b in zip(row.target_portrait,
                initial["target_portrait"])) / 144
            if difference > .75:
                raise PolicyError("Selected target portrait changed")
        return row, {"sequence": snapshot.sequence,
            "acquisition_sequence": snapshot.acquisition_sequence,
            "acquired_at": snapshot.acquired_at, "published_at": snapshot.published_at,
            "distance": row.distance, "speed": row.speed,
            "target_identity": row.target_identity, "target_max_hp": hp.value,
            "target_portrait": row.target_portrait,
            "context": plain(snapshot.context)}

    def input_guard(record):
        if record.key != "PageUp" or record.kind != "key_down" or acceleration_at is None:
            return False
        if canceled():
            return False
        now = time.monotonic()
        snapshot = store.latest()
        if (brake_at_time is not None or stop_failure is not None or snapshot is None
                or not 0 <= now - acceleration_at < 1):
            return False
        try:
            row, _ = valid_target(snapshot, now)
            return row.speed == 0 and row.distance >= brake_at + 250
        except PolicyError:
            return False

    def watchdog_run():
        while not watchdog_stop.wait(.05):
            if canceled():
                stop_ship("cancel_requested")
                continue
            if acceleration_at is None or brake_at_time is not None:
                continue
            now = time.monotonic()
            snapshot = store.latest()
            if snapshot is None or now - snapshot.acquired_at >= 2:
                stop_ship("stale_image_watchdog")
                continue
            try:
                row, _ = valid_target(snapshot, now)
            except Exception:
                stop_ship("invalid_image_watchdog")
                continue
            if row.distance <= brake_at:
                stop_ship("threshold_watchdog")
            elif now >= acceleration_at + max_thrust_seconds:
                stop_ship("thrust_limit")

    try:
        if canceled():
            raise RuntimeError("cancel_requested_before_input")
        source = source_factory()
        recognizer = recognizer_factory()
        backend = RecordingInputBackend(session_id="approach-" + diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1],
            journal=diagnostic.journal)
        backend.set_delivery_contract(input_guard)
        worker = RecognitionWorker(source, recognizer, store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        worker.start()
        baseline_deadline = started + baseline_seconds
        overall_deadline = started + baseline_seconds + max_thrust_seconds + stop_seconds + 2
        while time.monotonic() < overall_deadline:
            if canceled():
                if acceleration_at is None:
                    blocker = "cancel_requested_before_input"
                    break
                stop_ship("cancel_requested")
            snapshot = store.wait_for_sequence(observed_sequence, .025)
            now = time.monotonic()
            if snapshot:
                if snapshot.sequence != observed_sequence + 1:
                    blocker = blocker or "uninspected_observation_sequence"
                    stop_ship("observation_gap")
                if snapshot.acquisition_sequence <= previous_acquisition_sequence:
                    blocker = blocker or "repeated_or_reordered_acquisition"
                    stop_ship("acquisition_gap")
                observed_sequence = snapshot.sequence
                previous_acquisition_sequence = snapshot.acquisition_sequence
                if blocker and acceleration_at is None:
                    break
                if acceleration_at is None:
                    try:
                        row, reading = valid_target(snapshot, now)
                        if row.speed != 0:
                            raise PolicyError("Stopped baseline required")
                        if initial is None:
                            context, initial = snapshot.context, reading
                        elif abs(row.distance - initial["distance"]) > 5:
                            raise PolicyError("Stopped baseline distance changed")
                        baseline.append(reading)
                        if len(baseline) >= 2:
                            if canceled():
                                blocker = "cancel_requested_before_input"
                                break
                            action = policy.decide(snapshot, now)
                            if action != "accelerate":
                                raise PolicyError("Initial policy did not authorize acceleration")
                            dispatcher = dispatcher_factory(backend, diagnostic.journal)
                            dispatcher.start()
                            acceleration_at = time.monotonic()
                            watchdog = Thread(target=watchdog_run,
                                name="approach-safety-watchdog", daemon=True)
                            watchdog.start()
                            acceleration_records = backend.press_key("PageUp",
                                reason="supervised_approach_accelerate")
                            result["acceleration_requests"] = plain(acceleration_records)
                            diagnostic.submit({"event": "approach_acceleration_requested",
                                "at": acceleration_at,
                                "baseline_sequences": [r["sequence"] for r in baseline]})
                    except PolicyError as exc:
                        blocker = blocker or str(exc)
                        break
                else:
                    if brake_at_time is None and blocker is None:
                        try:
                            row, reading = valid_target(snapshot, now)
                            if previous is not None:
                                dt = row.acquired_at - previous.acquired_at
                                if dt <= 0 or abs(row.distance - previous.distance) > 44 * dt + 30:
                                    raise PolicyError("Impossible high-rate distance change")
                                if row.distance > previous.distance + 3:
                                    raise PolicyError("Target is not closing")
                            previous = row
                            motion.append(reading)
                            if row.distance <= brake_at:
                                action = policy.decide(snapshot, now)
                                if action != "brake":
                                    raise PolicyError("Policy did not request threshold brake")
                                stop_ship("threshold")
                            elif policy.last and row.acquired_at - policy.last.acquired_at >= decision_interval:
                                action = policy.decide(snapshot, now)
                                if action == "brake":
                                    stop_ship("threshold")
                        except PolicyError as exc:
                            blocker = blocker or str(exc)
                            stop_ship("image_evidence_rejected")
                    else:
                        requests = {r.sequence: r for r in backend.records()}
                        ups = [d for d in dispatcher.deliveries()
                            if d.outcome == "injected" and d.kind == "key_up"
                            and d.request_sequence in requests
                            and requests[d.request_sequence].key == "PageDown"]
                        if (ups and _speed_zero(snapshot, now=now, context=context,
                                window_id=expected_window_id, frame_size=frame_size)
                                and snapshot.acquired_at > ups[-1].at
                                and (not stop_images or snapshot.sequence > stop_images[-1])):
                            stop_images.append(snapshot.sequence)
                        else:
                            stop_images.clear()
            if acceleration_at is None and now >= baseline_deadline:
                blocker = blocker or "baseline_timeout"
                break
            if acceleration_at is not None and brake_at_time is None and now >= acceleration_at + max_thrust_seconds:
                stop_ship("thrust_limit")
            if brake_at_time is not None:
                if len(stop_images) >= 2:
                    result["final_stop_observed"] = True
                    break
                if now >= brake_at_time + stop_seconds:
                    blocker = blocker or "stop_not_freshly_observed"
                    break
            if worker.end_reason and not snapshot:
                blocker = blocker or "recognition_ended:" + worker.end_reason
                stop_ship("recognition_ended")
                if brake_at_time is None:
                    break
        else:
            blocker = blocker or "approach_probe_timeout"
        if acceleration_at is not None and brake_at_time is None:
            stop_ship("shutdown")
        if cancel_seen.is_set():
            blocker = "cancel_requested"
        if not blocker:
            if brake_reason not in ("threshold", "threshold_watchdog"):
                blocker = "thrust_limit_before_brake_threshold"
            elif not result["final_stop_observed"]:
                blocker = "stop_not_freshly_observed"
            elif not any(r["speed"] > 0 for r in motion):
                blocker = "motion_not_freshly_observed"
            elif not initial or not motion or min(r["distance"] for r in motion) >= initial["distance"] - 5:
                blocker = "distance_closure_not_observed"
        result.update(state="blocked" if blocker else "proved",
                      reason=blocker or "approach_and_stop_image_observed")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    except KeyboardInterrupt:
        error = "KeyboardInterrupt: supervisor_cancelled"
        result.update(state="blocked", reason=error)
    finally:
        stop_ship("shutdown")
        watchdog_stop.set()
        if watchdog:
            watchdog.join(1)
        shutdown["watchdog"] = not watchdog or not watchdog.is_alive()
        if dispatcher:
            until = time.monotonic() + .5
            while backend.pending_requests and time.monotonic() < until:
                time.sleep(.01)
            try:
                shutdown["dispatcher"] = dispatcher.stop(2)
            except Exception as exc:
                shutdown["dispatcher"] = False
                error = error or f"dispatcher_stop_failure: {type(exc).__name__}: {exc}"
            try:
                result["deliveries"] = plain(dispatcher.deliveries())
                result["dispatcher_error"] = dispatcher.error
            except Exception as exc:
                shutdown["dispatcher"] = False
                error = error or f"delivery_read_failure: {type(exc).__name__}: {exc}"
        else:
            shutdown["dispatcher"] = True
        if worker:
            try:
                worker.stop()
                shutdown["recognition"] = worker.join(2)
            except Exception as exc:
                shutdown["recognition"] = False
                error = error or f"recognition_stop_failure: {type(exc).__name__}: {exc}"
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
            try:
                backend.cancel(reason="approach_probe_finished")
                backend.flush_journal()
            except Exception as exc:
                error = error or f"input_cancel_failure: {type(exc).__name__}: {exc}"
        shutdown["input_idle"] = not backend or (not backend.pending_requests and not backend.held_keys)
        result.update(brake_reason=brake_reason, cleanup_attempts=stop_attempts,
                      cleanup_failure=stop_failure,
                      cancel_requested=cancel_seen.is_set())
        if stop_failure:
            result.update(state="blocked", reason=stop_failure)
            error = error or stop_failure
    if dispatcher and acceleration_records and brake_records:
        try:
            deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
            linked = [deliveries.get(r.sequence) for r in (*acceleration_records, *brake_records)]
            if len(linked) != 4 or any(d is None or d.outcome != "injected" for d in linked):
                result.update(state="blocked", reason="control_input_not_delivered")
            elif not (linked[0].at <= linked[1].at <= linked[2].at <= linked[3].at):
                result.update(state="blocked", reason="control_delivery_order_invalid")
            else:
                post_motion = [r for r in motion if r["acquired_at"] > linked[1].at
                    and r["speed"] > 0 and initial
                    and r["distance"] < initial["distance"] - 5]
                result["post_acceleration_motion_sequences"] = [r["sequence"] for r in post_motion]
                if result["state"] == "proved" and not post_motion:
                    result.update(state="blocked", reason="post_delivery_motion_not_observed")
        except Exception:
            result.update(state="blocked", reason="control_delivery_evidence_unavailable")
    elif acceleration_at is not None:
        result.update(state="blocked", reason="control_input_not_delivered")
    if cancel_seen.is_set():
        result.update(state="blocked", reason="cancel_requested",
                      cancel_requested=True)
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    if not report["complete_diagnostics"] or not all(shutdown.values()) or result.get("dispatcher_error"):
        result.update(state="blocked", reason="incomplete_diagnostic_evidence")
    _write_json(diagnostic.directory / "approach-probe.json", {"run_id": diagnostic.journal.run_id,
        "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result})
    return {"run_id": diagnostic.journal.run_id,
            "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--profile", default="largehud-20261003")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clear-corridor-assessed", action="store_true", required=True)
    parser.add_argument("--aligned-heading-assessed", action="store_true", required=True)
    parser.add_argument("--safe-stopped-scene-assessed", action="store_true", required=True)
    parser.add_argument("--max-thrust-seconds", type=float, default=12.)
    parser.add_argument("--stop-seconds", type=float, default=35.)
    parser.add_argument("--cancel-file", type=Path)
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd, expected_pid=args.pid)
    if geometry.title != "Battlestar Galactica Online":
        raise ValueError("unexpected_game_window_title")
    if geometry.client_size != (2560, 1440):
        raise ValueError("approach_requires_native_2560_client")
    result = run_approach_probe(args.output,
        source_factory=lambda: NativeSource(NativeCapture(geometry.hwnd, geometry.pid,
            layout=args.profile), .4),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend,
            geometry.hwnd, geometry.pid, expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin, journal=journal,
            minimum_key_hold_seconds=.05, keyboard_mode="virtual_key"),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
            "source_size": "native-2560", "minimum_key_hold_ms": 50,
            "keyboard_mode": "virtual_key"},
        profile=args.profile, expected_window_id=f"{geometry.pid}:{geometry.hwnd}",
        frame_size=geometry.client_size, clear_corridor_assessed=True,
        aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
        max_thrust_seconds=args.max_thrust_seconds,
        stop_seconds=args.stop_seconds, cancel_file=args.cancel_file)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] == "proved" and result["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
