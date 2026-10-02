"""Immutable observations and a small, in-memory latest-state mailbox."""
from __future__ import annotations

from dataclasses import dataclass
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
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite monotonic-clock timestamp")


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

    def __post_init__(self) -> None:
        for name in ("sequence", "session_epoch", "acquisition_sequence"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        _time(self.acquired_at, "acquired_at")
        _time(self.published_at, "published_at")
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

    def __init__(self) -> None:
        self._condition = Condition()
        self._latest: Snapshot | None = None

    def publish(self, snapshot: Snapshot) -> None:
        if not isinstance(snapshot, Snapshot):
            raise TypeError("Only immutable Snapshot observations can be published")
        with self._condition:
            if self._latest is not None and snapshot.sequence <= self._latest.sequence:
                raise ValueError("Snapshot sequence must strictly increase")
            self._latest = snapshot
            self._condition.notify_all()

    def latest(self) -> Snapshot | None:
        with self._condition:
            return self._latest

    def wait_for_sequence(self, after: int, timeout: float) -> Snapshot | None:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._latest is not None and self._latest.sequence > after,
                timeout,
            )
            return self._latest if ready else None
