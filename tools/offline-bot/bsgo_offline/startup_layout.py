"""Select observation geometry from a discovered physical game client size.

Dimensions do not identify HUD scale. The enlarged-HUD profile remains explicit;
only its measured native size admits input. Other 16:9 sizes can be observed via
experimental normalization, while unsupported aspect ratios publish unknowns.
"""
from dataclasses import dataclass
from types import SimpleNamespace

from PIL import Image

from .mining_recognition import MiningHudRecognizer, MINING_FIELDS
from .recognition import HUD_FIELDS, RecognitionResult
from .state import FieldValue


PROFILE = "largehud-20261003"
CALIBRATED_SIZE = (2560, 1440)


@dataclass(frozen=True)
class StartupLayout:
    client_size: tuple[int, int]

    def __post_init__(self):
        size = tuple(self.client_size)
        if len(size) != 2 or any(type(value) is not int or value <= 0 for value in size):
            raise ValueError("invalid_game_client_dimensions")
        object.__setattr__(self, "client_size", size)

    @property
    def mode(self):
        width, height = self.client_size
        return ("native" if self.client_size == CALIBRATED_SIZE else
                "normalized" if width * 9 == height * 16 else "unsupported_aspect_ratio")

    @property
    def control_supported(self):
        return self.mode == "native"

    @property
    def control_reason(self):
        return None if self.control_supported else "game_client_dimensions_not_control_validated"

    def metadata(self):
        return {"discovery": "physical_client_rectangle", "client_size": self.client_size,
            "calibrated_size": CALIBRATED_SIZE, "profile": PROFILE, "mode": self.mode,
            "scale_to_calibration": tuple(target / source for target, source in
                                          zip(CALIBRATED_SIZE, self.client_size)),
            "control_supported": self.control_supported, "control_reason": self.control_reason}


class StartupHudRecognizer:
    """Adapt only recognition pixels; retain native frame identity and dimensions."""
    def __init__(self, client_size, *, profile=PROFILE):
        if profile != PROFILE:
            raise ValueError("startup_requires_explicit_large_hud_profile")
        self.layout = StartupLayout(client_size)
        self.reader = MiningHudRecognizer(profile)
        self.profile = profile

    def __call__(self, sample):
        if callable(getattr(sample, "to_image", None)):
            image = sample.to_image().convert("RGB")
        else:
            with Image.open(sample.path) as opened:
                image = opened.convert("RGB")
        source_size = image.size
        reason = ("layout_profile_mismatch" if sample.layout != self.profile else
                  "startup_client_dimensions_changed" if source_size != self.layout.client_size else
                  "unsupported_startup_aspect_ratio" if self.layout.mode == "unsupported_aspect_ratio" else None)
        if reason:
            fields = {name: FieldValue(None, sample.acquired_at, False, reason)
                      for name in (*HUD_FIELDS, *MINING_FIELDS)}
            evidence = {"reasons": [reason]}
            healthy = False
        else:
            if self.layout.mode == "normalized":
                image = image.resize(CALIBRATED_SIZE, Image.Resampling.BOX)
            # This proxy is private to the reader. Capture/snapshot publication
            # continues to use the original sample, epoch, sequence and bytes.
            normalized = SimpleNamespace(layout=sample.layout, acquired_at=sample.acquired_at,
                                         to_image=lambda: image)
            result = self.reader(normalized)
            fields = dict(result.fields)
            evidence = dict(fields["ocr_evidence"].value)
            healthy = result.healthy
            reason = result.reason
        evidence.update(source_frame_size=source_size,
            recognition_frame_size=CALIBRATED_SIZE if self.layout.mode != "unsupported_aspect_ratio" and not reason else source_size,
            startup_layout=self.layout.metadata(),
            resampling=("box_startup_to_2560x1440" if self.layout.mode == "normalized" and not reason else "none"),
            resampling_validation=("experimental_unvalidated" if self.layout.mode == "normalized" else
                                   "native_profile_size" if self.layout.control_supported else "unsupported_layout"))
        fields["ocr_evidence"] = FieldValue(evidence, sample.acquired_at)
        return RecognitionResult(source_size, fields, healthy, reason)
