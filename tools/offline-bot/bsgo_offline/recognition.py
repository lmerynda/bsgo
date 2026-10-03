"""Independent recognition worker and adapter for the calibrated HUD reader."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
from threading import Event, Thread, Lock
import time
from typing import Callable, Mapping, Protocol

from .replay import FrameSample
from .state import FieldValue, Snapshot, SnapshotStore
from .events import EventDetector


HUD_FIELDS = ("distance", "speed", "asteroid_selected", "target_identity",
              "target_portrait", "target_max_hp")


@dataclass(frozen=True)
class RecognitionResult:
    frame_size: tuple[int, int]
    fields: Mapping[str, FieldValue]
    healthy: bool = True
    reason: str | None = None


class FrameSource(Protocol):
    def next_sample(self, stop_event: Event) -> FrameSample | None: ...


class RecognitionWorker:
    """Owns its thread; decision consumers never trigger or delay recognition.

    Stop is cooperative. Bounded join returns False if an injected source or
    recognizer has blocked, rather than falsely claiming that it was stopped.
    Native or model implementations must supply their own operation timeouts.
    """

    def __init__(self, source: FrameSource,
                 recognizer: Callable[[FrameSample], RecognitionResult],
                 store: SnapshotStore, clock: Callable[[], float] = time.monotonic,
                 *, diagnostics=None) -> None:
        self.source = source
        self.recognizer = recognizer
        self.store = store
        self.clock = clock
        self._stop = Event()
        self._thread: Thread | None = None
        self.error: str | None = None
        self.processed = 0
        self.end_reason: str | None = None
        self._events = EventDetector()
        self.diagnostics = diagnostics
        self.diagnostic_dropped = 0
        self._metrics_lock = Lock()
        self._stage = "not_started"
        self._stage_started = time.monotonic()
        self._durations = {}

    def metrics(self):
        with self._metrics_lock:
            return {"running": self.running, "processed": self.processed,
                "end_reason": self.end_reason, "error": self.error, "stage": self._stage,
                "stage_age_real_seconds": time.monotonic() - self._stage_started,
                "last_durations_real_seconds": dict(self._durations),
                "diagnostic_dropped": self.diagnostic_dropped}

    def _set_stage(self, stage):
        with self._metrics_lock:
            self._stage, self._stage_started = stage, time.monotonic()

    def _observe(self, snapshot, events, sample, timings):
        if self.diagnostics is not None:
            try:
                accepted = self.diagnostics.observation(snapshot, events, sample, timings)
            except Exception:
                accepted = False
            if accepted is False:
                self.diagnostic_dropped += 1

    @property
    def source_exhausted(self) -> bool:
        return self.end_reason == "source_exhausted"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RecognitionWorker can only be started once")
        self._thread = Thread(target=self._run, name="bsgo-offline-recognition", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 2.) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.running

    def _run(self) -> None:
        previous = self.store.latest()
        sequence = previous.sequence if previous else 0
        sample: FrameSample | None = None
        stage = "source"
        try:
            while not self._stop.is_set():
                stage = "source"
                self._set_stage(stage)
                source_started = time.monotonic()
                sample = self.source.next_sample(self._stop)
                source_elapsed = time.monotonic() - source_started
                if self._stop.is_set():
                    self.end_reason = "cancelled"
                    break
                if sample is None:
                    self.end_reason = "source_exhausted"
                    break
                stage = "recognizer"
                self._set_stage(stage)
                recognition_started = time.monotonic()
                result = self.recognizer(sample)
                recognition_elapsed = time.monotonic() - recognition_started
                if self._stop.is_set():
                    self.end_reason = "cancelled"
                    break
                sequence += 1
                stage = "publish"
                self._set_stage(stage)
                geometry = getattr(sample, "geometry", None)
                window_id = (f"{geometry.pid}:{geometry.hwnd}" if geometry else
                             getattr(sample, "window_id", None) or sample.source_id or "unknown")
                snapshot = Snapshot(
                    sequence, sample.session_epoch, sample.acquisition_sequence,
                    sample.acquired_at, self.clock(), sample.layout,
                    result.frame_size, result.fields, result.healthy, result.reason,
                    window_id=window_id,
                    ownership_generation=getattr(sample, "ownership_generation", 0),
                    capture_started_at=getattr(sample, "capture_started_at", None),
                    capture_completed_at=getattr(sample, "capture_completed_at", None),
                    frame_produced_at=getattr(sample, "frame_produced_at", None))
                events = self._events.update(snapshot)
                self.store.publish(snapshot, events)
                self.processed += 1
                with self._metrics_lock:
                    self._durations = {"source_wait_and_capture": source_elapsed,
                                       "recognition": recognition_elapsed}
                self._observe(snapshot, events, sample, dict(self._durations))
            if self.end_reason is None:
                self.end_reason = "cancelled"
        except Exception as exc:
            self.error = f"{stage}_failure: {type(exc).__name__}: {exc}"
            self.end_reason = f"{stage}_failure"
            now = self.clock()
            previous = self.store.latest()
            # Fault publication invalidates every currently available field. An
            # error is never a fresh copy of the previous numeric observation.
            fields = set(HUD_FIELDS) | (set(previous.fields) if previous else set())
            fault = Snapshot(
                (previous.sequence if previous else sequence) + 1,
                sample.session_epoch if sample else (previous.session_epoch if previous else 0),
                sample.acquisition_sequence if sample else
                    (previous.acquisition_sequence if previous else 0),
                sample.acquired_at if sample else (previous.acquired_at if previous else now),
                now, sample.layout if sample else (previous.layout if previous else "unknown"),
                previous.frame_size if previous else (0, 0),
                {name: FieldValue(None, now, False, self.error) for name in fields},
                False, self.error,
                window_id=previous.window_id if previous else "unknown",
                ownership_generation=previous.ownership_generation if previous else 0)
            self.store.publish(fault)
            self._observe(fault, (), sample if stage == "recognizer" else None, {})
        finally:
            self._set_stage("ended")


class HudRecognizer:
    """Read the configured HUD profile; never infer profile from successful OCR."""

    def __init__(self, profile: str = "original", reader_path: str | Path | None = None,
                 *, source_frame_size: tuple[int, int] | None = None) -> None:
        if source_frame_size not in (None, (3840, 2160)):
            raise ValueError("unsupported_source_frame_size")
        if profile == 'largehud-20261003' and source_frame_size is not None:
            raise ValueError('large_hud_requires_native_pixels')
        path = Path(reader_path) if reader_path else Path(__file__).resolve().parents[2] / "hud-reader" / "hud_reader.py"
        spec = importlib.util.spec_from_file_location("bsgo_offline_hud_reader", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load HUD reader from {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._reader = module.HudReader(profile=profile,
            speed_raster=("box_3840_to_2560" if profile == "restart-20261001"
                and source_frame_size == (3840, 2160) else "native"))
        self.profile = profile
        self.source_frame_size = source_frame_size

    def __call__(self, sample: FrameSample) -> RecognitionResult:
        from PIL import Image
        if sample.layout != self.profile:
            return RecognitionResult((0, 0), {
                name: FieldValue(None, sample.acquired_at, False, "layout_profile_mismatch")
                for name in HUD_FIELDS}, False, "layout_profile_mismatch")
        if callable(getattr(sample, "to_image", None)):
            image = sample.to_image().convert("RGB")
        else:
            with Image.open(sample.path) as opened:
                image = opened.convert("RGB")
        source_size = image.size
        converted = self.source_frame_size == source_size
        if converted:
            # The 4K HUD's thin decimal dot loses one pixel with Lanczos.
            # Area averaging preserved the complete 3x3 dot on all six
            # retained stationary frames from the first 4K run.
            image = image.resize((2560, 1440), Image.Resampling.BOX)
        # HudReader captured_at uses a wall clock. Keep monotonic freshness in
        # the shared-state layer, instead of feeding it a different time domain.
        raw = self._reader.read(image)
        fields: dict[str, FieldValue] = {}
        uncertainty = raw.get("ocr_uncertainty", {})
        size_ok = (image.size == (2560, 1440) and
                   source_size == (self.source_frame_size or (2560, 1440)))
        for name in HUD_FIELDS:
            value = raw.get(name)
            valid = size_ok and value is not None
            reason = None if valid else ("layout_size_mismatch" if not size_ok else
                uncertainty.get(name, {}).get("reason") or
                ("asteroid_title_not_verified" if name in ("target_identity", "target_portrait", "target_max_hp")
                 and not raw.get("asteroid_selected") else "unreadable_or_absent"))
            fields[name] = FieldValue(value, sample.acquired_at, valid, reason)
        # False asteroid_selected is a valid classification, not an OCR fault.
        # Invalid numeric fields remain unknown while their rejection evidence
        # remains available for contextual consumers.
        fields["ocr_evidence"] = FieldValue({
            "reasons": raw.get("reasons", []), "uncertainty": uncertainty,
            "distance_confidence": raw.get("distance_confidence"),
            "speed_confidence": raw.get("speed_confidence"),
            "target_hp_confidence": raw.get("target_hp_confidence"),
            "calibration_profile": self.profile,
            "calibration_validation": raw.get("calibration_validation", "existing_profile"),
            "source_frame_size": source_size,
            "recognition_frame_size": image.size,
            "resampling": "box_3840_to_2560" if converted else "none",
            "resampling_validation": "experimental_unvalidated" if converted else "native_profile_size",
        }, sample.acquired_at)
        return RecognitionResult(source_size, fields, size_ok,
                                 None if size_ok else "layout_size_mismatch")
