"""Shared game observation feed for indicators, planners, and optional features.

Reading this feed never queries an action controller or creates an assessment.
The immutable SnapshotStore is the in-process decision input; HTTP is a view.
"""
from collections.abc import Mapping
import math
import time

from .artifacts import plain
from .live_frame import LiveFrameStore
from .state import SnapshotStore

MAX_IMAGE_AGE_SECONDS = 2.0
SHIP_UNITS = {"speed": "m/s", "hull_current": "hp", "hull_max": "hp",
              "energy_current": "energy", "energy_max": "energy"}


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _ship_reading_reason(name, telemetry):
    """Reject one instrument without discarding unrelated ship instruments."""
    reasons = telemetry.get("reasons")
    reasons = reasons if isinstance(reasons, dict) else {}
    instrument = name.split("_", 1)[0]
    reader_reason = reasons.get("layout") or reasons.get(instrument)
    if reader_reason:
        return reader_reason
    if name == "speed":
        numeric = _number(telemetry.get(name))
        return None if numeric is not None and numeric >= 0 else "invalid_numeric_value"
    current, maximum = (telemetry.get(instrument + suffix) for suffix in ("_current", "_max"))
    if current is None or maximum is None:
        return "complete_ratio_not_verified"
    current, maximum = _number(current), _number(maximum)
    if current is None or maximum is None or current < 0 or maximum < 0:
        return "invalid_numeric_value"
    if maximum == 0:
        return "nonpositive_maximum"
    if current > maximum:
        return "current_exceeds_maximum"
    return None


def _game_state_ship(raw, age, errors):
    """Display projection only; never supplies the action-calibrated fields.

    The aggregate reader's validity belongs to this image. Individual ship
    instruments may still be unknown, and ratios must have both sides.
    """
    raw_fields = raw.get("fields")
    raw_fields = raw_fields if isinstance(raw_fields, dict) else {}
    field = raw_fields.get("ship_telemetry")
    missing = not isinstance(field, dict)
    field = field if isinstance(field, dict) else {}
    telemetry = field.get("value")
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    common_reason = (
        "startup_status_unavailable" if "startup" in errors else
        "actions_status_unavailable" if "actions" in errors else
        "current_image_unavailable" if not raw else
        "field_missing" if missing else
        "image_unhealthy" if raw.get("healthy") is not True else
        "image_age_unknown" if age is None else
        "image_stale" if not 0 <= age < MAX_IMAGE_AGE_SECONDS else
        "recognition_rejected" if field.get("valid") is not True else
        "field_stale" if field.get("fresh") is not True else
        "not_from_current_image" if _number(raw.get("acquired_at")) is None
            or _number(field.get("observed_at")) != raw.get("acquired_at") else
        "display_telemetry_required" if telemetry.get("display_only") is not True else None)
    ship = {}
    for name, unit in SHIP_UNITS.items():
        reason = common_reason or _ship_reading_reason(name, telemetry)
        ship[name] = {"value": telemetry.get(name) if reason is None else None,
                      "usable": reason is None, "reason": reason,
                      "unit": unit, "display_only": True}
    return ship


def snapshot_payload(snapshot, now):
    """Serialize the shared snapshot without evaluating a feature's readiness."""
    if snapshot is None:
        return None
    fields = {name: {"value": plain(field.value) if field.valid else None,
        "valid": field.valid, "reason": field.reason, "observed_at": field.observed_at,
        "fresh": 0 <= now-field.observed_at < MAX_IMAGE_AGE_SECONDS}
        for name, field in snapshot.fields.items()}
    result = {"sequence": snapshot.sequence, "acquisition_sequence": snapshot.acquisition_sequence,
        "session_epoch": snapshot.session_epoch, "acquired_at": snapshot.acquired_at,
        "age_seconds": now-snapshot.acquired_at, "healthy": snapshot.healthy,
        "reason": snapshot.reason, "window_id": snapshot.window_id,
        "frame_size": list(snapshot.frame_size), "fields": fields}
    evidence = snapshot.fields.get("ocr_evidence")
    if evidence and evidence.valid and isinstance(evidence.value, Mapping):
        calibration = evidence.value.get("startup_calibration")
        if isinstance(calibration, Mapping):
            result["layout_calibration"] = plain(calibration)
    return result


def game_state_view(snapshot, now):
    """One source frame, per-field validity, and explicit absence of input authority."""
    observation = snapshot_payload(snapshot, now)
    age = observation["age_seconds"] if observation else None
    ship = _game_state_ship(observation or {}, age, {})
    return {"schema_version": "bsgo-game-state-v1", "server_monotonic": now,
        "source": "shared_snapshot_store", "read_only": True, "input_authorized": False,
        "observation": observation, "ship": ship,
        "status": "current" if observation and observation["healthy"]
            and 0 <= age < MAX_IMAGE_AGE_SECONDS else "unavailable"}


class GameObservationFeed:
    """Core-owned latest state and live pixels; mining only receives a reader."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.store = SnapshotStore()
        self.frames = LiveFrameStore()
        self.connected = True

    def latest(self):
        return self.store.latest() if self.connected else None

    def snapshot(self):
        return snapshot_payload(self.latest(), self.clock())

    def game_state_snapshot(self):
        return game_state_view(self.latest(), self.clock())

    def disconnect(self, reason):
        self.connected = False
        self.frames.disconnect(reason)
