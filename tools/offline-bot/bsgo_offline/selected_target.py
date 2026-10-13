"""Read the selected-target card independently of optional asteroid automation.

The measured native display profile is deliberately narrow. Other layouts and
missing cards remain unknown; an unreadable card is never proof of no selection.
"""
import json
from pathlib import Path

import numpy as np

from .ship_telemetry import ShipTelemetryReader


NATIVE_SIZE = (2560, 1440)
# The shield bar sits one bar-pitch below the HP bar on the native target card.
SHIELD_OFFSET = 24


def _slash(glyph):
    """The ratio's thin '/' when it survives the mask: a narrow descending diagonal."""
    height, width = glyph.shape
    if not (10 <= height <= 13 and 5 <= width <= 8 and glyph[0, -1] and glyph[-1, 0]):
        return False
    ys, xs = np.where(glyph)
    return float(np.abs(xs / (width - 1) + ys / (height - 1) - 1).max()) <= .25


def card_ratio(large, image, box):
    """Read 'current/max' on a native target-card bar from the HP font bank.

    The thin slash does not survive the brightness mask (at most a speck), so
    the two numbers are split at the one gap clearly wider than digit spacing.
    At least two brightness thresholds must agree; anything else is unknown.
    """
    helpers = type(large).distance.__globals__  # the reader's own mask/pieces
    mask_of, pieces_of = helpers['mask'], helpers['pieces']
    readings = []
    for threshold in (140, 160, 180):
        mask = mask_of(image, box, threshold)
        if mask[:1].any() or mask[-1:].any() or mask[:, :1].any() or mask[:, -1:].any():
            continue
        digits, slashes = [], []
        for left, right, top, glyph in pieces_of(mask):
            if glyph.sum() <= 3:
                continue
            if _slash(glyph):
                slashes.append(len(digits))
                continue
            scores = sorted((float(np.mean(np.asarray(raster, dtype=bool) != glyph)), digit)
                            for digit, variants in large.hp_complete_bank.items()
                            for _, raster in variants if np.asarray(raster).shape == glyph.shape)
            if not scores or scores[0][0] > .08 or any(score - scores[0][0] < .04 and digit != scores[0][1]
                                                       for score, digit in scores[1:]):
                digits = None
                break
            digits.append((left, right, scores[0][1]))
        if not digits or len(digits) < 2 or len(slashes) > 1:
            continue
        if slashes:
            at = slashes[0] - 1
            if not 0 <= at < len(digits) - 1:
                continue
        else:
            gaps = [(b[0] - a[1], i) for i, (a, b) in enumerate(zip(digits, digits[1:]))]
            widest, at = max(gaps)
            if any(gap > widest - 6 for gap, i in gaps if i != at):
                continue
        current = "".join(d for *_, d in digits[:at + 1])
        maximum = "".join(d for *_, d in digits[at + 1:])
        if (not 1 <= len(current) <= 6 or not 1 <= len(maximum) <= 6
                or len(current) > 1 and current[0] == "0" or maximum[0] == "0"):
            continue
        if int(current) <= int(maximum):
            readings.append((int(current), int(maximum)))
    if len(readings) >= 2 and len(set(readings)) == 1:
        return readings[0]
    return None


class SelectedTargetReader:
    def __init__(self, ship_reader):
        self.large = getattr(ship_reader, 'reader', None)
        self.profiles = ship_reader.display_bar_profiles
        self.layout = json.loads(Path(__file__).with_name('selected-target-calibration.json').read_text())
        self.title = np.array([[pixel == '1' for pixel in row]
                               for row in self.layout['asteroid_title']], dtype=bool)

    @staticmethod
    def white(image, threshold):
        rgb = np.asarray(image.convert('RGB'))
        return (rgb.min(2) >= threshold) & (rgb.max(2).astype(int)-rgb.min(2) < 65)

    @classmethod
    def distance(cls, image, box, bank):
        readings = []
        for threshold in (120, 140, 160, 180):
            mask = cls.white(image.crop(box), threshold)
            if mask[:2].any() or mask[-2:].any() or mask[:, :2].any() or mask[:, -2:].any():
                continue
            edges = np.flatnonzero(np.diff(np.r_[False, mask.any(0), False]))
            runs = list(zip(edges[::2], edges[1::2]))
            if not 1 <= len(runs) <= 5:
                continue
            text, bottoms = '', []
            for left, right in runs:
                ys, _ = np.where(mask[:, left:right])
                glyph = mask[ys.min():ys.max()+1, left:right]
                scores = sorted((min((float(np.mean(glyph != variant)) for variant in variants
                    if glyph.shape == variant.shape), default=1.), char)
                    for char, variants in bank.items() if char.isdigit())
                if scores[0][0] > .06 or scores[1][0]-scores[0][0] < .06:
                    break
                text += scores[0][1]
                bottoms.append(int(ys.max()))
            if (len(text) == len(runs) and max(bottoms)-min(bottoms) <= 1
                    and abs((runs[0][0]+runs[-1][1])/2-mask.shape[1]/2) <= 5
                    and all(3 <= b[0]-a[1] <= 12 for a, b in zip(runs, runs[1:]))
                    and (len(text) == 1 or text[0] != '0')):
                readings.append(int(text))
        return readings[0] if len(readings) >= 2 and len(set(readings)) == 1 else None

    def read_native(self, image):
        """Native 2560 card: HP and shield ratios, range, and the asteroid title.

        Presence needs a readable range or HP; an unreadable card stays unknown.
        """
        large = self.large
        result = dict(present=None, kind=None, current_hp=None, max_hp=None, shield_current=None,
                      shield_max=None, distance=None, reason='selected_target_card_unverified',
                      display_only=True, profile='native-2560-target-card')
        hp_box = large.data['hp_box']
        hp = card_ratio(large, image, hp_box)
        shield = card_ratio(large, image, [hp_box[0], hp_box[1] + SHIELD_OFFSET,
                                           hp_box[2], hp_box[3] + SHIELD_OFFSET])
        distance = large.distance(image)[0]
        if hp is None and distance is None:
            return result
        helpers = type(large).distance.__globals__
        mask = helpers['mask'](image, large.data['title_box'])
        letters = [piece for piece in helpers['pieces'](mask) if piece[3].sum() > 3]
        # The same ASTEROID title test the large-HUD reader applies.
        title = (mask.shape == large.title.shape and float(np.mean(mask != large.title)) < .13
                 and len(letters) == 8)
        return {**result, 'present': True, 'kind': 'asteroid' if title else None,
                'current_hp': hp[0] if hp else None, 'max_hp': hp[1] if hp else None,
                'shield_current': shield[0] if shield else None, 'shield_max': shield[1] if shield else None,
                'distance': distance, 'reason': None}

    def read(self, image):
        result = dict(present=None, kind=None, current_hp=None, max_hp=None,
                      distance=None, reason='target_layout_unverified', display_only=True)
        if image.size == NATIVE_SIZE and self.large is not None and hasattr(self.large, 'hp_complete_bank'):
            return self.read_native(image)
        profiles = [p for p in self.profiles if tuple(p['frame_size']) == image.size]
        if image.size != tuple(self.layout['frame_size']) or len(profiles) != 1:
            return result
        bank = profiles[0]['bank']
        current, maximum, reason = ShipTelemetryReader._display_ratio(image, self.layout['hp_box'], bank)
        if reason:
            return {**result, 'reason': 'selected_target_card_unverified'}
        # Complete HP at the measured selected-card location establishes presence.
        # This is neither an asteroid identity nor permission to interact with it.
        title = self.white(image.crop(self.layout['title_box']), 160)
        asteroid = (np.mean(title != self.title) <= .02
                    and np.count_nonzero(title & self.title)/max(1, np.count_nonzero(self.title)) >= .95)
        return {**result, 'present': True, 'kind': 'asteroid' if asteroid else None,
                'current_hp': current, 'max_hp': maximum,
                'distance': self.distance(image, self.layout['distance_box'], bank),
                'reason': None, 'profile': self.layout['name']}
