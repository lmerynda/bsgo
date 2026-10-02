"""Offline coordinator: published observations, bounded input, optional delayed advice."""
from __future__ import annotations
from dataclasses import dataclass
from collections import deque
import math, threading, time
from .policy import ApproachPolicy, PolicyError

@dataclass(frozen=True)
class Proposal:
    session_epoch: int
    layout: str
    target_identity: str
    source_sequence: int
    source_acquired_at: float
    expires_at: float
    action: str = 'observe'
    frame_size: tuple = (2560,1440)
    target_portrait: tuple = ()

    def __post_init__(self):
        object.__setattr__(self,'frame_size',tuple(self.frame_size))
        object.__setattr__(self,'target_portrait',tuple(self.target_portrait))

def validate_proposal(proposal, snapshot, now):
    """Revalidate relevant dependencies, rather than rejecting every newer sequence."""
    if not snapshot.healthy:
        return False, 'recognition_unhealthy'
    numbers=(now,proposal.source_acquired_at,proposal.expires_at,snapshot.acquired_at,snapshot.published_at)
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in numbers):
        return False, 'invalid_timestamps'
    if not snapshot.acquired_at <= snapshot.published_at <= now or proposal.expires_at<proposal.source_acquired_at:
        return False, 'invalid_timestamps'
    field = snapshot.fields.get('target_identity')
    portrait=snapshot.fields.get('target_portrait')
    if (snapshot.session_epoch != proposal.session_epoch or snapshot.layout != proposal.layout
            or tuple(snapshot.frame_size)!=proposal.frame_size
            or field is None or not field.valid or field.value != proposal.target_identity
            or portrait is None or not portrait.valid or len(proposal.target_portrait)!=144
            or not isinstance(portrait.value,tuple) or len(portrait.value)!=144):
        return False, 'dependencies_changed'
    if any(type(v) is not int or not 0<=v<=7 for v in (*proposal.target_portrait,*portrait.value)):
        return False, 'invalid_portrait'
    if sum(abs(a-b) for a,b in zip(proposal.target_portrait,portrait.value))/144>.75:
        return False, 'portrait_changed'
    if now < proposal.source_acquired_at or now > proposal.expires_at:
        return False, 'proposal_expired'
    if any(type(f.observed_at) not in (int,float) or not math.isfinite(f.observed_at) or not 0<=now-f.observed_at<=2 or f.observed_at>snapshot.acquired_at
           for f in [field,portrait]) or now-snapshot.acquired_at>2:
        return False, 'current_state_stale'
    if proposal.action != 'observe':
        return False, 'unsupported_proposal'
    return True, 'dependencies_still_valid'

class DelayedPlanner:
    """Cancellable fake planner; no network or model. Delay is real wall seconds."""
    def __init__(self, delay=.4, ttl=12.0):
        if not math.isfinite(delay) or not math.isfinite(ttl) or delay < 0 or ttl <= 0:
            raise ValueError('planner delay/TTL invalid')
        self.delay, self.ttl = delay, ttl
        self._stop = threading.Event()
        self._thread = None
        self._proposal = None
        self._done = threading.Event()
        self.started_sequence = None

    def start(self, snapshot):
        if self._thread is not None:
            raise RuntimeError('Only one planner request is permitted')
        self.started_sequence = snapshot.sequence
        proposal = Proposal(snapshot.session_epoch, snapshot.layout,
            snapshot.fields['target_identity'].value, snapshot.sequence,
            snapshot.acquired_at, snapshot.acquired_at+self.ttl,
            frame_size=snapshot.frame_size,target_portrait=snapshot.fields['target_portrait'].value)
        def produce():
            if not self._stop.wait(self.delay):
                self._proposal = proposal
            self._done.set()
        self._thread = threading.Thread(target=produce, name='offline-delayed-planner')
        self._thread.start()

    def poll(self):
        return self._proposal if self._done.is_set() else None

    def close(self, timeout=2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

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
            self.backend.press_key(key,reason=reason,expected_session=self._input_session,expected_layout=self._input_layout)

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
                snapshot = self.store.latest()
                if snapshot is None:
                    self._cancel.wait(self.poll_interval)
                    continue
                now = self.clock()
                # Validate unchanged state too: age keeps increasing without publication.
                self.policy.validate(snapshot, now)
                if snapshot.sequence != last_sequence:
                    self.seen += 1
                    last_sequence = snapshot.sequence
                if self.planner and self.planner.started_sequence is None:
                    self.planner.start(snapshot)
                    self._record('planner_started', source_sequence=snapshot.sequence)
                if self.planner and not planner_handled:
                    proposal = self.planner.poll()
                    if proposal is not None:
                        accepted, reason = validate_proposal(proposal, snapshot, now)
                        self._record('planner_completed', accepted=accepted, reason=reason,
                            source_sequence=proposal.source_sequence, current_sequence=snapshot.sequence)
                        planner_handled = True
                due = self.policy.last is None or snapshot.acquired_at-self.policy.last.acquired_at >= self.decision_interval
                if due:
                    action = self.policy.decide(snapshot, now)
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
