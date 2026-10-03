"""Explicit native enlarged-HUD development profile; no input or auto-layout guessing."""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


PROFILE = 'largehud-20261003'


def mask(image, box):
    rgb = np.asarray(image.crop(box).convert('RGB'))
    return (rgb.min(2) >= 160) & (rgb.max(2).astype(int) - rgb.min(2) < 65)


def pieces(a):
    edges = np.flatnonzero(np.diff(np.r_[False, a.any(0), False]))
    result = []
    for left, right in zip(edges[::2], edges[1::2]):
        ys, xs = np.where(a[:, left:right])
        result.append((int(left), int(right), int(ys.min()),
                       a[ys.min():ys.max()+1, left:right]))
    return result


def normalized(a):
    return np.asarray(Image.fromarray(a.astype('uint8')*255).resize((12, 10),
                      Image.Resampling.NEAREST)) > 0


class LargeHudReader:
    def __init__(self):
        self.data = json.loads((Path(__file__).parent/'largehud-calibration.json').read_text())
        self.title = np.array(self.data['asteroid_title'], dtype=bool)
        self.bank = {k: [np.array(a, dtype=bool) for a in v]
                     for k, v in self.data['digits'].items()}

    def number(self, glyphs, field='hp'):
        text, confidence = '', 1.
        for _, _, top, g in glyphs:
            if g.shape[0] not in (11, 12) or not 4 <= g.shape[1] <= 14:
                return None, 'incomplete_numeric_glyph', 0.
            scores = sorted((min(float(np.mean(normalized(g) != t)) for t in bank), digit)
                            for digit, bank in self.bank.items())
            error, digit = scores[0]
            if error > .20 or scores[1][0] - error < .035:
                return None, 'ambiguous_numeric_glyph', round(1-error, 3)
            # The live distance six extends one row above its neighbours.
            # Anchor its complete raster so removing that row cannot become
            # an accepted eleven-row six through normalization.
            if field == 'distance' and digit == '6':
                expected = np.array(self.data['distance_six'], dtype=bool)
                if top != 3 or g.shape != expected.shape or np.mean(g != expected) > .10:
                    return None, 'incomplete_numeric_glyph', 0.
            elif g.shape[0] != 11:
                return None, 'incomplete_numeric_glyph', 0.
            text += digit
            confidence = min(confidence, 1-error)
        if not text or (len(text) > 1 and text[0] == '0'):
            return None, 'invalid_numeric_text', 0.
        return int(text), None, round(confidence, 3)

    def distance(self, image):
        a = mask(image, self.data['distance_box'])
        gs = pieces(a)
        if (not 1 <= len(gs) <= 6 or a[:2].any() or a[-2:].any()
                or a[:, :2].any() or a[:, -2:].any()):
            return None, 'distance_geometry_incomplete', 0.
        if abs((gs[0][0]+gs[-1][1])/2+self.data['distance_box'][0]-875) > 2:
            return None, 'distance_not_centered', 0.
        if any(not 3 <= b[0]-a_[1] <= 8 for a_, b in zip(gs, gs[1:])):
            return None, 'distance_spacing_invalid', 0.
        return self.number(gs, field='distance')

    def maximum_hp(self, image):
        gs = pieces(mask(image, self.data['hp_box']))
        if not 6 <= len(gs) <= 9:
            return None, 'invalid_target_hp_format', 0.
        left, right, top, slash = gs[-5]
        ys, xs = np.where(slash)
        if (left != 72 or right != 79 or top != 3 or slash.shape != (12, 7)
                or not slash[0, -1] or not slash[-1, 0] or not 11 <= len(ys) <= 18
                or float(np.abs(xs/6+ys/11-1).max()) > .20):
            return None, 'invalid_target_hp_separator', 0.
        return self.number(gs[-4:])

    def speed(self, image):
        a = mask(image, self.data['speed_box'])
        if a[:4].any() or a[25:].any() or a[:, :2].any() or a[:, -2:].any():
            return None, 'clipped_or_shifted_speed', 0.
        patterns = {'1110111':'0', '0010010':'1', '1011101':'2', '1011011':'3',
                    '0111010':'4', '1101011':'5', '1101111':'6', '1010010':'7',
                    '1111111':'8', '1111011':'9'}
        regions = [(4,0,9,3), (0,4,3,8), (10,4,13,8), (4,9,9,12),
                   (0,13,3,17), (10,13,13,17), (4,18,9,21)]
        digits, dot, previous_end, confidence = [], None, None, 1.
        for left, right, top, g in pieces(a):
            if g.shape == (3, 3):
                if (top != 22 or not g.all() or dot is not None or previous_end is None
                        or not 3 <= left-previous_end <= 7):
                    return None, 'invalid_speed_decimal', 0.
                dot = len(digits)
                previous_end = right
                continue
            if previous_end is not None and not 3 <= left-previous_end <= 7:
                return None, 'invalid_speed_spacing', 0.
            previous_end = right
            if right-left < 10:
                expected = np.array(self.data['speed_one'], dtype=bool)
                if top != 4 or g.shape != expected.shape or np.mean(g != expected) > .05:
                    return None, 'incomplete_speed_one', 0.
                digits.append('1')
                continue
            if top != 4 or g.shape != (21, 13):
                return None, 'incomplete_speed_glyph', 0.
            fractions = [float(g[y0:y1,x0:x1].mean()) for x0,y0,x1,y1 in regions]
            if any(.20 < f < .55 for f in fractions):
                return None, 'ambiguous_speed_segment', 0.
            key = ''.join('1' if f >= .55 else '0' for f in fractions)
            if key not in patterns:
                return None, 'invalid_speed_segments', 0.
            digits.append(patterns[key])
            confidence = min(confidence, min(max(f, 1-f) for f in fractions))
        if dot not in (1, 2) or len(digits) != dot+1:
            return None, 'missing_or_invalid_speed_decimal', 0.
        value = float(''.join(digits[:dot])+'.'+digits[-1])
        if value > 44 or (dot == 2 and digits[0] == '0'):
            return None, 'speed_out_of_range', 0.
        return value, None, round(confidence, 3)

    def read(self, image, captured_at=None):
        result = {'calibration_profile': PROFILE, 'calibrated_size': [2560,1440],
                  'distance': None, 'speed': None, 'valid': False, 'reasons': [],
                  'calibration_validation': 'stationary_development; motion_unvalidated'}
        if image.size != (2560,1440):
            return {**result, 'reasons': ['layout_size_mismatch']}
        distance, de, dc = self.distance(image)
        speed, se, sc = self.speed(image)
        title = mask(image, self.data['title_box'])
        asteroid = bool(title.shape == self.title.shape and np.mean(title != self.title) < .13
                        and len(pieces(title)) == 8)
        hp, he, hc = self.maximum_hp(image) if asteroid else (None, None, 0.)
        portrait = np.asarray(image.crop(self.data['portrait_box']).convert('L').resize((12,12)))//32
        reasons = [e for e in (de, se, he, None if asteroid else 'asteroid_title_not_verified') if e]
        result.update(distance=distance, speed=speed, distance_confidence=dc, speed_confidence=sc,
                      asteroid_selected=asteroid, target_max_hp=hp, target_hp_confidence=hc,
                      target_identity=f'asteroid:hp:{hp}' if hp is not None else None,
                      target_portrait=portrait.flatten().tolist() if asteroid else None,
                      reasons=reasons, ocr_uncertainty={name: {'reason': reason}
                          for name, reason in [('distance',de), ('speed',se), ('target_max_hp',he)] if reason},
                      valid=not reasons, captured_at=time.time() if captured_at is None else captured_at)
        return result
