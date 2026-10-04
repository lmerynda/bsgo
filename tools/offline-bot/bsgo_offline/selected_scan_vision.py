"""Conservative selected-marker scan evidence from annotated native textures.

Only bright blue/purple scanned textures are supported by this corpus. Cursor
glow, gray rocks, other mineral colors and missing/occluded markers stay unknown.
Absence of color NEVER proves unscanned. A transition needs a separately reviewed
unscanned baseline and uninterrupted local selection-track evidence. This does
not locate unselected rocks, identify world objects, or classify their resources.
"""
from dataclasses import dataclass
import colorsys
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .scan_catalog import CandidateObservation, overlap
from .tracking import SelectionTrack


@dataclass(frozen=True)
class SelectedMarkerLocation:
    box: tuple[int, int, int, int] | None
    reason: str
    label_matches: int = 0


def _neutral_text_mask(image):
    rgb = np.asarray(image.convert("RGB"))
    low, high = rgb.min(axis=2), rgb.max(axis=2)
    return (low > 110) & ((high.astype(np.int16)-low) < 35)


@lru_cache(maxsize=1)
def _label_template():
    with Image.open(Path(__file__).parent / "data" / "selected-asteroid-label.png") as image:
        return _neutral_text_mask(image)


def locate_selected_marker(image, *, selected_asteroid_verified=False):
    """Find one ASTEROID world label in native large-HUD flight view.

    A fresh HUD selected-asteroid observation is a separate prerequisite. This
    locator is not scene discovery; occlusion, other layouts or multiple labels
    remain unknown. It never clicks an inferred location.
    """
    if selected_asteroid_verified is not True:
        return SelectedMarkerLocation(None, "selected_asteroid_unverified")
    if image.size != (2560, 1440):
        return SelectedMarkerLocation(None, "unsupported_selected_marker_frame_geometry")
    template = _label_template()
    height, width = template.shape
    # Exclude target-panel title, sidebar, chat and own lower control HUD.
    area = _neutral_text_mask(image.crop((0, 200, 2010, 1040)))
    shape = (area.shape[0]-height+1, area.shape[1]-width+1)
    positive = list(zip(*np.where(template)))
    negative = list(zip(*np.where(~template)))
    hits, misses = np.zeros(shape, np.uint8), np.zeros(shape, np.uint8)
    for index in range(10):
        y, x = positive[index*len(positive)//10]
        hits += area[y:y+shape[0], x:x+shape[1]]
        y, x = negative[index*len(negative)//10]
        misses += ~area[y:y+shape[0], x:x+shape[1]]
    candidates = np.where((hits >= 5) & (misses >= 9))
    if len(candidates[0]) > 4096:
        return SelectedMarkerLocation(None, "selected_marker_search_ambiguous_or_cluttered")
    matches = []
    for y, x in zip(*candidates):
        patch = area[y:y+height, x:x+width]
        common = np.count_nonzero(patch & template)
        recall = common / np.count_nonzero(template)
        precision = common / max(1, np.count_nonzero(patch))
        if recall >= .8 and precision >= .85:
            matches.append((recall+precision, int(x), int(y)+200))
    # Adjacent antialias matches are the same label; preserve distinct labels.
    labels = []
    for score, x, y in sorted(matches, reverse=True):
        if not any(abs(x-old_x) <= 3 and abs(y-old_y) <= 3 for _, old_x, old_y in labels):
            labels.append((score, x, y))
    if len(labels) != 1:
        return SelectedMarkerLocation(None, "selected_marker_absent_or_multiple", len(labels))
    _, x, y = labels[0]
    return SelectedMarkerLocation((x+24, y+36, x+80, y+80),
                                  "measured_world_label_located", 1)


@dataclass(frozen=True)
class MarkerClassification:
    scan_state: str
    reason: str
    bright_chromatic_pixels: int = 0
    bright_chromatic_fraction: float = 0.


def classify_selected_marker(image, box, *, marker_location_verified=False):
    """Read a verified texture-only ROI; never search arbitrary image areas."""
    if marker_location_verified is not True:
        return MarkerClassification("unknown", "selected_marker_location_unverified")
    if (len(box) != 4 or any(type(v) is not int for v in box)
            or not 0 <= box[0] < box[2] <= image.width
            or not 0 <= box[1] < box[3] <= image.height
            or not 32 <= box[2]-box[0] <= 96
            or not 32 <= box[3]-box[1] <= 96):
        return MarkerClassification("unknown", "unsupported_marker_geometry")
    crop = image.crop(box).convert("RGB")
    # Measured 036-038 baselines contain up to 1,444 blue cursor-halo pixels;
    # all but one have value <= .65. Post-scan textures contain 684-1,147
    # bright chromatic pixels in 2,200-2,970 pixels. Keep an explicit gap.
    pixels = 0
    data = crop.tobytes()
    for red, green, blue in zip(data[::3], data[1::3], data[2::3]):
        hue, saturation, value = colorsys.rgb_to_hsv(red/255., green/255., blue/255.)
        if .5 < hue < .86 and saturation > .35 and value > .65:
            pixels += 1
    fraction = pixels / (crop.width * crop.height)
    if pixels >= 200 and fraction >= .20:
        return MarkerClassification("scanned", "measured_bright_scan_texture", pixels, fraction)
    return MarkerClassification("unknown", "scan_texture_unverified", pixels, fraction)


@dataclass(frozen=True)
class SelectedScanTransition:
    scan_state: str
    reason: str
    before_image_reference: str
    after_image_reference: str
    resource: str | None = None


def verify_selected_scan_transition(before_image, after_image, *,
                                    before: CandidateObservation, after: CandidateObservation,
                                    before_track: SelectionTrack, after_track: SelectionTrack):
    """Prove a visual transition for one uninterrupted local selected target.

    Track continuity must come from the independent per-frame SelectionTracker,
    including all selection/camera/ownership events. Probe delivery/HP equality
    alone cannot construct this proof. No native input is emitted here.
    """
    def result(state, reason):
        return SelectedScanTransition(state, reason, before.image_reference, after.image_reference)
    if (not isinstance(before, CandidateObservation) or not isinstance(after, CandidateObservation)
            or not isinstance(before_track, SelectionTrack) or not isinstance(after_track, SelectionTrack)):
        raise TypeError("candidate_and_selection_track_image_evidence_required")
    if (before.context != after.context or before.view_revision != after.view_revision
            or before_track.context != before.context or after_track.context != after.context
            or not before.selected or not after.selected
            or before_track.continuity != "confirmed" or after_track.continuity != "confirmed"
            or before_track.track_id != after_track.track_id
            or before_track.established_at != after_track.established_at
            or before_track.last_evidence_at != before.acquired_at
            or after_track.last_evidence_at != after.acquired_at
            or before.visual_signature != after.visual_signature
            or overlap(before.box, after.box) < .7):
        return result("unknown", "selected_visual_continuity_unverified")
    if (after.acquisition_sequence <= before.acquisition_sequence
            or not 0 < after.acquired_at-before.acquired_at <= 12
            or before.image_reference == after.image_reference):
        return result("unknown", "post_scan_image_not_newer")
    if (before.distance is None or after.distance is None
            or abs(before.distance-after.distance) > 5):
        return result("unknown", "stationary_target_range_unverified")
    if before.scan_state != "unscanned":
        return result("unknown", "unscanned_baseline_image_unverified")
    if before_image.size != before.context.frame_size or after_image.size != after.context.frame_size:
        return result("unknown", "image_context_geometry_mismatch")
    baseline = classify_selected_marker(before_image, before.box, marker_location_verified=True)
    observed = classify_selected_marker(after_image, after.box, marker_location_verified=True)
    if baseline.scan_state == "scanned":
        return result("unknown", "baseline_already_has_scan_texture")
    if observed.scan_state != "scanned":
        return result("unknown", observed.reason)
    return result("scanned", "selected_scan_texture_transition_verified")
