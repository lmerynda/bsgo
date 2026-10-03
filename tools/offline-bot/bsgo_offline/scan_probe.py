"""Bounded stationary scan experiment; input insertion never proves a scan effect.

The probe captures the selected target before and after one Z press. The current
mining recognizer has no validated scan-result classifier, so a complete run is
an image and delivery package for independent inspection, not mining readiness.
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


FIELDS = ("speed", "asteroid_selected", "distance", "target_max_hp", "target_identity")


def _reading(snapshot, *, now, expected_window_id, frame_size, context=None,
             target=None, max_distance_change=5):
    if snapshot.window_id != expected_window_id or snapshot.frame_size != tuple(frame_size):
        return None, "unexpected_capture_window_or_geometry"
    if context is not None and snapshot.context != context:
        return None, "observation_context_changed"
    if not snapshot.healthy:
        return None, "unhealthy_image_observation"
    if not 0 <= now - snapshot.acquired_at < 2:
        return None, "stale_image_observation"
    values = {}
    for name in FIELDS:
        field = snapshot.fields.get(name)
        if not field or not field.valid or not 0 <= now - field.observed_at < 2:
            return None, "invalid_or_stale_" + name
        values[name] = field.value
    if (values["speed"] != 0 or values["asteroid_selected"] is not True
            or type(values["distance"]) not in (int, float)
            or not math.isfinite(values["distance"]) or values["distance"] <= 0
            or type(values["target_max_hp"]) not in (int, float)
            or not math.isfinite(values["target_max_hp"]) or values["target_max_hp"] <= 0
            or not isinstance(values["target_identity"], str)
            or not values["target_identity"]):
        return None, "stopped_selected_asteroid_required"
    if target is not None:
        if (values["target_identity"] != target["target_identity"]
                or values["target_max_hp"] != target["target_max_hp"]):
            return None, "selected_target_changed"
        if abs(values["distance"] - target["distance"]) > max_distance_change:
            return None, "stationary_distance_changed"
    return {"sequence": snapshot.sequence,
            "acquisition_sequence": snapshot.acquisition_sequence,
            "acquired_at": snapshot.acquired_at,
            "published_at": snapshot.published_at,
            "context": plain(snapshot.context), **values}, None


def run_scan_probe(output, *, source_factory, recognizer_factory, dispatcher_factory,
                   configuration, profile, expected_window_id, frame_size,
                   safe_scene_assessed, scope="native", baseline_seconds=4.,
                   after_seconds=8., max_distance_change=5):
    """Run one image-gated Z press with injected seams for pixel-only tests."""
    if safe_scene_assessed is not True:
        raise ValueError("fresh_safe_scene_assessment_required")
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    for name, value, limit in (("baseline_seconds", baseline_seconds, 8.),
                               ("after_seconds", after_seconds, 10.),
                               ("max_distance_change", max_distance_change, 20.)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= limit:
            raise ValueError(name)
    diagnostic = DiagnosticRun(output, mode=scope + "_stationary_scan_probe",
        configuration={**configuration, "safe_scene_assessed": True,
            "baseline_seconds": baseline_seconds, "after_seconds": after_seconds,
            "max_distance_change": max_distance_change, "mission_started": False},
        frame_capacity=32, max_frames=64)
    source = worker = backend = dispatcher = None
    store = SnapshotStore()
    started = time.monotonic()
    baseline, after = [], []
    context = target = None
    request_at = None
    requests = ()
    observed_sequence = 0
    shutdown, error = {}, None
    result = {"state": "blocked", "reason": "not_started", "mining_ready": False,
              "scan_effect_verified": False, "baseline": baseline, "after": after,
              "scan_requests": [], "scan_deliveries": [], "post_delivery_sequences": [],
              "unverified": ["scan_result", "resource_classification", "mining",
                             "inventory", "combat_detection", "hull_health"]}

    def input_guard(record):
        if record.key != "Z" or record.kind != "key_down" or request_at is None:
            return False
        now = time.monotonic()
        snapshot = store.latest()
        if snapshot is None or not 0 <= now - request_at < 1:
            return False
        reading, reason = _reading(snapshot, now=now,
            expected_window_id=expected_window_id, frame_size=frame_size,
            context=context, target=target, max_distance_change=max_distance_change)
        return reason is None and reading is not None

    try:
        source = source_factory()
        recognizer = recognizer_factory()
        backend = RecordingInputBackend(session_id="scan-" + diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1],
            journal=diagnostic.journal)
        backend.set_delivery_contract(input_guard)
        worker = RecognitionWorker(source, recognizer, store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        worker.start()
        baseline_deadline = started + baseline_seconds
        overall_deadline = started + baseline_seconds + after_seconds + 2
        while time.monotonic() < overall_deadline:
            snapshot = store.wait_for_sequence(observed_sequence, .025)
            now = time.monotonic()
            if snapshot:
                if snapshot.sequence != observed_sequence + 1:
                    raise RuntimeError("uninspected_observation_sequence")
                observed_sequence = snapshot.sequence
                reading, reason = _reading(snapshot, now=now,
                    expected_window_id=expected_window_id, frame_size=frame_size,
                    context=context, target=target,
                    max_distance_change=max_distance_change)
                if reason:
                    raise RuntimeError(reason)
                if context is None:
                    context = snapshot.context
                    target = reading
                diagnostic.submit({"event": "scan_probe_reading", "phase":
                    "baseline" if request_at is None else "after_request", "reading": reading})
                if request_at is None:
                    baseline.append(reading)
                    if len(baseline) >= 2:
                        dispatcher = dispatcher_factory(backend, diagnostic.journal)
                        dispatcher.start()
                        request_at = time.monotonic()
                        requests = backend.press_key("Z", reason="stationary_scan_probe")
                        result["scan_requests"] = plain(requests)
                        diagnostic.submit({"event": "scan_probe_requested", "at": request_at,
                                           "baseline_sequences": [row["sequence"] for row in baseline]})
                else:
                    after.append(reading)
            if request_at is None and now >= baseline_deadline:
                raise RuntimeError("baseline_timeout")
            if request_at is not None and now >= request_at + after_seconds:
                break
            if worker.end_reason and not snapshot:
                raise RuntimeError("recognition_ended:" + worker.end_reason)
        else:
            raise RuntimeError("scan_probe_timeout")
        if dispatcher is None or len(after) < 2:
            raise RuntimeError("post_scan_images_missing")
        deliveries = {row.request_sequence: row for row in dispatcher.deliveries()}
        linked = [deliveries.get(row.sequence) for row in requests]
        result["scan_deliveries"] = plain([row for row in linked if row is not None])
        if len(linked) != 2 or any(row is None or row.outcome != "injected" for row in linked):
            raise RuntimeError("scan_input_not_delivered")
        post_delivery = [row for row in after if row["acquired_at"] > linked[1].at]
        result["post_delivery_sequences"] = [row["sequence"] for row in post_delivery]
        if (linked[0].kind != "key_down" or linked[1].kind != "key_up"
                or len(post_delivery) < 2):
            raise RuntimeError("fresh_post_scan_image_missing")
        result.update(state="recorded", reason="scan_effect_unverified")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    finally:
        if dispatcher:
            shutdown["dispatcher"] = dispatcher.stop(2)
            result["scan_deliveries"] = plain(dispatcher.deliveries())
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
            backend.cancel(reason="scan_probe_finished")
            backend.flush_journal()
        shutdown["input_idle"] = not backend or (not backend.pending_requests and not backend.held_keys)
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    if (not report["complete_diagnostics"] or not all(shutdown.values())
            or result.get("dispatcher_error")):
        result.update(state="blocked", reason="incomplete_diagnostic_evidence")
    _write_json(diagnostic.directory / "scan-probe.json", {"run_id": diagnostic.journal.run_id,
        "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result})
    return {"run_id": diagnostic.journal.run_id,
            "complete_diagnostics": report["complete_diagnostics"], "shutdown": shutdown, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", type=int, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--safe-scene-assessed", action="store_true", required=True)
    parser.add_argument("--source-size", choices=["native-2560", "3840x2160"], default="native-2560")
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd, expected_pid=args.pid)
    if geometry.title != "Battlestar Galactica Online":
        raise ValueError("unexpected_game_window_title")
    configured_size = (3840, 2160) if args.source_size == "3840x2160" else (2560, 1440)
    if geometry.client_size != configured_size:
        raise ValueError("unexpected_native_client_size")
    result = run_scan_probe(args.output,
        source_factory=lambda: NativeSource(NativeCapture(geometry.hwnd, geometry.pid,
            layout=args.profile), .4),
        recognizer_factory=lambda: MiningHudRecognizer(args.profile,
            source_frame_size=configured_size if args.source_size == "3840x2160" else None),
        dispatcher_factory=lambda backend, journal: NativeInputDispatcher(backend,
            geometry.hwnd, geometry.pid, expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin, journal=journal,
            minimum_key_hold_seconds=.05, keyboard_mode="virtual_key"),
        configuration={"hwnd": args.hwnd, "pid": args.pid, "profile": args.profile,
            "source_size": args.source_size, "minimum_key_hold_ms": 50,
            "keyboard_mode": "virtual_key"},
        profile=args.profile, expected_window_id=f"{geometry.pid}:{geometry.hwnd}",
        frame_size=geometry.client_size, safe_scene_assessed=True)
    print(json.dumps(plain(result), indent=2))
    return 0 if result["state"] == "recorded" and result["complete_diagnostics"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
