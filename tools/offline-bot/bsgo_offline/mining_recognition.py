"""Saved-image HUD adapter with explicit unsupported mining observations.

There is no visual target locator, scan-transition recognizer, own-fire detector,
or resource/reward recognizer validated on the retained corpus. Expose these
gaps as unknown fields so a consumer cannot mistake their absence for a zero,
an empty asteroid, inactive weapons, or a successful mining result.
"""
from .recognition import HudRecognizer, RecognitionResult
from .state import FieldValue, freeze
from .readiness import CYCLE_FIELDS


MINING_FIELDS = ("scan_result", "mining_active", "inventory", "inventory_conflict")


class MiningHudRecognizer:
    def __init__(self, profile="original", *, source_frame_size=None):
        self.hud = HudRecognizer(profile, source_frame_size=source_frame_size)
        self.capabilities = freeze({name: name in (
            "distance", "speed", "asteroid_selected", "target_identity", "target_portrait")
            and source_frame_size is None for name in CYCLE_FIELDS})

    def __call__(self, sample):
        result = self.hud(sample)
        fields = dict(result.fields)
        for name in MINING_FIELDS:
            fields[name] = FieldValue(None, sample.acquired_at, False,
                                     "mining_recognizer_not_validated:" + name)
        return RecognitionResult(result.frame_size, fields, result.healthy, result.reason)
