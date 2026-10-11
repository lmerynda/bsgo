"""Shared pixel geometry helpers, independent of game actions and target type."""


def unique_boxes(boxes, tolerance=2):
    """Merge near-identical region proposals without inventing coordinates."""
    unique = []
    for box in sorted(set(boxes)):
        if not any(max(abs(a-b) for a,b in zip(box,old)) <= tolerance for old in unique):
            unique.append(box)
    return unique
