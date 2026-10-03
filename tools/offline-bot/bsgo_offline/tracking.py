"""Local selection continuity hypotheses, never persistent world identities."""
from dataclasses import dataclass
from .events import current_field
from .state import ObservationContext


@dataclass(frozen=True)
class SelectionTrack:
    track_id: int
    context: ObservationContext
    established_at: float
    last_evidence_at: float
    signature: str
    portrait: tuple
    selection_revision: int | None
    continuity: str = "confirmed"
    reason: str = "continuous_evidence"


class SelectionTracker:
    def __init__(self, max_age=2.0):
        self.max_age = max_age
        self.track = None
        self._next_id = 0
        self._sequence = -1

    def invalidate(self, reason="explicit_reselection"):
        from dataclasses import replace
        if self.track:
            self.track = replace(self.track, continuity="lost", reason=reason)

    def update(self, snapshot, now, control_events=()):
        from dataclasses import replace
        if control_events:
            self.invalidate(str(control_events[0]))
        try:
            if current_field(snapshot, "asteroid_selected", now, self.max_age) is not True:
                self.invalidate("selection_lost")
                return self.track
            signature = current_field(snapshot, "target_identity", now, self.max_age)
            portrait = current_field(snapshot, "target_portrait", now, self.max_age)
            if (not isinstance(signature, str) or not signature or not isinstance(portrait, tuple)
                    or len(portrait) != 144 or any(type(v) is not int or not 0 <= v <= 7 for v in portrait)):
                raise ValueError("invalid_signature")
            revision_field = snapshot.fields.get("selection_revision")
            revision = current_field(snapshot, "selection_revision", now) if revision_field else None
            if revision is not None and (type(revision) is not int or revision < 0):
                raise ValueError("invalid_selection_revision")
        except ValueError as error:
            if self.track:
                self.track = replace(self.track, continuity="uncertain", reason=str(error))
            return self.track
        previous = self.track
        same = (previous is not None and previous.continuity == "confirmed"
                and previous.context == snapshot.context and previous.signature == signature
                and previous.selection_revision == revision
                and 0 <= snapshot.acquired_at - previous.last_evidence_at <= self.max_age
                and sum(abs(a-b) for a,b in zip(previous.portrait, portrait))/144 <= .75)
        if same:
            self.track = replace(previous, last_evidence_at=snapshot.acquired_at)
        elif snapshot.sequence != self._sequence or control_events:
            self._next_id += 1
            self.track = SelectionTrack(self._next_id, snapshot.context, snapshot.acquired_at,
                                        snapshot.acquired_at, signature, portrait, revision)
        # Once continuity is invalidated, the same old frame cannot reacquire it.
        self._sequence = snapshot.sequence
        return self.track
