"""Pure approach decisions from fresh observations; never emits input itself.

The target identity is the calibrated HUD signature, not a unique game object ID.
The independent portrait signature supplements it but does not prove identity.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .state import Snapshot


class PolicyError(ValueError):
    """The observed evidence does not authorize continuing this approach."""


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Observation:
    distance: float
    speed: float
    acquired_at: float
    acquisition_sequence: int
    target_identity: str
    target_portrait: tuple[int, ...]
    speed_observed_at: float


class ApproachPolicy:
    """Port of the prototype's guarded approach/braking policy.

    Time parameters are seconds on the same monotonic clock as Snapshot timestamps.
    A command to idle throttle does not establish a stop: a later fresh speed
    observation of zero is required. No input calls occur in this class.

    Call at the prototype's approximately two-second decision cadence, even when
    recognition publishes faster. Its conservative per-sample closing guard
    requires more than two metres of progress while moving faster than 5 m/s;
    blindly evaluating every high-rate frame can falsely report a stalled ship.
    The coordinator must still check freshness and identity between decisions.
    """

    def __init__(self, *, brake_at: float = 900, max_speed: float = 44,
                 max_frame_age: float = 2.0, brake_ack: float = 4.0,
                 max_braking: float = 30.0, max_observation_gap: float = 4.0):
        if not _number(brake_at) or not 600 <= brake_at <= 1400:
            raise PolicyError("Brake threshold must be 600..1400 m")
        if any(not _number(value) or value <= 0 for value in
               (max_speed, max_frame_age, brake_ack, max_braking, max_observation_gap)):
            raise PolicyError("Speed and timing bounds must be positive finite numbers")
        self.brake_at = brake_at
        self.max_speed = max_speed
        self.max_frame_age = max_frame_age
        self.brake_ack = brake_ack
        self.max_braking = max_braking
        self.max_observation_gap = max_observation_gap
        self.phase = "initial"
        self.identity: str | None = None
        self.portrait: tuple[int, ...] | None = None
        self.context: tuple[object, ...] | None = None
        self.last: Observation | None = None
        self.brake: Observation | None = None
        self.final: Observation | None = None
        self.brake_samples = 0
        self.brake_acknowledged = False
        self.brake_progress_at = 0.0
        self.minimum_brake_speed = 0.0

    def _observation(self, snapshot: Snapshot, now: float) -> Observation:
        if not _number(now) or not _number(snapshot.acquired_at):
            raise PolicyError("Invalid monotonic observation timestamp")
        if not snapshot.healthy:
            raise PolicyError(f"Unhealthy recognition: {snapshot.reason}")
        if not _number(snapshot.published_at) or not snapshot.acquired_at <= snapshot.published_at <= now:
            raise PolicyError("Invalid snapshot publication timestamp")
        age = now - snapshot.acquired_at
        if age < 0 or age > self.max_frame_age:
            raise PolicyError("Stale HUD observation")
        context = (snapshot.session_epoch, snapshot.layout, tuple(snapshot.frame_size))
        if self.context is not None and context != self.context:
            raise PolicyError("Session, layout, or frame size changed")
        values: dict[str, object] = {}
        for name in ("distance", "speed", "asteroid_selected", "target_identity", "target_portrait"):
            field = snapshot.fields.get(name)
            if field is None or not field.valid or field.value is None:
                raise PolicyError(f"Missing or invalid {name}")
            if (not _number(field.observed_at) or field.observed_at > snapshot.acquired_at
                    or not 0 <= now - field.observed_at <= self.max_frame_age):
                raise PolicyError(f"Stale or invalid {name} observation")
            values[name] = field.value
        distance, speed = values["distance"], values["speed"]
        if values["asteroid_selected"] is not True or not _number(distance) or distance < 0:
            raise PolicyError("Invalid HUD observation")
        if not _number(speed) or not 0 <= speed <= self.max_speed + 0.1:
            raise PolicyError("Invalid speed")
        identity = values["target_identity"]
        if not isinstance(identity, str) or not identity.strip():
            raise PolicyError("Missing visible target identity")
        portrait = values["target_portrait"]
        if (not isinstance(portrait, (list, tuple)) or len(portrait) != 144
                or any(type(value) is not int or not 0 <= value <= 7 for value in portrait)):
            raise PolicyError("Missing visible target portrait")
        if self.identity is not None and identity != self.identity:
            raise PolicyError("Selected target changed")
        if self.portrait is not None:
            difference = sum(abs(left - right) for left, right in zip(self.portrait, portrait)) / 144
            if difference > 0.75:
                raise PolicyError("Selected target portrait changed")
        return Observation(float(distance), float(speed), snapshot.acquired_at,
                           snapshot.acquisition_sequence, identity, tuple(portrait),
                           snapshot.fields["speed"].observed_at)

    def validate(self, snapshot: Snapshot, now: float) -> Observation:
        """Check fresh context/selection at recognition cadence without advancing policy."""
        return self._observation(snapshot, now)

    def decide(self, snapshot: Snapshot, now: float) -> str:
        row = self.validate(snapshot, now)
        if self.phase == "initial":
            if row.speed != 0:
                raise PolicyError("Approach must start stopped")
            if row.distance < self.brake_at + 250:
                raise PolicyError("Initial target too close for full-speed approach")
            self.identity, self.portrait = row.target_identity, row.target_portrait
            self.context = (snapshot.session_epoch, snapshot.layout, tuple(snapshot.frame_size))
            self.last = row
            self.phase = "approaching"
            return "accelerate"
        assert self.last is not None
        previous = self.last
        dt = row.acquired_at - previous.acquired_at
        if dt <= 0 or dt > self.max_observation_gap or row.acquisition_sequence <= previous.acquisition_sequence:
            raise PolicyError("Observation gap exceeded or acquisition repeated")
        if abs(row.distance - previous.distance) > self.max_speed * dt + 30:
            raise PolicyError("Impossible distance change")
        if self.phase == "approaching" and row.distance > previous.distance + 3:
            raise PolicyError("Target is not closing")
        if (self.phase == "approaching" and row.speed > 5 and previous.speed > 5
                and row.distance >= previous.distance - 2):
            raise PolicyError("Approach stalled or tangent")
        if self.phase == "complete":
            if row.speed != 0:
                raise PolicyError("Previously observed stop no longer holds")
            self.last = self.final = row
            return "complete"
        if self.phase == "approaching" and row.distance <= self.brake_at:
            self.last = self.brake = row
            self.phase = "braking"
            self.brake_samples = 0
            self.brake_acknowledged = False
            self.brake_progress_at = row.acquired_at
            self.minimum_brake_speed = row.speed
            return "brake"
        if self.phase == "braking" and row.speed == 0:
            # This is a new acquisition, not an input acknowledgement or cached zero.
            assert self.brake is not None
            if row.speed_observed_at <= self.brake.acquired_at:
                raise PolicyError("Stop requires speed observed after the brake decision")
            self.last = self.final = row
            self.phase = "complete"
            return "complete"
        if self.phase == "braking":
            assert self.brake is not None
            elapsed = row.acquired_at - self.brake.acquired_at
            samples = self.brake_samples + 1
            if row.speed > previous.speed + 0.3:
                raise PolicyError("Braking speed increased; stop input not holding")
            minimum = self.minimum_brake_speed
            progress_at = self.brake_progress_at
            acknowledged = self.brake_acknowledged
            if row.speed <= minimum - 0.5:
                minimum, progress_at, acknowledged = row.speed, row.acquired_at, True
            if not acknowledged and (samples >= 2 or elapsed >= self.brake_ack):
                raise PolicyError("Brake not acknowledged within two samples / four seconds")
            if acknowledged and row.acquired_at - progress_at >= self.brake_ack:
                raise PolicyError("Braking stalled without further deceleration")
            if elapsed >= self.max_braking:
                raise PolicyError("Braking exceeded the time limit without verified zero")
            self.brake_samples = samples
            self.minimum_brake_speed = minimum
            self.brake_progress_at = progress_at
            self.brake_acknowledged = acknowledged
        self.last = row
        return "continue"
