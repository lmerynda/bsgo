"""One supervised mining-gun command with image-observed asteroid HP feedback.

G may assign persistent autofire. The supervisor must stay with the game; this
experiment has no evidenced ceasefire command.
"""
import argparse
import json
import math
from pathlib import Path
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .recognition import RecognitionWorker
from .state import SnapshotStore


FIELDS = ("speed", "asteroid_selected", "distance", "target_identity",
          "target_portrait", "target_max_hp", "target_current_hp")


def _read(snapshot, now, window_id, frame_size, context, initial,
          min_distance, max_distance, max_distance_change,
          allow_unknown_current_hp=False):
    if (snapshot.window_id != window_id or snapshot.frame_size != tuple(frame_size)
            or context is not None and snapshot.context != context):
        raise RuntimeError("capture_context_changed")
    if not snapshot.healthy or not 0 <= now - snapshot.acquired_at < 2:
        raise RuntimeError("unhealthy_or_stale_image")
    values = {}
    for name in FIELDS:
        field = snapshot.fields.get(name)
        if (name == "target_current_hp" and allow_unknown_current_hp
                and field and not field.valid
                and field.reason == "unvalidated_current_hp_digit_count"
                and field.observed_at == snapshot.acquired_at
                and 0 <= now - field.observed_at < 2):
            values[name] = None
            values["target_current_hp_reason"] = field.reason
            continue
        if (not field or not field.valid or field.observed_at != snapshot.acquired_at
                or not 0 <= now - field.observed_at < 2):
            raise RuntimeError("invalid_or_stale_" + name)
        values[name] = field.value
    if (type(values["speed"]) not in (int, float) or values["speed"] != 0
            or values["asteroid_selected"] is not True
            or type(values["distance"]) not in (int, float)
            or not math.isfinite(values["distance"])
            or not min_distance <= values["distance"] <= max_distance
            or not isinstance(values["target_identity"], str)
            or not values["target_identity"]
            or type(values["target_max_hp"]) not in (int, float)
            or (values["target_current_hp"] is None
                and "target_current_hp_reason" not in values)
            or (values["target_current_hp"] is not None
                and (type(values["target_current_hp"]) not in (int, float)
                     or not 1000 <= values["target_current_hp"] <= values["target_max_hp"]))
            or not math.isfinite(values["target_max_hp"])
            or not isinstance(values["target_portrait"], (tuple, list))
            or len(values["target_portrait"]) != 144):
        raise RuntimeError("stopped_selected_in_range_four_digit_hp_required")
    if initial is not None:
        if (values["target_identity"] != initial["target_identity"]
                or values["target_max_hp"] != initial["target_max_hp"]):
            raise RuntimeError("selected_target_changed")
        if abs(values["distance"] - initial["distance"]) > max_distance_change:
            raise RuntimeError("stationary_distance_changed")
        if sum(abs(a-b) for a, b in zip(values["target_portrait"],
                                         initial["target_portrait"])) / 144 > .75:
            raise RuntimeError("selected_target_portrait_changed")
    return {"sequence": snapshot.sequence,
            "acquisition_sequence": snapshot.acquisition_sequence,
            "acquired_at": snapshot.acquired_at,
            "published_at": snapshot.published_at,
            "context": plain(snapshot.context), **values}


def _absent_target(snapshot, now, window_id, frame_size, context):
    """Two such images mean selection disappeared, not why it disappeared."""
    if (snapshot.window_id != window_id or snapshot.frame_size != tuple(frame_size)
            or snapshot.context != context or not snapshot.healthy
            or not 0 <= now - snapshot.acquired_at < 2):
        raise RuntimeError("invalid_absent_target_capture_context")
    for name, wanted in (("speed", 0), ("asteroid_selected", False)):
        field = snapshot.fields.get(name)
        if (not field or not field.valid or field.observed_at != snapshot.acquired_at
                or not 0 <= now - field.observed_at < 2 or field.value != wanted):
            raise RuntimeError("invalid_absent_target_" + name)
    return {"sequence": snapshot.sequence,
            "acquisition_sequence": snapshot.acquisition_sequence,
            "acquired_at": snapshot.acquired_at, "context": plain(snapshot.context),
            "speed": 0, "asteroid_selected": False}


def run_firing_probe(output, *, source_factory, recognizer_factory,
                     dispatcher_factory, configuration, profile,
                     expected_window_id, frame_size, safe_stopped_scene_assessed,
                     wanted_resource_assessed, no_preexisting_firing_assessed,
                     scope="native", baseline_seconds=6., stable_seconds=4.,
                     after_seconds=8., min_distance=480., max_distance=760.,
                     max_distance_change=5., cancel_file=None,
                     observe_until_target_gone=False):
    """Send one guarded G press and require two later, distinct falling HP reads."""
    if (safe_stopped_scene_assessed is not True
            or no_preexisting_firing_assessed is not True
            or wanted_resource_assessed not in ("Water", "Tylium")):
        raise ValueError("fresh_supervisor_assessments_required")
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    if type(observe_until_target_gone) is not bool:
        raise ValueError("observe_until_target_gone")
    if observe_until_target_gone and cancel_file is None:
        raise ValueError("continuous_observation_requires_cancel_file")
    if cancel_file is not None and not str(cancel_file).strip():
        raise ValueError("cancel_file")
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    for name, value, low, high in (("baseline_seconds", baseline_seconds, .1, 8.),
        ("stable_seconds", stable_seconds, 0, 6.),
        ("after_seconds", after_seconds, .1, 20.),
        ("min_distance", min_distance, 1, 760.),
        ("max_distance", max_distance, 480, 760.),
        ("max_distance_change", max_distance_change, 0, 20.)):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(name)
    if stable_seconds >= baseline_seconds or min_distance > max_distance:
        raise ValueError("invalid_baseline_or_range")
    diagnostic = DiagnosticRun(output, mode=scope + "_supervised_firing_probe",
        configuration={**configuration, "supervisor_assumptions": {
            "safe_stopped_scene": True, "wanted_resource": wanted_resource_assessed,
            "no_preexisting_firing": True}, "baseline_seconds": baseline_seconds,
            "stable_seconds": stable_seconds,
            "after_seconds": None if observe_until_target_gone else after_seconds,
            "min_distance": min_distance, "max_distance": max_distance,
            "max_distance_change": max_distance_change,
            "cancel_file": str(cancel_path) if cancel_path else None,
            "observe_until_target_gone": observe_until_target_gone,
            "after_observations_scope": "recent_256" if observe_until_target_gone else "all",
            "mission_started": False},
        frame_capacity=32, max_frames=None if observe_until_target_gone else 96)
    source = worker = backend = dispatcher = None
    store = SnapshotStore()
    started = time.monotonic()
    context = initial = None
    baseline, after, falling, absence, post_disappearance = [], [], [], [], []
    after_total = 0
    last_known_hp = None
    requests = ()
    requested_at = None
    observed_sequence = 0
    previous_acquisition = -1
    shutdown = {}
    error = None
    result = {"state": "blocked", "reason": "not_started", "mining_ready": False,
        "cancel_requested": False,
        "firing_effect_verified": False, "firing_state_after_probe":
        "unverified_supervisor_retains_control",
        "warning": "autofire may continue beyond probe",
        "supervisor_assumptions": {"safe_stopped_scene": True,
            "wanted_resource": wanted_resource_assessed,
            "no_preexisting_firing": True},
        "unverified": ["resource_classifier", "inventory", "resource_reward",
                       "autofire_state", "combat_detection", "hull_health"],
        "baseline": baseline, "after": after, "falling": falling,
        "observe_until_target_gone": observe_until_target_gone,
        "after_observation_count": 0,
        "after_observations_scope": "recent_256" if observe_until_target_gone else "all",
        "unknown_current_hp_count": 0, "target_disappearance_observed": False,
        "target_disappearance_interpretation": "selection_absent_cause_unverified",
        "absent_target_images": absence,
        "post_disappearance_images": post_disappearance,
        "requests": [], "deliveries": []}

    cancel_seen = False

    def canceled():
        nonlocal cancel_seen
        if cancel_path is None:
            return False
        try:
            requested = cancel_path.exists()
        except OSError:
            requested = True
        if requested and not cancel_seen:
            cancel_seen = True
            diagnostic.submit({"event": "firing_probe_cancel_requested",
                               "at": time.monotonic(), "path": str(cancel_path)})
        return requested

    def guard(record):
        if (record.kind != "key_down" or record.key != "G"
                or requested_at is None or time.monotonic() - requested_at >= 1
                or canceled()):
            return False
        snapshot = store.latest()
        if snapshot is None:
            return False
        try:
            row = _read(snapshot, time.monotonic(), expected_window_id,
                frame_size, context, initial, min_distance, max_distance,
                max_distance_change)
            return row["target_current_hp"] == initial["target_current_hp"]
        except Exception:
            return False

    try:
        if canceled():
            raise RuntimeError("cancel_requested")
        source = source_factory()
        backend = RecordingInputBackend(session_id="fire-" + diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1],
            journal=diagnostic.journal)
        backend.set_delivery_contract(guard)
        worker = RecognitionWorker(source, recognizer_factory(), store,
            diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        worker.start()
        deadline = started + baseline_seconds
        while True:
            if canceled():
                raise RuntimeError("cancel_requested")
            now = time.monotonic()
            if now >= deadline and (not observe_until_target_gone or not requests):
                if not requests:
                    raise RuntimeError("stable_baseline_timeout")
                raise RuntimeError("two_post_delivery_hp_declines_not_observed")
            if observe_until_target_gone and requests:
                latest = store.latest()
                if latest is None or now - latest.acquired_at >= 1.5:
                    raise RuntimeError("stale_image_stream")
            snapshot = store.wait_for_sequence(observed_sequence, .025)
            if snapshot is None:
                if worker.end_reason:
                    raise RuntimeError("recognition_ended:" + worker.end_reason)
                continue
            if (snapshot.sequence != observed_sequence + 1
                    or snapshot.acquisition_sequence <= previous_acquisition):
                raise RuntimeError("uninspected_or_repeated_image")
            observed_sequence = snapshot.sequence
            previous_acquisition = snapshot.acquisition_sequence
            if observe_until_target_gone and result["target_disappearance_observed"]:
                if (snapshot.window_id != expected_window_id
                        or snapshot.frame_size != tuple(frame_size)
                        or snapshot.context != context or not snapshot.healthy
                        or not 0 <= time.monotonic() - snapshot.acquired_at < 2):
                    raise RuntimeError("invalid_post_disappearance_capture")
                speed = snapshot.fields.get("speed")
                if (not speed or not speed.valid or speed.observed_at != snapshot.acquired_at
                        or not 0 <= time.monotonic() - speed.observed_at < 2
                        or type(speed.value) not in (int, float) or speed.value != 0):
                    raise RuntimeError("fresh_stopped_speed_required_after_disappearance")
                selected = snapshot.fields.get("asteroid_selected")
                post_disappearance.append({"sequence": snapshot.sequence,
                    "acquisition_sequence": snapshot.acquisition_sequence,
                    "acquired_at": snapshot.acquired_at,
                    "asteroid_selected": selected.value if selected and selected.valid else None})
                if len(post_disappearance) >= 2:
                    result.update(state="recorded" if result["firing_effect_verified"] else "blocked",
                        reason="selected_target_absent_cause_unverified" if result["firing_effect_verified"]
                        else "selected_target_absent_without_firing_proof")
                    break
                continue
            if (observe_until_target_gone and requests
                    and snapshot.fields.get("asteroid_selected")
                    and snapshot.fields["asteroid_selected"].valid
                    and snapshot.fields["asteroid_selected"].value is False):
                absent = _absent_target(snapshot, time.monotonic(),
                    expected_window_id, frame_size, context)
                absence.append(absent)
                diagnostic.submit({"event": "firing_probe_target_absent_image",
                                   "observation_sequence": snapshot.sequence})
                if len(absence) >= 2:
                    result["target_disappearance_observed"] = True
                    diagnostic.submit({"event": "firing_probe_target_disappearance_observed",
                        "observation_sequences": [r["sequence"] for r in absence],
                        "interpretation": "selection_absent_cause_unverified"})
                continue
            absence.clear()
            row = _read(snapshot, time.monotonic(), expected_window_id, frame_size,
                context, initial, min_distance, max_distance, max_distance_change,
                allow_unknown_current_hp=(observe_until_target_gone
                                          and result["firing_effect_verified"]))
            if context is None:
                context, initial = snapshot.context, row
            if not requests:
                if row["target_current_hp"] != initial["target_current_hp"]:
                    raise RuntimeError("preexisting_target_hp_change")
                baseline.append(row)
                if len(baseline) < 2 or row["acquired_at"] - baseline[0]["acquired_at"] < stable_seconds:
                    continue
                if canceled():
                    raise RuntimeError("cancel_requested")
                dispatcher = dispatcher_factory(backend, diagnostic.journal)
                dispatcher.start()
                requested_at = time.monotonic()
                requests = backend.press_key("G", reason="supervised_firing_effect_probe")
                result["requests"] = plain(requests)
                diagnostic.submit({"event": "firing_probe_requested", "at": requested_at,
                    "baseline_sequences": [b["sequence"] for b in baseline]})
                deadline = requested_at + after_seconds
                continue
            after.append(row)
            after_total += 1
            result["after_observation_count"] = after_total
            if observe_until_target_gone and len(after) > 256:
                del after[0]
            deliveries = {d.request_sequence: d for d in dispatcher.deliveries()}
            linked = [deliveries.get(r.sequence) for r in requests]
            if any(d and d.outcome != "injected" for d in linked):
                raise RuntimeError("G_input_not_delivered")
            if not all(linked):
                if row["target_current_hp"] != initial["target_current_hp"]:
                    raise RuntimeError("target_hp_changed_before_G_delivery_complete")
                continue
            if (linked[0].kind != "key_down" or linked[1].kind != "key_up"
                    or linked[0].at > linked[1].at):
                raise RuntimeError("invalid_G_delivery_pair")
            if row["acquired_at"] <= linked[1].at:
                if (row["target_current_hp"] is not None
                        and row["target_current_hp"] < initial["target_current_hp"]):
                    raise RuntimeError("target_hp_changed_before_G_delivery_complete")
                continue
            if row["target_current_hp"] is None:
                result["unknown_current_hp_count"] += 1
                continue
            if last_known_hp is not None and row["target_current_hp"] > last_known_hp:
                raise RuntimeError("post_G_target_hp_reversed")
            last_known_hp = row["target_current_hp"]
            if row["target_current_hp"] > initial["target_current_hp"]:
                raise RuntimeError("target_hp_increased_after_G")
            if (not result["firing_effect_verified"]
                    and row["target_current_hp"] < initial["target_current_hp"]):
                if falling and row["target_current_hp"] > falling[-1]["target_current_hp"]:
                    raise RuntimeError("post_G_target_hp_reversed")
                if not falling or row["target_current_hp"] < falling[-1]["target_current_hp"]:
                    falling.append(row)
                if len(falling) >= 2:
                    result.update(state="proved", reason="two_fresh_post_G_hp_declines",
                                  firing_effect_verified=True)
                    diagnostic.submit({"event": "firing_effect_image_proof",
                        "observation_sequences": [r["sequence"] for r in falling[:2]],
                        "baseline_current_hp": initial["target_current_hp"]})
                    if not observe_until_target_gone:
                        break
        result["deliveries"] = plain(dispatcher.deliveries())
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    except KeyboardInterrupt:
        error = "KeyboardInterrupt: supervisor_cancelled"
        result.update(state="blocked", reason=error)
    finally:
        if dispatcher:
            try:
                shutdown["dispatcher"] = dispatcher.stop(2)
                result["deliveries"] = plain(dispatcher.deliveries())
                result["dispatcher_error"] = dispatcher.error
            except Exception as exc:
                shutdown["dispatcher"] = False
                error = error or f"dispatcher_stop_failure: {type(exc).__name__}: {exc}"
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
                backend.cancel(reason="firing_probe_finished")
                backend.flush_journal()
            except Exception as exc:
                error = error or f"input_cancel_failure: {type(exc).__name__}: {exc}"
        shutdown["input_idle"] = not backend or (not backend.pending_requests and not backend.held_keys)
    if error and result["state"] in ("proved", "recorded"):
        result.update(state="blocked", reason=error, firing_effect_verified=False)
    if canceled():
        result.update(state="blocked", reason="cancel_requested",
                      cancel_requested=True)
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    if (not report["complete_diagnostics"] or not all(shutdown.values())
            or result.get("dispatcher_error")):
        result.update(state="blocked", reason="incomplete_probe_evidence",
                      firing_effect_verified=False)
        report["result"] = plain(result)
        _write_json(diagnostic.directory / "report.json", report)
    result.update(run_id=diagnostic.journal.run_id, shutdown=shutdown,
                  complete_diagnostics=report["complete_diagnostics"])
    _write_json(diagnostic.directory / "firing-probe.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=["largehud-20261003"],
                        default="largehud-20261003")
    parser.add_argument("--safe-stopped-scene-assessed", action="store_true", required=True)
    parser.add_argument("--wanted-resource-assessed", choices=["Water", "Tylium"], required=True)
    parser.add_argument("--no-preexisting-firing-assessed", action="store_true", required=True)
    parser.add_argument("--after-seconds", type=float, default=8.)
    parser.add_argument("--cancel-file", type=Path)
    parser.add_argument("--observe-until-target-gone", action="store_true")
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd, expected_pid=args.pid)
    if (geometry.title != "Battlestar Galactica Online"
            or geometry.client_size != (2560, 1440)):
        raise ValueError("unexpected_game_window_or_native_size")
    result = run_firing_probe(args.output,
        source_factory=lambda: NativeSource(NativeCapture(args.hwnd, args.pid,
            layout=args.profile), .4),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend,
            args.hwnd, args.pid, expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin, journal=journal,
            minimum_key_hold_seconds=.05, keyboard_mode="virtual_key"),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
            "keyboard_mode": "virtual_key", "minimum_key_hold_ms": 50},
        profile=args.profile, expected_window_id=f"{args.pid}:{args.hwnd}",
        frame_size=geometry.client_size,
        safe_stopped_scene_assessed=args.safe_stopped_scene_assessed,
        wanted_resource_assessed=args.wanted_resource_assessed,
        no_preexisting_firing_assessed=args.no_preexisting_firing_assessed,
        after_seconds=args.after_seconds, cancel_file=args.cancel_file,
        observe_until_target_gone=args.observe_until_target_gone)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] in ("proved", "recorded") and result["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
