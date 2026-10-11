"""Versioned, read-only projection of the cockpit's current controller data."""
from __future__ import annotations

from datetime import datetime, timezone
import math

SCHEMA_VERSION = "bsgo-observation-v1"
MAX_IMAGE_AGE_SECONDS = 2.0
UNITS = {"distance": "m", "speed": "m/s", "hull_current": "hp", "hull_max": "hp",
         "target_current_hp": "hp", "target_max_hp": "hp"}
ASSESSMENTS = {"scan_selected": ["safe_stopped_scene"],
               "auto_scan_selected": ["safe_stopped_scene"],
               "approach_current_target": ["clear_corridor", "aligned_heading",
                                           "safe_stopped_scene", "flight_point"]}
DISPLAY_FIELDS = ("distance", "speed", "asteroid_selected", "target_identity",
                  "hull_current", "hull_max", "target_current_hp", "target_max_hp")
SHIP_UNITS = {"speed": "m/s", "hull_current": "hp", "hull_max": "hp",
              "energy_current": "energy", "energy_max": "energy"}


def safe_json(value):
    """Keep unknown/nonfinite numbers JSON null, including retained evidence."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(item) for item in value]
    return value


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _semantic_reason(name, value, fields):
    if name == "selected_scan_resource":
        if value not in ("Water", "Titanium", "Tylium", "empty"):
            return "unsupported_resource"
        marker = fields.get("selected_marker_scan_state", {})
        resource = fields.get(name, {})
        if (marker.get("valid") is not True or marker.get("fresh") is not True
                or marker.get("observed_at") != resource.get("observed_at")
                or _semantic_reason("selected_marker_scan_state", marker.get("value"), fields)):
            return "selected_scan_appearance_unverified"
    if name == "selected_marker_scan_state":
        if value != "scanned":
            return "unsupported_scan_state"
        selected = fields.get("asteroid_selected", {})
        marker = fields.get(name, {})
        if (selected.get("value") is not True or selected.get("valid") is not True
                or selected.get("fresh") is not True
                or selected.get("observed_at") != marker.get("observed_at")):
            return "selected_asteroid_unverified"
    if name in UNITS:
        numeric = _number(value)
        if numeric is None or numeric < 0:
            return "invalid_numeric_value"
        if name in ("hull_max", "target_max_hp") and numeric == 0:
            return "nonpositive_maximum"
        maximum_name = {"hull_current": "hull_max", "target_current_hp": "target_max_hp"}.get(name)
        maximum = fields.get(maximum_name, {}) if maximum_name else {}
        limit = _number(maximum.get("value"))
        if maximum.get("valid") is True and limit is not None and numeric > limit:
            return "current_exceeds_maximum"
    return None


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


def observation_snapshot(startup, mode, actions, *, collected_at, started_at,
                         actions_collected_at=None, source_errors=None):
    """Normalize one collection; no history lookup, focus, input or start request.

    Providers publish independently, so this reports its collection interval
    rather than claiming an atomic snapshot across them.
    """
    startup, mode, actions = startup or {}, mode or {}, actions or {}
    errors = source_errors or {}
    connected = startup.get("phase") == "connected"
    control = actions.get("action_control") or startup.get("action_control") or {}
    ownership = actions.get("ownership") or {}
    raw = actions.get("current_observation") if connected else None
    raw = raw if isinstance(raw, dict) else {}
    age = _number(raw.get("age_seconds"))
    if age is not None and actions_collected_at is not None:
        age += max(0, collected_at - actions_collected_at)
    fresh_image = bool(raw and raw.get("healthy") is True and age is not None
                       and 0 <= age < MAX_IMAGE_AGE_SECONDS)
    raw_fields = raw.get("fields") or {}
    fields = {}
    for name in dict.fromkeys((*DISPLAY_FIELDS, *raw_fields)):
        field = raw_fields.get(name)
        missing = not isinstance(field, dict)
        field = field if isinstance(field, dict) else {}
        reason = ("current_image_unavailable" if not raw else
                  "field_missing" if missing else
                  "image_unhealthy" if raw.get("healthy") is not True else
                  "image_age_unknown" if age is None else
                  "image_stale" if not 0 <= age < MAX_IMAGE_AGE_SECONDS else
                  "recognition_rejected" if field.get("valid") is not True else
                  "field_stale" if field.get("fresh") is not True else
                  "not_from_current_image" if field.get("observed_at") != raw.get("acquired_at") else
                  "unsupported_selected_marker_frame_geometry" if name in ("selected_marker_scan_state", "selected_scan_resource")
                  and raw.get("frame_size") != [2560, 1440] else
                  _semantic_reason(name, field.get("value"), raw_fields))
        fields[name] = {"value": field.get("value") if reason is None else None,
                        "usable": reason is None, "reason": reason,
                        "recognition_valid": field.get("valid") is True,
                        "reader_reason": field.get("reason"),
                        "observed_at": field.get("observed_at"), "unit": UNITS.get(name)}
    issue_count = sum(not field["usable"] for field in fields.values()) if raw else None
    owner_known = bool(actions.get("attached_controller")
                       and "owner" in ownership and "actions" not in errors)
    owner = ownership.get("owner")
    input_owner = (owner or ("human" if connected else "unknown")) if owner_known else "unknown"
    routine_name = owner if owner_known else None
    raw_routines = actions.get("routines") or {}
    if not routine_name and raw_routines.get("auto_scan_selected", {}).get("active") is True:
        routine_name = "auto_scan_selected"
    active = raw_routines.get(routine_name, {}) if routine_name else {}
    context = actions.get("assessment_context") or {}
    expires_at = _number(context.get("expires_at"))
    reading = lambda name: fields.get(name, {}).get("value")
    compatible = (all(fields.get(name, {}).get("usable") is True for name in DISPLAY_FIELDS
                      if name != "target_current_hp")
                  and reading("asteroid_selected") is True and reading("speed") == 0
                  and _number(reading("distance")) is not None and reading("distance") > 0
                  and isinstance(reading("target_identity"), str) and bool(reading("target_identity"))
                  and reading("hull_current") == reading("hull_max"))
    context_fresh = bool(fresh_image and compatible and context.get("token") and context.get("context_key") and expires_at is not None
                         and collected_at < expires_at)
    blockers = []
    if errors:
        blockers.extend(f"{name}_status_unavailable" for name in errors)
    if not connected:
        blockers.append(startup.get("reason") or "game_not_connected")
    if control.get("enabled") is not True:
        blockers.append(control.get("pause_reason") or "manual_actions_not_enabled")
    if actions.get("paused") is True:
        blockers.append(actions.get("pause_reason") or "manual_actions_paused")
    if mode.get("pending") is True:
        blockers.append("mode_handover_pending")
    if mode.get("actual_mode") != "manual":
        blockers.append("manual_mode_required")
    if not fresh_image:
        blockers.append("current_image_unavailable_or_stale")
    if ownership.get("state") in ("stopping", "faulted"):
        blockers.append("input_cleanup_unresolved")
    if active:
        phase = active.get("phase") or active.get("state") or "active"
        processing = active.get("processing")
        waiting = active.get("waiting_for")
        next_step = active.get("expected_next_step")
    else:
        phase = ("status_unavailable" if errors else
                 startup.get("phase", "unknown") if not connected else
                 "paused" if control.get("enabled") is not True else "idle")
        processing = None
        waiting = blockers[0] if blockers else "explicit_routine_request_and_assessment"
        next_step = ("restore current controller status" if errors else
                     "restore game connection, then read fresh state" if not connected else
                     "wait for explicit Manual action enable" if control.get("enabled") is not True else
                     "wait for a fresh image" if not fresh_image else
                     "choose a routine and assess its current scene before an explicit Start")
    routines = {}
    for name, item in raw_routines.items():
        reasons = list(blockers)
        if item.get("reason"):
            reasons.append(item["reason"])
        point = context.get("wheel_point")
        point_known = (isinstance(point, (list, tuple)) and len(point) == 2
                       and all(_number(value) is not None and value >= 0 for value in point))
        if name == "approach_current_target" and not point_known:
            reasons.append("configured_flight_point_required")
        if not context_fresh:
            reasons.append("fresh_assessment_context_required")
        machine_ready = bool(not errors and connected and mode.get("actual_mode") == "manual"
                             and mode.get("pending") is not True and control.get("enabled") is True
                             and actions.get("paused") is False and item.get("available") is True
                             and item.get("ready") is True and context_fresh
                             and not owner and not any(row.get("active") is True for row in raw_routines.values())
                             and (name != "approach_current_target" or point_known))
        routines[name] = {key: value for key, value in item.items() if key not in ("ready", "start_allowed")}
        routines[name].update({"machine_ready": machine_ready,
                          "blockers": list(dict.fromkeys(reasons)),
                          "required_assessments": ASSESSMENTS.get(name, []),
                          "assessments_verified_by_api": False})
    status = ("unavailable" if not raw or "actions" in errors else
              "degraded" if errors or not fresh_image or issue_count else "ok")
    return safe_json({
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "server_monotonic": collected_at, "status": status, "source_errors": errors,
        "collection": {"started_monotonic": started_at, "finished_monotonic": collected_at,
                       "atomic": False, "source": "current_controller", "history_used": False},
        "connection": {"phase": startup.get("phase", "unknown"), "reason": startup.get("reason"),
                       "instance_id": startup.get("instance_id"), "game": startup.get("game"),
                       "retry_available": startup.get("retry_available") is True},
        "control": {"actual_mode": mode.get("actual_mode", "unknown"),
                    "requested_mode": mode.get("requested_mode"), "mode_pending": mode.get("pending"),
                    "autopilot_available": mode.get("autopilot_available") is True,
                    "actions_enabled": control.get("enabled"), "paused": control.get("paused"),
                    "pause_reason": control.get("pause_reason"), "input_owner": input_owner,
                    "ownership": ownership if owner_known else None,
                    "input_delivery_enabled": actions.get("app_control_enabled"),
                    "routine_decisions_enabled": actions.get("routine_decisions_enabled"),
                    "mission_decisions_owner": mode.get("mission_decisions_owner", "unknown")},
        "observation": {"available": bool(raw), "healthy": raw.get("healthy"), "fresh": fresh_image,
                        "sequence": raw.get("sequence"), "window_id": raw.get("window_id"),
                        "acquired_at": raw.get("acquired_at"), "age_seconds": age,
                        "max_age_seconds": MAX_IMAGE_AGE_SECONDS,
                        "valid_for_seconds": max(0, MAX_IMAGE_AGE_SECONDS - age) if fresh_image else 0,
                        "fields": fields, "issue_count": issue_count},
        "game_state": {"ship": _game_state_ship(raw, age, errors)},
        "state_machine": {"routine": routine_name, "phase": phase, "processing": processing,
                          "waiting_for": waiting, "expected_next_step": next_step,
                          "blockers": list(dict.fromkeys(blockers))},
        "routines": routines,
        "assessment_context": ({key: context.get(key) for key in (
            "context_key", "observation_sequence", "acquired_at", "expires_at", "window_id",
            "target_identity", "wheel_point", "flight_point_description", "view_assurance")}
            | {"remaining_seconds": max(0, expires_at - collected_at),
               "human_assessments_verified": False}) if context_fresh else None,
        "evidence": {key: actions.get(key) for key in (
            "last_input_intent", "last_input_delivery", "last_confirmed_effect")}
            | {"input_delivery_proves_game_effect": False},
        "synthetic": startup.get("synthetic") is True or actions.get("synthetic_demo") is True,
        "capabilities": {"read_only": True, "radar_interpretation": False,
                         "nearby_discovery": False, "history_in_live_observation": False},
        "links": {"schema": "/api/observation/schema", "startup": "/api/startup",
                  "mode": "/api/control-mode", "actions": "/api/manual-actions",
                  "history": "/api/status", "runs": "/api/runs",
                  "retained_image": "/api/image", "failure_sidecar": "/api/sidecar"},
    })


OBSERVATION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "BSGO current observation v1",
    "description": "Read-only cockpit data. Unknown values are null; no historical fallback."
                   " Providers are sampled independently; collection.atomic is false."
                   " game_state contains display-only readings independent of asteroid action calibration;"
                   " routine readiness uses observation.fields only.",
    "type": "object",
    "required": ["schema_version", "generated_at_utc", "server_monotonic", "status", "source_errors",
                 "collection", "connection", "control", "observation", "state_machine", "routines",
                 "assessment_context", "evidence", "synthetic", "capabilities", "links"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "generated_at_utc": {"type": "string", "format": "date-time"},
        "server_monotonic": {"type": "number"},
        "status": {"enum": ["ok", "degraded", "unavailable"]},
        "source_errors": {"type": "object"},
        "collection": {"type": "object", "properties": {
            "atomic": {"const": False}, "history_used": {"const": False}}},
        "connection": {"type": "object"}, "control": {"type": "object"},
        "observation": {"type": "object", "required": ["fresh", "fields", "valid_for_seconds"],
                        "properties": {"fresh": {"type": "boolean"},
                            "fields": {"type": "object", "additionalProperties": {"type": "object",
                                "required": ["value", "usable", "reason", "recognition_valid", "reader_reason", "observed_at", "unit"],
                                "properties": {"usable": {"type": "boolean"},
                                               "reason": {"type": ["string", "null"]}}},
                            }, "valid_for_seconds": {"type": "number", "minimum": 0}}},
        "game_state": {"type": "object", "required": ["ship"], "properties": {
            "ship": {"type": "object", "required": list(SHIP_UNITS), "properties": {
                name: {"type": "object", "required": ["value", "usable", "reason", "unit", "display_only"],
                       "properties": {"value": {"type": ["number", "null"]},
                                      "usable": {"type": "boolean"},
                                      "reason": {"type": ["string", "null"]},
                                      "unit": {"const": unit}, "display_only": {"const": True}}}
                for name, unit in SHIP_UNITS.items()}}}},
        "state_machine": {"type": "object", "required": ["routine", "phase", "processing",
                            "waiting_for", "expected_next_step", "blockers"]},
        "routines": {"type": "object"}, "assessment_context": {"type": ["object", "null"]},
        "evidence": {"type": "object", "properties": {"input_delivery_proves_game_effect": {"const": False}}},
        "synthetic": {"type": "boolean"}, "capabilities": {"type": "object"}, "links": {"type": "object"},
    },
}
