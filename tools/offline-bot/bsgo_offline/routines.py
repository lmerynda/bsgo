"""Pure, interruptible routines. Updates perform no I/O or waiting."""
from dataclasses import dataclass, field
import math
from typing import Mapping

from .events import current_field
from .execution import ActionIntent
from .policy import ApproachPolicy
from .state import ObservationContext, freeze


@dataclass(frozen=True)
class RoutineContext:
    observation: ObservationContext
    track_id: int | None
    generation: int
    event_cursor: int
    selection_revision: int | None = None


@dataclass(frozen=True)
class RoutineUpdate:
    status: str = "running"
    reason: str = "waiting_for_evidence"
    progress: Mapping = field(default_factory=dict)
    intents: tuple[ActionIntent, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "progress", freeze(self.progress))
        object.__setattr__(self, "intents", tuple(self.intents))


class Routine:
    name = "routine"

    def __init__(self, timeout=10.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("routine_timeout")
        self.timeout = timeout
        self.context = None
        self.started_at = None
        self._result = None
        self._emitted = False

    def start(self, context, now):
        if self.context is not None:
            raise RuntimeError("routine_already_started")
        self.context, self.started_at = context, now
        return RoutineUpdate(reason="started")

    def cancel(self, reason, now):
        self._result = RoutineUpdate("cancelled", reason)
        return self._result

    def _intent(self, operation, now, track=True, parameters=None):
        c = self.context
        return ActionIntent(f"{c.generation}:{self.name}:{operation}", c.generation,
            c.observation, c.track_id if track else None, operation, now, now + 2.0,
            c.event_cursor, parameters or {})

    def _check(self, view, statuses, now, *, allow_event_gap=False):
        if self._result:
            return self._result
        if now < self.started_at or now - self.started_at >= self.timeout:
            return RoutineUpdate("failed", self.name + "_timeout")
        if view.snapshot is None or view.snapshot.context != self.context.observation:
            return RoutineUpdate("failed", "context_changed")
        if view.events.gap and not allow_event_gap:
            return RoutineUpdate("failed", "observation_event_gap")
        for status in statuses:
            if (status.action_id.startswith(f"{self.context.generation}:{self.name}:")
                    and status.state in ("rejected", "expired", "cancelled")):
                return RoutineUpdate("failed", "action_" + status.state,
                                     {"action_reason": status.reason})
        return None

    def _events(self, view, kind):
        return tuple(e for e in view.events.events
            if e.sequence > self.context.event_cursor and e.context == self.context.observation
            and e.observed_at > self.started_at and e.kind == kind)


class ApproachRoutine(Routine):
    name = "approach"

    def __init__(self, policy=None, timeout=90):
        super().__init__(timeout)
        self.policy = policy or ApproachPolicy()

    def update(self, view, action_statuses, now):
        failed = self._check(view, action_statuses, now)
        if failed:
            return failed
        try:
            self.policy.validate(view.snapshot, now)
            due = (self.policy.last is None or
                   view.snapshot.acquired_at - self.policy.last.acquired_at >= 2.0)
            if not due:
                return RoutineUpdate(progress={"phase": self.policy.phase})
            action = self.policy.decide(view.snapshot, now)
            intents = ()
            if action in ("accelerate", "brake"):
                operation = "maximum_throttle" if action == "accelerate" else "idle_throttle"
                parameters = {"minimum_distance": self.policy.brake_at + 250} if action == "accelerate" else {}
                intents = (self._intent(operation, now, parameters=parameters),)
            result = RoutineUpdate("succeeded" if action == "complete" else "running",
                "fresh_stop_observed" if action == "complete" else action,
                {"phase": self.policy.phase, "distance": self.policy.last.distance,
                 "speed": self.policy.last.speed}, intents)
            if action == "complete":
                self._result = result
            return result
        except ValueError as error:
            return RoutineUpdate("failed", "approach_evidence_rejected", {"detail": str(error)})


class ScanRoutine(Routine):
    name = "scan"

    def update(self, view, action_statuses, now):
        failed = self._check(view, action_statuses, now)
        if failed:
            return failed
        if not self._emitted:
            self._emitted = True
            self.requested_at = now
            return RoutineUpdate(reason="scan_requested", intents=(self._intent("scan", now),))
        for event in self._events(view, "scan_result"):
            if (event.observed_at > self.requested_at
                    and event.payload.get("selection_revision") == self.context.selection_revision):
                self._result = RoutineUpdate("succeeded", "scan_observed",
                                             {"scan": event.payload, "event_sequence": event.sequence})
                return self._result
        return RoutineUpdate()


class StopRoutine(Routine):
    name = "verify_stop"

    def __init__(self, after, timeout=4):
        super().__init__(timeout)
        self.after = after

    def update(self, view, action_statuses, now):
        failed = self._check(view, action_statuses, now, allow_event_gap=True)
        if failed:
            return failed
        try:
            speed = current_field(view.snapshot, "speed", now)
            field = view.snapshot.fields["speed"]
            if (type(speed) in (int, float) and speed == 0
                    and view.snapshot.acquired_at > self.after and field.observed_at > self.after):
                self._result = RoutineUpdate("succeeded", "fresh_stop_observed",
                    {"source_sequence": view.snapshot.sequence, "observed_at": field.observed_at})
                return self._result
        except ValueError:
            pass
        return RoutineUpdate()


class MineRoutine(Routine):
    name = "mine"

    def __init__(self, timeout=15):
        super().__init__(timeout)
        self.active_observed_at = None

    def update(self, view, action_statuses, now):
        failed = self._check(view, action_statuses, now)
        if failed:
            return failed
        if not self._emitted:
            self._emitted = True
            self.requested_at = now
            return RoutineUpdate(reason="mining_requested", intents=(self._intent("start_mining", now),))
        try:
            if current_field(view.snapshot, "mining_active", now) is True:
                observed_at = view.snapshot.fields["mining_active"].observed_at
                if observed_at > self.requested_at:
                    self.active_observed_at = observed_at
        except ValueError:
            pass
        if self.active_observed_at is not None:
            for event in self._events(view, "selected_target_disappeared"):
                if (event.observed_at > self.active_observed_at
                        and event.payload.get("selection_revision") == self.context.selection_revision):
                    self._result = RoutineUpdate("succeeded", "depletion_candidate_observed",
                        {"event_sequence": event.sequence, "mining_started_at": self.requested_at})
                    return self._result
        return RoutineUpdate(progress={"activation_observed": self.active_observed_at is not None})


class OutcomeRoutine(Routine):
    name = "confirm_outcome"

    def __init__(self, mining_started_at, resource, timeout=10):
        super().__init__(timeout)
        self.mining_started_at, self.resource = mining_started_at, resource

    def update(self, view, action_statuses, now):
        failed = self._check(view, action_statuses, now)
        if failed:
            return failed
        try:
            if current_field(view.snapshot, "inventory_conflict", now) is not False:
                return RoutineUpdate("failed", "reward_attribution_uncertain")
        except ValueError:
            return RoutineUpdate("failed", "reward_attribution_unavailable")
        if any(e.kind == "inventory_conflict" and e.context == self.context.observation
               and e.sequence > self.context.event_cursor for e in view.events.events):
            return RoutineUpdate("failed", "reward_attribution_uncertain")
        for event in view.events.events:
            if (event.sequence > self.context.event_cursor and event.context == self.context.observation
                    and event.kind == "resource_change" and event.observed_at > self.mining_started_at
                    and 0 <= now - event.observed_at <= self.timeout
                    and event.payload.get("selection_revision") == self.context.selection_revision
                    and event.payload.get("resource") == self.resource
                    and type(event.payload.get("delta")) is int and event.payload["delta"] > 0):
                self._result = RoutineUpdate("succeeded", "reward_observed",
                    {"resource": self.resource, "delta": event.payload["delta"],
                     "event_sequence": event.sequence, "observed_at": event.observed_at})
                return self._result
        return RoutineUpdate()
