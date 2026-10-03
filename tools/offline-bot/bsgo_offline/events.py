"""Gameplay evidence, separate from lossy diagnostic logging."""
from dataclasses import dataclass
from typing import Mapping

from .state import ObservationContext, Snapshot, freeze, _time


@dataclass(frozen=True)
class EventEvidence:
    kind: str
    observed_at: float
    payload: Mapping

    def __post_init__(self):
        _time(self.observed_at, "event observed_at")
        object.__setattr__(self, "payload", freeze(self.payload))


@dataclass(frozen=True)
class ObservedEvent:
    sequence: int
    context: ObservationContext
    acquisition_sequence: int
    observed_at: float
    kind: str
    payload: Mapping

    def __post_init__(self):
        _time(self.observed_at, "event observed_at")
        object.__setattr__(self, "payload", freeze(self.payload))


@dataclass(frozen=True)
class EventBatch:
    events: tuple[ObservedEvent, ...]
    next_cursor: int
    oldest_sequence: int
    gap: bool


@dataclass(frozen=True)
class ObservationView:
    snapshot: Snapshot | None
    events: EventBatch


def current_field(snapshot, name, now, max_age=2.0):
    """Reject stale/invalid fields; a publication timestamp is not new evidence."""
    _time(now, "now")
    if (snapshot is None or not snapshot.healthy
            or not snapshot.acquired_at <= snapshot.published_at <= now
            or not 0 <= now - snapshot.acquired_at <= max_age):
        raise ValueError("observation_unavailable")
    field = snapshot.fields.get(name)
    if (field is None or not field.valid or field.value is None
            or field.observed_at > snapshot.acquired_at
            or not 0 <= now - field.observed_at <= max_age):
        raise ValueError("field_unavailable:" + name)
    return field.value


class EventDetector:
    """One producer derives transitions; invalid evidence breaks continuity."""

    def __init__(self):
        self.context = None
        self.scan_marker = None
        self.selected = None
        self.selection_revision = None
        self.inventory = None
        self.inventory_conflict = None

    def update(self, snapshot):
        if snapshot.context != self.context:
            self.__init__()
            self.context = snapshot.context
        def value(name):
            try:
                return current_field(snapshot, name, snapshot.published_at)
            except ValueError:
                return None
        events = []
        conflict = value("inventory_conflict")
        if conflict is True and self.inventory_conflict is not True:
            events.append(EventEvidence("inventory_conflict", snapshot.acquired_at, {}))
        self.inventory_conflict = conflict
        selected = value("asteroid_selected")
        revision = value("selection_revision")
        if self.selected is True and selected is False:
            events.append(EventEvidence("selected_target_disappeared", snapshot.acquired_at,
                                        {"selection_revision": self.selection_revision}))
        if (revision is not None and self.selection_revision is not None
                and revision != self.selection_revision):
            events.append(EventEvidence("selection_changed", snapshot.acquired_at,
                                        {"selection_revision": revision}))
        scan = value("scan_result")
        if isinstance(scan, Mapping) and scan.get("scan_id") is not None:
            marker = (revision, scan["scan_id"])
            if marker != self.scan_marker:
                # Field-level recognition must explicitly supply all required values.
                if (scan.get("resource") in ("Water", "Tylium", "empty")
                        and type(scan.get("eligible")) is bool):
                    events.append(EventEvidence("scan_result", snapshot.fields["scan_result"].observed_at,
                        {**scan, "selection_revision": revision}))
                    self.scan_marker = marker
        inventory = value("inventory")
        if (isinstance(inventory, Mapping) and inventory
                and all(k in ("Water", "Tylium") and type(v) is int and v >= 0
                        for k, v in inventory.items())):
            if self.inventory is not None:
                for resource, amount in inventory.items():
                    previous = self.inventory.get(resource)
                    if previous is not None and amount != previous:
                        events.append(EventEvidence("resource_change", snapshot.fields["inventory"].observed_at,
                            {"resource": resource, "before": previous, "after": amount,
                             "delta": amount - previous, "selection_revision": revision}))
            self.inventory = inventory
        else:
            self.inventory = None
        self.selected, self.selection_revision = selected, revision
        return tuple(events)
