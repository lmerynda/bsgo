"""Independent, display-only own-ship instruments from current game pixels.

Unknown layouts register ship bars and speed separately. Registration uses the
existing measured font readers; it neither learns values nor grants input.
"""
from dataclasses import dataclass
import math

import numpy as np
from PIL import Image

from .hud_regions import unique_boxes
from .ship_telemetry import ShipTelemetryReader


@dataclass(frozen=True)
class _Registration:
    scale: float
    boxes: tuple

    def mapping(self):
        return dict(self.boxes)

    def compatible(self, other):
        return bool(other and self.scale == other.scale and all(
            max(abs(a-b) for a, b in zip(box, other.mapping()[name])) <= 2
            for name, box in self.boxes))


class _Component:
    def __init__(self):
        self.reset('waiting_for_ship_anchors')

    def reset(self, reason):
        self.registration, self.count, self.reason = None, 0, reason

    def confirm(self, registration):
        self.count = min(3, self.count+1) if registration.compatible(self.registration) else 1
        self.registration = registration
        self.reason = 'ship_regions_registered' if self.count == 3 else 'confirming_ship_regions'

    def metadata(self):
        return {'status': 'registered' if self.count == 3 else 'pending',
                'reason': self.reason, 'consistent_frames': self.count,
                'required_frames': 3,
                'hud_scale': self.registration.scale if self.registration else None,
                'region_boxes_in_normalized_pixels': self.registration.mapping()
                    if self.registration else {}}


class GameStateReader:
    """Read ship instruments independently of target and scanner admission.

    Only geometry persists between captures. Every displayed number is read anew.
    The corner bound and paired hull/energy bars exclude other selected targets;
    speed has its own registration and cannot invalidate the ship bars.
    """
    def __init__(self, large_reader):
        self.reader = ShipTelemetryReader(large_reader)
        self.data = large_reader.data
        self._bars, self._speed = _Component(), _Component()
        self._context = self._sequence = self._observed_at = None

    def _reset(self, reason):
        self._bars.reset(reason)
        self._speed.reset(reason)

    def reset(self, reason):
        self._reset(reason)
        self._context = self._sequence = self._observed_at = None

    @staticmethod
    def _empty():
        return dict(profile='native-ship-telemetry', display_only=True, speed=None,
                    hull_current=None, hull_max=None, energy_current=None,
                    energy_max=None, reasons={})

    @staticmethod
    def _bounds(box, image):
        return 0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height

    def _canvas(self, image, boxes):
        canvas = Image.new('RGB', (2560, 1440))
        for name, box in boxes.items():
            destination = (self.data['speed_text_box'] if name == 'speed' else
                           tuple(v+(24 if i % 2 else 0) for i, v in enumerate(self.data['hull_box']))
                           if name == 'energy' else self.data['hull_box'])
            canvas.paste(image.crop(box), tuple(destination[:2]))
        return canvas

    @staticmethod
    def _separators(image):
        """Locate the existing reader's translucent diagonal contrast anchor."""
        light = np.asarray(image.convert('RGB'), dtype=float).mean(2)
        height, width = light.shape
        if height < 12 or width < 11:
            return []
        shape = (height-11, width-10)
        valid = np.ones(shape, bool)
        for row, expected in enumerate(np.linspace(6, 0, 12)):
            # x is two pixels before the seven-pixel slash so both background
            # samples remain in bounds, matching ShipTelemetryReader._separator.
            patches = [light[row:row+shape[0], column:column+shape[1]]
                       for column in range(2, 9)]
            peak = np.argmax(patches, axis=0)
            maximum = np.maximum.reduce(patches)
            backgrounds = [
                light[row:row+shape[0], column:column+shape[1]]
                for column in (0, 1, 9, 10)]
            background = (sum(backgrounds)-np.minimum.reduce(backgrounds)
                          -np.maximum.reduce(backgrounds))/2
            valid &= (maximum-background >= 20) & (np.abs(peak-expected) <= .65)
        ys, xs = np.where(valid)
        # Cap candidate work; repeated anchors remain ambiguous.
        return list(zip((xs+2).tolist(), ys.tolist())) if len(xs) <= 128 else []

    def _ratio_boxes(self, image, slash_x, slash_y):
        """Propose centered ratio crops; the unchanged ratio reader validates."""
        top = slash_y-3
        left, right = max(0, slash_x-110), min(image.width, slash_x+117)
        if top < 0 or top+18 > image.height:
            return []
        rgb = np.asarray(image.crop((left, top, right, top+18)).convert('RGB'))
        local_x = slash_x-left
        candidates = []
        for threshold in (100, 120, 140, 160):
            a = (rgb.min(2) >= threshold) & (rgb.max(2).astype(int)-rgb.min(2) < 65)
            a[:, local_x:local_x+7] = False
            edges = np.flatnonzero(np.diff(np.r_[False, a.any(0), False]))
            runs = []
            for start, end in zip(edges[::2], edges[1::2]):
                ys, _ = np.where(a[:, start:end])
                runs.append((int(start), int(end), int(ys.min()),
                             a[ys.min():ys.max()+1, start:end]))
            before = [g for g in runs if g[1] <= local_x]
            after = [g for g in runs if g[0] >= local_x+7]
            currents = [before[-count:] for count in range(1, min(5, len(before))+1)
                        if self.reader._number(before[-count:]) is not None]
            maxima = [after[:count] for count in range(1, min(5, len(after))+1)
                      if self.reader._number(after[:count]) is not None]
            for current in currents:
                for maximum in maxima:
                    center = left+(current[0][0]+maximum[-1][1])/2
                    x = round(center-75.5)
                    box = (x, top, x+151, top+18)
                    if self._bounds(box, image):
                        candidates.append(box)
        return sorted(set(candidates))

    def _locate_bars(self, image):
        # Own ship is the first panel in the upper left. Do not discover
        # squadron cards across the screen or substitute them when it vanishes.
        corner = image.crop((0, 0, min(image.width//4, 384), min(image.height//4, 240)))
        candidates = []
        for x, y in self._separators(corner):
            for hull in self._ratio_boxes(corner, x, y):
                if hull[0] > 192 or hull[1] > 140:
                    continue
                energy = tuple(v+(24 if index % 2 else 0) for index, v in enumerate(hull))
                if not self._bounds(energy, image):
                    continue
                first = self.reader.ratio(image, hull)
                second = self.reader.ratio(image, energy)
                if first[2] is None and second[2] is None:
                    candidates.append(hull)
        return [dict(hull=box, energy=tuple(v+(24 if index % 2 else 0)
                     for index, v in enumerate(box))) for box in unique_boxes(candidates)]

    @staticmethod
    def _dots(brightness):
        # Exact 3x3 positive raster with an empty one-pixel border, matching
        # the existing decimal template without a full template scan.
        height, width = brightness.shape
        shape = (height-4, width-4)
        if min(shape) <= 0:
            return []
        center = np.minimum.reduce([brightness[y:y+shape[0], x:x+shape[1]]
                                    for y in (1, 2, 3) for x in (1, 2, 3)])
        border = np.maximum.reduce([brightness[y:y+shape[0], x:x+shape[1]]
            for y in range(5) for x in range(5) if y in (0, 4) or x in (0, 4)])
        found = set()
        for threshold in (80, 100, 110, 120, 140, 160):
            ys, xs = np.where((center >= threshold) & (border < threshold))
            if len(xs) <= 256:
                found.update(zip(xs.tolist(), ys.tolist()))
        return found

    def _speed_origins(self, image, dot_x, dot_y):
        left, top = max(0, dot_x-55), dot_y-22
        if top < 0:
            return []
        rgb = np.asarray(image.crop((left, top+4, dot_x, top+25)).convert('RGB'))
        starts = set()
        for threshold in (80, 100, 110, 120, 140, 160):
            a = (rgb.min(2) >= threshold) & (rgb.max(2).astype(int)-rgb.min(2) < 65)
            edges = np.flatnonzero(np.diff(np.r_[False, a.any(0), False]))
            runs = [(int(start), int(end)) for start, end in zip(edges[::2], edges[1::2])]
            if not runs or not 3 <= dot_x-left-runs[-1][1] <= 7:
                continue
            first = len(runs)-1
            # Include every adjacent component of the same number. A crop of
            # 25.7 must never be relocated around its trailing 5.7 suffix.
            while first and runs[first][0]-runs[first-1][1] <= 7:
                first -= 1
            start = left+runs[first][0]
            starts.update((start-2, start-4))
        return starts

    def _speed_leading_clear(self, image, box):
        rgb = np.asarray(image.crop((max(0, box[0]-14), box[1]+4, box[0], box[1]+25)).convert('RGB'))
        if not rgb.size:
            return True
        return not ((rgb.min(2) >= 80) & (rgb.max(2).astype(int)-rgb.min(2) < 65)).any()

    def _locate_speed(self, image):
        left, top = image.width*3//10, image.height//2
        region = image.crop((left, top, image.width*3//4, max(top, image.height-20)))
        rgb = np.asarray(region.convert('RGB'))
        minimum, maximum = rgb.min(2), rgb.max(2)
        brightness = np.where(maximum.astype(np.int16)-minimum < 65, minimum, 0)
        dots = self._dots(brightness)
        boxes = []
        canvas = Image.new('RGB', (2560, 1440))
        for x, y in sorted(dots):
            dot_x, dot_y = x+left+1, y+top+1
            for origin in self._speed_origins(image, dot_x, dot_y):
                box = (origin, dot_y-22, origin+58, dot_y-22+29)
                if not self._bounds(box, image) or not self._speed_leading_clear(image, box):
                    continue
                canvas.paste(image.crop(box), tuple(self.data['speed_text_box'][:2]))
                speeds = [self.reader.reader.speed(canvas, minimum_brightness=threshold,
                    raster_error=.08, one_variants=self.reader.one_variants,
                    digit_variants=self.reader.digit_variants)[0]
                    for threshold in (80, 100, 110, 120, 140, 160)]
                valid = [value for value in speeds if value is not None]
                if len(set(valid)) == 1 and len(valid) >= 2:
                    boxes.append(box)
        return [dict(speed=box) for box in unique_boxes(boxes)]

    def _current(self, normalized, registration):
        return self.reader.read(self._canvas(normalized, registration.mapping()))

    def read(self, image, *, context, sequence, observed_at):
        result = self._empty()
        identity = (context, image.size)
        if identity != self._context:
            self._reset('observation_context_changed')
            self._sequence = self._observed_at = None
            self._context = identity
        valid_identity = (type(sequence) is int and sequence >= 0
                          and isinstance(observed_at, (int, float)) and not isinstance(observed_at, bool)
                          and math.isfinite(observed_at))
        rejected = (not valid_identity or self._sequence is not None and
                    (sequence <= self._sequence or observed_at <= self._observed_at))
        if rejected:
            self._reset('waiting_for_distinct_newer_capture')
            result['reasons'] = dict.fromkeys(('hull', 'energy', 'speed'), 'waiting_for_distinct_newer_capture')
        else:
            if self._observed_at is not None and observed_at-self._observed_at > 2:
                self._reset('calibration_observation_gap')
            self._sequence, self._observed_at = sequence, observed_at
            if image.size == (2560, 1440):
                result = self.reader.read(image)
                result['calibration'] = {'status': 'existing_profile', 'reason': 'native_profile_size',
                    'source_frame_size': image.size, 'validation': 'native_profile_size',
                    'input_authorized': False, 'numeric_accuracy_independently_validated': False}
                return result
            width, height = image.size
            scales = sorted({1., round(width/2560, 4), round(height/1440, 4), .75, 1.2, 1.25, 1.5, 2.})
            normalized = {scale: image if scale == 1 else image.resize(
                (round(width/scale), round(height/scale)), Image.Resampling.BOX)
                for scale in scales if .5 <= scale <= 2}
            for name, component, locate, fields in (
                ('own_ship', self._bars, self._locate_bars,
                 ('hull_current', 'hull_max', 'energy_current', 'energy_max')),
                ('speed', self._speed, self._locate_speed, ('speed',))):
                current = None
                if component.registration is not None:
                    current = self._current(normalized[component.registration.scale], component.registration)
                    if name == 'speed' and (current['speed'] is None or not self._speed_leading_clear(
                            normalized[component.registration.scale], component.registration.mapping()['speed'])):
                        current = None
                    elif name == 'own_ship' and (
                            current['hull_current'] is None and current['energy_current'] is None
                            or component.count < 3 and (current['hull_current'] is None
                                                       or current['energy_current'] is None)):
                        current = None
                geometry_validated = current is not None
                candidates = [] if current is not None else [_Registration(scale, tuple(sorted(boxes.items())))
                    for scale, candidate_image in normalized.items() for boxes in locate(candidate_image)]
                if geometry_validated:
                    component.confirm(component.registration)
                    if component.count < 3:
                        current = None
                elif len(candidates) == 1:
                    component.confirm(candidates[0])
                    if component.count == 3:
                        current = self._current(normalized[component.registration.scale], component.registration)
                elif not candidates and name == 'own_ship' and component.count == 3:
                    # A known bar location can still supply the intact current
                    # instrument when the other bar is covered. Never reuse a value.
                    current = self._current(normalized[component.registration.scale], component.registration)
                    if current['hull_current'] is None and current['energy_current'] is None:
                        current = None
                        component.reset('own_ship_anchor_missing_or_ambiguous')
                else:
                    component.reset(name+'_anchor_missing_or_ambiguous' if not candidates
                                    else name+'_multiple_geometry_models')
                if current is not None:
                    result.update({field: current[field] for field in fields})
                    for instrument in (('hull', 'energy') if name == 'own_ship' else ('speed',)):
                        if instrument in current['reasons']:
                            result['reasons'][instrument] = current['reasons'][instrument]
                else:
                    for instrument in (('hull', 'energy') if name == 'own_ship' else ('speed',)):
                        result['reasons'][instrument] = component.reason
        components = {'own_ship': self._bars.metadata(), 'speed': self._speed.metadata()}
        registered = sum(item['status'] == 'registered' for item in components.values())
        result['calibration'] = {'status': 'registered' if registered == 2 else 'partial' if registered else 'pending',
            'reason': 'independent_ship_instruments', 'components': components,
            'source_frame_size': image.size, 'last_acquisition_sequence': self._sequence,
            'last_observed_at': self._observed_at, 'validation': 'geometry_registration_only',
            'numeric_accuracy_independently_validated': False, 'input_authorized': False}
        return result
