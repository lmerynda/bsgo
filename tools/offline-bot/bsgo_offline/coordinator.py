"""Offline coordinator: published observations, bounded input, optional delayed advice."""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import math, threading, time
from .policy import ApproachPolicy, PolicyError
from .routines import ApproachRoutine, RoutineContext
from .tracking import SelectionTracker
from .execution import InputExecutor, ActionIntent, DEFAULT_BINDINGS

from .planning import Proposal, DelayedPlanner, validate_proposal

@dataclass(frozen=True)
class CoordinatorResult:
    completed: bool
    reason: str
    stop_requested: bool
    stop_observed: bool
    snapshots_seen: int
    events: tuple
    events_total: int = 0
    events_evicted: int = 0
    journal_dropped: int = 0

class Coordinator:
    def __init__(self, store, backend, *, policy=None, clock=time.monotonic,
                 decision_interval=2.0, planner=None, poll_interval=.005, journal=None,
                 history_capacity=64000):
        if decision_interval != 2.0:
            raise ValueError('The retained policy currently requires its two-second decision cadence')
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError('Polling interval must be finite and positive')
        if type(history_capacity) is not int or history_capacity <= 0:
            raise ValueError('Coordinator history capacity must be a positive integer')
        self.store, self.backend = store, backend
        self._input_session, self._input_layout = backend.session_id, backend.layout_id
        self.policy = policy or ApproachPolicy()
        self.clock, self.planner = clock, planner
        self.decision_interval, self.poll_interval = decision_interval, poll_interval
        self._cancel = threading.Event()
        self._submission_lock = threading.Lock()
        self.events = deque(maxlen=history_capacity)
        self.events_total = 0
        self.events_evicted = 0
        self.journal_dropped = 0
        self.journal = journal
        self.seen = 0
        self.stop_requested = False
        self.tracker = SelectionTracker()
        # Acquisition in the original harness precedes approach ownership.
        self.executor = None
        self._routine = None

    def cancel(self):
        with self._submission_lock:
            self._cancel.set()
            self.backend.cancel(reason='coordinator_cancelled')

    def _record(self, kind, **data):
        event = {'at': self.clock(), 'kind': kind, **data}
        self.events_total += 1
        if len(self.events) == self.events.maxlen:
            self.events_evicted += 1
        self.events.append(event)
        if self.journal is not None:
            try:
                submitted = self.journal.submit({'event': 'coordinator_event', 'record': event})
            except Exception:
                submitted = False
            if not submitted:
                self.journal_dropped += 1
        return True

    def telemetry_stats(self):
        return {'events_total': self.events_total, 'events_retained': len(self.events),
                'events_evicted': self.events_evicted, 'journal_dropped': self.journal_dropped}

    def _result(self, completed, reason, stop_observed):
        return CoordinatorResult(completed, reason, self.stop_requested, stop_observed,
            self.seen, tuple(self.events), self.events_total, self.events_evicted, self.journal_dropped)

    def _press(self,key,reason):
        with self._submission_lock:
            if self._cancel.is_set():
                raise RuntimeError('Input canceled before submission')
            operation = next(name for name, binding in DEFAULT_BINDINGS.items() if binding.key == key)
            snapshot = self.store.latest()
            now = self.clock()
            intent = ActionIntent(f'{self.executor.generation}:approach:{operation}',
                self.executor.generation, snapshot.context, self.tracker.track.track_id,
                operation, now, now + 2, self.store.read().events.next_cursor,
                {"minimum_distance": self.policy.brake_at + 250} if operation == 'maximum_throttle' else {})
            status = self.executor.submit(intent)
            if status.state != 'requested':
                raise RuntimeError(status.reason)

    def _fault(self, reason):
        self.backend.cancel(reason=reason)
        try:
            self.backend.request_cleanup_stop(reason=reason,expected_session=self._input_session,expected_layout=self._input_layout)
            self.stop_requested = True
            self._record('cleanup_stop_requested')
        except Exception as error:
            self._record('cleanup_stop_failed', reason=str(error))
        self._record('fault',reason=reason)
        # A request, receiver acknowledgement, or historic zero is not a fresh stop.
        return self._result(False, reason, False)

    def run(self, timeout=5.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Coordinator timeout must be finite and positive')
        deadline = time.monotonic()+timeout
        last_sequence = 0
        planner_handled = False
        try:
            while time.monotonic() < deadline:
                if self._cancel.is_set():
                    return self._fault('cancelled')
                view = self.store.read(self._routine.context.event_cursor if self._routine else 0)
                snapshot = view.snapshot
                if snapshot is None:
                    self._cancel.wait(self.poll_interval)
                    continue
                now = self.clock()
                # Validate unchanged state too: age keeps increasing without publication.
                self.policy.validate(snapshot, now)
                track = self.tracker.update(snapshot, now)
                if track is None or track.continuity != 'confirmed':
                    raise PolicyError('Selected target continuity is uncertain')
                if self._routine is None:
                    self.executor = InputExecutor(self.store, self.backend, self.tracker, self.clock)
                    self._routine = ApproachRoutine(self.policy)
                    self._routine.start(RoutineContext(snapshot.context, track.track_id,
                        self.executor.generation, self.store.read().events.next_cursor), now)
                elif track.track_id != self._routine.context.track_id:
                    raise PolicyError('Selected target continuity changed')
                if snapshot.sequence != last_sequence:
                    self.seen += 1
                    last_sequence = snapshot.sequence
                if self.planner and self.planner.started_sequence is None:
                    self.planner.start(snapshot, track=track, generation=self.executor.generation)
                    self._record('planner_started', source_sequence=snapshot.sequence)
                if self.planner and not planner_handled:
                    proposal = self.planner.poll()
                    if proposal is not None:
                        accepted, reason = validate_proposal(proposal, snapshot, now,
                            track=track, generation=self.executor.generation)
                        self._record('planner_completed', accepted=accepted, reason=reason,
                            source_sequence=proposal.source_sequence, current_sequence=snapshot.sequence)
                        planner_handled = True
                due = self.policy.last is None or snapshot.acquired_at-self.policy.last.acquired_at >= self.decision_interval
                if due:
                    update = self._routine.update(view, self.executor.statuses(), now)
                    if update.status == 'failed':
                        raise PolicyError(update.progress.get('detail', update.reason))
                    action = 'complete' if update.status == 'succeeded' else update.reason
                    self._record('decision', action=action, sequence=snapshot.sequence,
                                 distance=self.policy.last.distance, speed=self.policy.last.speed)
                    if action == 'accelerate':
                        self._press('PageUp','approach')
                    elif action == 'brake':
                        self._press('PageDown','brake_threshold')
                        self.stop_requested = True
                    elif action == 'complete':
                        self.backend.cancel(reason='observed_stop')
                        return self._result(True, 'fresh_stop_observed', True)
                self._cancel.wait(self.poll_interval)
            return self._fault('coordinator_timeout')
        except Exception as error:
            return self._fault(f'{type(error).__name__}: {error}')
        finally:
            if self.planner:
                self.planner.close()
