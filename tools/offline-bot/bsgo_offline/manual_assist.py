"""Manual mission ownership with one asynchronous, temporary input lease."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from .artifacts import plain


ROUTINES = {"scan_nearby_unscanned": ("toggle", "selection_and_scanner"),
            "scan_selected": ("once", "scanner"),
            "approach_current_target": ("once", "navigation")}
TARGET_FIELDS = ("speed", "distance", "asteroid_selected", "target_identity",
                 "target_portrait", "target_max_hp", "hull_current", "hull_max")


def target_reading(snapshot, now):
    if snapshot is None:
        return None, "no_current_image"
    if not snapshot.healthy or not 0 <= now - snapshot.acquired_at < 2:
        return None, "unhealthy_or_stale_current_image"
    values = {}
    for name in TARGET_FIELDS:
        field = snapshot.fields.get(name)
        if (field is None or not field.valid or field.observed_at != snapshot.acquired_at
                or not 0 <= now - field.observed_at < 2):
            return None, "invalid_or_stale_" + name
        values[name] = field.value
    if (type(values["speed"]) not in (int, float) or not math.isfinite(values["speed"])
            or values["speed"] != 0):
        return None, "stationary_ship_required"
    if (values["asteroid_selected"] is not True or not isinstance(values["target_identity"], str)
            or not values["target_identity"] or not isinstance(values["target_portrait"], tuple)
            or len(values["target_portrait"]) != 144
            or any(type(value) is not int or not 0 <= value <= 7 for value in values["target_portrait"])):
        return None, "fresh_selected_asteroid_required"
    for name in ("distance", "target_max_hp", "hull_current", "hull_max"):
        if type(values[name]) not in (int, float) or not math.isfinite(values[name]) or values[name] <= 0:
            return None, "invalid_" + name
    if values["hull_current"] != values["hull_max"]:
        return None, "full_hull_required"
    track = snapshot.fields.get("selection_track_id")
    if track and (not track.valid or track.observed_at != snapshot.acquired_at
                  or type(track.value) is not int or track.value < 1):
        return None, "local_selection_continuity_unverified"
    for name in ("view_revision", "selection_revision"):
        revision = snapshot.fields.get(name)
        if revision and (not revision.valid or revision.observed_at != snapshot.acquired_at
                or type(revision.value) is not int or revision.value < 0):
            return None, "invalid_or_stale_" + name
    return {**values, "context": plain(snapshot.context), "sequence": snapshot.sequence,
        "acquired_at": snapshot.acquired_at,
        **{name: snapshot.fields[name].value if snapshot.fields.get(name)
            and snapshot.fields[name].valid and snapshot.fields[name].observed_at == snapshot.acquired_at
            else None for name in ("view_revision", "selection_revision", "selection_track_id")}}, None


def same_target(first, current):
    return bool(first and current and first["context"] == current["context"]
        and first["target_identity"] == current["target_identity"]
        and first["target_max_hp"] == current["target_max_hp"]
        and first["hull_max"] == current["hull_max"]
        and all(first.get(name) == current.get(name) for name in
                ("view_revision", "selection_revision", "selection_track_id"))
        and sum(abs(a-b) for a, b in zip(first["target_portrait"], current["target_portrait"])) / 144 <= .75)


class ManualAssistController:
    """Never queues a second input job or promotes Manual to Autopilot.

    The injected runner owns guarded subflows. The controller owns cancellation,
    fresh human assessment tokens and the input lease until cleanup is evidenced.
    Nearby automatic discovery remains explicitly unsupported.
    """
    def __init__(self, directory, *, snapshot_provider, runner=None,
                 activation_ready=None, pause_file=None, paused=False,
                 clock=time.monotonic, arming_seconds=15., wheel_point=None,
                 focus_handover=None):
        if type(arming_seconds) not in (int, float) or not 0 < arming_seconds <= 15:
            raise ValueError("arming_seconds")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.snapshot_provider, self.runner = snapshot_provider, runner
        self.activation_ready = activation_ready or (lambda: None)
        self.pause_file = Path(pause_file).resolve() if pause_file is not None else None
        self.paused, self.clock, self.arming_seconds = paused, clock, arming_seconds
        self.focus_handover = focus_handover
        self.wheel_point = tuple(wheel_point) if wheel_point is not None else None
        self._lock = Lock()
        self._job = None
        self._last = {}
        self._tokens = deque(maxlen=32)
        self._closed = False
        self._requested_mode = "manual"
        self._last_ack = None

    def _pause_reason(self):
        if self.paused:
            return "session_paused"
        try:
            return "mining_pause_marker_present" if self.pause_file and self.pause_file.exists() else None
        except OSError:
            return "pause_channel_unavailable"

    def _current(self):
        snapshot = self.snapshot_provider()
        now = self.clock()
        reading, reason = target_reading(snapshot, now)
        observation = None
        token = None
        if snapshot is not None:
            fields = {name: {"value": plain(field.value) if field.valid
                    and not (type(field.value) is float and not math.isfinite(field.value)) else None,
                "valid": field.valid, "reason": field.reason, "observed_at": field.observed_at,
                "fresh": 0 <= now - field.observed_at < 2}
                for name, field in snapshot.fields.items() if name in TARGET_FIELDS or name == "target_current_hp"}
            observation = {"sequence": snapshot.sequence, "acquired_at": snapshot.acquired_at,
                "age_seconds": now - snapshot.acquired_at, "healthy": snapshot.healthy,
                "window_id": snapshot.window_id, "fields": fields,
                **{name: fields.get(name, {}).get("value") for name in ("target_identity", "distance", "speed")}}
        if reading:
            stable = {name: reading[name] for name in ("context", "target_identity", "target_max_hp",
                "target_portrait", "hull_max", "view_revision", "selection_revision", "selection_track_id")}
            key = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
            token_id = hashlib.sha256((key + str(snapshot.sequence) + str(snapshot.acquired_at)).encode()).hexdigest()
            token = {"token": token_id, "context_key": key, "observation_sequence": snapshot.sequence,
                "acquired_at": snapshot.acquired_at, "expires_at": snapshot.acquired_at + self.arming_seconds,
                "window_id": snapshot.window_id, "target_identity": reading["target_identity"],
                "wheel_point": self.wheel_point,
                "view_assurance": "image_view_revision" if stable["view_revision"] is not None
                    else "supervisor_assessment_only"}
            with self._lock:
                if not any(row["token"]["token"] == token_id for row in self._tokens):
                    self._tokens.append({"token": token, "reading": reading})
        return observation, reading, reason, token

    def actions_snapshot(self):
        observation, reading, reason, token = self._current()
        pause = self._pause_reason()
        with self._lock:
            job = dict(self._job) if self._job else None
            last = dict(self._last)
            closed = self._closed
        owner = job["name"] if job else None
        state = job["state"] if job else "idle"
        control = bool(job and job["input_acknowledged"])
        status = None
        if job and job.get("status_path"):
            try:
                path = Path(job["status_path"])
                if path.stat().st_size <= 8 * 1024 * 1024:
                    status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        execution = (status or {}).get("execution") or {}
        routines = {}
        for name, (kind, scope) in ROUTINES.items():
            blocked = pause or ("controller_closed" if closed else None)
            if self.runner is None:
                blocked = blocked or "controller_not_attached"
            if owner is not None and owner != name:
                blocked = blocked or "input_owner_conflict"
            if name == "scan_nearby_unscanned":
                blocked = blocked or "nearby_target_discovery_not_available"
                waiting = blocked
                active = False
            else:
                blocked = blocked or reason
                if name == "scan_selected" and reading and reading["distance"] > 4000:
                    blocked = blocked or "selected_target_outside_scanner_range"
                waiting = None
                active = owner == name
            item = last.get(name, {})
            routines[name] = {"kind": kind, "input_scope": scope,
                "available": self.runner is not None and name != "scan_nearby_unscanned", "ready": blocked is None,
                "start_allowed": not pause and not closed and self.runner is not None
                    and owner is None and blocked is None,
                "reason": blocked, "active": active,
                "state": state if owner == name else "waiting" if active else item.get("state", "idle"),
                "phase": execution.get("phase") if owner == name else item.get("phase"),
                "processing": execution.get("processing") if owner == name else None,
                "waiting_for": execution.get("waiting_for") if owner == name else waiting,
                "expected_next_step": execution.get("expected_next_step") if owner == name else
                    "wait for verified nearby candidates; do not move the ship" if active else None,
                "result": item.get("result"), "error": item.get("error")}
            if owner == name and state == "arming":
                routines[name].update(phase="awaiting_game_focus", processing="arming the explicitly requested action",
                    waiting_for=job.get("waiting_for", "game window focus and a fresh unchanged target"),
                    expected_next_step="start only after revalidation before the 15-second arming deadline")
            if owner == name and state in ("stopping", "faulted") and job.get("waiting_for"):
                routines[name]["waiting_for"] = job["waiting_for"]
            if owner == name and state == "faulted":
                routines[name].update(phase="faulted", processing="action cleanup remains unresolved",
                    waiting_for="fresh image-verified stop and drained input ownership",
                    expected_next_step="human intervention or explicit recovery is required; new actions remain blocked")
        return {"actual_mode": "manual", "paused": bool(pause), "pause_reason": pause,
            "focus_handover_enabled": self.focus_handover is not None,
            "app_control_enabled": control, "decisions_enabled": False,
            "routine_decisions_enabled": state == "active", "attached_controller": "manual_assist",
            "ownership": {"owner": owner, "state": state, "run_id": job["id"] if job else None,
                          "input_scope": ROUTINES[owner][1] if owner else None},
            "routines": routines, "current_observation": observation, "assessment_context": token,
            "focus_handover": job.get("focus_handover") if job else None,
            **{name: execution.get(name) for name in
               ("last_input_intent", "last_input_delivery", "last_confirmed_effect")}}

    def snapshot(self):
        state = self.actions_snapshot()
        return {"actual_mode": "manual", "requested_mode": self._requested_mode, "pending": False,
            "app_control_enabled": state["app_control_enabled"], "decisions_enabled": False,
            "attached_controller": "manual_assist", "autopilot_available": False,
            "reason": "manual_assist_only", "last_ack": self._last_ack}

    def request(self, mode):
        self._requested_mode = mode
        self._last_ack = {"requested_mode": mode, "accepted": mode == "manual",
            "reason": None if mode == "manual" else "autopilot_not_attached",
            "at_utc": datetime.now(timezone.utc).isoformat()}
        return {"accepted": mode == "manual", "reason": self._last_ack["reason"], "state": self.snapshot()}

    def start_action(self, name, request=None):
        if name not in ROUTINES:
            return {"accepted": False, "reason": "unknown_routine", "state": self.actions_snapshot()}
        request = request or {}
        if not isinstance(request, dict):
            return {"accepted": False, "reason": "invalid_action_request", "state": self.actions_snapshot()}
        observation, reading, reason, token = self._current()
        rejection = self._pause_reason()
        with self._lock:
            if self._closed:
                rejection = rejection or "controller_closed"
            if self._job is not None:
                rejection = rejection or "input_owner_conflict"
            if self.runner is None:
                rejection = rejection or "controller_not_attached"
            issued = next((row for row in self._tokens if row["token"]["token"] == request.get("assessment_token")), None)
        if name != "scan_nearby_unscanned":
            rejection = rejection or reason
            if (issued is None or not token or issued["token"]["context_key"] != token["context_key"]
                    or not 0 <= self.clock() - issued["token"]["acquired_at"] < self.arming_seconds):
                rejection = rejection or "fresh_target_assessment_token_required"
            assessment = request.get("supervisor_assessment", {})
            required = ("clear_corridor", "aligned_heading", "safe_stopped_scene", "flight_point") if name == "approach_current_target" else ("safe_stopped_scene",)
            if not isinstance(assessment, dict) or any(assessment.get(key) is not True for key in required):
                rejection = rejection or "fresh_supervisor_assessments_required"
            if name == "scan_selected" and reading and reading["distance"] > 4000:
                rejection = rejection or "selected_target_outside_scanner_range"
        else:
            rejection = rejection or "nearby_target_discovery_not_available"
        if rejection:
            return {"accepted": False, "reason": rejection, "state": self.actions_snapshot()}
        with self._lock:
            # Recheck inside the reservation lock: concurrent API requests have
            # one winner even if each checked readiness before either reserved.
            if self._job is not None or self._closed:
                busy = True
            else:
                busy = False
                job_id = "manual-assist-" + uuid4().hex
                cancel = self.directory / (job_id + ".cancel")
                job = {"name": name, "id": job_id, "state": "arming", "cancel": cancel,
                    "stop": Event(), "input_acknowledged": False, "status_path": None,
                    "pin": reading, "assessment": dict(assessment),
                    "deadline": issued["token"]["expires_at"]}
                self._job = job
                job["thread"] = Thread(target=self._run, args=(job,), daemon=True,
                                       name="manual-assist-" + name)
                job["thread"].start()
        if busy:
            return {"accepted": False, "reason": "input_owner_conflict", "state": self.actions_snapshot()}
        return {"accepted": True, "reason": "arming", "state": self.actions_snapshot()}

    def _run(self, job):
        result = None
        error = None
        ran = False
        monitor_stop = Event()
        def monitor():
            while not monitor_stop.wait(.05):
                if self._pause_reason():
                    job["stop"].set()
                    try:
                        job["cancel"].write_text("paused", encoding="utf-8")
                    except OSError:
                        pass
        monitor_thread = Thread(target=monitor, daemon=True, name="manual-assist-pause-watch")
        try:
            handed_over = False
            while True:
                if job["stop"].is_set() or self._pause_reason():
                    raise RuntimeError("action_canceled_before_input")
                _, current, reason, _ = self._current()
                if reason or not same_target(job["pin"], current) or abs(current["distance"] - job["pin"]["distance"]) > 5:
                    raise RuntimeError(reason or "requested_target_or_context_changed")
                if self.clock() >= job["deadline"]:
                    raise RuntimeError("arming_expired_before_input")
                if self.focus_handover is not None and not handed_over:
                    job["focus_handover"] = self.focus_handover("start")
                    handed_over = True
                    # Focus change can coincide with a target/camera change;
                    # validate the next current image before entering a probe.
                    continue
                waiting = self.activation_ready()
                if waiting is None:
                    break
                with self._lock:
                    job["waiting_for"] = waiting
                job["stop"].wait(.05)
            with self._lock:
                job["state"] = "active"
            monitor_thread.start()
            def progress(path):
                with self._lock:
                    job["status_path"] = str(Path(path) / "status.json")
            def acknowledge(record, outcome):
                if outcome in ("injected", "partial"):
                    with self._lock:
                        job["input_acknowledged"] = True
            ran = True
            result = self.runner(job["name"], output=self.directory / job["id"],
                cancel_file=job["cancel"], expected_target=job["pin"],
                assessment=job["assessment"], on_progress=progress, on_delivery=acknowledge,
                may_deliver=lambda: not job["stop"].is_set() and not self._pause_reason()
                    and (job["input_acknowledged"] or self.clock() < job["deadline"]))
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            monitor_stop.set()
            if monitor_thread.ident is not None:
                monitor_thread.join(.2)
            result = result or {"state": "blocked", "reason": error,
                                "shutdown": {}, "complete_diagnostics": True}
            shutdown = result.get("shutdown", {})
            clean = bool(all(shutdown.values()) and (not ran or
                all(shutdown.get(name) is True for name in ("input_idle", "dispatcher", "recognition", "source"))))
            # An exception after an input acknowledgment cannot certify drain.
            if error and job["input_acknowledged"]:
                clean = False
            if (job["name"] == "approach_current_target" and job["input_acknowledged"]
                    and result.get("final_stop_observed") is not True):
                clean = False
                error = error or "navigation_stop_not_observed; ownership retained"
            with self._lock:
                canceled = job["stop"].is_set() or result.get("cancel_requested") is True
                terminal = "faulted" if not clean else "canceled" if canceled else result.get("state", "blocked")
                self._last[job["name"]] = {"state": terminal,
                    "phase": terminal if terminal in ("blocked", "canceled", "faulted") else "completed",
                    "result": {"state": terminal, "reason": "explicit_stop" if canceled and clean
                        else result.get("reason"), "probe_evidence": plain(result)}, "error": error}
                if clean:
                    if self._job is job:
                        self._job = None
                else:
                    job["state"] = "faulted"

    def stop_action(self, name, *, focus_requested=True):
        if name not in ROUTINES:
            return {"accepted": False, "reason": "unknown_routine", "state": self.actions_snapshot()}
        with self._lock:
            job = self._job
            if name == "scan_nearby_unscanned" and (not job or job["name"] != name):
                job = None
            if job and job["name"] != name:
                rejected = True
            else:
                rejected = False
                if job:
                    if job["state"] != "faulted":
                        job["state"] = "stopping"
                    job["stop"].set()
        if rejected:
            return {"accepted": False, "reason": "different_input_owner_active", "state": self.actions_snapshot()}
        if job:
            if focus_requested and self.focus_handover is not None:
                try:
                    job["focus_handover"] = self.focus_handover("cancel")
                except Exception as exc:
                    job["focus_handover"] = {"accepted": False, "reason": str(exc)}
            if self.activation_ready() is not None:
                job["waiting_for"] = "waiting_for_game_focus_and_image_verified_stop"
            try:
                job["cancel"].write_text("explicit stop", encoding="utf-8")
            except OSError as exc:
                return {"accepted": False, "reason": "cancel_file_write_failed: " + str(exc),
                        "state": self.actions_snapshot()}
        return {"accepted": True, "reason": "stopping" if job else "already_idle",
                "state": self.actions_snapshot()}

    def close(self, timeout=2.):
        with self._lock:
            self._closed = True
            job = self._job
        if job:
            self.stop_action(job["name"], focus_requested=False)
            job["thread"].join(timeout)
        return self.actions_snapshot()["ownership"]["owner"] is None
