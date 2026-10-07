"""Read the measured large-HUD resource notification, without reward attribution.

This development reader supports four-digit amounts composed of 1,2,3,5,6,7,9.
Other digits, layouts and damaged text remain unknown. A notice is neither an
inventory total nor proof of a new reward: repeated images expose the same text.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from .state import FieldValue


NOTICE_BOX = (1060, 230, 1500, 310)
PROFILE = "largehud-20261003"


@dataclass(frozen=True)
class RewardNotice:
    resource: str | None = None
    amount: int | None = None
    reason: str = "reward_notice_unverified"

    def as_field(self, observed_at):
        valid = self.resource is not None and self.amount is not None
        return FieldValue({"resource": self.resource, "amount": self.amount,
                           "scope": "visible_notification", "attributed": False}
                          if valid else None, observed_at, valid,
                          None if valid else self.reason)


def _mask(image):
    rgb = np.asarray(image.convert("RGB"))
    low, high = rgb.min(axis=2), rgb.max(axis=2)
    return (low > 110) & (high.astype(np.int16) - low < 35)


def _trim(mask):
    rows, columns = np.where(mask)
    if not len(rows):
        return None
    return mask[rows.min():rows.max()+1, columns.min():columns.max()+1]


def _matches(actual, template):
    # No resizing or best-effort digit guessing. Require complete glyph geometry.
    return (actual is not None and actual.shape == template.shape
            and np.count_nonzero(actual ^ template) / template.size <= .08
            and np.count_nonzero(actual & template) / np.count_nonzero(template) >= .9)


@lru_cache(maxsize=1)
def _templates():
    directory = Path(__file__).parent / "data" / "reward-notice"
    with Image.open(directory / "water.png") as image:
        water = _mask(image)
    with Image.open(directory / "tylium.png") as image:
        tylium = _mask(image)
    digits = {}
    for mask, values, boxes in (
        (water, "1237", ((129,138),(146,162),(165,181),(184,199))),
        (tylium, "5936", ((127,143),(146,162),(165,181),(184,200))),
    ):
        for digit, (left, right) in zip(values, boxes):
            digits.setdefault(digit, []).append(_trim(mask[40:80, left:right]))
    return water, tylium, digits


def read_reward_notice_crop(image):
    """Read an exact native NOTICE_BOX crop; callers must verify its provenance."""
    if image.size != (440, 80):
        return RewardNotice(reason="unsupported_reward_notice_crop_geometry")
    mask = _mask(image)
    water, tylium, digits = _templates()
    if not _matches(mask[:40], water[:40]):
        return RewardNotice(reason="reward_notice_header_unverified")
    line = mask[40:80]
    resource = next((name for name, template in (("Water", water), ("Tylium", tylium))
                     if _matches(line[:, 242:346], template[40:80, 242:346])), None)
    if resource is None:
        return RewardNotice(reason="reward_notice_resource_unverified")
    if (not _matches(line[:,96:113], water[40:80,96:113])
            or not _matches(line[:,213:230], water[40:80,213:230])):
        return RewardNotice(reason="reward_notice_syntax_unverified")
    # The measured four-digit layout uses fixed 19-pixel cells. Unknown glyphs
    # cannot fall back to a nearest digit or be interpreted as numeric zero.
    value = ""
    for left, right in ((124,144),(144,163),(163,182),(182,202)):
        glyph = _trim(line[:,left:right])
        matches = [digit for digit, variants in digits.items()
                   if any(_matches(glyph, template) for template in variants)]
        if len(matches) != 1:
            return RewardNotice(reason="reward_notice_digit_unverified")
        value += matches[0]
    # Reject additional text/ink outside the measured grammar, with a small
    # allowance for isolated background stars away from glyphs.
    allowed = np.zeros(line.shape, dtype=bool)
    for left, right in ((96,113),(124,202),(213,230),(242,346)):
        allowed[:,left:right] = True
    if np.count_nonzero(line & ~allowed) > 4:
        return RewardNotice(reason="reward_notice_extra_ink")
    return RewardNotice(resource, int(value), "measured_resource_notification")


def read_reward_notice(image, *, profile):
    """Require native geometry and explicit profile; never resample other HUDs."""
    if profile != PROFILE or image.size != (2560, 1440):
        return RewardNotice(reason="unsupported_reward_notice_layout")
    return read_reward_notice_crop(image.crop(NOTICE_BOX))
