"""Read-only readiness assessment; diagnostics never grant input permission."""
from dataclasses import dataclass
import math

from .events import current_field
from .execution import Binding


CONTROL_FIELDS = ("speed", "owned", "asteroid_selected", "target_identity", "target_portrait")
CYCLE_FIELDS = (*CONTROL_FIELDS, "distance", "selection_revision", "scan_result",
                "mining_active", "inventory", "inventory_conflict")


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    state: str  # ready, blocked, unknown
    reason: str


@dataclass(frozen=True)
class PreflightReport:
    observation_sequence: int | None
    ready: bool
    checks: tuple[ReadinessCheck, ...]

    @property
    def blockers(self):
        return tuple(check for check in self.checks if check.state != "ready")


def observation_health(snapshot, now):
    """Keep pipeline health, usable fields and unknown ownership distinct."""
    fresh = bool(snapshot and snapshot.healthy
        and snapshot.acquired_at <= snapshot.published_at <= now
        and 0 <= now - snapshot.acquired_at <= 2)
    usable, rejections = [], {}
    for name in dict.fromkeys((*CYCLE_FIELDS, *(snapshot.fields if snapshot else ()))):
        try:
            current_field(snapshot, name, now)
            usable.append(name)
        except ValueError as error:
            field = snapshot.fields.get(name) if snapshot else None
            rejections[name] = (field.reason if fresh and field and not field.valid and field.reason
                                else str(error))
    owned = None
    if "owned" in usable:
        value = snapshot.fields["owned"].value
        owned = value if type(value) is bool else None
        if owned is None:
            rejections["owned"] = "invalid_ownership_type"
            usable.remove("owned")
    return fresh, all(name in usable for name in CONTROL_FIELDS), owned, tuple(usable), rejections


def assess_preflight(snapshot, now, *, route=None, equipment=None, bindings=None,
                     track=None, capabilities=None, event_gap=False):
    """List every independent blocker without changing tracking or queuing input.

    A recognizer must explicitly declare its validated fields. A missing future
    scan result is not proof that scan recognition is unsupported (or supported).
    """
    checks = []
    def add(name, condition, reason, *, unknown=False):
        checks.append(ReadinessCheck(name, "ready" if condition else
                                     "unknown" if unknown else "blocked",
                                     "ok" if condition else reason))
    def value(name):
        try:
            return current_field(snapshot, name, now)
        except ValueError:
            return None
    fresh, _, owned, _, rejections = observation_health(snapshot, now)
    add("observation", fresh, "observation_unavailable", unknown=snapshot is None)
    add("event_history", not event_gap, "observation_event_gap")
    add("ownership", owned is True, "ownership_lost" if owned is False else
        rejections.get("owned", "ownership_unknown"), unknown=owned is None)
    speed = value("speed")
    speed_valid = type(speed) in (int, float) and math.isfinite(speed) and speed >= 0
    add("stopped", speed_valid and speed == 0, "preflight_requires_stop" if speed_valid
        else rejections.get("speed", "invalid_speed"), unknown=speed is None)
    selected, identity, portrait = (value(name) for name in
        ("asteroid_selected", "target_identity", "target_portrait"))
    selection_ok = (selected is True and isinstance(identity, str) and bool(identity)
        and isinstance(portrait, tuple) and len(portrait) == 144
        and all(type(v) is int and 0 <= v <= 7 for v in portrait))
    if track is not None:
        selection_ok = selection_ok and track.continuity == "confirmed" and track.context == snapshot.context
    add("selection", selection_ok, "selection_unavailable", unknown=selected is None)
    distance = value("distance")
    add("distance", type(distance) in (int, float) and math.isfinite(distance) and distance >= 0,
        rejections.get("distance", "invalid_distance"), unknown=distance is None)
    revision = value("selection_revision")
    revision_ok = type(revision) is int and revision >= 0
    add("selection_revision", revision_ok, rejections.get("selection_revision", "invalid_selection_revision"),
        unknown=revision is None)
    established = track.established_at if track else (snapshot.acquired_at if snapshot else now)
    route_ok = bool(route and snapshot and selection_ok and revision_ok and route.clear
        and route.context == snapshot.context and route.selection_revision == revision
        and route.assessed_at <= now < route.expires_at and route.assessed_at >= established - 2
        and (route.source != "synthetic" or snapshot.window_id.startswith("synthetic:")))
    add("route", route_ok, "route_evidence_unavailable", unknown=route is None)
    equipment = equipment or {}
    add("equipment", equipment.get("configured") is True and equipment.get("resource") in ("Water", "Tylium"),
        "equipment_unavailable")
    for operation in ("maximum_throttle", "idle_throttle", "scan", "start_mining"):
        binding = (bindings or {}).get(operation)
        valid = isinstance(binding, Binding) and bool(binding.key)
        native_mismatch = bool(valid and binding.synthetic_only and snapshot
                               and not snapshot.window_id.startswith("synthetic:"))
        add("binding:" + operation, valid and not native_mismatch,
            "synthetic_binding_on_other_surface" if native_mismatch else "missing_binding")
    for name in CYCLE_FIELDS:
        supported = (capabilities or {}).get(name)
        add("recognizer:" + name, supported is True,
            "recognizer_not_validated" if supported is False else "recognizer_capability_unreported",
            unknown=supported is None)
    return PreflightReport(snapshot.sequence if snapshot else None,
                           all(check.state == "ready" for check in checks), tuple(checks))
