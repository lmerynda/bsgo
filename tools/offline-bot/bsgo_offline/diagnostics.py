"""Bounded diagnostic run package. Disk and image writes stay off control workers."""
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from threading import Condition, Event, Thread, Lock
import time

from .artifacts import plain
from .state import freeze
from .trace import TraceJournal


def recognition_failures(snapshot):
    """Actual rejected image reads, excluding declared unimplemented fields."""
    reasons = {name: field.reason or "invalid_image_read"
               for name, field in snapshot.fields.items() if not field.valid
               and not (field.reason or "").startswith("mining_recognizer_not_validated:")}
    # An explicitly absent asteroid panel is a valid classification, not a
    # failed attempt to recognize its identity/portrait/HP.
    selected = snapshot.fields.get("asteroid_selected")
    if selected and selected.valid and selected.value is False:
        reasons = {name: reason for name, reason in reasons.items()
                   if reason != "asteroid_title_not_verified"}
    if not snapshot.healthy:
        reasons["observation"] = snapshot.reason or "unhealthy_image_observation"
    return reasons


def _write_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(plain(payload), indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _terminal_result_summary(result):
    """Keep the terminal journal event bounded; report.json holds full result."""
    if not isinstance(result, dict):
        return {"available": result is not None}
    names = ("state", "reason", "mining_ready", "final_stop_observed",
             "cancel_requested", "brake_reason", "scan_effect_verified",
             "selection_effect_verified", "firing_effect_verified")
    summary = {}
    for name in names:
        value = result.get(name)
        if type(value) in (str, int, float, bool) or value is None:
            summary[name] = value[:512] if isinstance(value, str) else value
    rejection = result.get("first_rejection")
    if isinstance(rejection, dict):
        compact = {}
        for name in ("observation_sequence", "acquisition_sequence",
                     "snapshot_healthy"):
            value = rejection.get(name)
            if type(value) in (int, bool) or value is None:
                compact[name] = value
        for name in ("read_error", "image_reference", "sidecar_reference",
                     "snapshot_reason"):
            value = rejection.get(name)
            if isinstance(value, str) or value is None:
                compact[name] = value[:256] if isinstance(value, str) else None
        fields = rejection.get("fields")
        if isinstance(fields, dict):
            compact["fields"] = {}
            for name, field in list(fields.items())[:8]:
                if not isinstance(name, str) or not isinstance(field, dict):
                    continue
                compact["fields"][name[:48]] = {
                    key: field[key] for key in ("present", "valid", "fresh")
                    if type(field.get(key)) is bool}
                reason = field.get("reason")
                if isinstance(reason, str) or reason is None:
                    compact["fields"][name[:48]]["reason"] = (
                        reason[:160] if isinstance(reason, str) else None)
        summary["first_rejection"] = compact
    return summary


def source_manifest():
    package = Path(__file__).resolve().parent
    sources = {"bsgo_offline/" + path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(package.glob("*.py"))}
    reader = package.parents[1] / "hud-reader" / "hud_reader.py"
    sources["hud-reader/hud_reader.py"] = hashlib.sha256(reader.read_bytes()).hexdigest()
    for name in ("templates.json", "restart-calibration.json", "largehud-calibration.json", "large_hud_reader.py"):
        calibration = reader.parent / name
        sources["hud-reader/" + name] = hashlib.sha256(calibration.read_bytes()).hexdigest()
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package,
            capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    return {"code_revision": revision, "source_sha256": sources,
            "source_identity": "startup file hashes; revision alone may omit working edits"}


class DiagnosticRun:
    """Owns a journal, bounded frame writer and independent live status sampler.

    Native frames contain immutable RGB bytes; enqueueing one neither encodes an
    image nor touches disk. Saved replay paths are copied asynchronously and are
    labeled as source copies rather than native acquisition bytes. Budget skips,
    backpressure and writer errors are distinct and visible. Use a new directory.
    """
    def __init__(self, directory, *, mode, configuration, frame_capacity=3,
                 max_frames=64, max_frame_bytes=32*1024*1024, status_interval=.5,
                 report_filename="report.json"):
        for name, value in (("frame_capacity", frame_capacity),
                            ("max_frame_bytes", max_frame_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(name)
        if max_frames is not None and (type(max_frames) is not int or max_frames < 1):
            raise ValueError("max_frames")
        if type(status_interval) not in (int, float) or not 0 < status_interval <= 60:
            raise ValueError("status_interval")
        if report_filename not in ("report.json", "diagnostics-report.json"):
            raise ValueError("report_filename")
        self.report_filename = report_filename
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(self.directory.iterdir()):
            raise ValueError("diagnostic_directory_must_be_empty")
        self.manifest = {"schema_version": 1, "mode": mode,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "configuration": plain(freeze(configuration)), **source_manifest(),
            "retention": {"frame_queue_capacity": frame_capacity, "max_frames": max_frames,
                          "failed_recognition_frames": "all; no count budget; raw pixels and JSON sidecar",
                          "max_frame_bytes": max_frame_bytes, "status_interval_real_seconds": status_interval},
            "time_domains": {"observations": "component control clock", "worker_durations": "real monotonic",
                             "utc": "wall clock"},
            "lifecycle": "started; report.json confirms finalized shutdown"}
        _write_json(self.directory / "manifest.json", self.manifest)
        self.journal = TraceJournal(self.directory / "trace")
        self.frame_capacity, self.max_frames, self.max_frame_bytes = frame_capacity, max_frames, max_frame_bytes
        self._condition, self._status_lock = Condition(), Lock()
        self._queue = deque()
        self._closing = self._finished = False
        self._accepted = self._written = self._dropped = self._skipped = self._unavailable = 0
        self._routine_accepted = 0
        self._failed_observed = self._failed_accepted = self._failed_written = self._failed_dropped = 0
        self._frame_error = None
        self._status_errors = 0
        self._status_error = None
        self._journal_dropped = 0
        self._event_cursor = 0
        self._store = self._worker = self._coordinator = self._backend = None
        self._capabilities = None
        self._status_interval = status_interval
        self._sampler_stop = Event()
        self._sampler = None
        self._writer = Thread(target=self._write_frames, name="bsgo-frame-writer", daemon=True)
        self._writer.start()

    def submit(self, event):
        try:
            accepted = self.journal.submit(plain(event))
        except Exception:
            accepted = False
        if not accepted:
            with self._condition:
                self._journal_dropped += 1
        return accepted

    def attach(self, *, worker, store, coordinator, backend, capabilities=None):
        if self._sampler is not None or self._closing:
            raise RuntimeError("diagnostics_already_attached_or_closed")
        self._worker, self._store, self._coordinator, self._backend = worker, store, coordinator, backend
        self._capabilities = capabilities
        self._sampler = Thread(target=self._sample_status, name="bsgo-status-sampler", daemon=True)
        self._sampler.start()

    def frame_stats(self):
        with self._condition:
            return {"accepted": self._accepted, "written": self._written, "pending": len(self._queue),
                "unwritten": self._accepted - self._written, "dropped": self._dropped,
                "budget_skipped": self._skipped, "pixels_unavailable": self._unavailable,
                "queue_capacity": self.frame_capacity, "max_frames": self.max_frames,
                "error": self._frame_error, "writer_running": self._writer.is_alive(),
                "failed_recognition": {"observed": self._failed_observed,
                    "accepted": self._failed_accepted, "written": self._failed_written,
                    "missing": self._failed_observed - self._failed_written,
                    "dropped": self._failed_dropped, "count_budget": None,
                    "complete": self._finished and self._failed_observed == self._failed_written
                        and not self._frame_error},
                "write_complete": self._finished and self._accepted == self._written
                    and not self._dropped and not self._skipped and not self._frame_error}

    def _retain_frame(self, snapshot, sample):
        failures = recognition_failures(snapshot)
        reference = {"observation_sequence": snapshot.sequence,
                     "acquisition_sequence": snapshot.acquisition_sequence, "context": plain(snapshot.context),
                     "recognition_failures": failures}
        with self._condition:
            self._failed_observed += bool(failures)
        native = isinstance(getattr(sample, "rgb", None), bytes)
        path = getattr(sample, "path", None)
        if not native and path is None:
            with self._condition:
                self._unavailable += 1
            return {**reference, "state": "unavailable", "reason": "source_has_no_pixels"}
        folder = "failed-recognition" if failures else "frames"
        relative = f"{folder}/observation-{snapshot.sequence:08d}." + ("png" if native else "source")
        with self._condition:
            if self._closing or self._frame_error or len(self._queue) >= self.frame_capacity:
                self._dropped += 1
                state, reason = "dropped", "frame_writer_unavailable_or_queue_full"
            elif (not failures and self.max_frames is not None
                  and self._routine_accepted >= self.max_frames):
                self._skipped += 1
                state, reason = "skipped", "frame_retention_budget"
            elif native and len(sample.rgb) > self.max_frame_bytes:
                self._dropped += 1
                state, reason = "dropped", "frame_byte_limit"
            else:
                self._accepted += 1
                self._failed_accepted += bool(failures)
                self._routine_accepted += not failures
                reference.update(path=relative, provenance="native_rgb" if native else "saved_source_copy")
                self._queue.append((reference, sample, native, snapshot))
                self._condition.notify()
                return {**reference, "state": "queued"}
            if failures:
                self._failed_dropped += 1
        return {**reference, "state": state, "reason": reason}

    def observation(self, snapshot, events, sample, timings):
        frame = self._retain_frame(snapshot, sample)
        if self._store is not None:
            view = self._store.read(self._event_cursor)
            events = view.events.events
            self._event_cursor = view.events.next_cursor
        return self.submit({"event": "observation", "snapshot": snapshot,
                            "observed_events": events, "frame": frame,
                            "durations_real_seconds": timings})

    def _write_frames(self):
        try:
            (self.directory / "frames").mkdir()
            (self.directory / "failed-recognition").mkdir()
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._closing)
                    if not self._queue:
                        break
                    reference, sample, native, snapshot = self._queue.popleft()
                target = self.directory / reference["path"]
                if native:
                    sample.to_image().save(target, format="PNG")
                else:
                    # Reject huge files on the writer, never on the capture worker.
                    if Path(sample.path).stat().st_size > self.max_frame_bytes:
                        raise ValueError("saved_frame_byte_limit")
                    shutil.copyfile(sample.path, target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                retained = {"event": "frame_retained", **reference, "sha256": digest}
                if reference["recognition_failures"]:
                    sidecar = target.with_suffix(".json")
                    _write_json(sidecar, {"run_id": self.journal.run_id,
                        "saved_utc": datetime.now(timezone.utc).isoformat(),
                        **retained, "snapshot": plain(snapshot),
                        "source_sha256": self.manifest["source_sha256"]})
                    retained["sidecar"] = str(sidecar.relative_to(self.directory))
                with self._condition:
                    self._written += 1
                    self._failed_written += bool(reference["recognition_failures"])
                self.submit(retained)
        except Exception as error:
            with self._condition:
                self._frame_error = f"{type(error).__name__}: {error}"
            self.submit({"event": "frame_writer_failure", "reason": self._frame_error})
        finally:
            with self._condition:
                self._finished = True

    def read_status(self):
        snapshot = self._store.latest() if self._store else None
        return plain({"run_id": self.journal.run_id, "mode": self.manifest["mode"],
            "sampled_utc": datetime.now(timezone.utc).isoformat(),
            "runtime": self._coordinator.read_status() if self._coordinator else None,
            "preflight": self._coordinator.read_preflight(self._capabilities) if self._coordinator else None,
            "worker": self._worker.metrics() if self._worker else None,
            "latest_observation_sequence": snapshot.sequence if snapshot else None,
            "input_pending": self._backend.pending_requests if self._backend else 0,
            "input_history": self._backend.history_stats() if self._backend else None,
            "actions": self._coordinator.executor.telemetry_stats() if self._coordinator else None,
            "frames": self.frame_stats(), "trace": self.journal.stats(),
            "diagnostic_journal_dropped": self._journal_dropped,
            "status_write_errors": self._status_errors, "status_write_error": self._status_error})

    def _publish_status(self):
        with self._status_lock:
            status = self.read_status()
            _write_json(self.directory / "status.json", status)
            return status

    def _sample_status(self):
        while not self._sampler_stop.is_set():
            try:
                self._publish_status()
            except Exception as error:
                self._status_errors += 1
                self._status_error = f"{type(error).__name__}: {error}"
            self._sampler_stop.wait(self._status_interval)

    def close(self, *, shutdown, result=None, error=None, timeout=2):
        """Caller stops its runtime components first; incomplete joins stay visible."""
        self._sampler_stop.set()
        if self._sampler:
            self._sampler.join(timeout)
        sampler_stopped = not self._sampler or not self._sampler.is_alive()
        with self._condition:
            self._closing = True
            self._condition.notify()
        self._writer.join(timeout)
        shutdown = {**shutdown, "status_sampler": sampler_stopped,
                    "frame_writer": not self._writer.is_alive()}
        self.submit({"event": "run_shutdown", "run_id": self.journal.run_id,
                     "shutdown": shutdown, "result_summary": _terminal_result_summary(result),
                     "full_result": {"file": self.report_filename,
                                     "json_pointer": "/result"},
                     "error": str(error)[:512] if error is not None else None})
        shutdown["trace_writer"] = self.journal.close(timeout)
        status = self.read_status()
        telemetry_complete = (all(shutdown.values()) and status["trace"]["write_complete"]
            and status["frames"]["write_complete"] and not self._journal_dropped
            and not self._status_errors
            and not (status["worker"] or {}).get("diagnostic_dropped", 0)
            and not (status["actions"] or {}).get("journal_dropped", 0)
            and not (status["input_history"] or {}).get("logging_gaps", 0))
        report = {**status, "result": plain(result), "error": error, "shutdown": shutdown,
                  "complete_diagnostics": telemetry_complete,
                  "input_requests": plain(self._backend.records()) if self._backend else [],
                  "lifecycle": "finalized", "ended_utc": datetime.now(timezone.utc).isoformat()}
        _write_json(self.directory / self.report_filename, report)
        _write_json(self.directory / "status.json", report)
        return report
