"""Local mining dashboard; Manual actions require an injected native controller."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .observation_api import OBSERVATION_SCHEMA, observation_snapshot
from .live_frame import LiveImageError
from .npc_farm_demo import NpcFarmDemo


RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
IMAGE_NAME = re.compile(r"observation-[0-9]{8}\.png\Z")
HTML = Path(__file__).with_name("observer.html")
DOCK_HTML = Path(__file__).with_name("dock.html")
MAX_JSON_BYTES = 8 * 1024 * 1024
MANUAL_ROUTINES = ("scan_nearby_unscanned", "auto_scan_selected", "scan_selected",
                   "approach_current_target", "npc_fight")


class _DashboardHTTPServer(ThreadingHTTPServer):
    def server_close(self):
        demo = getattr(self, 'npc_demo', None)
        if demo:
            demo.close()
        super().server_close()

    # Windows SO_REUSEADDR permits a second listener on an occupied endpoint.
    # Exclusive ownership must be requested before bind; other platforms keep
    # their existing HTTP server reuse behavior.
    allow_reuse_address = False if os.name == "nt" else ThreadingHTTPServer.allow_reuse_address
    allow_reuse_port = False if os.name == "nt" else getattr(ThreadingHTTPServer,"allow_reuse_port",False)

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class UnattachedManualActions:
    """Honest action status when the observer has no native routine owner."""

    def actions_snapshot(self):
        routines = {name: {"kind": "toggle" if name in ("scan_nearby_unscanned", "auto_scan_selected") else "once",
                           "available": False, "ready": False,
                           "reason": "controller_not_attached", "active": False,
                           "state": "unavailable", "phase": None, "processing": None,
                           "waiting_for": None, "expected_next_step": None,
                           "result": None, "error": None}
                    for name in MANUAL_ROUTINES}
        return {"actual_mode": "manual", "paused": None,
                "app_control_enabled": False, "decisions_enabled": False,
                "attached_controller": None, "routine_decisions_enabled": False,
                "assessment_context": None,
                "ownership": {"owner": None, "state": "idle", "input_scope": None,
                              "run_id": None},
                "routines": routines, "last_input_intent": None,
                "last_input_delivery": None, "last_confirmed_effect": None}

    def start_action(self, name, request=None):
        return {"accepted": False, "reason": "controller_not_attached",
                "state": self.actions_snapshot()}

    def stop_action(self, name):
        return {"accepted": False, "reason": "controller_not_attached",
                "state": self.actions_snapshot()}


class UnattachedStartup:
    """A historical observer cannot attach to or control a game session."""

    def startup_snapshot(self):
        return {"protocol": "bsgo-manual-assist-launcher-v1",
                "phase": "unattached", "reason": "launcher_not_attached",
                "retry_available": False,
                "game": None,
                "action_control": {"paused": True, "enabled": False,
                                   "pause_reason": "launcher_not_attached",
                                   "global_pause_present": None,
                                   "acknowledgment_required": False,
                                   "acknowledgment_token": None}}

    def retry_connection(self):
        return {"accepted": False, "reason": "launcher_not_attached"}

    def resume_actions(self, *, acknowledgment_token):
        return {"accepted": False, "reason": "launcher_not_attached"}

    def pause_actions(self):
        return {"accepted": False, "reason": "launcher_not_attached"}

    def set_focus_handover(self, enabled):
        return {"accepted": False, "reason": "launcher_not_attached"}


class AttachedSessionStartup:
    """Startup controls for a pre-pinned advanced Manual-assist session."""

    def __init__(self, actions):
        self.actions = actions
        self.instance_id = uuid4().hex

    def startup_snapshot(self):
        return {"protocol": "bsgo-manual-assist-launcher-v1",
                "instance_id": self.instance_id,
                "phase": "connected", "reason": None,
                "retry_available": False,
                "game": {"connection_id": self.instance_id,
                         "description": "pinned Manual-assist session"},
                "action_control": self.actions.action_control_snapshot(),
                "focus_handover": self.actions.focus_handover_snapshot()
                    if hasattr(self.actions, "focus_handover_snapshot") else
                    {"enabled": False, "available": False,
                     "reason": "focus_handover_not_attached"}}

    def retry_connection(self):
        return {"accepted": False, "reason": "pinned_session_retry_unavailable"}

    def resume_actions(self, *, acknowledgment_token):
        return self.actions.resume_actions(acknowledgment_token=acknowledgment_token)

    def pause_actions(self):
        return self.actions.pause_actions()

    def set_focus_handover(self, enabled):
        if not hasattr(self.actions, "set_focus_handover"):
            return {"accepted": False, "reason": "focus_handover_not_attached"}
        return self.actions.set_focus_handover(enabled)


class UnattachedModeController:
    """Explicitly rejects handover when the observer has no control owner."""

    def __init__(self, actions=None):
        self._lock = Lock()
        self._requested = "manual"
        self._last_ack = None
        self._actions = actions

    def snapshot(self):
        action_state = self._actions.actions_snapshot() if self._actions else {}
        with self._lock:
            return {"actual_mode": "manual", "requested_mode": self._requested,
                    "pending": False,
                    "app_control_enabled": action_state.get("app_control_enabled") is True,
                    "decisions_enabled": False, "mission_decisions_owner": "human",
                    "routine_ownership": action_state.get("ownership"),
                    "routine_decisions_enabled": action_state.get("routine_decisions_enabled") is True,
                    "attached_controller": action_state.get("attached_controller"),
                    "autopilot_available": False,
                    "reason": "autopilot_controller_not_attached",
                    "handover_blocker": "autopilot_controller_not_attached",
                    "last_ack": self._last_ack}

    def request(self, mode):
        with self._lock:
            self._requested = mode
            self._last_ack = {"requested_mode": mode,
                              "accepted": mode == "manual",
                              "reason": None if mode == "manual" else "autopilot_controller_not_attached",
                              "at_utc": datetime.now(timezone.utc).isoformat()}
        state = self.snapshot()
        return {"accepted": mode == "manual", "state": state,
                "reason": None if mode == "manual" else "autopilot_controller_not_attached"}


def _json_file(path: Path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _result_summary(result):
    if not isinstance(result, dict):
        return None
    names = ("state", "reason", "first_blocker", "warning",
             "firing_state_after_probe", "mining_ready", "final_stop_observed",
             "final_stop_distance", "observed_reduced_speed", "brake_reason",
             "firing_effect_verified", "selection_effect_verified",
             "scan_effect_verified", "cancel_requested", "first_rejection")
    summary = {name: result[name] for name in names if name in result}
    for name in ("acceleration_requests", "transition_requests", "wheel_requests",
                 "brake_requests", "deliveries", "post_acceleration_motion_sequences"):
        if isinstance(result.get(name), list):
            summary[name + "_count"] = len(result[name])
    return summary


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class ObserverIndex:
    def __init__(self, runs_root: Path):
        self.root = Path(runs_root).resolve()
        self._freshness = {}

    def run(self, name: str) -> Path | None:
        if not isinstance(name, str) or not RUN_NAME.fullmatch(name):
            return None
        path = self.root / name
        if not path.is_dir() or not _inside(path, self.root):
            return None
        if not (path / "manifest.json").is_file() or not _inside(path / "manifest.json", path):
            return None
        return path

    def runs(self, limit=60):
        entries = []
        if not self.root.is_dir():
            return []
        for child in self.root.iterdir():
            if not child.is_dir() or not RUN_NAME.fullmatch(child.name) or not _inside(child, self.root):
                continue
            manifest = child / "manifest.json"
            if not manifest.is_file() or not _inside(manifest, child):
                continue
            status = child / "status.json"
            stamp = status.stat().st_mtime_ns if status.is_file() else manifest.stat().st_mtime_ns
            metadata = _json_file(manifest) or {}
            entries.append((stamp, child.name, metadata.get("synthetic") is True))
        entries.sort(reverse=True)
        return [{"name": name, "synthetic": synthetic, "modified_utc": datetime.fromtimestamp(
            stamp / 1e9, timezone.utc).isoformat()} for stamp, name, synthetic in entries[:limit]]

    def status_age(self, path: Path):
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return None
        key = str(path)
        now = time.monotonic()
        previous = self._freshness.get(key)
        if previous is None or previous[0] != stamp:
            age_at_seen = max(0., time.time() - stamp / 1e9)
            self._freshness[key] = (stamp, now, age_at_seen)
            return age_at_seen
        return previous[2] + now - previous[1]

    @staticmethod
    def image_path(run: Path, relative: str) -> Path | None:
        if not isinstance(relative, str) or "\\" in relative:
            return None
        parts = PurePosixPath(relative).parts
        if (len(parts) != 2 or parts[0] not in ("frames", "failed-recognition")
                or not IMAGE_NAME.fullmatch(parts[1])):
            return None
        path = run / parts[0] / parts[1]
        if (not _inside(run / parts[0], run) or not path.is_file()
                or not _inside(path, run / parts[0])):
            return None
        return path

    @staticmethod
    def _images(run: Path, folder: str):
        directory = run / folder
        if not directory.is_dir() or not _inside(directory, run):
            return []
        return sorted(p.name for p in directory.iterdir()
                      if p.is_file() and IMAGE_NAME.fullmatch(p.name)
                      and _inside(p, directory))

    def detail(self, name: str | None):
        choices = self.runs()
        selected = name or next((choice["name"] for choice in choices
                                 if not choice["synthetic"]), None)
        run = self.run(selected) if selected else None
        if run is None:
            return None
        status_path = run / "status.json"
        manifest_path = run / "manifest.json"
        manifest = _json_file(manifest_path) if _inside(manifest_path, run) else None
        status = _json_file(status_path) if _inside(status_path, run) else None
        report_path = run / "report.json"
        report = _json_file(report_path) if _inside(report_path, run) else None
        if report is None:
            report_path = run / "diagnostics-report.json"
            report = _json_file(report_path) if _inside(report_path, run) else None
        manifest = manifest or {}
        status = status or {}
        age = self.status_age(status_path) if _inside(status_path, run) else None
        finalized = bool(report is not None or status.get("lifecycle") == "finalized")
        worker_running = (status.get("worker") or {}).get("running") is True
        if finalized:
            lifecycle = "finalized"
        elif age is None or age > 3:
            lifecycle = "stale"
        elif worker_running:
            lifecycle = "live"
        else:
            lifecycle = "stopped_unfinalized"
        current = self._images(run, "frames")
        failures = self._images(run, "failed-recognition")
        retained = status.get("last_retained_frame") or {}
        retained_path = retained.get("path") if isinstance(retained, dict) else None
        latest_image = (retained_path if self.image_path(run, retained_path) else
                        f"frames/{current[-1]}" if current else None)
        first_failure = None
        result = (report or {}).get("result") or status.get("result") or {}
        first_rejection = result.get("first_rejection") or {}
        if isinstance(first_rejection, dict):
            image = first_rejection.get("image_reference")
            sidecar = first_rejection.get("sidecar_reference")
            if self.image_path(run, image):
                first_failure = {"image": image,
                    "sidecar": sidecar if self.sidecar_path(run, sidecar) else None,
                    "recognition_failures": {},
                    "field_reasons": {key: value.get("reason") for key, value in
                        (first_rejection.get("fields") or {}).items()
                        if isinstance(value, dict) and value.get("reason")},
                    "read_error": first_rejection.get("read_error"),
                    "observation_sequence": first_rejection.get("observation_sequence")}
        if first_failure is None and failures:
            image = f"failed-recognition/{failures[0]}"
            sidecar_path = run / "failed-recognition" / failures[0].replace(".png", ".json")
            sidecar = (_json_file(sidecar_path) if _inside(run / "failed-recognition", run)
                       and _inside(sidecar_path, run / "failed-recognition")
                       else None) or {}
            fields = (sidecar.get("snapshot") or {}).get("fields") or {}
            first_failure = {"image": image,
                "sidecar": f"failed-recognition/{sidecar_path.name}"
                    if sidecar_path.is_file() and _inside(run / "failed-recognition", run)
                    and _inside(sidecar_path, run / "failed-recognition") else None,
                "recognition_failures": sidecar.get("recognition_failures") or {},
                "field_reasons": {key: value.get("reason") for key, value in fields.items()
                    if isinstance(value, dict) and value.get("valid") is False}}
        final = report or {}
        return {"run": selected, "run_path": str(run), "lifecycle": lifecycle,
            "status_age_seconds": round(age, 2) if age is not None else None,
            "server_monotonic": time.monotonic(),
            "server_utc": datetime.now(timezone.utc).isoformat(),
            "status": status, "manifest": manifest,
            "report": {"present": report is not None,
                "lifecycle": final.get("lifecycle"),
                "result": _result_summary(final.get("result")),
                "path": str(report_path) if report is not None else None,
                "complete_diagnostics": final.get("complete_diagnostics"),
                "shutdown": final.get("shutdown"), "error": final.get("error")},
            "latest_image": latest_image, "first_failure": first_failure}

    @staticmethod
    def sidecar_path(run: Path, relative: str) -> Path | None:
        if not isinstance(relative, str) or "\\" in relative:
            return None
        parts = PurePosixPath(relative).parts
        if (len(parts) != 2 or parts[0] != "failed-recognition"
                or not re.fullmatch(r"observation-[0-9]{8}\.json", parts[1])):
            return None
        directory = run / parts[0]
        path = directory / parts[1]
        return path if (_inside(directory, run) and path.is_file()
                        and _inside(path, directory)) else None


def make_server(runs_root: Path, port: int = 8765, mode_controller=None,
                action_adapter=None, startup_adapter=None, observation_adapter=None,
                dock_adapter=None):
    index = ObserverIndex(runs_root)
    npc_demo = NpcFarmDemo()
    actions = action_adapter or UnattachedManualActions()
    observations = observation_adapter if observation_adapter is not None else actions
    controller = mode_controller or UnattachedModeController(actions)
    startup = (startup_adapter if startup_adapter is not None else
               AttachedSessionStartup(actions) if all(hasattr(actions, name) for name in
                   ("action_control_snapshot", "resume_actions", "pause_actions")) else
               UnattachedStartup())
    actions_lock = Lock()

    def mode_snapshot():
        state = controller.snapshot()
        if state.get("actual_mode") == "manual":
            routine = actions.actions_snapshot()
            state = {**state, "app_control_enabled": routine.get("app_control_enabled") is True,
                     "decisions_enabled": False, "mission_decisions_owner": "human",
                     "routine_decisions_enabled": routine.get("routine_decisions_enabled") is True,
                     "routine_ownership": routine.get("ownership")}
        return state

    def action_status():
        state = actions.actions_snapshot()
        mode = controller.snapshot()
        artifacts = state.get("action_artifacts")
        if isinstance(artifacts, dict):
            directory = artifacts.get("directory")
            if isinstance(directory, str):
                run = index.run(Path(directory).name)
                if run is not None and Path(directory).resolve() == run:
                    artifacts = {**artifacts, "run": run.name}
                    state = {**state, "action_artifacts": artifacts}
        return {**state, "actual_mode": mode.get("actual_mode", "unknown"),
                "mode_pending": mode.get("pending") is True,
                "mode_reason": mode.get("reason"),
                "server_monotonic": time.monotonic()}

    def startup_status():
        state = startup.startup_snapshot()
        if not isinstance(state, dict) or not isinstance(state.get("action_control"), dict):
            raise ValueError("invalid_startup_snapshot")
        return state

    def observation_status():
        started = time.monotonic()
        sources, errors = {}, {}
        actions_at = None
        # Read providers independently. Do not hold the mutation lock across
        # observation callbacks: a slow read must not block emergency Pause.
        for name, provider in (("startup", startup_status),
                               ("mode", controller.snapshot),
                               ("actions", actions.actions_snapshot)):
            try:
                if name == "actions":
                    actions_at = time.monotonic()
                state = provider()
                if not isinstance(state, dict):
                    raise ValueError("invalid_status_snapshot")
                sources[name] = state
            except Exception as exc:
                errors[name] = {"error": f"{name}_status_unavailable", "cause": type(exc).__name__}
        payload = observation_snapshot(sources.get("startup"), sources.get("mode"),
            sources.get("actions"), collected_at=time.monotonic(), started_at=started,
            actions_collected_at=actions_at, source_errors=errors)
        provider = getattr(observations, 'game_state_snapshot', None)
        if callable(provider):
            try:
                state = provider()
                payload['game_state'] = {'ship': state['ship'],
                    'observation': state['observation'], 'source': state['source']}
            except Exception as exc:
                from .game_observation import game_state_view
                payload['game_state'] = {'ship': game_state_view(None, time.monotonic())['ship']}
                errors['game_state'] = {'error': 'game_state_unavailable', 'cause': type(exc).__name__}
                payload['source_errors'] = errors
        return (503 if errors else 200), payload

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, mime: str):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, payload):
            self._send(code, json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self._json(403, {"error": "invalid_local_host"})
                return
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._json(403, {"error": "cross_site_request_rejected"})
                return
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            if request.path == "/":
                self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if request.path == "/dock":
                self._send(200, DOCK_HTML.read_bytes(), "text/html; charset=utf-8")
                return
            if request.path == "/api/dock":
                if query:
                    self._json(400, {"error": "dock_state_does_not_accept_query_parameters"})
                    return
                provider = getattr(dock_adapter, "dock_snapshot", None)
                self._json(200 if callable(provider) else 503,
                    provider() if callable(provider) else {"error": "side_panel_not_attached"})
                return
            if request.path == '/npc-farm.js':
                self._send(200, HTML.with_name('npc-farm.js').read_bytes(), 'text/javascript; charset=utf-8')
                return
            if request.path == "/api/live-frame":
                if query:
                    self._json(400, {"error": "live_frame_does_not_accept_query_parameters"})
                    return
                provider = getattr(actions, "live_frame_snapshot", None)
                try:
                    payload = provider() if callable(provider) else {
                        "available": False, "identity": None, "version": None, "image_url": None,
                        "status": "waiting", "fresh": False, "connected": False,
                        "reason": "native_live_frame_provider_unavailable", "acquired_at": None,
                        "age_seconds": None, "frame_size": None}
                except Exception as exc:
                    self._json(503, {"error": "live_frame_status_unavailable", "cause": type(exc).__name__})
                    return
                self._json(200, payload)
                return
            if request.path == "/api/live-image":
                if (set(query) != {"identity", "version"} or any(len(v) != 1 for v in query.values())
                        or not re.fullmatch(r"[a-f0-9]{32}", query["identity"][0])
                        or not re.fullmatch(r"[1-9][0-9]{0,15}", query["version"][0])):
                    self._json(400, {"error": "live_image_identity_and_version_required"})
                    return
                provider = getattr(actions, "live_frame_image", None)
                if not callable(provider):
                    self._json(404, {"error": "live_image_unavailable"})
                    return
                try:
                    data, mime = provider(query["identity"][0], int(query["version"][0]))
                except LiveImageError as exc:
                    self._json(exc.code, {"error": exc.reason})
                    return
                except Exception as exc:
                    self._json(503, {"error": "live_image_unavailable", "cause": type(exc).__name__})
                    return
                self._send(200, data, mime)
                return
            if request.path == '/api/game-state':
                if request.query:
                    self._json(400, {'error': 'game_state_does_not_accept_query_parameters'})
                    return
                provider = getattr(observations, 'game_state_snapshot', None)
                if not callable(provider):
                    self._json(503, {'error': 'game_observation_not_attached'})
                    return
                try:
                    self._json(200, provider())
                except Exception as exc:
                    self._json(503, {'error': 'game_state_unavailable', 'cause': type(exc).__name__})
                return
            if request.path == '/api/npc-stats':
                provider = getattr(startup, 'npc_stats_snapshot', None)
                if not callable(provider):
                    self._json(503, {'error': 'npc_stats_unavailable'})
                    return
                self._json(200, provider())
                return
            if request.path == '/api/features':
                provider = getattr(startup, 'features_snapshot', None)
                self._json(200 if callable(provider) else 503,
                    provider() if callable(provider) else {'error': 'features_not_attached'})
                return
            if request.path == '/api/npc-farm':
                if request.query:
                    self._json(400, {'error': 'npc_state_does_not_accept_query_parameters'})
                else:
                    self._json(200, npc_demo.snapshot())
                return
            if request.path in ("/api/observation", "/api/observation/schema"):
                if query:
                    self._json(400, {"error": "live_observation_does_not_accept_query_parameters"})
                    return
                if request.path.endswith("/schema"):
                    self._json(200, OBSERVATION_SCHEMA)
                else:
                    code, payload = observation_status()
                    self._json(code, payload)
                return
            if request.path == "/api/runs":
                self._json(200, {"runs": index.runs(), "runs_root": str(index.root)})
                return
            if request.path == "/api/control-mode":
                try:
                    payload = mode_snapshot()
                except Exception as exc:
                    self._json(503, {"error": "mode_status_unavailable",
                                     "cause": type(exc).__name__})
                    return
                self._json(200, payload)
                return
            if request.path == "/api/manual-actions":
                try:
                    payload = action_status()
                except Exception as exc:
                    self._json(503, {"error": "action_status_unavailable",
                                     "cause": type(exc).__name__})
                    return
                self._json(200, payload)
                return
            if request.path == "/api/startup":
                try:
                    payload = startup_status()
                except Exception as exc:
                    self._json(503, {"error": "startup_status_unavailable",
                                     "cause": type(exc).__name__})
                    return
                self._json(200, payload)
                return
            if request.path == "/api/status":
                run = query.get("run", [None])[0]
                detail = index.detail(run)
                self._json(200 if detail else 404, detail or {"error": "run_not_found"})
                return
            if request.path == "/api/image":
                name = query.get("run", [None])[0]
                relative = query.get("path", [None])[0]
                run = index.run(name) if name else None
                image = index.image_path(run, relative) if run else None
                if image is None:
                    self._json(404, {"error": "image_not_found"})
                    return
                try:
                    if image.stat().st_size > 16 * 1024 * 1024:
                        self._json(413, {"error": "image_too_large"})
                        return
                    data = image.read_bytes()
                except OSError:
                    self._json(404, {"error": "image_not_found"})
                    return
                self._send(200, data, "image/png")
                return
            if request.path == "/api/sidecar":
                name = query.get("run", [None])[0]
                relative = query.get("path", [None])[0]
                run = index.run(name) if name else None
                path = index.sidecar_path(run, relative) if run else None
                if path is None:
                    self._json(404, {"error": "sidecar_not_found"})
                    return
                payload = _json_file(path)
                self._json(200 if payload is not None else 404,
                           payload if payload is not None else {"error": "sidecar_not_found"})
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self._json(403, {"error": "invalid_local_host"})
                return
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._json(403, {"error": "cross_site_request_rejected"})
                return
            origin = self.headers.get("Origin")
            if origin != f"http://127.0.0.1:{self.server.server_port}":
                self._json(403, {"error": "invalid_origin"})
                return
            path = urlsplit(self.path).path
            if path not in ("/api/control-mode", "/api/manual-actions", "/api/startup", "/api/features", "/api/npc-farm",
                            "/api/dock"):
                self._json(404, {"error": "not_found"})
                return
            if self.headers.get("Content-Type") != "application/json":
                self._json(415, {"error": "json_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", ""))
                if not 0 < size <= (4096 if path == "/api/manual-actions" else 1024):
                    raise ValueError
                data = json.loads(self.rfile.read(size))
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_mode_request"})
                return
            try:
                if path == '/api/npc-farm':
                    decision = npc_demo.command(data)
                elif path == '/api/dock':
                    claim = getattr(dock_adapter, 'claim_hotkey', None)
                    if (not isinstance(data, dict) or set(data) != {'action', 'sequence'}
                            or data['action'] != 'claim_hotkey' or type(data['sequence']) is not int):
                        self._json(400, {'error': 'invalid_dock_request'})
                        return
                    if not callable(claim):
                        self._json(503, {'error': 'side_panel_not_attached'})
                        return
                    decision = {**claim(data['sequence']), 'state': {}}
                elif path == '/api/features':
                    if (not isinstance(data, dict) or set(data) != {'feature', 'enabled'}
                            or data['feature'] != 'mining' or type(data['enabled']) is not bool):
                        self._json(400, {'error': 'invalid_feature_setting'})
                        return
                    provider = getattr(startup, 'set_feature', None)
                    if not callable(provider):
                        self._json(503, {'error': 'features_not_attached'})
                        return
                    with actions_lock:
                        decision = provider(data['feature'], data['enabled'])
                elif path == "/api/control-mode":
                    mode = data.get("mode") if isinstance(data, dict) else None
                    if mode not in ("manual", "autopilot"):
                        self._json(400, {"error": "invalid_mode"})
                        return
                    # Mode handover and routine admission must share the same
                    # mutation boundary as Pause/Enable and Start/Cancel.
                    with actions_lock:
                        decision = controller.request(mode)
                        decision["state"] = mode_snapshot()
                elif path == "/api/startup":
                    if not isinstance(data, dict) or data.get("action") not in (
                            "retry_connection", "enable_manual_actions", "pause_manual_actions",
                            "set_focus_handover", "fit_game_window"):
                        self._json(400, {"error": "invalid_startup_action"})
                        return
                    action = data["action"]
                    token = data.get("acknowledgment_token")
                    if action == "enable_manual_actions" and (
                            not isinstance(token, str) or not 0 < len(token) <= 256):
                        self._json(400, {"error": "acknowledgment_token_required"})
                        return
                    if action == "set_focus_handover" and type(data.get("enabled")) is not bool:
                        self._json(400, {"error": "focus_preference_boolean_required"})
                        return
                    with actions_lock:
                        if action == "retry_connection":
                            decision = startup.retry_connection()
                        elif action == "fit_game_window":
                            fit = getattr(startup, "fit_game_window", None)
                            decision = fit() if callable(fit) else {
                                "accepted": False, "reason": "fit_game_window_unavailable"}
                        elif action == "enable_manual_actions":
                            decision = startup.resume_actions(acknowledgment_token=token)
                        elif action == "set_focus_handover":
                            decision = startup.set_focus_handover(data["enabled"])
                        else:
                            decision = startup.pause_actions()
                        decision["state"] = startup_status()
                else:
                    if (not isinstance(data, dict) or data.get("action") not in ("start", "stop")
                            or data.get("routine") not in MANUAL_ROUTINES
                            or not isinstance(data.get("request", {}), dict)):
                        self._json(400, {"error": "invalid_manual_action"})
                        return
                    name, action = data["routine"], data["action"]
                    with actions_lock:
                        current = action_status()
                        owner = (current.get("ownership") or {}).get("owner")
                        routines = current.get("routines") or {}
                        active = [routine for routine, state in routines.items()
                                  if isinstance(state, dict) and state.get("active") is True]
                        if action == "start" and (current.get("actual_mode") != "manual"
                                                  or current.get("mode_pending")):
                            decision = {"accepted": False, "reason": "manual_mode_required",
                                        "state": current}
                        elif (action == "start" and not isinstance(startup, UnattachedStartup)
                                and startup_status()["action_control"].get("enabled") is not True):
                            decision = {"accepted": False, "reason": "manual_actions_paused",
                                        "state": current}
                        elif action == "start" and (owner not in (None, "none", "idle") or active):
                            decision = {"accepted": False, "reason": "input_owner_busy",
                                        "state": current}
                        elif action == "start" and not ((routines.get(name) or {}).get("available") is True
                                                        and (routines.get(name) or {}).get("ready") is True):
                            decision = {"accepted": False,
                                        "reason": (routines.get(name) or {}).get("reason") or "routine_not_ready",
                                        "state": current}
                        elif action == "start":
                            decision = actions.start_action(name, data.get("request", {}))
                        elif owner != name and name not in active:
                            decision = {"accepted": False, "reason": "routine_not_active",
                                        "state": current}
                        else:
                            decision = actions.stop_action(name)
                        decision["state"] = action_status()
            except Exception as exc:
                self._json(503, {"error": "action_adapter_unavailable",
                                 "cause": type(exc).__name__})
                return
            self._json(200 if decision["accepted"] else
                       202 if decision["state"].get("pending") else 409, decision)

        def log_message(self, format, *args):
            pass

    def hotkey_pause():
        # Same mutation boundary as the dashboard's Pause request.
        with actions_lock:
            return startup.pause_actions()

    server = _DashboardHTTPServer(("127.0.0.1", port), Handler)
    server.hotkey_pause = hotkey_pause
    server.npc_demo = npc_demo
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path,
                        default=Path("logs/mining/live-20261003"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be 0..65535")
    server = make_server(args.runs_root, args.port)
    print(f"Observer: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
