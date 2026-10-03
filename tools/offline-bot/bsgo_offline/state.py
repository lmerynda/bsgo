"""Immutable observations and a small, in-memory latest-state mailbox."""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import math
from threading import Condition
from types import MappingProxyType
from typing import Any, Mapping


def freeze(value: Any) -> Any:
    """Copy mutable containers; reject opaque objects with mutable internals."""
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({freeze(key): freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    raise TypeError(f"Unsupported observation value: {type(value).__name__}")


def _time(value: float, name: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite monotonic-clock timestamp")


@dataclass(frozen=True)
class ObservationContext:
    session_epoch: int
    layout: str
    frame_size: tuple[int, int]
    window_id: str = "unknown"
    ownership_generation: int = 0

    def __post_init__(self):
        object.__setattr__(self, "frame_size", tuple(self.frame_size))
        if (type(self.session_epoch) is not int or self.session_epoch < 0
                or type(self.ownership_generation) is not int or self.ownership_generation < 0
                or len(self.frame_size) != 2 or any(type(v) is not int or v < 0 for v in self.frame_size)
                or not isinstance(self.layout, str) or not self.layout
                or not isinstance(self.window_id, str) or not self.window_id):
            raise ValueError("invalid_observation_context")


@dataclass(frozen=True)
class FieldValue:
    value: Any
    observed_at: float
    valid: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        _time(self.observed_at, "observed_at")
        object.__setattr__(self, "value", freeze(self.value) if self.valid else None)


@dataclass(frozen=True)
class Snapshot:
    sequence: int
    session_epoch: int
    acquisition_sequence: int
    acquired_at: float
    published_at: float
    layout: str
    frame_size: tuple[int, int]
    fields: Mapping[str, FieldValue]
    healthy: bool = True
    reason: str | None = None
    window_id: str = "unknown"
    ownership_generation: int = 0
    capture_started_at: float | None = None
    capture_completed_at: float | None = None
    frame_produced_at: float | None = None

    @property
    def context(self) -> ObservationContext:
        return ObservationContext(self.session_epoch, self.layout, self.frame_size,
                                  self.window_id, self.ownership_generation)

    def __post_init__(self) -> None:
        for name in ("sequence", "session_epoch", "acquisition_sequence"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        _time(self.acquired_at, "acquired_at")
        _time(self.published_at, "published_at")
        if type(self.ownership_generation) is not int or self.ownership_generation < 0:
            raise ValueError("ownership_generation must be a nonnegative integer")
        if not isinstance(self.window_id, str) or not self.window_id:
            raise ValueError("window_id must be a nonempty string")
        for name in ("capture_started_at", "capture_completed_at", "frame_produced_at"):
            if getattr(self, name) is not None:
                _time(getattr(self, name), name)
        if (self.capture_started_at is not None and self.capture_completed_at is not None
                and self.capture_started_at > self.capture_completed_at):
            raise ValueError("capture timestamps are out of order")
        size = tuple(self.frame_size)
        if len(size) != 2 or any(not isinstance(v, int) or v < 0 for v in size):
            raise ValueError("frame_size must contain two nonnegative integers")
        copied = {}
        for name, field in self.fields.items():
            if not isinstance(name, str) or not isinstance(field, FieldValue):
                raise TypeError("fields must map string names to FieldValue observations")
            # FieldValue has already recursively frozen its payload.
            copied[name] = field
        object.__setattr__(self, "frame_size", size)
        object.__setattr__(self, "fields", MappingProxyType(copied))


class SnapshotStore:
    """Single publisher, arbitrary consumers; no work occurs while holding a lock."""

    def __init__(self, event_capacity: int = 256) -> None:
        if type(event_capacity) is not int or event_capacity <= 0:
            raise ValueError("event_capacity must be a positive integer")
        self._condition = Condition()
        self._latest: Snapshot | None = None
        self._events = deque(maxlen=event_capacity)
        self._watermark = 0

    def publish(self, snapshot: Snapshot, events=()) -> None:
        from .events import EventEvidence, ObservedEvent
        if not isinstance(snapshot, Snapshot):
            raise TypeError("Only immutable Snapshot observations can be published")
        events = tuple(events)
        if any(not isinstance(event, EventEvidence) for event in events):
            raise TypeError("events must be immutable EventEvidence")
        if any(event.observed_at > snapshot.acquired_at for event in events):
            raise ValueError("event cannot be observed after its source frame")
        with self._condition:
            if self._latest is not None and snapshot.sequence <= self._latest.sequence:
                raise ValueError("Snapshot sequence must strictly increase")
            for event in events:
                self._watermark += 1
                self._events.append(ObservedEvent(self._watermark, snapshot.context,
                    snapshot.acquisition_sequence, event.observed_at, event.kind,
                    event.payload))
            self._latest = snapshot
            self._condition.notify_all()

    def latest(self) -> Snapshot | None:
        with self._condition:
            return self._latest

    def read(self, after_event: int = 0):
        """Read observation and event watermark in one publication transaction."""
        from .events import EventBatch, ObservationView
        if type(after_event) is not int or after_event < 0:
            raise ValueError("event cursor must be a nonnegative integer")
        with self._condition:
            if after_event > self._watermark:
                raise ValueError("event cursor is ahead of this store")
            oldest = self._events[0].sequence if self._events else self._watermark + 1
            batch = EventBatch(tuple(e for e in self._events if e.sequence > after_event),
                               self._watermark, oldest, after_event < oldest - 1)
            return ObservationView(self._latest, batch)

    def wait_for_sequence(self, after: int, timeout: float) -> Snapshot | None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._latest is not None and self._latest.sequence > after,
                timeout,
            )
            return self._latest if ready else None
