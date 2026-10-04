"""Read-only localhost dashboard for retained mining diagnostic runs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path, PurePosixPath
import re
from threading import Lock
import time
from urllib.parse import parse_qs, urlsplit


RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
IMAGE_NAME = re.compile(r"observation-[0-9]{8}\.png\Z")
HTML = Path(__file__).with_name("observer.html")
MAX_JSON_BYTES = 8 * 1024 * 1024


class UnattachedModeController:
    """Explicitly rejects handover when the observer has no control owner."""

    def __init__(self):
        self._lock = Lock()
        self._requested = "manual"
        self._last_ack = None

    def snapshot(self):
        with self._lock:
            return {"actual_mode": "manual", "requested_mode": self._requested,
                    "pending": False, "app_control_enabled": False,
                    "decisions_enabled": False, "attached_controller": None,
                    "autopilot_available": False,
                    "reason": "controller_not_attached",
                    "handover_blocker": "controller_not_attached",
                    "last_ack": self._last_ack}

    def request(self, mode):
        with self._lock:
            self._requested = mode
            self._last_ack = {"requested_mode": mode,
                              "accepted": mode == "manual",
                              "reason": None if mode == "manual" else "controller_not_attached",
                              "at_utc": datetime.now(timezone.utc).isoformat()}
        state = self.snapshot()
        return {"accepted": mode == "manual", "state": state,
                "reason": None if mode == "manual" else "controller_not_attached"}


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


def make_server(runs_root: Path, port: int = 8765, mode_controller=None):
    index = ObserverIndex(runs_root)
    controller = mode_controller or UnattachedModeController()

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
            if request.path == "/api/runs":
                self._json(200, {"runs": index.runs(), "runs_root": str(index.root)})
                return
            if request.path == "/api/control-mode":
                self._json(200, controller.snapshot())
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
            if urlsplit(self.path).path != "/api/control-mode":
                self._json(404, {"error": "not_found"})
                return
            if self.headers.get("Content-Type") != "application/json":
                self._json(415, {"error": "json_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", ""))
                if not 0 < size <= 256:
                    raise ValueError
                data = json.loads(self.rfile.read(size))
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_mode_request"})
                return
            mode = data.get("mode") if isinstance(data, dict) else None
            if mode not in ("manual", "autopilot"):
                self._json(400, {"error": "invalid_mode"})
                return
            decision = controller.request(mode)
            self._json(200 if decision["accepted"] else
                       202 if decision["state"].get("pending") else 409, decision)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
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
