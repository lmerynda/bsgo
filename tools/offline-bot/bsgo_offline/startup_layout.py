"""Select observation geometry from a discovered physical game client size.

Dimensions do not identify HUD scale. The enlarged-HUD profile remains explicit;
only its measured native size admits input. Other sizes require visual region
registration across fresh captures before experimental observation is published.
"""
from dataclasses import dataclass
from types import SimpleNamespace

from PIL import Image

from .mining_recognition import MiningHudRecognizer, MINING_FIELDS
from .recognition import HUD_FIELDS, RecognitionResult, load_hud_reader
from .state import FieldValue
from .hud_calibration import HudAnchorCalibrator
from .game_state import GameStateReader
from .selected_target import SelectedTargetReader


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
        return "native" if self.client_size == CALIBRATED_SIZE else "auto_calibration"

    @property
    def control_supported(self):
        return self.mode == "native"

    @property
    def control_reason(self):
        return None if self.control_supported else "game_client_dimensions_not_control_validated"

    def metadata(self):
        return {"discovery": "physical_client_rectangle", "client_size": self.client_size,
            "calibrated_size": CALIBRATED_SIZE, "profile": PROFILE, "mode": self.mode,
            "registration_required": not self.control_supported,
            "control_supported": self.control_supported, "control_reason": self.control_reason}


class StartupHudRecognizer:
    """Compose independent game observation and asteroid-specific calibration.

    Ship instruments read the original image before target registration. Their
    display readings never replace the validated fields consumed by routines.
    """
    def __init__(self, client_size, *, profile=PROFILE, mining_enabled_provider=lambda: True):
        if profile != PROFILE:
            raise ValueError("startup_requires_explicit_large_hud_profile")
        self.layout = StartupLayout(client_size)
        self._mining_reader = None
        self.mining_enabled_provider = mining_enabled_provider
        self.profile = profile
        fonts = load_hud_reader(profile)._large
        self.calibrator = HudAnchorCalibrator(fonts)
        self.game_state = GameStateReader(fonts)
        self.selected_target = SelectedTargetReader(self.game_state.reader)

    @property
    def reader(self):
        if self._mining_reader is None:
            self._mining_reader = MiningHudRecognizer(self.profile)
        return self._mining_reader

    @reader.setter
    def reader(self, value):
        self._mining_reader = value

    def __call__(self, sample):
        if callable(getattr(sample, "to_image", None)):
            image = sample.to_image().convert("RGB")
        else:
            with Image.open(sample.path) as opened:
                image = opened.convert("RGB")
        source_size = image.size
        source_image = image
        reason = ("layout_profile_mismatch" if sample.layout != self.profile else
                  "startup_client_dimensions_changed" if source_size != self.layout.client_size else None)
        source_reason = reason
        geometry = getattr(sample, "geometry", None)
        context = (getattr(sample, "session_epoch", None), sample.layout, source_size, geometry,
                   getattr(sample, "source_id", None))
        sequence = getattr(sample, "acquisition_sequence", None)
        if source_reason:
            self.game_state.reset(source_reason)
            ship_field = FieldValue(None, sample.acquired_at, False, source_reason)
        else:
            ship_field = FieldValue(self.game_state.read(source_image,
                context=context, sequence=sequence, observed_at=sample.acquired_at), sample.acquired_at)
        mining_enabled = self.mining_enabled_provider() is True
        if not mining_enabled:
            reason = reason or 'mining_feature_disabled'
        fields = None
        try:
            if reason:
                self.calibrator.reset(reason)
            elif not self.layout.control_supported:
                if sequence is None:
                    self.calibrator.reset("capture_identity_required_for_calibration")
                    image = None
                else:
                    image = self.calibrator.update(image, context=context, sequence=sequence,
                                                   observed_at=sample.acquired_at)
                if image is None:
                    reason = self.calibrator.reason
            if not reason:
                normalized = SimpleNamespace(layout=sample.layout, acquired_at=sample.acquired_at,
                                             to_image=lambda: image)
                result = self.reader(normalized)
                fields = dict(result.fields)
                evidence = dict(fields["ocr_evidence"].value)
                reason = result.reason
        except Exception as exc:
            reason = 'mining_recognition_failed: ' + type(exc).__name__
            self.calibrator.reset(reason)
            fields = None
        if fields is None:
            fields = {name: FieldValue(None, sample.acquired_at, False, reason)
                      for name in (*HUD_FIELDS, *MINING_FIELDS)}
            evidence = {"reasons": [reason]}
            # Missing target anchors are field-level recognition failures;
            # they do not make the independently captured game image unhealthy.
        healthy = source_reason is None
        fields["ship_telemetry"] = ship_field
        fields['selected_target'] = (FieldValue(None, sample.acquired_at, False, source_reason)
            if source_reason else FieldValue(self.selected_target.read(source_image), sample.acquired_at))
        fields['features'] = FieldValue({'mining': {'enabled': mining_enabled,
            'reason': reason}}, sample.acquired_at)
        evidence.update(source_frame_size=source_size,
            recognition_frame_size=CALIBRATED_SIZE if image is not None and not reason else source_size,
            startup_layout=self.layout.metadata(),
            startup_calibration=(self.calibrator.metadata() if not self.layout.control_supported else
                                 {"status":"existing_profile", "validation":"native_profile_size"}),
            resampling=("registered_hud_regions" if not self.layout.control_supported and not reason else "none"),
            resampling_validation=("native_profile_size" if self.layout.control_supported else "experimental_unvalidated"))
        fields["ocr_evidence"] = FieldValue(evidence, sample.acquired_at)
        return RecognitionResult(source_size, fields, healthy, reason)
