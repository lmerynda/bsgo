"""Semantic action ownership and validation at submission AND delivery."""
from collections import OrderedDict
from dataclasses import dataclass
import math
from threading import RLock
from typing import Mapping

from .events import current_field
from .state import ObservationContext, freeze


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    generation: int
    context: ObservationContext
    track_id: int | None
    operation: str
    created_at: float
    expires_at: float
    event_cursor: int
    parameters: Mapping

    def __post_init__(self):
        if (not self.action_id or type(self.generation) is not int or self.generation < 1
                or not isinstance(self.context, ObservationContext)
                or type(self.event_cursor) is not int or self.event_cursor < 0
                or self.track_id is not None and (type(self.track_id) is not int or self.track_id < 1)
                or any(type(t) not in (int, float) or not math.isfinite(t)
                       for t in (self.created_at, self.expires_at))
                or self.expires_at <= self.created_at):
            raise ValueError("invalid_action_intent")
        object.__setattr__(self, "parameters", freeze(self.parameters))


@dataclass(frozen=True)
class ActionStatus:
    action_id: str
    state: str
    at: float
    reason: str
    request_sequences: tuple[int, ...] = ()


@dataclass(frozen=True)
class Binding:
    key: str
    hold: bool = False
    synthetic_only: bool = False


DEFAULT_BINDINGS = {"maximum_throttle": Binding("PageUp"),
                    "idle_throttle": Binding("PageDown")}
SYNTHETIC_BINDINGS = {**DEFAULT_BINDINGS, "scan": Binding("S", synthetic_only=True),
                      "start_mining": Binding("M", hold=True, synthetic_only=True)}


class InputExecutor:
    def __init__(self, store, backend, tracker, clock, *, bindings=None, capacity=256, permission=None):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("executor_capacity")
        self.store, self.backend, self.tracker, self.clock = store, backend, tracker, clock
        self.bindings = dict(DEFAULT_BINDINGS if bindings is None else bindings)
        self.capacity, self.generation = capacity, 1
        self.permission = permission
        self._session, self._layout = backend.session_id, backend.layout_id
        self._lock = RLock()
        self._intents = OrderedDict()
        self._statuses = OrderedDict()
        self._delivered_down = set()
        self._delivered_records = set()
        backend.set_delivery_contract(self._guard, self._delivered)

    def statuses(self):
        with self._lock:
            return tuple(self._statuses.values())

    def _status(self, intent, state, reason, sequences=()):
        result = ActionStatus(intent.action_id, state, self.clock(), reason, tuple(sequences))
        self._statuses[intent.action_id] = result
        return result

    def _validate(self, intent):
        now = self.clock()
        if not intent.created_at <= now < intent.expires_at:
            return "expired", "action_expired"
        if intent.generation != self.generation or self.backend.canceled:
            return "cancelled", "execution_generation_changed"
        try:
            view = self.store.read(intent.event_cursor)
        except ValueError:
            return "rejected", "invalid_event_cursor"
        snapshot = view.snapshot
        if snapshot is None or snapshot.context != intent.context:
            return "rejected", "context_changed"
        try:
            # Even a valid selection cannot authorize motion from stale speed.
            speed = current_field(snapshot, "speed", now)
            if type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
                return "rejected", "invalid_speed"
            if current_field(snapshot, "owned", now) is not True and "owned" in snapshot.fields:
                return "rejected", "ownership_lost"
        except ValueError as error:
            # Legacy snapshots do not carry explicit ownership; backend guards
            # still apply. New runtime preflight requires the owned field.
            if "owned" in snapshot.fields or str(error) != "field_unavailable:owned":
                return "rejected", str(error)
        if intent.track_id is not None:
            track = self.tracker.track
            if (track is None or track.continuity != "confirmed" or track.track_id != intent.track_id
                    or track.context != intent.context or view.events.gap):
                return "rejected", "selection_dependency_changed"
            try:
                if current_field(snapshot, "asteroid_selected", now) is not True:
                    return "rejected", "selection_lost"
                identity = current_field(snapshot, "target_identity", now)
                portrait = current_field(snapshot, "target_portrait", now)
                revision = snapshot.fields.get("selection_revision")
                if (identity != track.signature or not isinstance(portrait, tuple) or len(portrait) != 144
                        or sum(abs(a-b) for a,b in zip(track.portrait, portrait))/144 > .75
                        or revision is not None and current_field(snapshot, "selection_revision", now) != track.selection_revision):
                    return "rejected", "selection_dependency_changed"
            except (ValueError, TypeError):
                return "rejected", "selection_evidence_unavailable"
            if any(e.context == intent.context and e.kind in
                   ("selected_target_disappeared", "selection_changed") for e in view.events.events):
                return "rejected", "selection_continuity_broken"
        binding = self.bindings.get(intent.operation)
        if not isinstance(binding, Binding):
            return "rejected", "missing_binding"
        if binding.synthetic_only and not snapshot.window_id.startswith("synthetic:"):
            return "rejected", "synthetic_binding_on_other_surface"
        if intent.operation in ("scan", "start_mining") and speed != 0:
            return "rejected", "mining_requires_stop" if intent.operation == "start_mining" else "scan_requires_stop"
        if intent.operation == "maximum_throttle":
            if speed != 0:
                return "rejected", "approach_requires_stop"
            try:
                distance = current_field(snapshot, "distance", now)
                if type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0:
                    return "rejected", "invalid_distance"
                minimum = intent.parameters.get("minimum_distance", 0)
                if type(minimum) not in (int, float) or not math.isfinite(minimum) or distance < minimum:
                    return "rejected", "approach_start_distance_changed"
            except ValueError as error:
                return "rejected", str(error)
        if self.permission:
            reason = self.permission(intent, snapshot, now)
            if reason:
                return "rejected", reason
        return None

    def submit(self, intent):
        with self._lock:
            if intent.action_id in self._statuses:
                if self._intents[intent.action_id] != intent:
                    return ActionStatus(intent.action_id, "rejected", self.clock(), "action_id_collision")
                return self._statuses[intent.action_id]
            if not intent.action_id.startswith(str(intent.generation) + ":"):
                return ActionStatus(intent.action_id, "rejected", self.clock(), "action_generation_prefix")
            # One mission has a small bounded number of actions. Fail closed
            # before a lifecycle can lose the evidence needed to deduplicate.
            if len(self._intents) >= self.capacity:
                return ActionStatus(intent.action_id, "rejected", self.clock(), "action_capacity")
            self._intents[intent.action_id] = intent
            rejected = self._validate(intent)
            if rejected:
                return self._status(intent, *rejected)
            binding = self.bindings[intent.operation]
            metadata = {"action_id": intent.action_id, "generation": intent.generation,
                        "operation": intent.operation, "expires_at": intent.expires_at}
            try:
                call = self.backend.key_down if binding.hold else self.backend.press_key
                records = call(binding.key, reason=intent.operation, metadata=metadata,
                               expected_session=self._session, expected_layout=self._layout)
                if not isinstance(records, tuple):
                    records = (records,)
                return self._status(intent, "requested", "recorded_request", (r.sequence for r in records))
            except Exception as error:
                return self._status(intent, "rejected", str(error))

    def _guard(self, record):
        with self._lock:
            action_id = dict(record.metadata).get("action_id")
            intent = self._intents.get(action_id)
            if intent is None:
                return False
            status = self._statuses.get(action_id)
            binding = self.bindings.get(intent.operation)
            if (status is None or record.sequence not in status.request_sequences
                    or record.sequence in self._delivered_records
                    or binding is None or record.key != binding.key
                    or record.kind not in ("key_down", "key_up")):
                return False
            # Expiring a press between down and up must never leave a held key.
            if record.kind == "key_up" and action_id in self._delivered_down:
                return True
            rejected = self._validate(intent)
            if rejected:
                self._status(intent, *rejected, self._statuses[action_id].request_sequences)
                return False
            return True

    def _delivered(self, record, outcome):
        with self._lock:
            action_id = dict(record.metadata).get("action_id")
            intent = self._intents.get(action_id)
            if intent is None:
                return
            if outcome in ("injected", "received"):
                self._delivered_records.add(record.sequence)
                if record.kind == "key_down":
                    self._delivered_down.add(action_id)
                    self._status(intent, "dispatched", outcome, self._statuses[action_id].request_sequences)
                elif record.kind == "key_up":
                    self._delivered_down.discard(action_id)
            elif record.kind != "key_up" and self._statuses[action_id].state == "requested":
                self._status(intent, "rejected", outcome, self._statuses[action_id].request_sequences)

    def cancel(self, reason):
        with self._lock:
            self.generation += 1
            for intent in self._intents.values():
                if self._statuses[intent.action_id].state == "requested":
                    self._status(intent, "cancelled", reason, self._statuses[intent.action_id].request_sequences)
            self.backend.cancel(reason=reason)

    def new_generation(self):
        """Caller first drains cleanup and establishes a new backend session."""
        with self._lock:
            if self.backend.canceled:
                raise RuntimeError("backend_still_cancelled")
            self.generation += 1
            self._session, self._layout = self.backend.session_id, self.backend.layout_id
            # A generation prefix is mandatory in routine action IDs. Dropping
            # histories here keeps resume memory bounded without allowing replay.
            self._intents.clear()
            self._statuses.clear()
            self._delivered_down.clear()
            self._delivered_records.clear()
