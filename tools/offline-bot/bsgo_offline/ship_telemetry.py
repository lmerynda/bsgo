"""Display-only native ship instruments, independent of mining action gates.

The own-ship bars contain dim translucent separators and digits over colored
fills. Require a complete ratio, measured glyphs and agreement across brightness
thresholds. These readings never replace the calibrated fields used for input.
"""
import json
from pathlib import Path
import numpy as np


class ShipTelemetryReader:
    def __init__(self, reader):
        self.reader = reader
        calibration = json.loads((Path(__file__).with_name('ship-telemetry-calibration.json')).read_text())
        self.one_variants = [(variant['top'], np.array(
            [[c == '1' for c in row] for row in variant['rows']], dtype=bool))
            for variant in calibration['speed_one_variants']]
        self.digit_variants = [(variant['digit'], variant['top'], np.array(
            [[c == '1' for c in row] for row in variant['rows']], dtype=bool))
            for variant in calibration.get('speed_digit_variants', [])]

    def _number(self, glyphs):
        text, confidence = '', 1.
        if not 1 <= len(glyphs) <= 5:
            return None
        for left, right, top, glyph in glyphs:
            scores = {}
            for digit, variants in self.reader.hp_complete_bank.items():
                for expected_top, expected in variants:
                    if top == expected_top and glyph.shape == expected.shape:
                        error = float(np.mean(glyph != expected))
                        scores[digit] = min(scores.get(digit, 1.), error)
            best = sorted((error, digit) for digit, error in scores.items())
            if (not best or best[0][0] > .04 or
                    len(best) > 1 and best[1][0]-best[0][0] < .06):
                return None
            text += best[0][1]
            confidence = min(confidence, 1-best[0][0])
        if len(text) > 1 and text[0] == '0':
            return None
        return int(text), confidence

    @staticmethod
    def _separator(rgb, x):
        # The slash is translucent; measure its contrast against the same
        # row of the bar rather than requiring opaque neutral-white pixels.
        light = rgb.astype(float).mean(2)
        patch = light[3:15, x:x+7]
        baseline = np.median(light[3:15, [x-2, x-1, x+7, x+8]], axis=1)
        peaks = patch.argmax(1)
        if (np.any(patch.max(1)-baseline < 20) or
                np.max(np.abs(peaks-np.linspace(6, 0, 12))) > .65):
            return False
        return True

    def ratio(self, image, box):
        # Column runs retain complete native glyphs, without resizing.
        rgb = np.asarray(image.crop(box).convert('RGB'))
        candidates = []
        for x in range(48, 91):
            if not self._separator(rgb, x):
                continue
            for threshold in (100, 120, 140, 160):
                a = (rgb.min(2) >= threshold) & (rgb.max(2).astype(int)-rgb.min(2) < 65)
                a[:, x:x+7] = False
                if a[:2].any() or a[-2:].any():
                    continue
                edges = np.flatnonzero(np.diff(np.r_[False, a.any(0), False]))
                glyphs = []
                for left, right in zip(edges[::2], edges[1::2]):
                    ys, _ = np.where(a[:, left:right])
                    glyphs.append((int(left), int(right), int(ys.min()),
                        a[ys.min():ys.max()+1, left:right]))
                current = [g for g in glyphs if g[1] <= x]
                maximum = [g for g in glyphs if g[0] >= x+7]
                if (len(current)+len(maximum) != len(glyphs) or not current or not maximum
                        or not 4 <= x-current[-1][1] <= 12
                        or not 4 <= maximum[0][0]-(x+7) <= 12
                        or abs((current[0][0]+maximum[-1][1])/2-75.5) > 3
                        or any(not 2 <= b[0]-a_[1] <= 8 for group in (current, maximum)
                               for a_, b in zip(group, group[1:]))):
                    continue
                first, limit = self._number(current), self._number(maximum)
                if first and limit and 0 <= first[0] <= limit[0] and limit[0] > 0:
                    candidates.append((first[0], limit[0], min(first[1], limit[1])))
        values = {(a, b) for a, b, _ in candidates}
        if len(values) != 1 or len(candidates) < 2:
            return None, None, 'complete_ratio_not_verified'
        current, maximum = next(iter(values))
        return current, maximum, None

    def read(self, image):
        result = dict(profile='native-ship-telemetry', display_only=True,
                      speed=None, hull_current=None, hull_max=None,
                      energy_current=None, energy_max=None, reasons={})
        if image.size != (2560, 1440):
            result['reasons']['layout'] = 'native_layout_required'
            return result
        hull = tuple(self.reader.data['hull_box'])
        energy = tuple(value+(24 if index % 2 else 0) for index, value in enumerate(hull))
        for name, box in (('hull', hull), ('energy', energy)):
            current, maximum, reason = self.ratio(image, box)
            result[name+'_current'], result[name+'_max'] = current, maximum
            if reason:
                result['reasons'][name] = reason
        speeds = [self.reader.speed(image, minimum_brightness=threshold, raster_error=.08,
                                   one_variants=self.one_variants,
                                   digit_variants=self.digit_variants)[0]
                  for threshold in (80, 100, 110, 120, 140, 160)]
        valid = [value for value in speeds if value is not None]
        if len(set(valid)) == 1 and len(valid) >= 2:
            result['speed'] = valid[0]
        else:
            result['reasons']['speed'] = 'complete_speed_not_verified'
        return result
