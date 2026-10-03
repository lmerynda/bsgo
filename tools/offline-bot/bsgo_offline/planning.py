"""Bounded optional advice. No model server or executable actions."""
from dataclasses import dataclass
import math
from threading import Event, Thread

from .events import current_field
from .state import ObservationContext


@dataclass(frozen=True)
class AdviceRequest:
    request_id: str
    generation: int
    context: ObservationContext
    track_id: int
    source_acquired_at: float
    expires_at: float
    candidate_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "candidate_ids", tuple(self.candidate_ids))
        if (not self.request_id or type(self.generation) is not int or self.generation < 1
                or type(self.track_id) is not int or self.track_id < 1
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in (self.source_acquired_at, self.expires_at))
                or self.expires_at <= self.source_acquired_at):
            raise ValueError("invalid_advice_request")


@dataclass(frozen=True)
class AdviceProposal:
    request_id: str
    generation: int
    action: str = "observe"
    candidate_id: str | None = None


def validate_advice(proposal, request, snapshot, track, generation, now):
    if not isinstance(proposal, AdviceProposal):
        return False, "malformed_advice"
    if (proposal.request_id != request.request_id or proposal.generation != request.generation
            or generation != request.generation):
        return False, "obsolete_request"
    if type(now) not in (int, float) or not math.isfinite(now) or not request.source_acquired_at <= now < request.expires_at:
        return False, "proposal_expired"
    if (snapshot is None or snapshot.context != request.context or track is None
            or track.track_id != request.track_id or track.continuity != "confirmed"):
        return False, "dependencies_changed"
    try:
        if current_field(snapshot, "asteroid_selected", now) is not True:
            return False, "selection_lost"
    except ValueError:
        return False, "current_state_stale"
    if proposal.action == "observe" and proposal.candidate_id is None:
        return True, "dependencies_still_valid"
    if proposal.action == "choose_candidate" and proposal.candidate_id in request.candidate_ids:
        return True, "eligible_candidate"
    return False, "unsupported_proposal"


class AdvicePlanner:
    """Cancellable fake planner with one request, reusable after completion."""
    def __init__(self, delay=.4):
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            raise ValueError("planner_delay")
        self.delay = delay
        self._thread = None
        self._stop = Event()
        self._done = Event()
        self._proposal = None
        self._reported = False

    def request(self, request):
        if self._thread is not None:
            if not self._done.is_set() or not self._reported:
                raise RuntimeError("planner_request_outstanding")
            self._thread.join(timeout=0)
            if self._thread.is_alive():
                raise RuntimeError("planner_worker_still_exiting")
        self._stop.clear()
        self._done.clear()
        self._proposal, self._reported = None, False
        def produce():
            if not self._stop.wait(self.delay):
                self._proposal = AdviceProposal(request.request_id, request.generation)
            self._done.set()
        self._thread = Thread(target=produce, name="offline-mining-advice", daemon=True)
        self._thread.start()

    def poll(self):
        if self._done.is_set() and not self._reported:
            self._reported = True
            return self._proposal
        return None

    def cancel(self):
        self._stop.set()
        self._reported = True

    def close(self, timeout=2):
        self.cancel()
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True


# Compatibility API for the original approach experiment.
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
    track_id: int | None = None
    generation: int = 1
    context: ObservationContext | None = None

    def __post_init__(self):
        object.__setattr__(self,'frame_size',tuple(self.frame_size))
        object.__setattr__(self,'target_portrait',tuple(self.target_portrait))

def validate_proposal(proposal, snapshot, now, *, track=None, generation=1):
    """Revalidate relevant dependencies, rather than rejecting every newer sequence."""
    if not snapshot.healthy:
        return False, 'recognition_unhealthy'
    if proposal.track_id is not None:
        if (track is None or track.continuity != 'confirmed' or track.track_id != proposal.track_id
                or snapshot.context != proposal.context or generation != proposal.generation):
            return False, 'dependencies_changed'
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
        self._stop = Event()
        self._thread = None
        self._proposal = None
        self._done = Event()
        self.started_sequence = None

    def start(self, snapshot, *, track=None, generation=1):
        if self._thread is not None:
            raise RuntimeError('Only one planner request is permitted')
        self.started_sequence = snapshot.sequence
        proposal = Proposal(snapshot.session_epoch, snapshot.layout,
            snapshot.fields['target_identity'].value, snapshot.sequence,
            snapshot.acquired_at, snapshot.acquired_at+self.ttl,
            frame_size=snapshot.frame_size,target_portrait=snapshot.fields['target_portrait'].value,
            track_id=track.track_id if track else None, generation=generation, context=snapshot.context)
        def produce():
            if not self._stop.wait(self.delay):
                self._proposal = proposal
            self._done.set()
        self._thread = Thread(target=produce, name='offline-delayed-planner')
        self._thread.start()

    def poll(self):
        return self._proposal if self._done.is_set() else None

    def close(self, timeout=2.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True
