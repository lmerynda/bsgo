"""Input-free Manual toggle for scanning the target the human selected.

Unknown scan state can justify one scan intention, never an 'unscanned' claim.
An image-known scanned marker suppresses that track. No nearby discovery,
selection, navigation or scan-effect proof is supplied by this scheduler.
"""
from dataclasses import dataclass
import math

from .state import Snapshot
from .tracking import SelectionTrack


@dataclass(frozen=True)
class ScanToggleDecision:
    action: str
    reason: str
    track_id: int | None = None
    request_id: str | None = None
    scan_state: str = "unknown"


class SelectedScanToggle:
    def __init__(self, scanner_distance=4000):
        if (type(scanner_distance) not in (int, float) or not math.isfinite(scanner_distance)
                or scanner_distance <= 0):
            raise ValueError("scanner_distance")
        self.scanner_distance = scanner_distance
        self.enabled = False
        self._generation = 0
        self._serial = 0
        self._context = None
        self._last_acquired_at = None
        self._last_target = None
        self._last_track = None
        self._suppressed_track = None
        self._gap = None
        self._context_gap = False
        self._pending = None
        self._last_result = None
        self._attempts = self._delivered = 0

    def start(self):
        if not self.enabled:
            self.restart()

    def restart(self):
        if self._pending is not None:
            raise RuntimeError("scan_action_still_pending_cleanup")
        self.enabled = True
        self._generation += 1
        self._context = self._last_target = self._last_track = self._suppressed_track = None
        self._last_acquired_at = None
        self._gap = None
        self._context_gap = False
        self._attempts = self._delivered = 0
        self._last_result = None

    def stop(self):
        # A native executor must still cancel/drain its outstanding scanner job.
        # Do not discard its reservation or claim ownership was released.
        self.enabled = False

    def _blocked(self, reason):
        self._gap = self._gap or reason
        return ScanToggleDecision("blocked", reason)

    def evaluate(self, snapshot, selection_track, *, marker_scan_state="unknown", now):
        if marker_scan_state not in ("unknown", "unscanned", "scanned"):
            raise ValueError("marker_scan_state")
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("now")
        if not self.enabled:
            return ScanToggleDecision("disabled", "selected_scan_toggle_off")
        if (not isinstance(snapshot, Snapshot) or not snapshot.healthy
                or not 0 <= now-snapshot.acquired_at < 2):
            return self._blocked("fresh_healthy_image_required")
        if self._context is None:
            self._context = snapshot.context
        elif snapshot.context != self._context:
            self._context_gap = True
            return self._blocked("capture_context_changed_restart_required")
        # Observe continuously even while the executor is scanning. A fresh
        # frame after a missing interval does not itself restore continuity.
        if (self._last_acquired_at is not None
                and not 0 <= snapshot.acquired_at - self._last_acquired_at <= 2):
            self._gap = self._gap or "selected_image_continuity_interval_lost"
        self._last_acquired_at = snapshot.acquired_at

        def field(name):
            value = snapshot.fields.get(name)
            if (value is None or not value.valid or value.observed_at != snapshot.acquired_at
                    or not 0 <= now-value.observed_at < 2):
                from .stationary_scan import stationary_scan_values
                scanner = stationary_scan_values(snapshot, now)
                if scanner is not None and name in scanner:
                    return scanner[name]
                raise ValueError("invalid_or_stale_" + name)
            return value.value

        try:
            selected = field("asteroid_selected")
            if selected is False:
                if not self._context_gap:
                    self._last_target = self._last_track = self._suppressed_track = self._gap = None
                return ScanToggleDecision("wait", "observed_selected_target_loss")
            if selected is not True:
                raise ValueError("selected_asteroid_image_required")
            signature, portrait = field("target_identity"), field("target_portrait")
            if (not isinstance(signature, str) or not signature or not isinstance(portrait, tuple)
                    or len(portrait) != 144
                    or any(type(v) is not int or not 0 <= v <= 7 for v in portrait)):
                raise ValueError("selected_image_signature_required")
            if (not isinstance(selection_track, SelectionTrack)
                    or type(selection_track.track_id) is not int or selection_track.track_id < 1
                    or selection_track.continuity != "confirmed"
                    or selection_track.context != snapshot.context
                    or selection_track.last_evidence_at != snapshot.acquired_at
                    or type(selection_track.established_at) not in (int, float)
                    or not math.isfinite(selection_track.established_at)
                    or not selection_track.established_at <= snapshot.acquired_at
                    or selection_track.signature != signature
                    or not isinstance(selection_track.portrait, tuple)
                    or len(selection_track.portrait) != 144
                    or any(type(v) is not int or not 0 <= v <= 7 for v in selection_track.portrait)
                    or sum(abs(a-b) for a,b in zip(selection_track.portrait, portrait))/144 > .75):
                raise ValueError("fresh_confirmed_local_selection_track_required")
            target = (signature, portrait)
            changed = bool(self._last_target and (signature != self._last_target[0]
                or sum(abs(a-b) for a,b in zip(portrait, self._last_target[1]))/144 > .75))
            if changed and not self._context_gap:
                self._gap = None
                self._suppressed_track = None
            elif self._last_track is not None and self._last_track != selection_track.track_id:
                return self._blocked("new_track_without_observed_target_change_restart_required")
            if self._gap:
                return ScanToggleDecision("blocked", "continuity_gap_restart_or_observed_target_change_required")
            self._last_target, self._last_track = target, selection_track.track_id
            speed, distance = field("speed"), field("distance")
            hull, maximum = field("hull_current"), field("hull_max")
            for name, value in (("speed", speed), ("distance", distance),
                                ("hull_current", hull), ("hull_max", maximum)):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("invalid_numeric_" + name)
            if speed < 0 or distance <= 0 or hull <= 0 or maximum <= 0 or hull > maximum:
                raise ValueError("invalid_selected_ship_or_range_values")
            if speed != 0:
                return ScanToggleDecision("wait", "stationary_ship_required", selection_track.track_id)
            if hull != maximum:
                return ScanToggleDecision("wait", "full_hull_required", selection_track.track_id)
            if distance > self.scanner_distance:
                return ScanToggleDecision("wait", "selected_target_outside_scanner_range", selection_track.track_id)
        except ValueError as error:
            return self._blocked(str(error))
        if self._pending is not None:
            return ScanToggleDecision("wait", "scanner_action_pending_cleanup",
                                      self._pending["track_id"], self._pending["request_id"])
        if marker_scan_state == "scanned":
            self._suppressed_track = selection_track.track_id
            return ScanToggleDecision("wait", "selected_marker_image_known_scanned",
                                      selection_track.track_id, scan_state="scanned")
        if self._suppressed_track == selection_track.track_id:
            return ScanToggleDecision("wait", "selected_track_already_attempted_or_image_scanned",
                                      selection_track.track_id, scan_state=marker_scan_state)
        self._serial += 1
        self._attempts += 1
        request_id = f"selected-scan-{self._generation}-{self._serial}"
        self._suppressed_track = selection_track.track_id
        self._pending = {"track_id": selection_track.track_id, "request_id": request_id}
        return ScanToggleDecision("scan", "one_scan_for_selected_unknown_state" if marker_scan_state == "unknown"
                                  else "one_scan_for_image_reviewed_unscanned_state",
                                  selection_track.track_id, request_id, marker_scan_state)

    def record_result(self, request_id, *, delivered, scan_effect_verified=False):
        if (not self._pending or self._pending["request_id"] != request_id
                or type(delivered) is not bool or type(scan_effect_verified) is not bool):
            raise ValueError("matching_pending_scan_result_required")
        # This input-free scheduler has no image proof of an effect. The caller's
        # boolean is never promoted into evidence; classification is separate.
        self._last_result = {**self._pending, "delivered": delivered,
                             "scan_effect_verified": False,
                             "reason": "scan_delivery_recorded_effect_unverified"}
        self._delivered += int(delivered)
        self._pending = None

    def status(self):
        return {"enabled": self.enabled, "scope": "human_selected_target_only",
                "nearby_discovery_supported": False, "native_input_owned": False,
                "continuity_blocker": self._gap, "pending": dict(self._pending) if self._pending else None,
                "suppressed_track_id": self._suppressed_track,
                "attempts": self._attempts, "delivered": self._delivered,
                "last_result": dict(self._last_result) if self._last_result else None}
