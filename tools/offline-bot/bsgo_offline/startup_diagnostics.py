"""Read-only startup calibration and readiness assessment for a live game window.

This command never starts native input. Stable OCR is evidence of repeatability,
not proof that a field is accurate or that the ship can be controlled.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .diagnose import run_preflight
from .mining_recognition import MiningHudRecognizer
from .native_capture import NativeCapture, NativeSource, inspect_window
from .readiness import CYCLE_FIELDS
from .trace import iter_trace_events


def _check(name, state, reason, **evidence):
    return {"name": name, "state": state, "reason": reason, "evidence": evidence}


def assess_startup(report, manifest, observations, frame_events, *, expected_frames,
                   expected_window_id=None):
    """Summarize retained observations without converting a read into authority."""
    if type(expected_frames) is not int or expected_frames < 1:
        raise ValueError("expected_frames")
    snapshots = [event["snapshot"] for event in observations]
    contexts = [event["frame"]["context"] for event in observations]
    window_ids = sorted({context["window_id"] for context in contexts})
    frame_sizes = sorted({tuple(context["frame_size"]) for context in contexts})
    healthy = sum(snapshot["healthy"] is True for snapshot in snapshots)
    frame_states = Counter(event["frame"]["state"] for event in observations)
    retained = {event["observation_sequence"]: event for event in frame_events}
    expected_pairs = {(event["snapshot"]["sequence"], event["frame"].get("path"))
                      for event in observations if event["frame"]["state"] == "queued"}
    retained_pairs = {(event["observation_sequence"], event.get("path"))
                      for event in frame_events}
    matched_frames = len(expected_pairs & retained_pairs)
    fields = {}
    for name in (*CYCLE_FIELDS, "ocr_evidence"):
        valid = 0
        rejected = Counter()
        values = []
        for snapshot in snapshots:
            field = snapshot["fields"].get(name)
            if field and field["valid"] is True:
                valid += 1
                if name in ("speed", "distance", "asteroid_selected", "owned"):
                    values.append(field["value"])
            else:
                rejected[(field or {}).get("reason") or "missing_field"] += 1
        fields[name] = {"valid_frames": valid, "rejected_frames": len(snapshots) - valid,
                        "rejection_reasons": dict(rejected)}
        if values:
            fields[name]["observed_values"] = values
            fields[name]["distinct_values"] = len({json.dumps(value, sort_keys=True) for value in values})
            fields[name]["same_value_on_every_frame"] = valid == len(snapshots) and fields[name]["distinct_values"] == 1

    reads = []
    count = len(snapshots)
    reads.append(_check("capture_count", "ready" if count >= expected_frames else "blocked",
        "requested_frames_seen" if count >= expected_frames else "too_few_observations",
        requested=expected_frames, observed=count))
    identity_ok = bool(contexts and len({json.dumps(c, sort_keys=True) for c in contexts}) == 1
                       and (expected_window_id is None or window_ids == [expected_window_id]))
    reads.append(_check("capture_identity", "ready" if identity_ok else "unknown" if not contexts else "blocked",
        "stable_expected_context" if identity_ok else "no_observation" if not contexts else "context_changed_or_unexpected",
        window_ids=window_ids, frame_sizes=[list(size) for size in frame_sizes],
        expected_window_id=expected_window_id))
    pixels_ok = bool(count and len(retained) == count and expected_pairs == retained_pairs
                     and matched_frames == count and not frame_states.get("dropped")
                     and not frame_states.get("skipped") and not frame_states.get("unavailable"))
    reads.append(_check("retained_pixels", "ready" if pixels_ok else "blocked",
        "all_observations_have_hashed_frames" if pixels_ok else "frame_evidence_incomplete",
        states=dict(frame_states), hashed_frames=matched_frames))
    reads.append(_check("recognition_pipeline", "ready" if count and healthy == count else "blocked",
        "all_observations_healthy" if count and healthy == count else "unhealthy_or_missing_observation",
        healthy=healthy, observed=count))
    for name in ("speed", "distance", "asteroid_selected"):
        valid = fields[name]["valid_frames"]
        state = "ready" if count >= 2 and valid == count else "unknown" if count == 0 else "blocked"
        reads.append(_check("read:" + name, state,
            "repeated_read_available" if state == "ready" else "no_observation" if count == 0
            else "read_missing_or_unstable", valid=valid, observed=count,
            rejections=fields[name]["rejection_reasons"]))
    evidence = [snapshot["fields"].get("ocr_evidence", {}).get("value", {}) for snapshot in snapshots]
    profiles = sorted({item.get("calibration_profile") for item in evidence if item.get("calibration_profile")})
    transforms = sorted({item.get("resampling") for item in evidence if item.get("resampling")})
    profile = manifest["configuration"].get("profile")
    profile_ok = bool(count and fields["ocr_evidence"]["valid_frames"] == count and profiles == [profile])
    reads.append(_check("calibration_profile", "ready" if profile_ok else "blocked",
        "explicit_profile_applied" if profile_ok else "profile_unverified",
        configured=profile, observed=profiles, transforms=transforms))

    preflight = report.get("preflight") or {}
    controls = list(preflight.get("checks") or [])
    worker = report.get("worker") or {}
    worker_ok = (not report.get("error") and worker.get("error") is None
                 and worker.get("diagnostic_dropped") == 0 and report.get("shutdown", {}).get("recognition") is True)
    controls.append(_check("worker", "ready" if worker_ok else "blocked",
        "worker_finished_cleanly" if worker_ok else "worker_error_or_incomplete_shutdown",
        error=worker.get("error"), end_reason=worker.get("end_reason")))
    queues_ok = (report.get("input_pending") == 0 and report.get("input_requests") == []
                 and report.get("complete_diagnostics") is True)
    controls.append(_check("queues_and_diagnostics", "ready" if queues_ok else "blocked",
        "no_input_and_complete_trace" if queues_ok else "queue_or_trace_not_clear",
        pending=report.get("input_pending"), input_requests=len(report.get("input_requests") or []),
        complete_diagnostics=report.get("complete_diagnostics")))
    source_hashes = manifest.get("source_sha256") or {}
    calibration_hashes = {name: digest for name, digest in source_hashes.items()
                          if name.startswith("hud-reader/")}
    read_pass = all(check["state"] == "ready" for check in reads)
    control_pass = all(check["state"] == "ready" for check in controls)
    return {"schema_version": 1, "experiment": "startup_calibration", "read_only": True,
            "manifest": "manifest.json", "diagnostic_report": "report.json",
            "source_identity": {"code_revision": manifest.get("code_revision"),
                                "source_hash_count": len(source_hashes),
                                "calibration_sha256": calibration_hashes},
            "capture": {"observed_frames": count, "requested_frames": expected_frames,
                        "window_ids": window_ids, "frame_sizes": [list(size) for size in frame_sizes],
                        "healthy_frames": healthy, "retained_hashed_frames": matched_frames},
            "fields": fields, "read_checks": reads, "control_checks": controls,
            "read_checks_pass": read_pass, "control_checks_pass": control_pass,
            "ready_for_control_experiment": read_pass and control_pass,
            "limits": ["Repeated OCR readings do not establish reading accuracy.",
                       "A configured binding does not prove native delivery or a game effect.",
                       "This command sends no native game input and performs no mining action."]}


def run_startup(output, *, source_factory, recognizer_factory, configuration,
                duration=6, snapshot_limit=6, expected_window_id=None):
    output = Path(output)
    report = run_preflight(output, source_factory=source_factory,
        recognizer_factory=recognizer_factory,
        configuration={**configuration, "expected_window_id": expected_window_id},
        mode="startup_calibration", duration=duration, snapshot_limit=snapshot_limit)
    assessment = reassess_saved_run(output)
    (output / "startup-calibration.json").write_text(
        json.dumps(assessment, indent=2, allow_nan=False), encoding="utf-8")
    return assessment


def reassess_saved_run(directory):
    """Verify a completed package again from disk without opening the game."""
    output = Path(directory)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("mode") != "startup_calibration":
        raise ValueError("not_a_startup_calibration_run")
    configuration = manifest.get("configuration") or {}
    expected_frames = configuration.get("snapshot_limit")
    pid, hwnd = configuration.get("expected_pid"), configuration.get("hwnd")
    expected_window_id = configuration.get("expected_window_id") or (
        f"{pid}:{hwnd}" if type(pid) is int and type(hwnd) is int else None)
    observations, frames = [], []
    for event in iter_trace_events(output / "trace", report["run_id"]):
        payload = event["payload"]
        if payload.get("event") == "observation":
            observations.append(payload)
        elif payload.get("event") == "frame_retained":
            target = output / payload["path"]
            if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == payload["sha256"]:
                frames.append(payload)
    assessment = assess_startup(report, manifest, observations, frames,
        expected_frames=expected_frames, expected_window_id=expected_window_id)
    return assessment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reassess", type=Path, help="Verify a saved startup package without game access")
    parser.add_argument("--hwnd", type=int)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--profile", choices=("original", "restart-20261001", "largehud-20261003"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=float, default=6)
    parser.add_argument("--frames", type=int, default=6)
    parser.add_argument("--interval", type=float, default=.5)
    parser.add_argument("--source-size", choices=("native-2560", "3840x2160"), default="native-2560")
    args = parser.parse_args(argv)
    if args.reassess:
        assessment = reassess_saved_run(args.reassess)
        print(json.dumps({"source": str(args.reassess.resolve()),
            "read_checks_pass": assessment["read_checks_pass"],
            "control_checks_pass": assessment["control_checks_pass"],
            "capture": assessment["capture"],
            "blockers": [check for check in (*assessment["read_checks"], *assessment["control_checks"])
                         if check["state"] != "ready"]}, indent=2))
        return 0 if assessment["read_checks_pass"] else 1
    if args.hwnd is None or args.pid is None or args.profile is None or args.output is None:
        parser.error("--hwnd, --pid, --profile and --output are required for live capture")
    def source_factory():
        geometry = inspect_window(args.hwnd, expected_pid=args.pid)
        if geometry.title != "Battlestar Galactica Online":
            raise ValueError("unexpected_game_window_title")
        return NativeSource(NativeCapture(geometry.hwnd, geometry.pid, layout=args.profile), args.interval)
    assessment = run_startup(args.output, source_factory=source_factory,
        recognizer_factory=lambda: MiningHudRecognizer(args.profile,
            source_frame_size=(3840, 2160) if args.source_size == "3840x2160" else None),
        configuration={"hwnd": args.hwnd, "expected_pid": args.pid, "profile": args.profile,
                       "capture_interval_real_seconds": args.interval, "source_size": args.source_size,
                       "native_input_started": False}, duration=args.seconds,
        snapshot_limit=args.frames, expected_window_id=f"{args.pid}:{args.hwnd}")
    print(json.dumps({"output": str(args.output.resolve() / "startup-calibration.json"),
        "read_checks_pass": assessment["read_checks_pass"],
        "control_checks_pass": assessment["control_checks_pass"],
        "capture": assessment["capture"],
        "blockers": [check for check in (*assessment["read_checks"], *assessment["control_checks"])
                     if check["state"] != "ready"]}, indent=2))
    return 0 if assessment["ready_for_control_experiment"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
