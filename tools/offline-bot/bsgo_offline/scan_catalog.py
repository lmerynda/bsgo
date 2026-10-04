"""Image-evidence catalog for a single stationary camera view.

Entries are local continuity hypotheses, not world identities. A caller must
supply observations from a validated detector or explicit review of the cited
image. The current HUD reader cannot supply scene locations or scan states.
HP signatures and input delivery reports are deliberately not identity evidence.
"""
from dataclasses import dataclass, replace
import math

from .state import ObservationContext


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class CandidateObservation:
    context: ObservationContext
    view_revision: int
    acquisition_sequence: int
    acquired_at: float
    image_reference: str
    box: tuple[int, int, int, int]
    visual_signature: str
    distance: float | None = None
    scan_state: str = "unknown"
    resource: str | None = None
    selected: bool = False
    provenance: str = "supervisor_image_review"

    def __post_init__(self):
        object.__setattr__(self, "box", tuple(self.box))
        if (not isinstance(self.context, ObservationContext)
                or type(self.view_revision) is not int or self.view_revision < 0
                or type(self.acquisition_sequence) is not int or self.acquisition_sequence < 1
                or not _finite(self.acquired_at)
                or not isinstance(self.image_reference, str) or not self.image_reference.strip()
                or not isinstance(self.visual_signature, str) or not self.visual_signature.strip()
                or self.visual_signature.startswith("asteroid:hp:")
                or self.provenance not in ("supervisor_image_review", "validated_image_recognizer")
                or self.scan_state not in ("unknown", "unscanned", "scanned")
                or type(self.selected) is not bool):
            raise ValueError("invalid_candidate_image_evidence")
        if (len(self.box) != 4 or any(type(v) is not int for v in self.box)
                or not 0 <= self.box[0] < self.box[2] <= self.context.frame_size[0]
                or not 0 <= self.box[1] < self.box[3] <= self.context.frame_size[1]):
            raise ValueError("candidate_box_outside_frame")
        if self.distance is not None and (not _finite(self.distance) or self.distance <= 0):
            raise ValueError("invalid_candidate_distance")
        if self.resource is not None and (self.scan_state != "scanned"
                or self.resource not in ("Water", "Tylium", "Titanium", "empty")):
            raise ValueError("resource_requires_classified_scan_image")


@dataclass(frozen=True)
class CatalogEntry:
    candidate_id: str
    observation: CandidateObservation
    scan_state: str
    resource: str | None
    scan_image_reference: str | None = None

    @property
    def click(self):
        left, top, right, bottom = self.observation.box
        return ((left + right) // 2, (top + bottom) // 2)


def overlap(first, second):
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right, bottom = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0, right-left) * max(0, bottom-top)
    area = ((first[2]-first[0]) * (first[3]-first[1])
            + (second[2]-second[0]) * (second[3]-second[1]) - intersection)
    return intersection / area if area else 0


class ScanCatalog:
    """Fresh locations supplied by image review; no implicit discovery fallback.

    Increment view_revision after camera or ship motion. Changing session,
    window, layout, geometry or input ownership also clears the entire view.
    Catalog state is intentionally ephemeral and is never a respawn cache.
    """
    def __init__(self, max_age=2.0):
        if not _finite(max_age) or not 0 < max_age <= 2:
            raise ValueError("max_age")
        self.max_age = max_age
        self.context = None
        self.view_revision = None
        self._entries = {}
        self._next_id = 0
        self._last_acquisition = 0

    def reset_view(self, context, view_revision):
        if (not isinstance(context, ObservationContext) or type(view_revision) is not int
                or view_revision < 0):
            raise ValueError("invalid_view_context")
        if (context, view_revision) != (self.context, self.view_revision):
            self._entries.clear()
            self._last_acquisition = 0
            self.context, self.view_revision = context, view_revision

    def entries(self, now):
        if not _finite(now):
            raise ValueError("now")
        return tuple(entry for entry in self._entries.values()
                     if 0 <= now - entry.observation.acquired_at < self.max_age)

    def get(self, candidate_id, now):
        return next((entry for entry in self.entries(now)
                     if entry.candidate_id == candidate_id), None)

    def observe(self, observations, now):
        observations = tuple(observations)
        if not observations:
            return ()
        if any(not isinstance(row, CandidateObservation) for row in observations):
            raise TypeError("candidate_image_observation_required")
        first = observations[0]
        if any((row.context, row.view_revision, row.acquisition_sequence,
                row.acquired_at, row.image_reference) != (first.context, first.view_revision,
                first.acquisition_sequence, first.acquired_at, first.image_reference)
               for row in observations):
            raise ValueError("candidate_batch_must_share_one_image")
        if not _finite(now) or not 0 <= now-first.acquired_at < self.max_age:
            raise ValueError("stale_candidate_image")
        self.reset_view(first.context, first.view_revision)
        if first.acquisition_sequence < self._last_acquisition:
            raise ValueError("out_of_order_candidate_image")
        # Validate conflicting same-image detections before modifying the catalog.
        for index, row in enumerate(observations):
            for other in observations[:index]:
                if overlap(row.box, other.box) >= .7 and row != other:
                    raise ValueError("ambiguous_overlapping_candidates")
        output = []
        for row in observations:
            matches = [entry for entry in self.entries(now)
                       if entry.observation.visual_signature == row.visual_signature
                       and overlap(entry.observation.box, row.box) >= .7
                       and 0 <= row.acquired_at-entry.observation.acquired_at < self.max_age]
            if len(matches) > 1:
                raise ValueError("ambiguous_candidate_continuity")
            previous = matches[0] if matches else None
            if previous is None:
                self._next_id += 1
                entry = CatalogEntry(f"view-candidate-{self._next_id}", row,
                                     row.scan_state, row.resource)
            else:
                # A repeated acquisition cannot relabel an old image as a new scan.
                if (row.acquisition_sequence == previous.observation.acquisition_sequence
                        and row != previous.observation):
                    raise ValueError("conflicting_same_acquisition_evidence")
                entry = replace(previous, observation=row)
                if row.scan_state != "unknown":
                    entry = replace(entry, scan_state=row.scan_state, resource=row.resource)
            if row.scan_state == "scanned":
                entry = replace(entry, scan_image_reference=row.image_reference)
            self._entries[entry.candidate_id] = entry
            output.append(entry)
        self._last_acquisition = first.acquisition_sequence
        return tuple(output)
