"""Bounded in-memory game view, independent of diagnostic archive budgets."""
from io import BytesIO
from threading import Lock
import time
from uuid import uuid4

from .artifacts import plain
from .native_capture import NativeFrame


class LiveImageError(RuntimeError):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code, self.reason = code, reason


class LiveFrameStore:
    """Retain one immutable native sample and at most one encoded copy.

    Acquisition publishes pixels without encoding or writing them to disk.
    HTTP encoding takes place outside the sample lock, and only one encoder
    may run at a time. Identity/version fencing never returns replacement
    pixels for an older request.
    """
    def __init__(self, *, clock=time.monotonic, maximum_age=2.):
        self.clock, self.maximum_age = clock, maximum_age
        self.identity = uuid4().hex
        self._lock, self._encoder = Lock(), Lock()
        self._frame = None
        self._version = 0
        self._connected = True
        self._reason = None
        self._observation_sequence = None
        self._observation_healthy = None
        self._encoded = None

    def publish(self, frame):
        # Synthetic/replay images are not presented as native game captures.
        if not isinstance(frame, NativeFrame):
            return
        if len(frame.rgb) > 64 * 1024 * 1024:
            raise ValueError("live_frame_exceeds_pixel_budget")
        with self._lock:
            self._frame = frame
            self._version += 1
            self._encoded = None
            self._observation_sequence = self._observation_healthy = None

    def observe(self, snapshot, sample):
        with self._lock:
            frame = self._frame
            if (frame is not None and sample is frame
                    and snapshot.acquisition_sequence == frame.acquisition_sequence
                    and snapshot.session_epoch == frame.session_epoch):
                self._observation_sequence = snapshot.sequence
                self._observation_healthy = snapshot.healthy

    def disconnect(self, reason):
        with self._lock:
            self._connected, self._reason = False, reason

    def snapshot(self):
        with self._lock:
            frame, version = self._frame, self._version
            connected, reason = self._connected, self._reason
            sequence, healthy = self._observation_sequence, self._observation_healthy
        age = self.clock() - frame.acquired_at if frame else None
        fresh = bool(connected and age is not None and 0 <= age < self.maximum_age)
        return {"available": frame is not None,
            "identity": self.identity if frame else None, "version": version if frame else None,
            "image_url": f"/api/live-image?identity={self.identity}&version={version}" if frame else None,
            "status": "current" if fresh else "last_known" if frame else "waiting",
            "fresh": fresh, "connected": connected,
            "reason": reason or (None if fresh else "waiting_for_native_frame" if not frame else "capture_stale"),
            "acquired_at": frame.acquired_at if frame else None, "age_seconds": age,
            "acquisition_sequence": frame.acquisition_sequence if frame else None,
            "observation_sequence": sequence, "observation_healthy": healthy,
            "frame_size": list(frame.frame_size) if frame else None,
            "geometry": plain(frame.geometry) if frame else None,
            "source_id": frame.source_id if frame else None,
            "session_epoch": frame.session_epoch if frame else None,
            "layout": frame.layout if frame else None,
            "capture_started_at": frame.capture_started_at if frame else None,
            "capture_completed_at": frame.capture_completed_at if frame else None,
            "frame_produced_at": frame.frame_produced_at if frame else None}

    def image(self, identity, version):
        def selected():
            with self._lock:
                if self._frame is None:
                    raise LiveImageError(404, "live_image_unavailable")
                if identity != self.identity or version != self._version:
                    raise LiveImageError(409, "live_image_replaced")
                return self._frame, self._encoded
        frame, encoded = selected()
        if encoded is not None:
            return encoded, "image/jpeg"
        if not self._encoder.acquire(blocking=False):
            raise LiveImageError(503, "live_image_encoder_busy")
        try:
            frame, encoded = selected()
            if encoded is None:
                buffer = BytesIO()
                frame.to_image().save(buffer, format="JPEG", quality=85)
                encoded = buffer.getvalue()
                if len(encoded) > 16 * 1024 * 1024:
                    raise LiveImageError(413, "live_image_exceeds_encoded_budget")
                with self._lock:
                    if self._frame is not frame or version != self._version:
                        raise LiveImageError(409, "live_image_replaced")
                    self._encoded = encoded
            return encoded, "image/jpeg"
        finally:
            self._encoder.release()


class LiveFrameSource:
    def __init__(self, source, frames):
        self.source, self.frames = source, frames

    def next_sample(self, stop_event):
        try:
            sample = self.source.next_sample(stop_event)
        except Exception as exc:
            self.frames.disconnect(f"source_failure: {type(exc).__name__}: {exc}")
            raise
        if sample is not None:
            self.frames.publish(sample)
        else:
            self.frames.disconnect("capture_stream_ended")
        return sample

    def close(self):
        close = getattr(self.source, "close", None)
        if callable(close):
            close()


class LiveFrameDiagnostics:
    def __init__(self, diagnostic, frames):
        self.diagnostic, self.frames = diagnostic, frames

    def observation(self, snapshot, events, sample, timings):
        self.frames.observe(snapshot, sample)
        return self.diagnostic.observation(snapshot, events, sample, timings)
