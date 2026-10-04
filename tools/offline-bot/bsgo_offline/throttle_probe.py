"""Supervised one-notch thrust calibration with image-observed motion and stop."""
import argparse
import json
import math
from pathlib import Path
from threading import Event, Lock, Thread
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .recognition import RecognitionWorker
from .state import SnapshotStore


def run_throttle_probe(output, *, source_factory, recognizer_factory,
                       dispatcher_factory, configuration, profile,
                       expected_window_id, frame_size, wheel_point, wheel_steps,
                       clear_corridor_assessed, aligned_heading_assessed,
                       safe_stopped_scene_assessed, flight_point_assessed,
                       scope="native", sample_interval=5., baseline_seconds=12.,
                       stop_seconds=35., travel_budget=300., cancel_file=None,
                       brake_at=None):
    if (clear_corridor_assessed, aligned_heading_assessed,
            safe_stopped_scene_assessed, flight_point_assessed) != (True, True, True, True):
        raise ValueError("fresh_supervisor_assessments_required")
    if scope not in ("native", "synthetic") or sample_interval != 5:
        raise ValueError("five_second_native_calibration_required")
    if (type(wheel_steps) is not int or wheel_steps not in (-1, 1)
            or len(wheel_point) != 2 or any(type(v) is not int for v in wheel_point)
            or not 0 <= wheel_point[0] < frame_size[0]
            or not 0 <= wheel_point[1] < frame_size[1]):
        raise ValueError("one_notch_and_pinned_frame_point_required")
    for name, value, low, high in (("baseline_seconds", baseline_seconds, 1, 30),
                                   ("stop_seconds", stop_seconds, 1, 45),
                                   ("travel_budget", travel_budget, 50, 300)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(name)
    if brake_at is not None and (type(brake_at) not in (int, float) or brake_at != 800):
        raise ValueError("reduced_speed_transit_requires_brake_at_800")
    transit_mode = brake_at is not None
    range_threshold = brake_at
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    diagnostic = DiagnosticRun(output, mode=scope + "_throttle_probe",
        configuration={**configuration, "wheel_point": wheel_point,
            "wheel_steps": wheel_steps, "sample_interval": sample_interval,
            "maximum_image_age": 7.,
            "travel_budget": None if transit_mode else travel_budget,
            "mode": "reduced_speed_transit" if transit_mode else "one_notch_calibration",
            "brake_at": range_threshold, "final_range_acceptance": [700, 800] if transit_mode else None,
            "cancel_file": str(cancel_path) if cancel_path else None,
            "supervisor_assumptions": {"clear_corridor": True,
                "aligned_heading": True, "safe_stopped_scene": True,
                "flight_point": True}, "mining_ready": False},
        frame_capacity=32, max_frames=None)
    store = SnapshotStore()
    source = worker = backend = dispatcher = watchdog = None
    watchdog_end, canceled_seen = Event(), Event()
    stop_lock = Lock()
    initial = context = None
    baseline, motion, stop_images = [], [], []
    stopped_target_ranges = []
    wheel_request = brake_records = ()
    wheel_requested_at = brake_at = None
    brake_reason = blocker = error = None
    stop_attempts = 0
    stop_failure = None
    observed_sequence, acquisition_sequence = 0, -1
    stable = []
    first_rejection = None
    shutdown = {}
    result = {"state": "blocked", "reason": "not_started", "mining_ready": False,
        "baseline": baseline, "motion": motion, "stable_motion_sequences": stable,
        "stop_observation_sequences": stop_images, "wheel_requests": [],
        "brake_requests": [], "deliveries": [], "first_rejection": None,
        "observed_partial_speed": None, "final_stop_observed": False,
        "final_stop_distance": None, "brake_at": range_threshold,
        "stopped_target_ranges": stopped_target_ranges,
        "unverified": ["throttle_setting", "other_wheel_direction", "combat_detection",
                       "resource", "mining", "inventory"]}

    def canceled():
        if cancel_path is None:
            return False
        try:
            exists = cancel_path.exists()
        except OSError:
            exists = True
        if exists:
            canceled_seen.set()
        return exists

    def reject(reason, snapshot=None):
        nonlocal first_rejection
        diagnostic.update_execution(reason=str(reason), blocker=str(reason),
            waiting_for="fresh stop evidence" if wheel_requested_at is not None else None)
        if first_rejection is None:
            first_rejection = {"reason": str(reason)[:200],
                "observation_sequence": snapshot.sequence if snapshot else None,
                "acquisition_sequence": snapshot.acquisition_sequence if snapshot else None,
                "field_reasons": {name: (field.reason if field else "missing")
                    for name in ("speed", "distance", "target_identity", "target_portrait",
                                 "target_max_hp", "hull_current", "hull_max")
                    for field in [snapshot.fields.get(name) if snapshot else None]
                    if field is None or not field.valid}}
            result["first_rejection"] = first_rejection
            diagnostic.submit({"event": "throttle_first_rejection", **first_rejection})

    def valid(snapshot, now):
        if (not snapshot.healthy or snapshot.window_id != expected_window_id
                or snapshot.frame_size != tuple(frame_size)
                or context is not None and snapshot.context != context
                or not 0 <= now - snapshot.acquired_at < 7):
            raise ValueError("invalid_or_stale_capture_context")
        values = {}
        for name in ("speed", "distance", "asteroid_selected", "target_identity",
                     "target_portrait", "target_max_hp", "hull_current", "hull_max"):
            field = snapshot.fields.get(name)
            if (field is None or not field.valid or field.observed_at != snapshot.acquired_at
                    or not 0 <= now - field.observed_at < 7):
                raise ValueError("invalid_or_stale_" + name)
            values[name] = field.value
        if (type(values["speed"]) not in (int, float) or not math.isfinite(values["speed"])
                or not 0 <= values["speed"] <= 44
                or type(values["distance"]) not in (int, float)
                or not math.isfinite(values["distance"]) or values["distance"] < 0
                or values["asteroid_selected"] is not True
                or not isinstance(values["target_identity"], str)
                or not values["target_identity"].startswith("asteroid:")
                or type(values["target_max_hp"]) not in (int, float)
                or not math.isfinite(values["target_max_hp"])
                or values["target_max_hp"] <= 0
                or not isinstance(values["target_portrait"], tuple)
                or len(values["target_portrait"]) != 144
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in values["target_portrait"])
                or type(values["hull_max"]) not in (int, float)
                or not math.isfinite(values["hull_max"])
                or values["hull_max"] <= 0
                or type(values["hull_current"]) not in (int, float)
                or not math.isfinite(values["hull_current"])
                or values["hull_current"] != values["hull_max"]):
            raise ValueError("invalid_target_speed_or_full_hull")
        if initial is not None:
            if (values["target_identity"] != initial["target_identity"]
                    or values["target_max_hp"] != initial["target_max_hp"]
                    or values["hull_max"] != initial["hull_max"]
                    or sum(abs(a-b) for a, b in zip(values["target_portrait"],
                                  initial["target_portrait"])) / 144 > .75):
                raise ValueError("selected_target_or_hull_changed")
        return {"sequence": snapshot.sequence, "acquisition_sequence": snapshot.acquisition_sequence,
            "acquired_at": snapshot.acquired_at, "published_at": snapshot.published_at,
            **values}

    def speed_zero(snapshot, now):
        field = snapshot.fields.get("speed")
        return bool(snapshot.healthy and snapshot.window_id == expected_window_id
            and snapshot.frame_size == tuple(frame_size) and snapshot.context == context
            and 0 <= now - snapshot.acquired_at < 7 and field and field.valid
            and field.observed_at == snapshot.acquired_at
            and type(field.value) in (int, float) and field.value == 0)

    def input_guard(record):
        if record.kind != "mouse_wheel" or (wheel_request and record.sequence != wheel_request[0].sequence):
            return False
        if canceled() or brake_at is not None or stop_failure is not None:
            return False
        snapshot = store.latest()
        if snapshot is None:
            return False
        try:
            row = valid(snapshot, time.monotonic())
            return (row["speed"] == 0 and abs(row["distance"] - initial["distance"]) <= 5
                    and time.monotonic() - wheel_requested_at < 2)
        except (ValueError, TypeError):
            return False

    def stop_ship(reason):
        nonlocal brake_at, brake_reason, brake_records, stop_attempts, stop_failure
        with stop_lock:
            if backend is None or wheel_requested_at is None or brake_at is not None or stop_attempts >= 2:
                return
            stop_attempts += 1
            try:
                records = backend.request_cleanup_stop(reason="throttle_" + reason)
                brake_records = records
                brake_at = time.monotonic()
                brake_reason = reason
                result["brake_requests"] = plain(records)
                diagnostic.update_execution(phase="braking", processing="checking the requested stop",
                    waiting_for="PageDown delivery and two fresh zero-speed images",
                    expected_next_step="finalize the checked stop or blocker", reason=reason,
                    blocker=blocker, progress="PageDown cleanup stop requested")
                diagnostic.submit({"event": "throttle_brake_requested", "reason": reason,
                                   "at": brake_at})
            except Exception as exc:
                stop_failure = f"cleanup_request_failure: {type(exc).__name__}: {exc}"
                diagnostic.submit({"event": "throttle_brake_request_failed",
                                   "reason": stop_failure})

    def watchdog_run():
        nonlocal blocker
        while not watchdog_end.wait(.05):
            if canceled():
                stop_ship("cancel_requested")
            if wheel_requested_at is None or brake_at is not None:
                continue
            now = time.monotonic()
            snapshot = store.latest()
            if snapshot is None or now - snapshot.acquired_at >= 7:
                blocker = blocker or "stale_image_watchdog"
                reject("stale_image_watchdog", snapshot)
                stop_ship("stale_image_watchdog")
                continue
            try:
                row = valid(snapshot, now)
                if row["speed"] >= 32.9:
                    blocker = blocker or "full_speed_not_partial"
                    reject("full_speed_not_partial", snapshot)
                    stop_ship("full_speed")
                elif not transit_mode and initial and initial["distance"] - row["distance"] >= travel_budget:
                    blocker = blocker or "travel_budget_reached"
                    reject("travel_budget_reached", snapshot)
                    stop_ship("travel_budget")
                elif transit_mode and row["distance"] <= range_threshold:
                    stop_ship("range_threshold")
            except ValueError as exc:
                blocker = blocker or str(exc)
                reject(str(exc), snapshot)
                stop_ship("invalid_image_watchdog")

    try:
        if canceled():
            raise ValueError("cancel_requested_before_input")
        source = source_factory()
        backend = RecordingInputBackend(session_id="throttle-" + diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1],
            journal=diagnostic.journal)
        backend.set_delivery_contract(input_guard)
        worker = RecognitionWorker(source, recognizer_factory(), store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        diagnostic.update_execution(phase="baseline", processing="checking stopped target and full hull",
            waiting_for="two fresh stationary same-target images",
            expected_next_step="request one wheel notch only if the baseline passes")
        worker.start()
        baseline_deadline = time.monotonic() + baseline_seconds
        while True:
            if canceled():
                if wheel_requested_at is None:
                    blocker = blocker or "cancel_requested_before_input"
                    break
                stop_ship("cancel_requested")
            snapshot = store.wait_for_sequence(observed_sequence, .025)
            now = time.monotonic()
            if snapshot:
                if snapshot.sequence != observed_sequence + 1 or snapshot.acquisition_sequence <= acquisition_sequence:
                    blocker = blocker or "observation_sequence_gap"
                    reject(blocker, snapshot)
                    stop_ship("observation_gap")
                observed_sequence = snapshot.sequence
                acquisition_sequence = snapshot.acquisition_sequence
                if wheel_requested_at is None:
                    if blocker:
                        break
                    try:
                        row = valid(snapshot, now)
                        if row["speed"] != 0:
                            raise ValueError("stopped_baseline_required")
                        if initial is None:
                            initial, context = row, snapshot.context
                        elif abs(row["distance"] - initial["distance"]) > 5:
                            raise ValueError("baseline_distance_changed")
                        baseline.append(row)
                        if len(baseline) >= 2:
                            if canceled():
                                blocker = "cancel_requested_before_input"
                                break
                            dispatcher = dispatcher_factory(backend, diagnostic.journal)
                            diagnostic.attach_dispatcher(dispatcher)
                            dispatcher.start()
                            wheel_requested_at = time.monotonic()
                            watchdog = Thread(target=watchdog_run, name="throttle-safety-watchdog",
                                              daemon=True)
                            watchdog.start()
                            wheel_request = (backend.wheel_mouse(*wheel_point, steps=wheel_steps,
                                             reason="supervised_one_notch_thrust"),)
                            result["wheel_requests"] = plain(wheel_request)
                            diagnostic.update_execution(phase="slow_approach",
                                processing="checking wheel delivery and partial motion",
                                waiting_for="three stable closing post-delivery motion images",
                                expected_next_step="brake at the configured range or after the calibration evidence",
                                progress="one wheel notch requested")
                    except ValueError as exc:
                        blocker = blocker or str(exc)
                        reject(str(exc), snapshot)
                        break
                elif brake_at is None:
                    try:
                        row = valid(snapshot, now)
                        motion.append(row)
                        if not transit_mode and initial["distance"] - row["distance"] >= travel_budget:
                            raise ValueError("travel_budget_reached")
                        if row["speed"] >= 32.9:
                            raise ValueError("full_speed_not_partial")
                        delivery = next((d for d in dispatcher.deliveries()
                            if wheel_request and d.request_sequence == wheel_request[0].sequence), None)
                        if delivery and delivery.outcome != "injected":
                            raise ValueError("wheel_input_not_delivered")
                        if delivery and row["acquired_at"] > delivery.at:
                            if row["speed"] == 0:
                                stable.clear()
                                zeros = [r for r in motion if r["acquired_at"] > delivery.at
                                         and r["speed"] == 0]
                                if len(zeros) >= 2:
                                    raise ValueError("no_motion_after_wheel")
                            else:
                                if motion and len(motion) >= 2 and row["distance"] > motion[-2]["distance"] + 3:
                                    raise ValueError("target_not_closing")
                                if (len(motion) >= 3 and row["distance"] >= motion[-3]["distance"] - 3):
                                    raise ValueError("distance_not_closing")
                                if initial["distance"] - row["distance"] > 5:
                                    if stable:
                                        prior = next(r for r in motion if r["sequence"] == stable[-1])
                                        if abs(row["speed"] - prior["speed"]) > .3:
                                            stable.clear()
                                    stable.append(row["sequence"])
                                if len(stable) >= 3:
                                    result["observed_partial_speed"] = row["speed"]
                                    diagnostic.update_execution(phase="slow_approach",
                                        processing="checking verified partial-speed closing motion",
                                        waiting_for="the range threshold" if transit_mode else "a fresh stop after braking",
                                        expected_next_step="request PageDown at the range threshold" if transit_mode
                                            else "request the calibration cleanup stop",
                                        progress="three stable closing motion images verified",
                                        confirmed_effect={"effect": "post_delivery_stable_partial_motion",
                                            "observation_sequences": stable[-3:],
                                            "request_sequences": [r.sequence for r in wheel_request]})
                                    if not transit_mode:
                                        stop_ship("stable_partial_speed")
                        if transit_mode and row["distance"] <= range_threshold and brake_at is None:
                            stop_ship("range_threshold")
                    except ValueError as exc:
                        blocker = blocker or str(exc)
                        reject(str(exc), snapshot)
                        stop_ship("image_evidence_rejected")
                else:
                    try:
                        row = valid(snapshot, now)
                        if row["speed"] == 0:
                            result["final_stop_distance"] = row["distance"]
                            previous_stopped_range = (stopped_target_ranges[-1]
                                if stopped_target_ranges else None)
                            stopped_target_ranges.append(row["distance"])
                            if transit_mode and not 700 <= row["distance"] <= 800:
                                raise ValueError("final_range_outside_reduced_speed_acceptance_band")
                            if (previous_stopped_range is not None
                                    and abs(row["distance"] - previous_stopped_range) > 5):
                                raise ValueError("stopped_target_distance_changed")
                    except ValueError as exc:
                        blocker = blocker or str(exc)
                        reject(str(exc), snapshot)
                    ups = [d for d in dispatcher.deliveries()
                        if d.outcome == "injected" and d.kind == "key_up"
                        and brake_records and d.request_sequence == brake_records[-1].sequence]
                    if (ups and snapshot.acquired_at > ups[-1].at
                            and speed_zero(snapshot, now)):
                        stop_images.append(snapshot.sequence)
                    else:
                        stop_images.clear()
            if wheel_requested_at is None and now >= baseline_deadline:
                blocker = blocker or "baseline_timeout"
                reject(blocker, snapshot)
                break
            if stop_failure and stop_attempts >= 2:
                blocker = blocker or stop_failure
                reject(blocker, snapshot)
                break
            if wheel_requested_at is not None and brake_at is None:
                delivery = next((d for d in dispatcher.deliveries()
                    if wheel_request and d.request_sequence == wheel_request[0].sequence), None)
                if now >= wheel_requested_at + 2 and delivery is None:
                    blocker = blocker or "wheel_delivery_timeout"
                    reject(blocker, snapshot)
                    stop_ship("wheel_delivery_timeout")
            if brake_at is not None:
                if len(stop_images) >= 2:
                    result["final_stop_observed"] = True
                    break
                if now >= brake_at + stop_seconds:
                    blocker = blocker or "stop_not_freshly_observed"
                    break
            if worker.end_reason and not snapshot:
                blocker = blocker or "recognition_ended:" + worker.end_reason
                reject(blocker, store.latest())
                stop_ship("recognition_ended")
                if wheel_requested_at is None:
                    break
        if wheel_requested_at is not None and brake_at is None:
            stop_ship("shutdown")
        if canceled_seen.is_set():
            blocker = "cancel_requested"
        if blocker is None:
            if len(stable) < 3 or not result["final_stop_observed"]:
                blocker = "partial_throttle_or_stop_unverified"
            elif transit_mode and (result["final_stop_distance"] is None
                    or not 700 <= result["final_stop_distance"] <= 800):
                blocker = "final_range_outside_reduced_speed_acceptance_band"
            elif transit_mode and brake_reason != "range_threshold":
                blocker = "range_threshold_not_reached"
        result.update(state="blocked" if blocker else "proved",
                      reason=blocker or "one_notch_partial_speed_and_stop_observed")
    except (Exception, KeyboardInterrupt) as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    finally:
        stop_ship("shutdown")
        diagnostic.update_execution(phase="cleanup", processing="stopping throttle workers and flushing evidence",
            waiting_for="watchdog, dispatcher, recognition and diagnostic writers to stop",
            expected_next_step="publish the checked throttle outcome", blocker=blocker or error)
        watchdog_end.set()
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
                error = error or f"dispatcher_stop_failure: {exc}"
            try:
                result["deliveries"] = plain(dispatcher.deliveries())
                result["dispatcher_error"] = dispatcher.error
            except Exception as exc:
                shutdown["dispatcher"] = False
                error = error or f"delivery_read_failure: {exc}"
        else:
            shutdown["dispatcher"] = True
        if worker:
            try:
                worker.stop()
                shutdown["recognition"] = worker.join(2)
            except Exception as exc:
                shutdown["recognition"] = False
                error = error or f"recognition_stop_failure: {exc}"
        else:
            shutdown["recognition"] = True
        shutdown["source"] = shutdown["recognition"]
        if source and shutdown["recognition"]:
            try:
                source.close()
            except Exception as exc:
                shutdown["source"] = False
                error = error or f"source_close_failure: {exc}"
        if backend:
            try:
                backend.cancel(reason="throttle_probe_finished")
                backend.flush_journal()
                shutdown["backend_cancel"] = True
            except Exception as exc:
                shutdown["backend_cancel"] = False
                error = error or f"input_cancel_failure: {exc}"
        else:
            shutdown["backend_cancel"] = True
        shutdown["input_idle"] = not backend or (not backend.pending_requests and not backend.held_keys)
        result.update(brake_reason=brake_reason, cleanup_attempts=stop_attempts,
                      cleanup_failure=stop_failure, cancel_requested=canceled_seen.is_set())
    if stop_failure:
        result.update(state="blocked", reason=stop_failure)
    if dispatcher and wheel_request and brake_records:
        deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
        linked = [deliveries.get(r.sequence) for r in (*wheel_request, *brake_records)]
        if (len(linked) != 3 or any(d is None or d.outcome != "injected" for d in linked)
                or not (linked[0].at <= linked[1].at <= linked[2].at)):
            result["delivery_blocker"] = "control_input_not_delivered"
            if result["state"] == "proved" or result["reason"] == "not_started":
                result.update(state="blocked", reason="control_input_not_delivered")
        elif result["state"] == "proved" and not any(r["acquired_at"] > linked[0].at
                and r["speed"] > 0 and r["distance"] < initial["distance"] - 5 for r in motion):
            result.update(state="blocked", reason="post_delivery_partial_motion_unverified")
    elif wheel_requested_at is not None:
        result["delivery_blocker"] = "control_input_not_delivered"
        if result["state"] == "proved" or result["reason"] == "not_started":
            result.update(state="blocked", reason="control_input_not_delivered")
    if canceled_seen.is_set():
        result.update(state="blocked", reason="cancel_requested")
    if result["state"] == "proved":
        diagnostic.update_execution(phase="cleanup", processing="finalizing checked partial motion and stop evidence",
            confirmed_effect={"effect": "post_delivery_partial_motion_and_stop_verified",
                "observation_sequences": stop_images[-2:],
                "motion_observation_sequences": stable[-3:],
                "request_sequences": [r.sequence for r in (*wheel_request, *brake_records)]})
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    if not report["complete_diagnostics"] or not all(shutdown.values()) or result.get("dispatcher_error"):
        result.update(state="blocked", reason="incomplete_diagnostic_evidence")
    _write_json(diagnostic.directory / "throttle-probe.json", {"run_id": diagnostic.journal.run_id,
        "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result})
    return {"run_id": diagnostic.journal.run_id,
            "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--profile", default="largehud-20261003")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel-x", type=int, required=True)
    parser.add_argument("--wheel-y", type=int, required=True)
    parser.add_argument("--wheel-steps", type=int, choices=(-1, 1), required=True)
    parser.add_argument("--brake-at", type=float,
                        help="Opt-in reduced-speed transit to 800 m; otherwise bounded one-notch calibration")
    for flag in ("clear-corridor-assessed", "aligned-heading-assessed",
                 "safe-stopped-scene-assessed", "flight-point-assessed"):
        parser.add_argument("--" + flag, action="store_true", required=True)
    parser.add_argument("--cancel-file", type=Path)
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd, expected_pid=args.pid)
    if geometry.title != "Battlestar Galactica Online" or geometry.client_size != (2560, 1440):
        raise ValueError("throttle_probe_requires_native_2560_game_client")
    result = run_throttle_probe(args.output,
        source_factory=lambda: NativeSource(NativeCapture(geometry.hwnd, geometry.pid,
            layout=args.profile), 5.),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend,
            geometry.hwnd, geometry.pid, expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin, journal=journal,
            minimum_key_hold_seconds=.05, minimum_mouse_settle_seconds=.05,
            keyboard_mode="virtual_key"),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
            "keyboard_mode": "virtual_key", "key_hold_ms": 50, "mouse_settle_ms": 50},
        profile=args.profile, expected_window_id=f"{geometry.pid}:{geometry.hwnd}",
        frame_size=geometry.client_size, wheel_point=(args.wheel_x, args.wheel_y),
        wheel_steps=args.wheel_steps, clear_corridor_assessed=True,
        aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
        flight_point_assessed=True, cancel_file=args.cancel_file,
        brake_at=args.brake_at)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] == "proved" and result["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
