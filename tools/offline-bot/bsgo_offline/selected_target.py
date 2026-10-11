"""Read the selected-target card independently of optional asteroid automation.

The measured native display profile is deliberately narrow. Other layouts and
missing cards remain unknown; an unreadable card is never proof of no selection.
"""
import json
from pathlib import Path

import numpy as np

from .ship_telemetry import ShipTelemetryReader


class SelectedTargetReader:
    def __init__(self, ship_reader):
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

    def read(self, image):
        result = dict(present=None, kind=None, current_hp=None, max_hp=None,
                      distance=None, reason='target_layout_unverified', display_only=True)
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
