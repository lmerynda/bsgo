"""One deterministic mining cycle; all input passes through InputExecutor."""
from collections import deque
from dataclasses import dataclass, field
import math
from threading import Event, RLock
import time
from typing import Mapping
from .artifacts import plain

from .events import current_field
from .execution import InputExecutor
from .routines import (RoutineContext, ApproachRoutine, ScanRoutine, StopRoutine,
                       MineRoutine, OutcomeRoutine)
from .state import ObservationContext, freeze
from .tracking import SelectionTracker


@dataclass(frozen=True)
class RouteAssessment:
    context: ObservationContext
    selection_revision: int | None
    assessed_at: float
    expires_at: float
    source: str
    clear: bool = True

    def __post_init__(self):
        if (any(type(t) not in (int, float) or not math.isfinite(t)
                for t in (self.assessed_at, self.expires_at))
                or self.expires_at <= self.assessed_at or not self.source):
            raise ValueError("invalid_route_assessment")


@dataclass(frozen=True)
class RuntimeStatus:
    phase: str
    routine_phase: str
    generation: int
    observation_age: float | None
    recognition_healthy: bool
    owned: bool
    reason: str
    pending_action_ids: tuple[str, ...]
    cleanup: str
    track_id: int | None


@dataclass(frozen=True)
class MissionResult:
    completed: bool
    reason: str
    cleanup: str
    stop_observed: bool
    reward: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "reward", freeze(self.reward))


class MiningCoordinator:
    """Tickable coordinator; run() is only a thin threaded-harness adapter."""

    def __init__(self, store, backend, *, clock=time.monotonic, route=None,
                 equipment=None, bindings=None, cleanup_timeout=30, mission_timeout=120,
                 planner=None, journal=None, history_capacity=64000):
        for name, number in (("cleanup_timeout", cleanup_timeout), ("mission_timeout", mission_timeout)):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError(name)
        if type(history_capacity) is not int or history_capacity < 1:
            raise ValueError("history_capacity")
        self.store, self.backend, self.clock = store, backend, clock
        self.route, self.equipment = route, freeze(equipment or {})
        self.cleanup_timeout, self.mission_timeout = cleanup_timeout, mission_timeout
        self.planner, self.journal = planner, journal
        self.tracker = SelectionTracker()
        self.executor = InputExecutor(store, backend, self.tracker, clock, bindings=bindings,
                                      permission=self._permission)
        self._lock, self._wake = RLock(), Event()
        self.transitions = deque(maxlen=history_capacity)
        self.transitions_total = self.journal_dropped = 0
        self._reset()

    def _reset(self):
        self.phase, self.reason = "preflight", "waiting_for_preflight"
        self.routine = None
        self.context = self.expected_track = None
        self.phase_cursor = self.mining_cursor = 0
        self.started_at = self.clock()
        self.mining_started_at = None
        self.resource = None
        self.result = None
        self.cleanup = "not_requested"
        self._cancel_reason = None
        self._pause = False
        self._moved = False
        self._planner_requested = False
        self._last_view = None
        self._tracker_cursor = self.store.read().events.next_cursor

    def _permission(self, intent, snapshot, now):
        # This pure callback runs at delivery too; it must not acquire the
        # coordinator lock while the dispatcher holds the executor lock.
        if intent.operation == "maximum_throttle":
            route = self.route
            if (route is None or not route.clear or route.context != snapshot.context
                    or not route.assessed_at <= now < route.expires_at):
                return "route_evidence_expired"
        return None

    def _record(self, kind, **data):
        event = freeze({"at": self.clock(), "kind": kind, **data})
        self.transitions.append(event)
        self.transitions_total += 1
        if self.journal:
            try:
                accepted = self.journal.submit({"event": "mining_transition", "record": plain(event)})
            except Exception:
                accepted = False
            if not accepted:
                self.journal_dropped += 1

    def read_status(self):
        with self._lock:
            snapshot = self.store.latest()
            age = self.clock() - snapshot.acquired_at if snapshot else None
            try:
                owned = current_field(snapshot, "owned", self.clock()) is True
            except ValueError:
                owned = False
            return RuntimeStatus(self.phase,
                self.routine.policy.phase if isinstance(self.routine, ApproachRoutine) else self.phase,
                self.executor.generation, age, bool(snapshot and snapshot.healthy), owned,
                self.reason, tuple(s.action_id for s in self.executor.statuses() if s.state == "requested"),
                self.cleanup, self.tracker.track.track_id if self.tracker.track else None)

    def cancel(self, reason="cancelled"):
        with self._lock:
            if self.result is not None:
                return
            self._cancel_reason = reason
            self.executor.cancel(reason)
            self._wake.set()

    def pause(self):
        with self._lock:
            if self.result is None:
                self._pause = True
                self.cancel("paused")

    def resume(self, *, route, equipment):
        """Requires drained cleanup, a new backend session and fresh route evidence."""
        with self._lock:
            if self.phase != "paused" or self.result is None:
                raise RuntimeError("runtime_not_paused")
            self.executor.new_generation()
            self.tracker.invalidate("human_resume")
            self.route, self.equipment = route, freeze(equipment)
            self._wake.clear()
            self._reset()
            self._record("resumed", generation=self.executor.generation)

    def selection_changed(self, reason="explicit_reselection"):
        with self._lock:
            self.tracker.invalidate(reason)
            self.cancel(reason)

    def _routine_context(self, cursor=None):
        track = self.tracker.track
        return RoutineContext(self.context, self.expected_track, self.executor.generation,
            self.phase_cursor if cursor is None else cursor,
            track.selection_revision if track else None)

    def _enter(self, phase, routine, view, now, *, cursor=None):
        self.phase, self.routine = phase, routine
        self.phase_cursor = view.events.next_cursor if cursor is None else cursor
        routine.start(self._routine_context(), now)
        self.reason = phase + "_started"
        self._record("phase", phase=phase, track_id=self.expected_track,
                     generation=self.executor.generation, source_sequence=view.snapshot.sequence)

    def _finish(self, completed, reason, reward=None):
        self.reason = reason
        self.phase = "paused" if self._pause else "terminal"
        self.result = MissionResult(completed, reason, self.cleanup,
                                   self.cleanup in ("stopped", "already_stopped"), reward or {})
        self._record("result", completed=completed, reason=reason, cleanup=self.cleanup,
                     reward=reward or {})
        return self.result

    def _begin_cleanup(self, reason, view, now):
        self.reason = reason
        if self.routine:
            self.routine.cancel(reason, now)
        self.executor.cancel(reason)
        if self.planner is not None:
            try:
                self.planner.cancel()
            except Exception as error:
                self._record("planner_cancel_failed", detail=str(error))
        self.phase = "stopping"
        self._cleanup_reason = reason
        self._cleanup_started_at = now
        self._record("cleanup_started", reason=reason)
        snapshot = view.snapshot
        if self.context is None and snapshot:
            self.context = snapshot.context
        try:
            if (snapshot is None or snapshot.context != self.context
                    or current_field(snapshot, "owned", now) is not True):
                self.cleanup = "control_unavailable"
                return self._finish(False, reason)
            if not self._moved and current_field(snapshot, "speed", now) == 0:
                self.cleanup = "already_stopped"
                return self._finish(False, reason)
        except ValueError:
            # Unreadable speed alone must not prevent an owned stop request.
            if snapshot is None or snapshot.context != self.context:
                self.cleanup = "control_unavailable"
                return self._finish(False, reason)
            owned = snapshot.fields.get("owned")
            if owned is None or not owned.valid or owned.value is not True:
                self.cleanup = "control_unavailable"
                return self._finish(False, reason)
        try:
            requests = self.backend.request_cleanup_stop(reason=reason,
                expected_session=self.executor._session, expected_layout=self.executor._layout)
            self._cleanup_started_at = max(now, requests[0].at)
            self.cleanup = "waiting_for_stop"
            self._record("cleanup_stop_requested", request_sequences=tuple(r.sequence for r in requests))
        except Exception as error:
            self.cleanup = "control_unavailable"
            self._record("cleanup_failed", detail=str(error))
            return self._finish(False, reason)
        self._stop_observer = StopRoutine(self._cleanup_started_at, self.cleanup_timeout)
        self._stop_observer.start(RoutineContext(self.context, None, self.executor.generation,
                                                view.events.next_cursor), now)
        return None

    def _cleanup_step(self, view, now):
        if view.snapshot is None or view.snapshot.context != self.context:
            self.cleanup = "control_unavailable"
            return self._finish(False, self._cleanup_reason)
        owned = view.snapshot.fields.get("owned")
        if owned and owned.valid and owned.value is False:
            self.cleanup = "control_unavailable"
            return self._finish(False, self._cleanup_reason)
        update = self._stop_observer.update(view, (), now)
        if update.status == "succeeded":
            self.cleanup = "stopped"
            self._record("cleanup_stop_observed", **update.progress)
            return self._finish(False, self._cleanup_reason)
        if now - self._cleanup_started_at >= self.cleanup_timeout or update.status == "failed":
            self.cleanup = "stop_unverified"
            return self._finish(False, self._cleanup_reason)
        return None

    def _advice(self, view, now):
        if self.planner is None:
            return
        from .planning import AdviceRequest, validate_advice
        try:
            if not self._planner_requested:
                request = AdviceRequest(f"{self.executor.generation}:advice", self.executor.generation,
                    self.context, self.expected_track, view.snapshot.acquired_at, now + 10, ())
                self._planner_requested = True
                self._advice_request = request
                self.planner.request(request)
                self._record("planner_requested", request_id=request.request_id)
            proposal = self.planner.poll()
            if proposal is not None:
                accepted, reason = validate_advice(proposal, self._advice_request, view.snapshot,
                                                  self.tracker.track, self.executor.generation, now)
                self._record("planner_result", accepted=accepted, reason=reason)
                # First mission is deterministic; advice is recorded only.
        except Exception as error:
            self._record("planner_unavailable", detail=str(error))
            self.planner = None

    def step(self):
        with self._lock:
            if self.result:
                return self.result
            now = self.clock()
            view = self.store.read(self.phase_cursor)
            self._last_view = view
            if self.phase == "stopping":
                return self._cleanup_step(view, now)
            if self._cancel_reason:
                return self._begin_cleanup(self._cancel_reason, view, now)
            if now - self.started_at >= self.mission_timeout:
                return self._begin_cleanup("mission_timeout", view, now)
            if view.snapshot is None:
                return None
            snapshot = view.snapshot
            unseen_losses = tuple(e.kind for e in view.events.events
                if e.sequence > self._tracker_cursor and e.kind in
                ("selected_target_disappeared", "selection_changed"))
            self._tracker_cursor = view.events.next_cursor
            track = self.tracker.update(snapshot, now, unseen_losses)
            try:
                speed = current_field(snapshot, "speed", now)
                if type(speed) not in (int, float) or not math.isfinite(speed) or speed < 0:
                    raise ValueError("invalid_speed")
                if current_field(snapshot, "owned", now) is not True:
                    raise ValueError("ownership_lost")
                if self.context is not None and snapshot.context != self.context:
                    raise ValueError("context_changed")
                if view.events.gap:
                    raise ValueError("observation_event_gap")
                if self.phase not in ("mine", "confirm_outcome"):
                    if track is None or track.continuity != "confirmed":
                        raise ValueError("selection_unavailable")
                elif speed != 0:
                    raise ValueError("mining_requires_stop")
                if self.expected_track is not None:
                    if track is None or track.track_id != self.expected_track:
                        raise ValueError("selection_changed")
                    if self.phase in ("mine", "confirm_outcome"):
                        selected = current_field(snapshot, "asteroid_selected", now)
                        if selected is True and track.continuity != "confirmed":
                            raise ValueError("selection_uncertain")
                        revision = current_field(snapshot, "selection_revision", now)
                        if revision != track.selection_revision:
                            raise ValueError("selection_changed")
                if any(e.kind == "selection_changed" for e in view.events.events):
                    raise ValueError("selection_changed")
            except ValueError as error:
                return self._begin_cleanup(str(error), view, now)
            if self.phase == "preflight":
                self.context, self.expected_track = snapshot.context, track.track_id
                if speed != 0:
                    self._moved = True
                    return self._begin_cleanup("preflight_requires_stop", view, now)
                route = self.route
                if (route is None or not route.clear or route.context != snapshot.context
                        or route.selection_revision != track.selection_revision
                        or not route.assessed_at <= now < route.expires_at
                        or route.assessed_at < track.established_at - 2
                        or route.source == "synthetic" and not snapshot.window_id.startswith("synthetic:")):
                    return self._begin_cleanup("route_evidence_unavailable", view, now)
                if (self.equipment.get("resource") not in ("Water", "Tylium")
                        or self.equipment.get("configured") is not True):
                    return self._begin_cleanup("equipment_unavailable", view, now)
                self._enter("scan", ScanRoutine(), view, now)
                return None
            self._advice(view, now)
            update = self.routine.update(view, self.executor.statuses(), now)
            self.reason = update.reason
            for intent in update.intents:
                status = self.executor.submit(intent)
                self._record("action", action_id=intent.action_id, operation=intent.operation,
                             state=status.state, request_sequences=status.request_sequences)
                if status.state in ("rejected", "expired", "cancelled"):
                    return self._begin_cleanup(status.reason, view, now)
                if intent.operation == "maximum_throttle":
                    self._moved = True
                if intent.operation == "start_mining":
                    self.mining_started_at = now
            if update.status == "failed":
                return self._begin_cleanup(update.reason, view, now)
            if update.status != "succeeded":
                return None
            self._record("routine_completed", phase=self.phase, reason=update.reason,
                         evidence=update.progress)
            if self.phase == "scan":
                self.phase = "assess"
                scan = update.progress["scan"]
                self._record("phase", phase="assess")
                if scan.get("eligible") is not True or scan.get("resource") != self.equipment["resource"]:
                    return self._begin_cleanup("resource_ineligible", view, now)
                self.resource = scan["resource"]
                self._enter("approach", ApproachRoutine(), view, now)
            elif self.phase == "approach":
                after = self.routine.policy.brake.acquired_at
                self._enter("verify_stop", StopRoutine(after), view, now)
            elif self.phase == "verify_stop":
                self.mining_cursor = view.events.next_cursor
                self._enter("mine", MineRoutine(), view, now)
            elif self.phase == "mine":
                self._enter("confirm_outcome", OutcomeRoutine(self.mining_started_at, self.resource),
                            view, now, cursor=self.mining_cursor)
            elif self.phase == "confirm_outcome":
                # Cancelled generations cannot issue further ordinary input.
                self.executor.cancel("mining_cycle_complete")
                if self.planner is not None:
                    try:
                        self.planner.cancel()
                    except Exception as error:
                        self._record("planner_cancel_failed", detail=str(error))
                self.cleanup = "stopped"
                return self._finish(True, "reward_observed", update.progress)
            return None

    def run(self, real_timeout=15.0, poll_interval=.005):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
               for v in (real_timeout, poll_interval)):
            raise ValueError("invalid_run_timing")
        deadline = time.monotonic() + real_timeout
        while time.monotonic() < deadline:
            result = self.step()
            if result:
                return result
            self._wake.wait(poll_interval)
            self._wake.clear()
        with self._lock:
            if self.phase != "stopping":
                self._begin_cleanup("real_run_timeout", self.store.read(self.phase_cursor), self.clock())
            if self.result:
                return self.result
            # A stuck injected control clock must not leave run() waiting forever.
            self.cleanup = "stop_unverified"
            return self._finish(False, self._cleanup_reason)
