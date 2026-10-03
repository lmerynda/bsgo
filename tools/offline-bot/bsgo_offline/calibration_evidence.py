"""Evaluate a bounded motion/braking probe from input delivery and image evidence.

An inserted key event is not a game effect. The certificate below attests only
that fresh HUD speed changed after the delivered commands in one captured
context. It grants no mission or mining permission by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from .input import ActionRecord
from .native_input import DeliveryRecord
from .state import ObservationContext, Snapshot, freeze


@dataclass(frozen=True)
class ProbeCertificate:
    scope: str
    result: str
    session_id: str
    context: ObservationContext
    profile: str
    source_sha256: Mapping[str, str]
    run_id: str | None
    issued_at: float
    expires_at: float
    baseline_observation_sequences: tuple[int, int]
    moving_observation_sequence: int
    stopped_observation_sequences: tuple[int, int]
    acceleration_action_id: str
    brake_action_id: str
    acceleration_request_sequences: tuple[int, int]
    brake_request_sequences: tuple[int, int]
    acceleration_delivered_at: float
    brake_delivered_at: float
    baseline_speed: tuple[float, float]
    moving_speed: float
    stopped_speed: tuple[float, float]


@dataclass(frozen=True)
class ProbeAssessment:
    state: str  # pending, blocked, proved
    reason: str
    certificate: ProbeCertificate | None = None


@dataclass(frozen=True)
class _Delivery:
    operation: str
    action_id: str
    request: ActionRecord
    result: DeliveryRecord


class MotionBrakeProbe:
    """Single-use bounded evaluator. All timestamps share a monotonic clock.

    Feed each acquired snapshot and each dispatched key event. In native scope,
    only complete Win32 stream insertions count as delivered. The evaluator
    checks two independent stopped images before acceleration, a positive speed
    image after PageUp, and two stopped images after PageDown. It never infers
    effect from requested or inserted input alone.
    """

    def __init__(self, *, context: ObservationContext, session_id: str, profile: str,
                 source_sha256: Mapping[str, str], scope: str, started_at: float,
                 expires_at: float, run_id: str | None = None,
                 max_observation_age: float = 2., response_timeout: float = 5.,
                 certificate_ttl: float = 5., max_observations: int = 64):
        if not isinstance(context, ObservationContext) or scope not in ("native", "synthetic"):
            raise ValueError("invalid_probe_context_or_scope")
        if not session_id or not profile or profile != context.layout:
            raise ValueError("invalid_probe_session_or_profile")
        for name, value in (("started_at", started_at), ("expires_at", expires_at),
                            ("max_observation_age", max_observation_age),
                            ("response_timeout", response_timeout), ("certificate_ttl", certificate_ttl)):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(name)
        if (expires_at <= started_at or max_observation_age <= 0 or response_timeout <= 0
                or certificate_ttl <= 0 or type(max_observations) is not int or max_observations < 5):
            raise ValueError("invalid_probe_bounds")
        hashes = dict(source_sha256)
        for key in ("hud-reader/hud_reader.py", "hud-reader/templates.json",
                    "hud-reader/restart-calibration.json"):
            digest = hashes.get(key)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("missing_or_invalid_calibration_source_hash:" + key)
        self.context, self.session_id, self.profile = context, session_id, profile
        self.source_sha256, self.scope, self.run_id = freeze(hashes), scope, run_id
        self.started_at, self.expires_at = started_at, expires_at
        self.max_observation_age, self.response_timeout = max_observation_age, response_timeout
        self.certificate_ttl, self.max_observations = certificate_ttl, max_observations
        self._snapshots: list[Snapshot] = []
        self._deliveries: list[_Delivery] = []
        self._failure: str | None = None

    def _block(self, reason):
        self._failure = self._failure or reason

    def observe(self, snapshot: Snapshot):
        if self._failure:
            return
        if not isinstance(snapshot, Snapshot):
            self._block("invalid_observation_type")
            return
        if len(self._snapshots) >= self.max_observations:
            self._block("observation_budget_exceeded")
            return
        if snapshot.context != self.context:
            self._block("capture_context_changed")
            return
        if self.scope == "native" and (snapshot.window_id.startswith(("saved:", "synthetic:"))
            or snapshot.capture_started_at is None or snapshot.capture_completed_at is None):
            self._block("native_image_provenance_missing")
            return
        if self.scope == "synthetic" and not snapshot.window_id.startswith("synthetic:"):
            self._block("synthetic_image_provenance_missing")
            return
        if (not snapshot.healthy or snapshot.reason or snapshot.acquired_at < self.started_at
                or snapshot.published_at < snapshot.acquired_at
                or snapshot.published_at - snapshot.acquired_at > self.max_observation_age):
            self._block("unhealthy_or_late_observation")
            return
        field = snapshot.fields.get("speed")
        if (field is None or not field.valid or type(field.value) not in (int, float)
                or not math.isfinite(field.value) or field.value < 0
                or abs(field.observed_at - snapshot.acquired_at) > 1e-6):
            self._block("speed_not_freshly_read_from_image")
            return
        if self._snapshots and (snapshot.sequence <= self._snapshots[-1].sequence
            or snapshot.acquisition_sequence <= self._snapshots[-1].acquisition_sequence
            or snapshot.acquired_at <= self._snapshots[-1].acquired_at):
            self._block("observation_sequence_not_advancing")
            return
        self._snapshots.append(snapshot)

    def record_delivery(self, operation: str, action_id: str, request: ActionRecord,
                        delivery: DeliveryRecord):
        if self._failure:
            return
        if operation not in ("accelerate", "brake") or not action_id:
            self._block("invalid_probe_operation")
            return
        if not isinstance(request, ActionRecord) or not isinstance(delivery, DeliveryRecord):
            self._block("invalid_delivery_type")
            return
        if len(self._deliveries) >= 4:
            self._block("unexpected_extra_input_delivery")
            return
        if request.session_id != self.session_id or request.layout_id != self.profile:
            self._block("input_session_or_profile_changed")
            return
        expected_key = "PageUp" if operation == "accelerate" else "PageDown"
        expected_kind = "key_down" if len(self._deliveries) % 2 == 0 else "key_up"
        expected_operation = "accelerate" if len(self._deliveries) < 2 else "brake"
        if (operation != expected_operation or request.kind != expected_kind
                or delivery.kind != expected_kind or request.key != expected_key):
            self._block("unexpected_probe_input_sequence")
            return
        if self._deliveries and self._deliveries[-1].action_id != action_id and expected_kind == "key_up":
            self._block("input_action_id_changed_within_command")
            return
        if operation == "brake" and self._deliveries and action_id == self._deliveries[0].action_id:
            self._block("input_action_id_reused")
            return
        if (request.sequence != delivery.request_sequence or delivery.at < request.at
                or request.at < self.started_at or delivery.at > self.expires_at
                or self._deliveries and (request.sequence <= self._deliveries[-1].request.sequence
                    or request.at < self._deliveries[-1].result.at)):
            self._block("input_delivery_order_or_identity_invalid")
            return
        expected_outcome = "injected" if self.scope == "native" else "received"
        expected_evidence = ("windows_input_stream_insertion" if self.scope == "native"
                             else "synthetic_receiver_reported_effect")
        if (delivery.outcome != expected_outcome or delivery.evidence != expected_evidence
                or delivery.requested_count < 1 or delivery.inserted_count != delivery.requested_count):
            self._block("input_not_completely_delivered")
            return
        self._deliveries.append(_Delivery(operation, action_id, request, delivery))

    def evaluate(self, now: float) -> ProbeAssessment:
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("now")
        if self._failure:
            return ProbeAssessment("blocked", self._failure)
        if now >= self.expires_at:
            return ProbeAssessment("blocked", "probe_expired")
        if now < self.started_at:
            return ProbeAssessment("blocked", "probe_not_started")
        if (any(s.acquired_at > now or s.published_at > now for s in self._snapshots)
                or any(item.result.at > now for item in self._deliveries)):
            return ProbeAssessment("blocked", "future_dated_evidence")
        if len(self._deliveries) < 2:
            return ProbeAssessment("pending", "waiting_for_acceleration_delivery")
        accelerate = self._deliveries[:2]
        baseline = [s for s in self._snapshots if s.published_at < accelerate[0].request.at]
        baseline = baseline[-2:]
        if (len(baseline) != 2 or any(s.fields["speed"].value != 0 for s in baseline)
                or accelerate[0].request.at - baseline[-1].acquired_at > self.max_observation_age):
            return ProbeAssessment("blocked", "stopped_baseline_not_observed")
        movement = next((s for s in self._snapshots
                         if s.acquired_at > accelerate[-1].result.at
                         and s.fields["speed"].value > 0
                         and s.acquired_at - accelerate[-1].result.at <= self.response_timeout), None)
        if movement is None:
            if len(self._deliveries) > 2:
                return ProbeAssessment("blocked", "brake_before_observed_motion")
            if now - accelerate[-1].result.at > self.response_timeout:
                return ProbeAssessment("blocked", "motion_response_timeout")
            return ProbeAssessment("pending", "waiting_for_image_observed_motion")
        if len(self._deliveries) < 4:
            return ProbeAssessment("pending", "waiting_for_brake_delivery")
        brake = self._deliveries[2:4]
        if brake[0].request.at <= movement.published_at:
            return ProbeAssessment("blocked", "brake_before_motion_observation_published")
        after_brake = [s for s in self._snapshots if s.acquired_at > brake[-1].result.at]
        saw_zero = False
        for snapshot in after_brake:
            if snapshot.fields["speed"].value == 0:
                saw_zero = True
            elif saw_zero:
                return ProbeAssessment("blocked", "speed_reappeared_after_stop")
        stopped = [s for s in after_brake
                   if s.fields["speed"].value == 0
                   and s.acquired_at - brake[-1].result.at <= self.response_timeout]
        if len(stopped) < 2 or after_brake[-2:] != stopped[-2:]:
            if now - brake[-1].result.at > self.response_timeout:
                return ProbeAssessment("blocked", "stop_response_timeout")
            return ProbeAssessment("pending", "waiting_for_image_observed_stop")
        final = stopped[:2]
        if now - final[-1].acquired_at > self.max_observation_age:
            return ProbeAssessment("blocked", "final_observation_stale")
        certificate = ProbeCertificate(
            self.scope, "observed_motion_and_stop", self.session_id, self.context,
            self.profile, self.source_sha256, self.run_id, now,
            min(self.expires_at, now + self.certificate_ttl),
            (baseline[0].sequence, baseline[1].sequence), movement.sequence,
            (final[0].sequence, final[1].sequence), accelerate[0].action_id,
            brake[0].action_id,
            (accelerate[0].request.sequence, accelerate[1].request.sequence),
            (brake[0].request.sequence, brake[1].request.sequence),
            accelerate[-1].result.at, brake[-1].result.at,
            (baseline[0].fields["speed"].value, baseline[1].fields["speed"].value),
            movement.fields["speed"].value,
            (final[0].fields["speed"].value, final[1].fields["speed"].value))
        return ProbeAssessment("proved", "image_observed_motion_and_stop", certificate)
