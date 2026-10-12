"""Explicit native enlarged-HUD development profile; no input or auto-layout guessing."""
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image


PROFILE = 'largehud-20261003'


def mask(image, box, minimum_brightness=160):
    rgb = np.asarray(image.crop(box).convert('RGB'))
    return (rgb.min(2) >= minimum_brightness) & (rgb.max(2).astype(int) - rgb.min(2) < 65)


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
        self.hull_bank = {digit: [(variant['top'],
            np.array([[c == '1' for c in row] for row in variant['rows']],dtype=bool))
            for variant in variants] for digit, variants in self.data['hull_digits'].items()}
        self.hp_complete_bank = {digit:list(variants) for digit,variants in self.hull_bank.items()}
        for digit,variants in self.data['target_hp_complete_digits'].items():
            self.hp_complete_bank.setdefault(digit,[]).extend((variant['top'],
                np.array([[c == '1' for c in row] for row in variant['rows']],dtype=bool))
                for variant in variants)

        self.hp_font_slots = self.build_hp_font_slots()

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
            elif field == 'hp' and digit == '6' and g.shape[0] == 12:
                expected = np.array(self.data['hp_six'], dtype=bool)
                if top != 3 or g.shape != expected.shape or np.mean(g != expected) > .05:
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
        # Complete native distance glyphs end at row14. A wholly empty row15
        # proves any lower pixels are separate scenery, such as the measured
        # star below516. Keep connected intrusions for the geometry rejection.
        # Row-15 ink touching no row-14 glyph ink is scenery too: a star passing
        # behind the translucent panel in flight split a valid 4436 this way.
        if not (a[15] & np.convolve(a[14], [1, 1, 1], 'same').astype(bool)).any():
            a[15:] = False
        gs = pieces(a)
        if (not 1 <= len(gs) <= 6 or a[:2].any() or a[-2:].any()
                or a[:, :2].any() or a[:, -2:].any()):
            return None, 'distance_geometry_incomplete', 0.
        if abs((gs[0][0]+gs[-1][1])/2+self.data['distance_box'][0]-875) > 2:
            return None, 'distance_not_centered', 0.
        if any(not 3 <= b[0]-a_[1] <= 8 for a_, b in zip(gs, gs[1:])):
            return None, 'distance_spacing_invalid', 0.
        return self.number(gs, field='distance')

    def three_digit_hp_layout(self, glyphs):
        """Require the measured recentered three-digit ratio, not a lost digit."""
        return [[left,right,top,glyph.shape[0]] for left,right,top,glyph in glyphs] == \
               self.data['hp_three_digit_layout']['glyph_bounds']

    def complete_hp_layout(self, glyphs):
        bounds = [[left,right,top,glyph.shape[0]] for left,right,top,glyph in glyphs]
        return next((layout for layout in self.data['hp_complete_layouts']
                     if bounds == layout['glyph_bounds']),None)

    @staticmethod
    def hp_font_slot_key(text, index):
        """Select measured font phase by wide/narrow character counts."""
        return (len(text)-1, text.count('1'), index,
                text[:index].count('1'), text[:index].count('/'), text[index])

    def build_hp_font_slots(self, excluded_labels=()):
        """Compose only complete measured glyphs; never learn from live OCR."""
        rasters = [np.array([[c == '1' for c in row] for row in rows], dtype=bool)
                   for rows in self.data['hp_font_slot_rasters']]
        slots = {}
        for sample in self.data['hp_font_slot_samples']:
            text = sample['label']
            if text in excluded_labels:
                continue
            for index, (left, right, top, raster) in enumerate(sample['glyphs']):
                glyph = rasters[raster]
                key = self.hp_font_slot_key(text, index)
                variants = slots.setdefault(key, [])
                if not any((left, right, top) == variant[:3]
                           and np.array_equal(glyph, variant[3]) for variant in variants):
                    variants.append((left, right, top, glyph))
        return slots

    def hp_font_slot_context(self, glyphs):
        """Return a candidate only when every font slot is already measured."""
        count = len(glyphs)-5
        if not 1 <= count <= 4:
            return None
        current, reason, _ = self.complete_number(glyphs[:-5], digit_count=count,
            field='current_hp', bank=self.hp_complete_bank, allow_zero=count == 1)
        maximum, maximum_reason, _ = self.complete_number(glyphs[-4:],
            field='target_max_hp', bank=self.hp_complete_bank)
        if reason or maximum_reason or current > maximum:
            return None
        text = f'{current}/{maximum}'
        if any(self.hp_font_slot_key(text, index) not in self.hp_font_slots
               for index in range(len(glyphs))):
            return None
        return text

    def compositional_hp_layout(self, glyphs):
        """Require every complete measured slot in this whole ratio's phase."""
        text = self.hp_font_slot_context(glyphs)
        if text is None:
            return None
        for index, (left, right, top, glyph) in enumerate(glyphs):
            variants = self.hp_font_slots[self.hp_font_slot_key(text, index)]
            if not any((left, right, top) == variant[:3]
                       and glyph.shape == variant[3].shape
                       and float(np.mean(glyph != variant[3])) <= .02
                       for variant in variants):
                return None
        return {'current_digit_count': len(glyphs)-5}

    def maximum_hp(self, image):
        gs = pieces(mask(image, self.data['hp_box']))
        if not 6 <= len(gs) <= 9:
            return None, 'invalid_target_hp_format', 0.
        left, right, top, slash = gs[-5]
        ys, xs = np.where(slash)
        # A measured narrow one on the maximum side recenters this complete
        # four-digit HP label by 5px, whether it ends 3781 or contains 4102.
        narrow_one_layout = ((left, right) == (77, 84) and len(gs) == 9
                             and any(top_ == 4 and glyph.shape == (11, 4)
                                 and min(float(np.mean(normalized(glyph) != template))
                                         for template in self.bank['1']) <= .02
                                 for _, _, top_, glyph in gs[-4:])
                             and abs((gs[0][0]+gs[-1][1])/2-76) <= .5)
        three_digit_layout = self.three_digit_hp_layout(gs)
        composed_layout = self.compositional_hp_layout(gs)
        # A decoded, fully measured context cannot use a looser anchor or
        # legacy route to admit geometry that contradicts its complete slots.
        if composed_layout is None and self.hp_font_slot_context(gs) is not None:
            return None, 'target_hp_slot_geometry_incomplete', 0.
        complete_layout = self.complete_hp_layout(gs) or composed_layout
        if (((left, right) != (72, 79) and not narrow_one_layout
                and not three_digit_layout and complete_layout is None)
                or top != 3 or slash.shape != (12, 7)
                or not slash[0, -1] or not slash[-1, 0] or not 11 <= len(ys) <= 18
                or float(np.abs(xs/6+ys/11-1).max()) > .20):
            return None, 'invalid_target_hp_separator', 0.
        if complete_layout is not None:
            return self.complete_number(gs[-4:],field='target_max_hp',bank=self.hp_complete_bank)
        if three_digit_layout:
            return self.complete_number(gs[-4:],field='target_max_hp')
        return self.complete_number(gs[-4:],field='target_max_hp',bank=self.hp_complete_bank)

    def current_hp(self, image, maximum_hp):
        """Read complete target HP with measured centered-ratio layouts."""
        if maximum_hp is None:
            return None, 'maximum_hp_context_unverified', 0.
        gs = pieces(mask(image, self.data['hp_box']))
        composed_layout = self.compositional_hp_layout(gs)
        # A decoded, fully measured context cannot use a looser anchor or
        # legacy route to admit geometry that contradicts its complete slots.
        if composed_layout is None and self.hp_font_slot_context(gs) is not None:
            return None, 'target_hp_slot_geometry_incomplete', 0.
        complete_layout = self.complete_hp_layout(gs) or composed_layout
        if complete_layout is not None:
            count = complete_layout['current_digit_count']
            value,reason,confidence = self.complete_number(gs[:-5],digit_count=count,
                field='current_hp',bank=self.hp_complete_bank,allow_zero=count == 1)
            if reason:
                return None,reason,confidence
            if value > maximum_hp:
                return None,'current_hp_exceeds_maximum',confidence
            return value,None,confidence
        if self.three_digit_hp_layout(gs):
            value, reason, confidence = self.complete_number(gs[:3],digit_count=3,
                                                              field='current_hp')
            if reason:
                return None, reason, confidence
            if value > maximum_hp:
                return None, 'current_hp_exceeds_maximum', confidence
            return value, None, confidence
        # The development crops cover four-digit current values. A digit-count
        # transition has its own anchored layout above; removing a glyph from
        # a four-digit value must not silently become a valid lower HP.
        if len(gs) != 9:
            return None, 'unvalidated_current_hp_digit_count', 0.
        current, separator = gs[:4], gs[-5]
        # The measured complete leading 4 in 4500/4500 begins at column 1.
        # Admit that exact anchored raster, retaining the wider margin guard
        # for other digits and the existing glyph/spacing/slash checks.
        leading_one_pixel_four = (current[0][0] == 1
            and current[0][2] == 4 and current[0][3].shape == (11, 14)
            and min(float(np.mean(normalized(current[0][3]) != template))
                    for template in self.bank['4']) <= .02)
        # In the native 4384/4384 label, a complete trailing 4 is one pixel
        # wider than a trailing 0 and leaves a six-pixel pre-slash gap.
        # Require its measured full raster; do not admit a clipped edge.
        trailing_six_pixel_four = (separator[0]-current[-1][1] == 6
            and current[-1][2] == 4 and current[-1][3].shape == (11, 14)
            and min(float(np.mean(normalized(current[-1][3]) != template))
                    for template in self.bank['4']) <= .02)
        if ((current[0][0] < 2 and not leading_one_pixel_four)
                or (not 7 <= separator[0]-current[-1][1] <= 8
                    and not trailing_six_pixel_four)
                or any(not 2 <= right[0]-left[1] <= 8
                       for left, right in zip(current, current[1:]))
                or any(not ((top == 4 and glyph.shape[0] == 11)
                            or (top == 3 and glyph.shape[0] == 12))
                       for _, _, top, glyph in current)):
            return None, 'current_hp_geometry_incomplete', 0.
        value, reason, confidence = self.complete_number(current,field='current_hp',
                                                         bank=self.hp_complete_bank)
        if reason:
            return None, 'current_hp_' + reason, confidence
        if value > maximum_hp:
            return None, 'current_hp_exceeds_maximum', confidence
        return value, None, confidence

    def speed(self, image, *, minimum_brightness=160, raster_error=.02, one_variants=(), digit_variants=()):
        # The wider archival crop includes scenery before the fixed HUD text.
        # Read the measured text region with a two-pixel left margin; stars in
        # unused padding must not be mistaken for partial HUD digits.
        a = mask(image, self.data['speed_text_box'], minimum_brightness)
        # A native star may also land wholly within the two-pixel margin.
        # Clear only separate components ending before the fixed font origin.
        # A glyph reaching into that margin spans farther right and retains
        # its pixels, so the clipping guard still rejects it.
        for left, right, _, _ in pieces(a):
            if right <= 2:
                a[:, left:right] = False
        if a[:4].any() or a[25:].any() or a[:, :2].any() or a[:, -2:].any():
            return None, 'clipped_or_shifted_speed', 0.
        gs = pieces(a)
        if not gs or gs[0][0] not in (2, 4):
            return None, 'speed_not_anchored', 0.
        patterns = {'1110111':'0', '0010010':'1', '1011101':'2', '1011011':'3',
                    '0111010':'4', '1101011':'5', '1101111':'6', '1010010':'7',
                    '1111111':'8', '1111011':'9'}
        regions = [(4,0,9,3), (0,4,3,8), (10,4,13,8), (4,9,9,12),
                   (0,13,3,17), (10,13,13,17), (4,18,9,21)]
        digits, dot, previous_end, confidence = [], None, None, 1.
        for left, right, top, g in gs:
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
            measured = [(float(np.mean(g != variant)), digit) for digit, variant_top, variant in digit_variants
                        if top == variant_top and g.shape == variant.shape]
            measured_digits = {digit for error, digit in measured if error <= .02}
            if len(measured_digits) > 1:
                return None, 'ambiguous_speed_variant', 0.
            if measured_digits:
                digits.append(min(measured)[1])
                confidence = min(confidence, 1-min(measured)[0])
                continue
            if right-left < 10:
                expected = np.array(self.data['speed_one'], dtype=bool)
                if top != 4 or g.shape != expected.shape or np.mean(g != expected) > .05:
                    if not any(top == variant_top and g.shape == variant.shape
                               and np.mean(g != variant) <= .02
                               for variant_top, variant in one_variants):
                        return None, 'incomplete_speed_one', 0.
                digits.append('1')
                continue
            if top == 4 and g.shape == (21, 12):
                candidates = [(float(np.mean(g != np.array(t, dtype=bool))), digit)
                              for digit, t in self.data.get('speed_complete_variants', {}).items()
                              if np.array(t).shape == g.shape]
                if candidates and min(candidates)[0] <= raster_error:
                    digits.append(min(candidates)[1])
                    confidence = min(confidence, 1-min(candidates)[0])
                    continue
                return None, 'unverified_speed_raster', 0.
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

    def complete_number(self, glyphs, *, digit_count=4, field='hull', bank=None,
                        allow_zero=False):
        """Match measured shared HUD font rasters without resizing glyphs."""
        text, confidence = '', 1.
        for _, _, top, glyph in glyphs:
            scores = []
            for digit, variants in (self.hull_bank if bank is None else bank).items():
                for expected_top, expected in variants:
                    if top == expected_top and glyph.shape == expected.shape:
                        scores.append((float(np.mean(glyph != expected)), digit))
            by_digit = sorted((min(error for error, d in scores if d == digit), digit)
                              for digit in {d for _, d in scores})
            if (not by_digit or by_digit[0][0] > .02
                    or len(by_digit) > 1 and by_digit[1][0]-by_digit[0][0] < .035):
                return None, 'incomplete_or_ambiguous_'+field+'_glyph', 0.
            error, digit = by_digit[0]
            text += digit
            confidence = min(confidence, 1-error)
        if len(text) != digit_count or text.startswith('0') and not (allow_zero and text == '0'):
            return None, 'unvalidated_'+field+'_digit_count', 0.
        return int(text), None, round(confidence, 3)

    def hull(self, image):
        """Read the measured player hull ratio independently of target state."""
        a = mask(image, self.data['hull_box'])
        gs = pieces(a)
        rejected = (None, None, 'hull_ratio_geometry_incomplete', 0., 0.)
        if (len(gs) != 9 or a[:2].any() or a[-2:].any()
                or a[:, :2].any() or a[:, -2:].any()
                or abs((gs[0][0]+gs[-1][1])/2-75.5) > .5):
            return rejected
        current, separator, maximum = gs[:4], gs[4], gs[-4:]
        left, right, top, slash = separator
        ys, xs = np.where(slash)
        if ((left, right) not in ((72, 79), (68, 75)) or top != 3
                or slash.shape != (12, 7) or not slash[0, -1] or not slash[-1, 0]
                or not 11 <= len(ys) <= 18
                or float(np.abs(xs/6+ys/11-1).max()) > .20
                or not 6 <= left-current[-1][1] <= 8
                or not 6 <= maximum[0][0]-right <= 8
                or any(not 3 <= b[0]-a_[1] <= 6
                       for group in (current, maximum)
                       for a_, b in zip(group, group[1:]))):
            return rejected
        value, reason, confidence = self.complete_number(current)
        limit, limit_reason, limit_confidence = self.complete_number(maximum)
        if reason or limit_reason:
            return None, None, reason or limit_reason, 0., 0.
        if value > limit:
            return None, None, 'hull_current_exceeds_maximum', 0., 0.
        return value, limit, None, confidence, limit_confidence

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
        current_hp, current_reason, current_confidence = (
            self.current_hp(image, hp) if asteroid else (None, None, 0.))
        hull_current, hull_max, hull_reason, hull_confidence, hull_max_confidence = self.hull(image)
        portrait = np.asarray(image.crop(self.data['portrait_box']).convert('L').resize((12,12)))//32
        reasons = [e for e in (de, se, he, current_reason, hull_reason,
                              None if asteroid else 'asteroid_title_not_verified') if e]
        result.update(distance=distance, speed=speed, distance_confidence=dc, speed_confidence=sc,
                      asteroid_selected=asteroid, target_max_hp=hp, target_hp_confidence=hc,
                      target_current_hp=current_hp,
                      target_current_hp_confidence=current_confidence,
                      hull_current=hull_current, hull_max=hull_max,
                      hull_current_confidence=hull_confidence,
                      hull_max_confidence=hull_max_confidence,
                      target_identity=f'asteroid:hp:{hp}' if hp is not None else None,
                      target_portrait=portrait.flatten().tolist() if asteroid else None,
                      reasons=reasons, ocr_uncertainty={name: {'reason': reason}
                          for name, reason in [('distance',de), ('speed',se),
                              ('target_max_hp',he), ('target_current_hp',current_reason),
                              ('hull_current',hull_reason), ('hull_max',hull_reason)] if reason},
                      valid=not reasons, captured_at=time.time() if captured_at is None else captured_at)
        return result
