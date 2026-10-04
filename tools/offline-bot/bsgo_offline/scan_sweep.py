"""Manual-mode scan toggle planner over a supplied image-verified catalog.

This module emits intentions, never native input. An executor can use the
existing selection/scan probes, but must then supply fresh visual evidence.
The HUD-only probe's HP signature or delivered Z cannot complete a scan.
"""
from dataclasses import dataclass

from .scan_catalog import CandidateObservation, ScanCatalog, overlap, _finite


@dataclass(frozen=True)
class SweepStep:
    action: str
    reason: str
    candidate_id: str | None = None
    x: int | None = None
    y: int | None = None


class ScanSweep:
    def __init__(self, catalog: ScanCatalog, scanner_distance=4000):
        if not isinstance(catalog, ScanCatalog):
            raise TypeError("scan_catalog_required")
        if not _finite(scanner_distance) or scanner_distance <= 0:
            raise ValueError("scanner_distance")
        self.catalog = catalog
        self.scanner_distance = scanner_distance
        self.enabled = False
        self._pending = None
        self._attempted = {}

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled")
        self.enabled = enabled

    def _retire_attempts(self, now):
        active = {entry.candidate_id for entry in self.catalog.entries(now)}
        self._attempted = {key: value for key, value in self._attempted.items() if key in active}

    def _acknowledge(self, action, candidate_id, now):
        self._retire_attempts(now)
        if self._pending != (action, candidate_id):
            raise ValueError(action + "_not_pending")
        if self.catalog.get(candidate_id, now) is None:
            self._pending = None
            raise ValueError("acknowledgment_candidate_context_changed_or_stale")

    def next_step(self, now, *, stationary_verified, cancelled=False):
        self._retire_attempts(now)
        if type(stationary_verified) is not bool or type(cancelled) is not bool:
            raise ValueError("explicit_stationary_and_cancel_state_required")
        if cancelled:
            self.enabled = False
            return SweepStep("disabled", "cancel_requested")
        if not self.enabled:
            return SweepStep("disabled", "scan_toggle_off")
        if not stationary_verified:
            return SweepStep("blocked", "fresh_stopped_scene_required")
        if self._pending:
            if self.catalog.get(self._pending[1], now) is None:
                self._pending = None
                return SweepStep("blocked", "pending_candidate_context_changed_or_stale")
            return SweepStep("observe", "action_in_flight", self._pending[1])
        entries = self.catalog.entries(now)
        if not entries:
            return SweepStep("blocked", "nearby_image_discovery_unsupported_or_catalog_stale")
        candidates = [entry for entry in entries if entry.scan_state == "unscanned"
                      and entry.candidate_id not in self._attempted
                      and (entry.observation.distance is None
                           or entry.observation.distance <= self.scanner_distance)]
        if not candidates:
            if any(entry.candidate_id in self._attempted and entry.scan_state != "scanned"
                   for entry in entries):
                return SweepStep("blocked", "scan_result_unverified")
            if any(entry.scan_state == "unknown" for entry in entries):
                return SweepStep("blocked", "unscanned_image_classification_unsupported")
            return SweepStep("observe", "supplied_catalog_exhausted_discovery_not_proved")
        entry = min(candidates, key=lambda item: item.observation.distance
                    if item.observation.distance is not None else float("inf"))
        if not entry.observation.selected:
            self._pending = ("select", entry.candidate_id)
            return SweepStep("select", "fresh_supplied_unscanned_candidate",
                             entry.candidate_id, *entry.click)
        if entry.observation.distance is None:
            return SweepStep("blocked", "selected_candidate_range_unverified", entry.candidate_id)
        self._pending = ("scan", entry.candidate_id)
        return SweepStep("scan", "fresh_selected_unscanned_within_scanner_range", entry.candidate_id)

    def record_selection(self, candidate_id, evidence: CandidateObservation, *, now):
        self._acknowledge("select", candidate_id, now)
        previous = self.catalog.get(candidate_id, now)
        if (previous is None or not isinstance(evidence, CandidateObservation)
                or not evidence.selected or evidence.context != previous.observation.context
                or evidence.view_revision != previous.observation.view_revision
                or evidence.acquisition_sequence <= previous.observation.acquisition_sequence
                or evidence.visual_signature != previous.observation.visual_signature
                or overlap(evidence.box, previous.observation.box) < .7):
            raise ValueError("selected_candidate_visual_continuity_unverified")
        entries = self.catalog.observe((evidence,), now)
        if entries[0].candidate_id != candidate_id:
            raise ValueError("selected_candidate_continuity_lost")
        self._pending = None

    def record_scan_attempt(self, candidate_id, *, delivery_confirmed, now):
        self._acknowledge("scan", candidate_id, now)
        if type(delivery_confirmed) is not bool:
            raise ValueError("scan_not_pending_or_delivery_unknown")
        self._attempted[candidate_id] = ("delivered_effect_unverified" if delivery_confirmed
                                         else "scan_delivery_failed")
        self._pending = None

    def record_failure(self, candidate_id, reason, *, now):
        if self._pending:
            self._acknowledge(self._pending[0], candidate_id, now)
        if not self._pending or self._pending[1] != candidate_id or not isinstance(reason, str) or not reason:
            raise ValueError("pending_action_and_failure_reason_required")
        self._attempted[candidate_id] = reason
        self._pending = None

    def status(self, now):
        self._retire_attempts(now)
        entries = self.catalog.entries(now)
        return {"enabled": self.enabled, "scope": "supplied_image_verified_catalog",
                "automatic_discovery_supported": False, "native_input_owned": False,
                "pending": self._pending, "attempted": dict(self._attempted),
                "fresh_candidates": len(entries),
                "visually_scanned": sum(row.scan_state == "scanned" for row in entries),
                "scan_image_references": {row.candidate_id: row.scan_image_reference
                                          for row in entries if row.scan_state == "scanned"}}
