"""Supervised distance-driven approach to one preselected asteroid and observed stop.

This is a single experiment, not mining readiness. Scene, corridor and heading
are supervisor assertions; combat and route clearance remain unrecognized.
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


class HullGuardError(PolicyError):
    """Fresh hull evidence cannot authorize continuing this approach."""


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
                       scope="native", brake_at=900., launch_clearance=250.,
                       max_thrust_seconds=None,
                       baseline_seconds=4., stop_seconds=35.,
                       decision_interval=2., cancel_file=None, sample_interval=.4,
                       reduced_transition_at=None, wheel_point=None,
                       flight_point_assessed=False):
    """Injectable supervised probe with an independent PageDown watchdog."""
    if (clear_corridor_assessed, aligned_heading_assessed,
            safe_stopped_scene_assessed) != (True, True, True):
        raise ValueError("fresh_supervisor_scene_assessments_required")
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    if cancel_file is not None and not str(cancel_file).strip():
        raise ValueError("cancel_file")
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    if max_thrust_seconds is not None and (type(max_thrust_seconds) not in (int, float)
            or not math.isfinite(max_thrust_seconds) or not .1 <= max_thrust_seconds <= 180):
        raise ValueError("max_thrust_seconds")
    for name, value, low, high in (("brake_at", brake_at, 600, 1400),
        ("launch_clearance", launch_clearance, 75, 250),
        ("baseline_seconds", baseline_seconds, .1, 30),
        ("stop_seconds", stop_seconds, .1, 45),
        ("decision_interval", decision_interval, .01, 10),
        ("sample_interval", sample_interval, .01, 10)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(name)
    two_regime = reduced_transition_at is not None
    if two_regime:
        if (reduced_transition_at != 1300 or brake_at != 800
                or sample_interval != 5 or max_thrust_seconds is not None
                or flight_point_assessed is not True or wheel_point is None
                or len(wheel_point) != 2 or any(type(v) is not int for v in wheel_point)
                or not 0 <= wheel_point[0] < frame_size[0]
                or not 0 <= wheel_point[1] < frame_size[1]):
            raise ValueError("two_regime_requires_1300_to_800_uncapped_assessed_wheel_point")
    maximum_image_age = max(2., sample_interval + 2.)
    policy = ApproachPolicy(brake_at=brake_at, launch_clearance=launch_clearance,
                            max_frame_age=maximum_image_age,
                            max_observation_gap=max(4., sample_interval + 2.))
    frame_budget = (None if max_thrust_seconds is None else max(256,
        math.ceil((baseline_seconds + max_thrust_seconds + stop_seconds + 8) / sample_interval)))
    diagnostic = DiagnosticRun(output, mode=scope + "_supervised_approach_probe",
        configuration={**configuration, "supervisor_assumptions": {
            "clear_corridor": True, "aligned_heading": True,
            "safe_stopped_scene": True}, "brake_at": brake_at,
            "launch_clearance": launch_clearance,
            "max_thrust_seconds": max_thrust_seconds,
            "sample_interval": sample_interval, "maximum_image_age": maximum_image_age,
            "baseline_seconds": baseline_seconds, "stop_seconds": stop_seconds,
            "gun_optimal_range": 760, "gun_max_range": 800,
            "two_regime": two_regime, "reduced_transition_at": reduced_transition_at,
            "wheel_point": wheel_point if two_regime else None,
            "flight_point_assessed": flight_point_assessed if two_regime else None,
            "reduced_speed_target": 5. if two_regime else None,
            "final_range_acceptance": [700, 800] if two_regime else None,
            "cancel_file": str(cancel_path) if cancel_path else None,
            "mission_started": False}, frame_capacity=32, max_frames=frame_budget)
    store = SnapshotStore()
    source = worker = backend = dispatcher = watchdog = None
    watchdog_stop = Event()
    cancel_seen = Event()
    stop_lock = Lock()
    started = time.monotonic()
    baseline, motion, stop_images = [], [], []
    braking_observations = []
    context = initial = previous = None
    observed_sequence = 0
    previous_acquisition_sequence = -1
    post_delivery_zero_samples = 0
    acceleration_at = brake_at_time = None
    brake_reason = blocker = error = None
    acceleration_records = brake_records = ()
    transition_records = wheel_records = ()
    transition_requested_at = wheel_requested_at = None
    transition_speed = None
    reduced_speed_sequences = []
    deceleration_observed = False
    post_wheel_full_samples = 0
    previous_post_wheel_speed = None
    stop_attempts = 0
    stop_failure = None
    watchdog_rejection = None
    shutdown = {}
    result = {"state": "blocked", "reason": "not_started", "mining_ready": False,
        "baseline": baseline, "motion": motion, "stop_observation_sequences": stop_images,
        "acceleration_requests": [], "brake_requests": [], "deliveries": [],
        "post_acceleration_motion_sequences": [],
        "final_stop_observed": False,
        "braking_observations": braking_observations,
        "brake_start_observation": None, "final_stop_distance": None,
        "observed_braking_distance": None,
        "transition_requests": [], "wheel_requests": [],
        "transition_observation": None, "deceleration_observed": False,
        "reduced_speed_sequences": reduced_speed_sequences,
        "two_regime": two_regime, "reduced_speed_target": 5. if two_regime else None,
        "final_stop_range_observations": [],
        "cancel_requested": False,
        "unverified": ["combat_detection", "exclusive_input_ownership",
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
            snapshot = store.latest()
            if snapshot is not None:
                brake_start = {"observation_sequence": snapshot.sequence,
                    "acquired_at": snapshot.acquired_at,
                    "fresh": 0 <= at - snapshot.acquired_at < maximum_image_age}
                for name in ("distance", "speed"):
                    field = snapshot.fields.get(name)
                    brake_start[name] = (field.value if field and field.valid
                        and type(field.value) in (int, float) and math.isfinite(field.value)
                        and field.observed_at == snapshot.acquired_at else None)
                result["brake_start_observation"] = brake_start
            try:
                brake_records = backend.request_cleanup_stop(reason="approach_" + reason)
                brake_at_time, brake_reason = at, reason
                result["brake_requests"] = plain(brake_records)
                result["actual_brake_requested_at"] = at
                diagnostic.submit({"event": "approach_brake_requested", "at": at,
                                   "reason": reason, "attempt": stop_attempts})
            except Exception as exc:
                stop_failure = f"cleanup_request_failure: {type(exc).__name__}: {exc}"
                diagnostic.submit({"event": "approach_brake_request_failed", "at": at,
                                   "reason": stop_failure, "attempt": stop_attempts})

    def valid_hull(snapshot, now):
        if (not snapshot.healthy or snapshot.window_id != expected_window_id
                or snapshot.frame_size != tuple(frame_size)
                or context is not None and snapshot.context != context
                or not 0 <= now - snapshot.acquired_at < maximum_image_age):
            raise HullGuardError("Invalid or stale hull capture context")
        hull = {}
        for name in ("hull_current", "hull_max"):
            field = snapshot.fields.get(name)
            if (not field or not field.valid or type(field.value) not in (int, float)
                    or not math.isfinite(field.value) or field.value <= 0
                    or field.observed_at != snapshot.acquired_at
                    or not 0 <= now - field.observed_at < maximum_image_age):
                raise HullGuardError("Missing, invalid or stale " + name)
            hull[name] = field.value
        if initial is not None and hull["hull_max"] != initial["hull_max"]:
            raise HullGuardError("Hull maximum changed")
        if hull["hull_current"] != hull["hull_max"]:
            raise HullGuardError("Hull loss observed" if initial else "Full hull required before approach")
        return hull

    def valid_target(snapshot, now):
        if (snapshot.window_id != expected_window_id or snapshot.frame_size != tuple(frame_size)
                or context is not None and snapshot.context != context):
            raise PolicyError("Capture window or context changed")
        row = policy.validate(snapshot, now)
        hull = valid_hull(snapshot, now)
        hp = snapshot.fields.get("target_max_hp")
        if (not hp or not hp.valid or type(hp.value) not in (int, float)
                or not math.isfinite(hp.value) or hp.value <= 0
                or not 0 <= now - hp.observed_at < maximum_image_age):
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
            "context": plain(snapshot.context), **hull}

    def input_guard(record):
        if canceled():
            return False
        now = time.monotonic()
        snapshot = store.latest()
        if brake_at_time is not None or stop_failure is not None or snapshot is None:
            return False
        try:
            row, _ = valid_target(snapshot, now)
            if record.key == "PageUp" and record.kind == "key_down":
                return (acceleration_at is not None and 0 <= now - acceleration_at < 1
                        and row.speed == 0 and row.distance >= brake_at + launch_clearance
                        and (not two_regime or row.distance >= 1550))
            if not two_regime:
                return False
            if record.key == "PageDown" and record.kind == "key_down":
                return (transition_requested_at is not None
                        and (not transition_records or record.sequence == transition_records[0].sequence)
                        and 0 <= now - transition_requested_at < 2
                        and 800 < row.distance <= reduced_transition_at and row.speed > 5)
            if record.kind == "mouse_wheel":
                if (wheel_requested_at is None or record.wheel_steps != 1
                        or (wheel_records and record.sequence != wheel_records[0].sequence)
                        or not 0 <= now - wheel_requested_at < 2
                        or not 800 < row.distance <= reduced_transition_at):
                    return False
                deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
                return (len(transition_records) == 2
                    and all(deliveries.get(r.sequence) is not None
                            and deliveries[r.sequence].outcome == "injected"
                            for r in transition_records)
                    and deliveries[transition_records[0].sequence].at
                        <= deliveries[transition_records[1].sequence].at)
            return False
        except PolicyError:
            return False

    def request_reduced_transition(row, snapshot):
        nonlocal transition_requested_at, wheel_requested_at
        nonlocal transition_records, wheel_records, transition_speed
        with stop_lock:
            if (not two_regime or transition_requested_at is not None
                    or brake_at_time is not None or canceled()):
                return
            if not 800 < row.distance <= reduced_transition_at or row.speed <= 5:
                return
            transition_requested_at = time.monotonic()
            transition_speed = row.speed
            result["transition_observation"] = {
                "sequence": snapshot.sequence, "acquired_at": row.acquired_at,
                "distance": row.distance, "speed": row.speed}
            try:
                transition_records = backend.press_key("PageDown",
                    reason="supervised_reduce_from_full_thrust")
                result["transition_requests"] = plain(transition_records)
                wheel_requested_at = time.monotonic()
                wheel_records = (backend.wheel_mouse(*wheel_point, steps=1,
                    reason="supervised_one_notch_after_idle"),)
                result["wheel_requests"] = plain(wheel_records)
                diagnostic.submit({"event": "approach_reduced_transition_requested",
                    "observation_sequence": snapshot.sequence,
                    "transition_sequences": [r.sequence for r in transition_records],
                    "wheel_sequence": wheel_records[0].sequence})
            except Exception:
                # Caller requests the reserved PageDown, which preempts queued wheel input.
                raise

    def watchdog_run():
        nonlocal watchdog_rejection
        while not watchdog_stop.wait(.05):
            if canceled():
                stop_ship("cancel_requested")
                continue
            if acceleration_at is None or brake_at_time is not None:
                continue
            now = time.monotonic()
            snapshot = store.latest()
            if snapshot is None or now - snapshot.acquired_at >= maximum_image_age:
                stop_ship("stale_image_watchdog")
                continue
            try:
                row, _ = valid_target(snapshot, now)
            except Exception as exc:
                watchdog_rejection = str(exc)
                stop_ship("hull_guard" if isinstance(exc, HullGuardError)
                          else "invalid_image_watchdog")
                continue
            if row.distance <= brake_at:
                stop_ship("threshold_watchdog")
            elif (two_regime and wheel_requested_at is not None
                    and now - wheel_requested_at >= 2):
                deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
                if (not wheel_records or wheel_records[0].sequence not in deliveries
                        or deliveries[wheel_records[0].sequence].outcome != "injected"):
                    watchdog_rejection = "Reduced-throttle wheel delivery missing or rejected"
                    stop_ship("transition_delivery_watchdog")
            elif max_thrust_seconds is not None and now >= acceleration_at + max_thrust_seconds:
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
        overall_deadline = (None if max_thrust_seconds is None else
            started + baseline_seconds + max_thrust_seconds + stop_seconds + 2)
        while overall_deadline is None or time.monotonic() < overall_deadline:
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
                            if two_regime and row.distance < 1550:
                                raise PolicyError("Two-regime full-speed launch requires at least 1550 m")
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
                            acceleration_ups = [d for d in dispatcher.deliveries()
                                if d.outcome == "injected" and d.kind == "key_up"
                                and acceleration_records
                                and d.request_sequence == acceleration_records[-1].sequence]
                            if (transition_requested_at is None and acceleration_ups
                                    and row.acquired_at > acceleration_ups[-1].at):
                                post_delivery_zero_samples = (post_delivery_zero_samples + 1
                                    if row.speed == 0 else 0)
                                if post_delivery_zero_samples >= 2:
                                    raise PolicyError("Acceleration not observed in two fresh images")
                            if two_regime and transition_requested_at is not None:
                                deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
                                linked = [deliveries.get(r.sequence)
                                          for r in (*transition_records, *wheel_records)]
                                if any(d is not None and d.outcome != "injected" for d in linked):
                                    raise PolicyError("Reduced-throttle transition input not delivered")
                                wheel_up = linked[-1] if len(linked) == 3 else None
                                if wheel_up is not None and row.acquired_at > wheel_up.at:
                                    if (previous_post_wheel_speed is not None
                                            and row.speed > previous_post_wheel_speed + .3):
                                        raise PolicyError("Speed increased after reduced-throttle transition")
                                    previous_post_wheel_speed = row.speed
                                    if (result.get("observed_reduced_speed") is not None
                                            and not 4.7 <= row.speed <= 5.3):
                                        raise PolicyError("Observed reduced-speed regime changed")
                                    post_wheel_full_samples = (post_wheel_full_samples + 1
                                        if row.speed >= 32.9 else 0)
                                    if post_wheel_full_samples >= 2:
                                        raise PolicyError("Full speed persisted after wheel delivery")
                                    if row.speed <= transition_speed - .5:
                                        deceleration_observed = True
                                        result["deceleration_observed"] = True
                                    if row.speed >= 32.9 and deceleration_observed:
                                        raise PolicyError("Full speed returned after reduced-throttle transition")
                                    if row.speed == 0:
                                        reduced_speed_sequences.clear()
                                        zeros = [r for r in motion
                                            if r["acquired_at"] > wheel_up.at and r["speed"] == 0]
                                        if len(zeros) >= 2:
                                            raise PolicyError("Reduced throttle did not sustain motion")
                                    elif 4.7 <= row.speed <= 5.3:
                                        if (len(motion) >= 2
                                                and row.distance >= motion[-2]["distance"] - 3):
                                            raise PolicyError("Reduced-speed target is not closing")
                                        reduced_speed_sequences.append(snapshot.sequence)
                                    else:
                                        reduced_speed_sequences.clear()
                                    if len(reduced_speed_sequences) >= 3:
                                        result["observed_reduced_speed"] = row.speed
                            if row.distance <= brake_at:
                                action = policy.decide(snapshot, now)
                                if action != "brake":
                                    raise PolicyError("Policy did not request threshold brake")
                                stop_ship("threshold")
                            elif two_regime and transition_requested_at is None and row.distance <= reduced_transition_at:
                                request_reduced_transition(row, snapshot)
                            elif policy.last and (sample_interval >= decision_interval or
                                    row.acquired_at - policy.last.acquired_at >= decision_interval):
                                action = policy.decide(snapshot, now)
                                if action == "brake":
                                    stop_ship("threshold")
                        except PolicyError as exc:
                            blocker = blocker or str(exc)
                            stop_ship("hull_guard" if isinstance(exc, HullGuardError)
                                      else "image_evidence_rejected")
                    else:
                        try:
                            _, reading = valid_target(snapshot, now)
                            braking_observations.append(reading)
                            if reading["speed"] == 0:
                                result["final_stop_distance"] = reading["distance"]
                        except PolicyError as exc:
                            blocker = blocker or str(exc)
                            diagnostic.submit({"event": "approach_braking_hull_rejected"
                                if isinstance(exc, HullGuardError) else "approach_braking_target_rejected",
                                "observation_sequence": snapshot.sequence,
                                "acquisition_sequence": snapshot.acquisition_sequence,
                                "reason": str(exc), "at": now})
                        ups = [d for d in dispatcher.deliveries()
                            if d.outcome == "injected" and d.kind == "key_up"
                            and brake_records
                            and d.request_sequence == brake_records[-1].sequence]
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
            if (max_thrust_seconds is not None and acceleration_at is not None
                    and brake_at_time is None and now >= acceleration_at + max_thrust_seconds):
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
        if blocker is None and watchdog_rejection:
            blocker = watchdog_rejection
        if two_regime and len(stop_images) >= 2:
            stop_set = set(stop_images[-2:])
            result["final_stop_range_observations"] = [
                {"sequence": r["sequence"], "distance": r["distance"],
                 "speed": r["speed"], "acquired_at": r["acquired_at"]}
                for r in braking_observations if r["sequence"] in stop_set and r["speed"] == 0]
        if not blocker:
            if brake_reason not in ("threshold", "threshold_watchdog"):
                blocker = "thrust_limit_before_brake_threshold"
            elif not result["final_stop_observed"]:
                blocker = "stop_not_freshly_observed"
            elif not any(r["speed"] > 0 for r in motion):
                blocker = "motion_not_freshly_observed"
            elif not initial or not motion or min(r["distance"] for r in motion) >= initial["distance"] - 5:
                blocker = "distance_closure_not_observed"
            elif two_regime and (not deceleration_observed or len(reduced_speed_sequences) < 3):
                blocker = "reduced_speed_regime_not_freshly_observed"
            elif two_regime:
                stops = result["final_stop_range_observations"]
                if (len(stops) != 2
                        or any(not 700 <= r["distance"] <= 800 for r in stops)):
                    blocker = "final_range_outside_reduced_speed_acceptance_band"
                elif abs(stops[1]["distance"] - stops[0]["distance"]) > 5:
                    blocker = "stopped_distance_changed"
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
                      watchdog_rejection=watchdog_rejection,
                      cancel_requested=cancel_seen.is_set())
        if stop_failure:
            result.update(state="blocked", reason=stop_failure)
            error = error or stop_failure
    if dispatcher and acceleration_records and brake_records:
        try:
            deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
            requested = (*acceleration_records, *transition_records,
                         *wheel_records, *brake_records)
            linked = [deliveries.get(r.sequence) for r in requested]
            expected_count = 7 if two_regime else 4
            if (len(linked) != expected_count
                    or any(d is None or d.outcome != "injected" for d in linked)):
                result.update(state="blocked", reason="control_input_not_delivered")
            elif any(left.at > right.at for left, right in zip(linked, linked[1:])):
                result.update(state="blocked", reason="control_delivery_order_invalid")
            else:
                result["actual_brake_delivered_at"] = linked[-2].at
                if two_regime:
                    result["actual_transition_idle_delivered_at"] = linked[3].at
                    result["actual_wheel_delivered_at"] = linked[4].at
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
    brake_start = result["brake_start_observation"]
    if (brake_start and brake_start["fresh"] and brake_start["distance"] is not None
            and result["final_stop_distance"] is not None):
        result["observed_braking_distance"] = brake_start["distance"] - result["final_stop_distance"]
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
    parser.add_argument("--max-thrust-seconds", type=float, default=None,
                        help="Optional bounded diagnostic trial only; normal transit has no elapsed-time cap")
    parser.add_argument("--brake-at", type=float, default=900.,
                        help="Distance brake threshold, 600 to 1400 metres")
    parser.add_argument("--reduced-transition-at", type=float,
                        help="Experimental full-speed to one-notch transition at 1300 m; use --brake-at 800")
    parser.add_argument("--wheel-x", type=int)
    parser.add_argument("--wheel-y", type=int)
    parser.add_argument("--flight-point-assessed", action="store_true")
    parser.add_argument("--launch-clearance", type=float, default=250.,
                        help="Supervised calibration only: minimum launch distance above brake threshold, 75 to 250 metres")
    parser.add_argument("--sample-seconds", type=float, default=5.,
                        help="Distance observation cadence, 5 to 10 seconds")
    parser.add_argument("--stop-seconds", type=float, default=35.)
    parser.add_argument("--cancel-file", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.sample_seconds) or not 5 <= args.sample_seconds <= 10:
        parser.error("--sample-seconds must be between 5 and 10")
    if args.reduced_transition_at is not None and (args.wheel_x is None or args.wheel_y is None
            or not args.flight_point_assessed):
        parser.error("two-regime mode requires --wheel-x, --wheel-y and --flight-point-assessed")
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
            layout=args.profile), args.sample_seconds),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend,
            geometry.hwnd, geometry.pid, expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin, journal=journal,
            minimum_key_hold_seconds=.05, minimum_mouse_settle_seconds=.05,
            keyboard_mode="virtual_key"),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
            "source_size": "native-2560", "minimum_key_hold_ms": 50,
            "keyboard_mode": "virtual_key",
            "wheel_pointer_settle_ms": 50 if args.reduced_transition_at is not None else None},
        profile=args.profile, expected_window_id=f"{geometry.pid}:{geometry.hwnd}",
        frame_size=geometry.client_size, clear_corridor_assessed=True,
        aligned_heading_assessed=True, safe_stopped_scene_assessed=True,
        brake_at=args.brake_at, launch_clearance=args.launch_clearance,
        max_thrust_seconds=args.max_thrust_seconds,
        baseline_seconds=args.sample_seconds * 2 + 2,
        sample_interval=args.sample_seconds, decision_interval=args.sample_seconds,
        stop_seconds=args.stop_seconds, cancel_file=args.cancel_file,
        reduced_transition_at=args.reduced_transition_at,
        wheel_point=(args.wheel_x, args.wheel_y) if args.reduced_transition_at is not None else None,
        flight_point_assessed=args.flight_point_assessed)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] == "proved" and result["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
