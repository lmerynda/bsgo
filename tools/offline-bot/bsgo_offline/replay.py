"""Ordered frozen-corpus replay with explicitly synthetic acquisition timing."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from threading import Event
import time
from typing import Callable


@dataclass(frozen=True)
class FrameSample:
    path: Path
    acquisition_sequence: int
    session_epoch: int
    layout: str
    acquired_at: float
    source_id: str = ""
    window_id: str = "saved:replay"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))


def _source_order(row: dict) -> tuple:
    # Frame timestamp and ordinal live in original source names. Hash names are
    # content identifiers, not an ordering or a clock.
    parts = re.split(r"(\d+)", row["source"])
    natural = tuple((1, int(part)) if part.isdigit() else (0, part) for part in parts)
    return (row["source_run"], natural, row["id"])


class ReplaySource:
    """Replay paths, retaining run boundaries while assigning fresh replay times.

    ``interval`` is a synthetic spacing between acquisitions. It cannot recover
    capture timing or simulate game dynamics; original source names are retained
    only for ordering and traceability. Every source-run boundary advances epoch.
    The source is consumed by exactly one RecognitionWorker.
    """

    def __init__(self, manifest_path: str | Path, source_run: str | None = None,
                 interval: float = .05, session_epoch: int = 1,
                 layout: str = "original", clock: Callable[[], float] = time.monotonic,
                 limit: int | None = None, labeled_only: bool = False) -> None:
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("interval must be finite and nonnegative")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        if session_epoch < 0:
            raise ValueError("session_epoch must be nonnegative")
        self.manifest_path = Path(manifest_path).resolve()
        corpus = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if corpus.get("version") != 1 or not isinstance(corpus.get("rows"), list):
            raise ValueError("Expected frozen vision corpus manifest version 1")
        rows = [row for row in corpus["rows"] if row.get("kind") == "real_capture"
                and (source_run is None or row.get("source_run") == source_run)
                and (not labeled_only or bool(row.get("expected")))]
        self.rows = tuple(sorted(rows, key=_source_order)[:limit])
        if not self.rows:
            raise ValueError(f"No replay frames for source run {source_run!r}")
        self.interval = interval
        self.layout = layout
        self._clock = clock
        self._epoch = session_epoch
        self._position = 0
        self._run: str | None = None
        self._last_acquired: float | None = None

    def next_sample(self, stop_event: Event) -> FrameSample | None:
        if stop_event.is_set() or self._position >= len(self.rows):
            return None
        if self._last_acquired is not None:
            remaining = max(0., self._last_acquired + self.interval - self._clock())
            if stop_event.wait(remaining):
                return None
        if stop_event.is_set():
            return None
        row = self.rows[self._position]
        source_run = row["source_run"]
        if self._run is not None and source_run != self._run:
            self._epoch += 1
        self._run = source_run
        # Constrain data references to the frozen corpus directory.
        base = self.manifest_path.parent
        path = (base / row["file"]).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Replay frame path escapes frozen corpus")
        self._position += 1
        acquired_at = self._clock()
        self._last_acquired = acquired_at
        return FrameSample(path, self._position, self._epoch, self.layout,
                           acquired_at, row["source"])
