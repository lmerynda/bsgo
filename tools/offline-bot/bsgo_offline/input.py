"""Bounded, recording-only input requests for an offline receiver.

This module never calls an operating-system input API. A record proves that a
request was recorded; only the separate receiver can report a simulated effect.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import threading
import time
from typing import Callable, Mapping


class InputRejected(RuntimeError):
    """An input request was rejected and the session was canceled."""


class InputOverflow(InputRejected):
    """The finite ordinary-action receiver backlog is full."""


_KEYS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") + (
    "Space", "Enter", "Escape", "Tab", "Backspace", "Delete", "Insert",
    "Home", "End", "PageUp", "PageDown", "Left", "Right", "Up", "Down",
    "Shift", "Control", "Alt",
) + tuple(f"F{i}" for i in range(1, 13))
_KEY_LOOKUP = {key.casefold(): key for key in _KEYS}
_BUTTONS = frozenset(("left", "right", "middle"))
Scalar = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ActionRecord:
    sequence: int
    at: float
    kind: str
    session_id: str
    layout_id: str
    coordinate_space: str = "frame"
    key: str | None = None
    button: str | None = None
    x: int | None = None
    y: int | None = None
    reason: str = ""
    metadata: tuple[tuple[str, Scalar], ...] = ()
    evidence: str = "recorded_request"


class RecordingInputBackend:
    """Serialize requests with rolling recent history and a bounded receiver queue.

    ``record_capacity`` retains that many recent requests in memory; evicting old
    history never cancels a session. The independent receiver queue still limits
    pending ordinary requests and reserves one release per supported key plus a
    two-record PageDown cleanup stop. Sequence numbers are lifetime monotonic.

    An optional ``journal`` must implement nonblocking ``submit(event) -> bool``.
    Submission runs outside the action lock; failures are telemetry gaps and
    never control failures. Its full trace is separate from recent-history export.

    Cancellation discards pending ordinary receiver requests, retains history,
    and records releases for requested or dequeued holds. Releases and dequeuing
    are not confirmation that the receiver applied an effect. A receiver must
    recheck cancellation before applying an ordinary record already dequeued.
    A new session requires an empty queue and no outstanding dequeued holds.
    """

    supported_keys = _KEYS

    def __init__(self, *, session_id: str = "offline-session",
                 layout_id: str = "2560x1440", width: int = 2560,
                 height: int = 1440, queue_capacity: int = 256,
                 record_capacity: int = 64000,
                 clock: Callable[[], float] = time.monotonic, journal=None):
        self._validate_identifier(session_id, "session_id")
        self._validate_identifier(layout_id, "layout_id")
        for name, value in (("width", width), ("height", height),
                            ("queue_capacity", queue_capacity),
                            ("record_capacity", record_capacity)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._condition = threading.Condition(threading.RLock())
        self._session_id, self._layout_id = session_id, layout_id
        self._width, self._height = width, height
        self._queue_capacity, self._record_capacity = queue_capacity, record_capacity
        self._clock = clock
        if journal is not None and not callable(getattr(journal, "submit", None)):
            raise ValueError("journal must implement nonblocking submit(event)")
        self._journal = journal
        self._journal_flush_lock = threading.Lock()
        self._journal_pending_capacity = queue_capacity + len(self.supported_keys) + 32
        self._journal_pending: deque[dict] = deque()
        self._submit_failures = 0
        self._logging_gaps = 0
        self._queue: deque[ActionRecord] = deque()
        self._records: deque[ActionRecord] = deque(maxlen=record_capacity)
        self._held: set[str] = set()
        self._dequeued_held: set[str] = set()
        self._discarded_sequences: set[int] = set()
        self._dequeued_sequences: set[int] = set()
        self._cleanup_stop: tuple[ActionRecord, ActionRecord] | None = None
        self._sequence = 0
        self._total_records = 0
        self._canceled = False
        self._cancel_reason: str | None = None
        self._delivery_guard = None
        self._delivery_observer = None

    def set_delivery_contract(self, guard, observer=None) -> None:
        """One executor owns nonblocking validation and delivery feedback.

        Callbacks run outside the queue lock. Receivers check immediately before
        applying ordinary input. Releases after a delivered down remain allowed.
        """
        with self._condition:
            if self._delivery_guard is not None:
                raise RuntimeError("input executor already owns this backend")
            self._delivery_guard, self._delivery_observer = guard, observer

    def delivery_allowed(self, record) -> bool:
        cleanup = dict(record.metadata).get("cleanup") is True
        with self._condition:
            guard, canceled = self._delivery_guard, self._canceled
        if cleanup:
            return True
        if canceled:
            return False
        if guard is None:
            return True
        try:
            return guard(record) is True
        except Exception:
            return False

    def report_delivery(self, record, outcome) -> None:
        with self._condition:
            observer = self._delivery_observer
        if observer:
            observer(record, outcome)

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError(f"{name} must be a nonempty string of at most 128 characters")

    @contextmanager
    def _transaction(self):
        try:
            with self._condition:
                yield
        finally:
            self.flush_journal()

    def _stage_journal_locked(self, event: dict) -> None:
        if self._journal is None:
            return
        if len(self._journal_pending) >= self._journal_pending_capacity:
            self._logging_gaps += 1
            return
        self._journal_pending.append(event)

    def flush_journal(self) -> None:
        """Submit staged events; the sink owns disk writing and shutdown flushing.

        A concurrent submitter never holds up another input operation. A sink
        violating its nonblocking contract can delay its submitting caller but
        cannot hold the backend action lock; staged memory is still bounded.
        """
        if self._journal is None or not self._journal_flush_lock.acquire(blocking=False):
            return
        try:
            # Bound each flush even if producers continually add telemetry.
            remaining = self._journal_pending_capacity
            while remaining:
                with self._condition:
                    count = min(remaining, len(self._journal_pending))
                    batch = tuple(self._journal_pending.popleft() for _ in range(count))
                if not batch:
                    break
                remaining -= len(batch)
                for event in batch:
                    try:
                        submitted = self._journal.submit(event) is True
                    except Exception:
                        submitted = False
                    if not submitted:
                        with self._condition:
                            self._submit_failures += 1
                            self._logging_gaps += 1
        finally:
            self._journal_flush_lock.release()

    def _retains_sequence_locked(self, sequence: int) -> bool:
        # Appending lifetime-consecutive requests leaves a consecutive recent
        # window. Explicit clear removes the whole window, preserving this fact.
        return bool(self._records) and self._records[0].sequence <= sequence <= self._records[-1].sequence

    def _remember_record_locked(self, record: ActionRecord) -> None:
        # Disposition bookkeeping follows each eviction in constant time. It
        # must never scan the complete 64,000-record window per input operation.
        if len(self._records) == self.record_capacity:
            evicted = self._records.popleft()
            self._discarded_sequences.discard(evicted.sequence)
            self._dequeued_sequences.discard(evicted.sequence)
        self._records.append(record)

    def _history_stats_locked(self) -> dict:
        return {"total_records": self._total_records,
                "retained_records": len(self._records),
                # Explicit clear_records is included in records no longer retained.
                "evicted_records": self._total_records - len(self._records),
                "first_retained_sequence": self._records[0].sequence if self._records else None,
                "last_retained_sequence": self._records[-1].sequence if self._records else None,
                "journal_configured": self._journal is not None,
                "submit_failures": self._submit_failures,
                "logging_gaps": self._logging_gaps,
                "pending_journal_events": len(self._journal_pending)}

    def history_stats(self) -> dict:
        """Fresh scalar snapshot; lifetime totals survive clearing recent history."""
        with self._condition:
            return self._history_stats_locked()

    @property
    def pending_requests(self) -> int:
        with self._condition:
            return len(self._queue)

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def queue_capacity(self) -> int:
        return self._queue_capacity

    @property
    def record_capacity(self) -> int:
        return self._record_capacity

    @property
    def session_id(self) -> str:
        with self._condition:
            return self._session_id

    @property
    def layout_id(self) -> str:
        with self._condition:
            return self._layout_id

    @property
    def canceled(self) -> bool:
        with self._condition:
            return self._canceled

    @property
    def cancel_reason(self) -> str | None:
        with self._condition:
            return self._cancel_reason

    @property
    def held_keys(self) -> tuple[str, ...]:
        with self._condition:
            return tuple(sorted(self._held))

    @property
    def discarded_sequences(self) -> tuple[int, ...]:
        """Retained recent requests removed before the receiver could dequeue them."""
        with self._condition:
            return tuple(sorted(self._discarded_sequences))

    def _context(self, expected_session: str | None,
                 expected_layout: str | None) -> None:
        if self._canceled:
            raise InputRejected(f"session canceled: {self._cancel_reason}")
        if expected_session is not None and expected_session != self._session_id:
            raise InputRejected("session mismatch")
        if expected_layout is not None and expected_layout != self._layout_id:
            raise InputRejected("layout mismatch")

    @staticmethod
    def _metadata(metadata: Mapping[str, Scalar] | None) -> tuple[tuple[str, Scalar], ...]:
        items = []
        for name, value in (metadata or {}).items():
            if not isinstance(name, str) or len(name) > 128:
                raise InputRejected("metadata names must be bounded strings")
            if type(value) not in (str, int, float, bool, type(None)):
                raise InputRejected("metadata values must be immutable JSON scalars")
            if isinstance(value, str) and len(value) > 512:
                raise InputRejected("metadata strings exceed 512 characters")
            if type(value) is float and not math.isfinite(value):
                raise InputRejected("metadata numbers must be finite")
            items.append((name, value))
        if len(items) > 32:
            raise InputRejected("metadata exceeds 32 fields")
        return tuple(sorted(items))

    def _append(self, specs: list[dict], reason: str,
                metadata: tuple[tuple[str, Scalar], ...], *, cleanup: bool = False
                ) -> tuple[ActionRecord, ...]:
        # One timestamp and one lock span make a key press an indivisible pair.
        # Preserve the configured clock domain, including virtual replay time.
        # Emergency releases survive a broken clock but annotate the fallback.
        try:
            at = self._clock()
            if type(at) not in (int, float) or not math.isfinite(at):
                raise InputRejected("clock returned an invalid timestamp")
        except Exception:
            if not cleanup:
                raise
            at = time.monotonic()
            metadata += (("clock_fallback", "wall_monotonic"),)
        records = tuple(ActionRecord(self._sequence + i + 1, at,
                                    session_id=self._session_id,
                                    layout_id=self._layout_id,
                                    reason=reason, metadata=metadata, **spec)
                        for i, spec in enumerate(specs))
        self._sequence += len(records)
        self._total_records += len(records)
        for record in records:
            self._remember_record_locked(record)
        self._queue.extend(records)
        if self._journal is not None:
            for record in records:
                self._stage_journal_locked({"event": "input_request", "record": asdict(record)})
        self._condition.notify_all()
        return records

    def _release_locked(self, reason: str) -> tuple[ActionRecord, ...]:
        pending_releases = {record.key for record in self._queue
                            if record.kind == "key_up" and dict(record.metadata).get("cleanup")}
        keys = (self._held | self._dequeued_held) - pending_releases
        specs = [{"kind": "key_up", "key": key} for key in sorted(keys)]
        if not specs:
            self._held.clear()
            return ()
        result = self._append(specs, str(reason)[:512], (("cleanup", True),), cleanup=True)
        self._held.clear()
        return result

    def _abort_locked(self, reason: str) -> tuple[ActionRecord, ...]:
        self._canceled = True
        self._cancel_reason = str(reason)[:512]
        retained = deque()
        for record in self._queue:
            if dict(record.metadata).get("cleanup") is True:
                retained.append(record)
            else:
                if self._retains_sequence_locked(record.sequence):
                    self._discarded_sequences.add(record.sequence)
                self._stage_journal_locked({"event": "input_disposition", "sequence": record.sequence,
                                           "disposition": "discarded_before_receive",
                                           "session_id": record.session_id})
        self._queue = retained
        result = self._release_locked(reason)
        return result

    def _command(self, build: Callable[[], tuple[list[dict], set[str]]], *,
                 reason: str = "", metadata: Mapping[str, Scalar] | None = None,
                 expected_session: str | None = None,
                 expected_layout: str | None = None,
                 **unknown_context) -> tuple[ActionRecord, ...]:
        with self._transaction():
            try:
                self._context(expected_session, expected_layout)
                if unknown_context:
                    raise InputRejected("unsupported context arguments")
                if not isinstance(reason, str) or len(reason) > 512:
                    raise InputRejected("reason must be a string of at most 512 characters")
                frozen_metadata = self._metadata(metadata)
                if dict(frozen_metadata).get("cleanup"):
                    raise InputRejected("cleanup metadata is reserved for dedicated cleanup operations")
                specs, held = build()
                if len(self._queue) + len(specs) > self.queue_capacity:
                    raise InputOverflow("receiver backlog full")
                result = self._append(specs, reason, frozen_metadata)
                self._held = held
                return result
            except Exception as exc:
                self._abort_locked(str(exc))
                if isinstance(exc, InputRejected):
                    raise
                raise InputRejected(str(exc)) from exc

    @staticmethod
    def _key(key: str) -> str:
        normalized = _KEY_LOOKUP.get(key.casefold()) if isinstance(key, str) else None
        if normalized is None:
            raise InputRejected(f"unsupported key: {key!r}")
        return normalized

    def key_down(self, key: str, **context) -> ActionRecord:
        def build():
            normalized = self._key(key)
            if normalized in self._held:
                raise InputRejected(f"key already held: {normalized}")
            return [{"kind": "key_down", "key": normalized}], self._held | {normalized}
        return self._command(build, **context)[0]

    def key_up(self, key: str, **context) -> ActionRecord:
        def build():
            normalized = self._key(key)
            if normalized not in self._held:
                raise InputRejected(f"key is not held: {normalized}")
            return [{"kind": "key_up", "key": normalized}], self._held - {normalized}
        return self._command(build, **context)[0]

    def press_key(self, key: str, **context) -> tuple[ActionRecord, ActionRecord]:
        def build():
            normalized = self._key(key)
            if normalized in self._held:
                raise InputRejected(f"cannot press held key: {normalized}")
            return [{"kind": "key_down", "key": normalized},
                    {"kind": "key_up", "key": normalized}], set(self._held)
        return self._command(build, **context)

    def _point(self, x: int, y: int) -> None:
        if type(x) is not int or type(y) is not int:
            raise InputRejected("frame coordinates must be integers")
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise InputRejected(f"point outside {self.width}x{self.height} frame")

    def move_mouse(self, x: int, y: int, **context) -> ActionRecord:
        def build():
            self._point(x, y)
            return [{"kind": "mouse_move", "x": x, "y": y}], set(self._held)
        return self._command(build, **context)[0]

    def click_mouse(self, x: int, y: int, *, button: str = "left", **context) -> ActionRecord:
        def build():
            self._point(x, y)
            if button not in _BUTTONS:
                raise InputRejected(f"unsupported mouse button: {button!r}")
            return [{"kind": "mouse_click", "button": button, "x": x, "y": y}], set(self._held)
        return self._command(build, **context)[0]

    def release_all(self, *, reason: str = "release all") -> tuple[ActionRecord, ...]:
        with self._transaction():
            return self._release_locked(reason)

    def cancel(self, *, reason: str = "canceled") -> tuple[ActionRecord, ...]:
        with self._transaction():
            return self._abort_locked(reason)

    def request_cleanup_stop(self, key: str = "PageDown", *,
                             reason: str = "cleanup stop",
                             expected_session: str | None = None,
                             expected_layout: str | None = None
                             ) -> tuple[ActionRecord, ActionRecord]:
        """Preempt ordinary work and record one PageDown stop per session.

        This dedicated, idempotent operation is accepted even after cancellation;
        arbitrary keys and clicks cannot bypass cancellation. The receiver still
        must observe any simulated stopped state independently of this request.
        """
        with self._transaction():
            try:
                if self._key(key) != "PageDown":
                    raise InputRejected("only PageDown is permitted as a cleanup stop")
                if not isinstance(reason, str) or len(reason) > 512:
                    raise InputRejected("reason must be a string of at most 512 characters")
                if expected_session is not None and expected_session != self._session_id:
                    raise InputRejected("session mismatch")
                if expected_layout is not None and expected_layout != self._layout_id:
                    raise InputRejected("layout mismatch")
                self._abort_locked(reason)
                if self._cleanup_stop is not None:
                    return self._cleanup_stop
                reserve = len(self.supported_keys) + 2
                if len(self._queue) + 2 > self.queue_capacity + reserve:
                    raise InputOverflow("reserved cleanup backlog full")
                specs = [{"kind": "key_down", "key": "PageDown"},
                         {"kind": "key_up", "key": "PageDown"}]
                self._cleanup_stop = self._append(specs, reason, (("cleanup", True),), cleanup=True)
                return self._cleanup_stop
            except Exception as exc:
                self._abort_locked(str(exc))
                if isinstance(exc, InputRejected):
                    raise
                raise InputRejected(str(exc)) from exc

    def new_session(self, session_id: str, *, layout_id: str | None = None,
                    clear_records: bool = False) -> None:
        """Begin another ownership session; clear_records clears recent memory only."""
        with self._transaction():
            try:
                self._validate_identifier(session_id, "session_id")
                self._validate_identifier(self._layout_id if layout_id is None else layout_id,
                                          "layout_id")
                if self._held or self._queue or self._dequeued_held:
                    raise InputRejected("drain all outstanding requests before starting a new session")
                if session_id == self._session_id:
                    raise InputRejected("new session requires a different session_id")
                self._session_id = session_id
                if layout_id is not None:
                    self._layout_id = layout_id
                if clear_records:
                    self._records.clear()
                    self._discarded_sequences.clear()
                    self._dequeued_sequences.clear()
                self._cleanup_stop = None
                self._canceled = False
                self._cancel_reason = None
            except Exception as exc:
                self._abort_locked(str(exc))
                if isinstance(exc, InputRejected):
                    raise
                raise InputRejected(str(exc)) from exc

    def records(self) -> tuple[ActionRecord, ...]:
        """Immutable recent-history snapshot, not a complete lifetime action log."""
        with self._condition:
            return tuple(self._records)

    def drain(self, limit: int | None = None) -> tuple[ActionRecord, ...]:
        if limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("limit must be a nonnegative integer")
        with self._transaction():
            size = len(self._queue) if limit is None else min(limit, len(self._queue))
            return tuple(self._dequeue_locked() for _ in range(size))

    def _dequeue_locked(self) -> ActionRecord:
        record = self._queue.popleft()
        if self._retains_sequence_locked(record.sequence):
            self._dequeued_sequences.add(record.sequence)
        self._stage_journal_locked({"event": "input_disposition", "sequence": record.sequence,
                                   "disposition": "dequeued_for_receiver",
                                   "session_id": record.session_id})
        if record.kind == "key_down":
            self._dequeued_held.add(record.key)
        elif record.kind == "key_up":
            self._dequeued_held.discard(record.key)
        return record

    def get_action(self, timeout: float | None = None) -> ActionRecord | None:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and nonnegative")
        with self._transaction():
            if not self._condition.wait_for(lambda: bool(self._queue), timeout):
                return None
            return self._dequeue_locked()

    def export_jsonl(self, path: str | Path) -> dict:
        """Export recent history only and path+'.stats.json'; return snapshot stats.

        Evicted requests are available only from a separately configured journal,
        subject to its reported logging gaps. This export is not a lifetime trace.
        """
        # Snapshot under the lock; slow disk or a receiver's logging never holds it.
        self.flush_journal()
        with self._condition:
            snapshot = tuple(self._records)
            discarded = frozenset(self._discarded_sequences)
            dequeued = frozenset(self._dequeued_sequences)
            stats = self._history_stats_locked()
        with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
            for record in snapshot:
                row = asdict(record)
                row["disposition"] = ("discarded_before_receive" if record.sequence in discarded
                                      else "dequeued_for_receiver" if record.sequence in dequeued
                                      else "pending_receiver")
                stream.write(json.dumps(row, allow_nan=False) + "\n")
        Path(str(path) + ".stats.json").write_text(json.dumps({
            "scope": "recent_record_history", "complete_lifetime_trace": False,
            "history_stats": stats}, allow_nan=False, indent=2), encoding="utf-8")
        return stats
