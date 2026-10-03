"""Saved-image HUD adapter with explicit unsupported mining observations.

There is no visual target locator, scan-transition recognizer, own-fire detector,
or resource/reward recognizer validated on the retained corpus. Expose these
gaps as unknown fields so a consumer cannot mistake their absence for a zero,
an empty asteroid, inactive weapons, or a successful mining result.
"""
from .recognition import HudRecognizer, RecognitionResult
from .state import FieldValue


MINING_FIELDS = ("scan_result", "mining_active", "inventory", "inventory_conflict")


class MiningHudRecognizer:
    def __init__(self, profile="original"):
        self.hud = HudRecognizer(profile)

    def __call__(self, sample):
        result = self.hud(sample)
        fields = dict(result.fields)
        for name in MINING_FIELDS:
            fields[name] = FieldValue(None, sample.acquired_at, False,
                                     "mining_recognizer_not_validated:" + name)
        return RecognitionResult(result.frame_size, fields, result.healthy, result.reason)
