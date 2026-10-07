"""Read-only system-map contact proposals, not asteroid/resource identities.

Hollow map marks supply potential click locations. The selected-target HUD must
independently establish asteroid type and range before any scan is considered.
No input module is imported and these proposals cannot populate ScanCatalog as
validated asteroid or unscanned evidence.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image


NATIVE_SIZE = (2560,1440)
FOOTER_BOX = (1400,1350,1620,1435)
SEARCH_BOX = (40,40,2510,1310)


@dataclass(frozen=True)
class MapContact:
    box: tuple[int,int,int,int]
    ring_contrast: float
    object_type: str = 'unknown'
    scan_state: str = 'unknown'
    resource: str | None = None
    distance: float | None = None

    @property
    def center(self):
        left,top,right,bottom = self.box
        return ((left+right-1)//2,(top+bottom-1)//2)


@dataclass(frozen=True)
class MapDiscovery:
    contacts: tuple[MapContact,...]
    map_verified: bool
    reason: str
    input_authorized: bool = False
    asteroid_classification_verified: bool = False


def neutral(image, *, minimum=110):
    rgb = np.asarray(image.convert('RGB'))
    low,high = rgb.min(2),rgb.max(2)
    return (low >= minimum) & (high.astype(np.int16)-low <= 35)


@lru_cache(maxsize=1)
def footer_template():
    with Image.open(Path(__file__).parent/'data'/'system-map-controls.png') as image:
        return neutral(image,minimum=160)


def map_view_verified(image):
    if image.size != NATIVE_SIZE:
        return False
    template = footer_template()
    observed = neutral(image.crop(FOOTER_BOX),minimum=160)
    common = np.count_nonzero(template & observed)
    if (common/max(1,np.count_nonzero(template)) < .90
            or common/max(1,np.count_nonzero(observed)) < .90):
        return False
    # Footer icons also exist in other UI surfaces. Require the native map's
    # top-right red close triangle and neutral X independently.
    corner = np.asarray(image.crop((2505,0,2560,55)).convert('RGB'))
    red = (corner[:,:,0] > 100) & (corner[:,:,0] > corner[:,:,1]*1.5) & (corner[:,:,0] > corner[:,:,2]*1.5)
    return np.count_nonzero(red) >= 600 and np.count_nonzero(neutral(Image.fromarray(corner),minimum=160)) >= 20


def component_boxes(mask, *, max_runs=20000):
    """Bounded row-run union avoids Python work for every scenery pixel."""
    parents,rows = [],[]
    previous = []
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    for y,row in enumerate(mask):
        edges = np.flatnonzero(np.diff(np.r_[False,row,False]))
        current = []
        start = 0
        for left,right in zip(edges[::2],edges[1::2]):
            if len(rows) >= max_runs:
                return None
            index = len(rows)
            parents.append(index)
            rows.append((int(left),y,int(right),y+1))
            while start < len(previous) and previous[start][1] < left:
                start += 1
            for old_left,old_right,old_index in previous[start:]:
                if old_left > right:
                    break
                parents[root(old_index)] = root(index)
            current.append((left,right,index))
        previous = current
    boxes = {}
    for index,box in enumerate(rows):
        key = root(index)
        if key not in boxes:
            boxes[key] = box
        else:
            old = boxes[key]
            boxes[key] = (min(old[0],box[0]),min(old[1],box[1]),max(old[2],box[2]),max(old[3],box[3]))
    return tuple(boxes.values())


def locate_hollow_contacts(image, *, origin=(0,0), capacity=256):
    """Geometry-only crop reader. Caller must separately establish map view."""
    if type(capacity) is not int or not 1 <= capacity <= 256:
        raise ValueError('contact_capacity')
    mask = neutral(image)
    boxes = component_boxes(mask)
    if boxes is None:
        return None
    gray = np.asarray(image.convert('L'))
    contacts = []
    for left,top,right,bottom in boxes:
        width,height = right-left,bottom-top
        if not (4 <= width <= 9 and 4 <= height <= 9 and abs(width-height) <= 2):
            continue
        if left < 2 or top < 2 or right > image.width-2 or bottom > image.height-2:
            continue
        patch = mask[top:bottom,left:right]
        cy,cx = (height-1)/2,(width-1)/2
        yy,xx = np.indices(patch.shape)
        radius = np.sqrt(((xx-cx)/(width/2))**2+((yy-cy)/(height/2))**2)
        inner = radius < .45
        outer = (radius >= .55) & (radius <= 1.15)
        if not inner.any() or patch[inner].any() or np.mean(patch[outer]) < .55:
            continue
        # A real ring surrounds its dark center in all four directions.
        if not (patch[:2].any() and patch[-2:].any() and patch[:,:2].any() and patch[:,-2:].any()):
            continue
        pixels = gray[top:bottom,left:right]
        contrast = float(np.mean(pixels[outer])-np.mean(pixels[inner]))
        if contrast < 35:
            continue
        # Touching/overlapping contacts and unrelated nearby neutral glyphs
        # cannot supply an isolated click center.
        expanded = mask[top-2:bottom+2,left-2:right+2].copy()
        expanded[2:-2,2:-2] = False
        if expanded.any():
            continue
        contacts.append(MapContact((left+origin[0],top+origin[1],right+origin[0],bottom+origin[1]),contrast))
        if len(contacts) > capacity:
            return None
    return tuple(sorted(contacts,key=lambda contact:(contact.box[1],contact.box[0])))


def discover_map_contacts(image):
    if image.size != NATIVE_SIZE:
        return MapDiscovery((),False,'unsupported_system_map_geometry')
    if not map_view_verified(image):
        return MapDiscovery((),False,'system_map_view_unverified')
    left,top,right,bottom = SEARCH_BOX
    contacts = locate_hollow_contacts(image.crop(SEARCH_BOX),origin=(left,top))
    if contacts is None:
        return MapDiscovery((),True,'map_contact_search_cluttered_or_over_capacity')
    return MapDiscovery(contacts,True,'unclassified_map_contacts_observed' if contacts else 'no_isolated_supported_map_contacts')
