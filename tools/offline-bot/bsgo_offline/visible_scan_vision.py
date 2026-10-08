"""Bounded brown-rock candidates in the native flight view, verified by HUD later.

Candidates are click proposals, not asteroid identities or unscanned claims.
Exclude native HUD, chat, sidebar, own ship and large gray planetoids. Never
authorize Z until a later selected-asteroid HUD and world marker agree.
"""
import hashlib

import numpy as np
from PIL import Image, ImageFilter


def _components(mask):
    # Row spans keep this dependency-free and avoid a Python flood per pixel.
    groups, previous = [], []
    for y, row in enumerate(mask):
        changes = np.diff(np.r_[False, row, False].astype(np.int8))
        spans = []
        for left, right in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            touches = {index for a, b, index in previous if left <= b and right >= a}
            if touches:
                index = min(touches)
                for old in touches - {index}:
                    if groups[old] is not None:
                        a, b = groups[index], groups[old]
                        groups[index] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]),
                                         max(a[3], b[3]), a[4] + b[4]]
                        groups[old] = None
                    previous = [(a, b, index if n == old else n) for a, b, n in previous]
                    spans = [(a, b, index if n == old else n) for a, b, n in spans]
                a = groups[index]
                groups[index] = [min(a[0], int(left)), a[1], max(a[2], int(right)), y + 1,
                                 a[4] + int(right - left)]
            else:
                index = len(groups)
                groups.append([int(left), y, int(right), y + 1, int(right - left)])
            spans.append((int(left), int(right), index))
        previous = spans
    return [group for group in groups if group is not None]


def visible_rock_candidates(image):
    if image.size != (2560, 1440):
        return []
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mask = ((red > 25) & (red < 180) & (red > green * 1.04) & (red < green * 1.6)
            & (green > blue * 1.05) & (green < blue * 1.9))
    mask[:200] = False
    mask[925:] = False
    mask[800:, :690] = False
    mask[:, 2010:] = False
    mask[625:925, 1080:1460] = False
    # Close small texture gaps; proposals still require substantial source ink.
    closed = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    candidates = []
    for left, top, right, bottom, area in _components(np.asarray(closed) > 0):
        width, height = right-left, bottom-top
        if not (12 <= width <= 105 and 10 <= height <= 105 and area >= 90
                and area / (width * height) >= .35):
            continue
        if np.count_nonzero(mask[top:bottom, left:right]) < 60:
            continue
        patch = rgb[top:bottom, left:right]
        if float(patch.max(axis=2).std()) < 7:
            continue
        # Click a source-mask pixel near the center, not a closed-background gap.
        ys, xs = np.where(mask[top:bottom, left:right])
        index = np.argmin((xs-width/2)**2 + (ys-height/2)**2)
        x, y = left+int(xs[index]), top+int(ys[index])
        signature = hashlib.sha256(image.crop((left, top, right, bottom)).resize((12, 12)).tobytes()).hexdigest()[:16]
        candidates.append({"x": x, "y": y, "box": [left, top, right, bottom],
                           "signature": signature, "area": area})
    return sorted(candidates, key=lambda row: -row["area"])[:64]


def view_fingerprint(image):
    """Coarse stationary-view guard; dynamic HUD and our ship are excluded."""
    if image.size != (2560, 1440):
        return None
    small = np.asarray(image.convert("L").resize((160, 90)), dtype=np.uint8).copy()
    small[:13] = 0
    small[58:] = 0
    small[:, 125:] = 0
    small[39:58, 67:92] = 0
    return small.tobytes()


def same_view(first, current):
    if not isinstance(first, bytes) or not isinstance(current, bytes) or len(first) != 14400 or len(current) != 14400:
        return False
    delta = np.abs(np.frombuffer(first, np.uint8).astype(np.int16) - np.frombuffer(current, np.uint8))
    return float(delta.mean()) <= 3 and float(np.mean(delta > 32)) <= .025
