"""Read-only HUD registration using measured anchors and existing font guards.

Registration locates regions; it never learns numeric labels from its own OCR,
changes a font bank, or authorizes input at a previously unvalidated size.
"""
from dataclasses import dataclass

import numpy as np
from PIL import Image


def white(image):
    rgb = np.asarray(image.convert("RGB"))
    return (rgb.min(2) >= 160) & (rgb.max(2).astype(np.int16) - rgb.min(2) < 65)


def find_template(area, template, *, limit=256):
    """Bound candidate work before checking complete positive/negative rasters."""
    height, width = template.shape
    shape = (area.shape[0]-height+1, area.shape[1]-width+1)
    if min(shape) <= 0:
        return []
    positives, negatives = np.argwhere(template), np.argwhere(~template)
    hits, misses = np.zeros(shape, np.uint8), np.zeros(shape, np.uint8)
    for points, scores, invert in ((positives,hits,False),(negatives,misses,True)):
        for index in range(8):
            y,x = points[index*len(points)//8]
            patch = area[y:y+shape[0],x:x+shape[1]]
            scores += ~patch if invert else patch
    ys,xs = np.where((hits >= 7) & (misses >= 7))
    if len(ys) > limit:
        return []
    found = []
    for y,x in zip(ys,xs):
        patch = area[y:y+height,x:x+width]
        common = np.count_nonzero(patch & template)
        if (common / np.count_nonzero(template) >= .95
                and common / max(1,np.count_nonzero(patch)) >= .92):
            found.append((int(x),int(y)))
    return found


def unique_boxes(boxes, tolerance=2):
    unique = []
    for box in sorted(set(boxes)):
        if not any(max(abs(a-b) for a,b in zip(box,old)) <= tolerance for old in unique):
            unique.append(box)
    return unique


@dataclass(frozen=True)
class HudRegistration:
    scale: float
    boxes: tuple

    def mapping(self):
        return dict(self.boxes)

    def compatible(self, other):
        return (other is not None and self.scale == other.scale
                and all(max(abs(a-b) for a,b in zip(box,other.mapping()[name])) <= 2
                        for name,box in self.boxes))


class HudAnchorCalibrator:
    REQUIRED_FRAMES = 3

    def __init__(self, large_reader):
        self.reader = large_reader
        self.data = large_reader.data
        self.title = large_reader.title
        # Complete measured separator from the existing own-hull regression.
        self.slash = np.array([[c == '1' for c in row] for row in (
            '0000001','0000010','0000010','0000100','0000100','0001000',
            '0001000','0010000','0010000','0100000','0100000','1000000')])
        self.dot = np.pad(np.ones((3,3),bool),1)
        self.context = None
        self.last_sequence = self.last_time = None
        self.registration = None
        self.count = 0
        self.reason = 'waiting_for_hud_anchors'
        self.ready = False

    def reset(self, reason):
        self.registration = None
        self.count = 0
        self.ready = False
        self.reason = reason

    def metadata(self):
        return {'status':'registered' if self.ready else 'pending', 'reason':self.reason,
            'consistent_frames':self.count, 'required_frames':self.REQUIRED_FRAMES,
            'last_acquisition_sequence':self.last_sequence,
            'last_observed_at':self.last_time,
            'hud_scale':self.registration.scale if self.registration else None,
            'region_boxes_in_normalized_pixels':self.registration.mapping() if self.registration else {},
            'validation':'geometry_registration_only', 'numeric_accuracy_independently_validated':False,
            'input_authorized':False}

    @staticmethod
    def in_bounds(box, image):
        return 0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height

    def canvas(self, image, boxes):
        result = Image.new('RGB',(2560,1440))
        for name,box in boxes.items():
            destination = self.data[name]
            result.paste(image.crop(box),tuple(destination[:2]))
        return result

    def locate(self, image):
        width,height = image.size
        title_region = (width//5,0,width*4//5,min(height//3,400))
        left,top,right,bottom = title_region
        titles = find_template(white(image.crop(title_region)),self.title,limit=128)
        title_boxes = unique_boxes([(x+left,y+top,x+left+self.title.shape[1],y+top+self.title.shape[0])
                                    for x,y in titles])
        if len(title_boxes) != 1:
            return None,'selected_panel_anchor_missing_or_ambiguous'
        title_box = title_boxes[0]
        dx,dy = title_box[0]-self.data['title_box'][0],title_box[1]-self.data['title_box'][1]
        boxes = {name:tuple(value+(dx if index%2==0 else dy) for index,value in enumerate(self.data[name]))
                 for name in ('title_box','distance_box','hp_box','portrait_box')}
        if not all(self.in_bounds(box,image) for box in boxes.values()):
            return None,'selected_panel_regions_out_of_bounds'

        # Own hull is located independently of the selected target panel.
        hulls = []
        region = white(image.crop((0,0,width//4,min(height//3,240))))
        canvas = Image.new('RGB',(2560,1440))
        for x,y in find_template(region,self.slash,limit=128):
            for offset in (68,72):
                box = (x-offset,y-3,x-offset+151,y-3+18)
                if not self.in_bounds(box,image):
                    continue
                canvas.paste(image.crop(box),tuple(self.data['hull_box'][:2]))
                current,maximum,reason,_,_ = self.reader.hull(canvas)
                if reason is None and current is not None and maximum is not None:
                    hulls.append(box)
        hulls = unique_boxes(hulls)
        if len(hulls) != 1:
            return None,'own_hull_anchor_missing_or_ambiguous'
        boxes['hull_box'] = hulls[0]

        # A decimal candidate alone is insufficient: complete seven-segment
        # text, margins, spacing, range and exactly one decimal must all pass.
        speeds = []
        sx,sy = width*3//10,height//2
        region = white(image.crop((sx,sy,width*3//4,max(sy,height-20))))
        for x,y in find_template(region,self.dot,limit=256):
            dot_x,dot_y = x+sx+1,y+sy+1
            for offset in range(13,46):
                box = (dot_x-offset,dot_y-22,dot_x-offset+58,dot_y-22+29)
                if not self.in_bounds(box,image):
                    continue
                canvas.paste(image.crop(box),tuple(self.data['speed_text_box'][:2]))
                value,reason,_ = self.reader.speed(canvas)
                if reason is None and value is not None:
                    speeds.append(box)
        speeds = unique_boxes(speeds)
        if len(speeds) != 1:
            return None,'speed_anchor_missing_or_ambiguous'
        boxes['speed_text_box'] = speeds[0]
        candidate = self.canvas(image,boxes)
        # Independent font/ratio constraints verify the proposed panel offsets;
        # successful title matching alone cannot relocate numeric fields.
        result = self.reader.read(candidate)
        if (result.get('asteroid_selected') is not True
                or any(result.get(name) is None for name in
                       ('distance','speed','target_max_hp','target_current_hp','hull_current','hull_max'))):
            return None,'registered_regions_failed_existing_font_guards'
        return boxes,None

    def update(self, image, *, context, sequence, observed_at):
        if context != self.context:
            self.reset('observation_context_changed')
            self.last_sequence = self.last_time = None
            self.context = context
        if (self.last_sequence is not None
                and (sequence <= self.last_sequence or observed_at <= self.last_time)):
            self.reset('waiting_for_distinct_newer_capture')
            return None
        if self.last_time is not None and observed_at-self.last_time > 2:
            self.reset('calibration_observation_gap')
        self.last_sequence,self.last_time = sequence,observed_at
        # Dimensions only propose scale candidates. All three visual anchors
        # and existing numeric raster checks must agree on one model.
        width,height = image.size
        scales = sorted({1.,round(width/2560,4),round(height/1440,4),.75,1.25,1.5,2.})
        candidates = []
        last_reason = 'unsupported_hud_scale'
        for scale in scales:
            if not .5 <= scale <= 2:
                continue
            normalized = (image if scale==1 else image.resize(
                (round(width/scale),round(height/scale)),Image.Resampling.BOX))
            boxes,reason = self.locate(normalized)
            if boxes is not None:
                candidates.append((HudRegistration(scale,tuple(sorted(boxes.items()))),normalized))
            if scale == 1:
                last_reason = reason
        if len(candidates) != 1:
            self.reset('multiple_hud_scale_models' if candidates else last_reason)
            return None
        registration,normalized = candidates[0]
        self.count = min(self.REQUIRED_FRAMES,self.count+1) if registration.compatible(self.registration) else 1
        self.registration = registration
        self.ready = self.count >= self.REQUIRED_FRAMES
        self.reason = 'hud_regions_registered' if self.ready else 'confirming_hud_regions_across_captures'
        return self.canvas(normalized,registration.mapping()) if self.ready else None
