"""Auto-fight statistics: one live run, plus daily totals kept on disk.

Each run writes its own counters into the file for the local day it is in,
`<directory>/<YYYY-MM-DD>.json`, replacing its earlier entry, so totals are a
plain sum over runs and survive app restarts. A run crossing midnight closes
its entry for the old day and starts counting from zero in the new day's file.
"""
from datetime import datetime
import json
from pathlib import Path
import time

COUNTERS = ("kills", "bursts", "engaged", "skipped")


def _day(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("runs"), dict) else None
    except (OSError, ValueError):
        return None


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


class RunRecorder:
    """Records one Auto-fight run; writes at most every `interval` seconds unless forced."""
    def __init__(self, directory, run_id, *, clock=time.time, interval=2.):
        self.directory, self.run_id, self.clock, self.interval = Path(directory), run_id, clock, interval
        self.started = clock()
        self.day = _day(self.started)
        self.day_started = self.started
        self.baseline = dict.fromkeys(COUNTERS, 0)
        self.last = dict.fromkeys(COUNTERS, 0)  # counters at the previous write
        self.last_write = None
        self.error = None

    def record(self, counters, *, force=False, ended=False):
        now = self.clock()
        if not force and self.last_write is not None and now - self.last_write < self.interval:
            return False
        try:
            today = _day(now)
            if today != self.day:
                # Close the old day with what was recorded before midnight; anything
                # since then counts towards the new day.
                self._store(self.day, self.last, self.last_write or now, ended=True)
                self.day = today
                self.day_started = datetime.strptime(today, "%Y-%m-%d").timestamp()
                self.baseline = dict(self.last)
            self._store(self.day, counters, now, ended=ended)
            self.last = {name: int(counters.get(name, 0)) for name in COUNTERS}
            self.last_write = now
            return True
        except OSError as exc:  # statistics never stop the fight loop
            self.error = f"{type(exc).__name__}: {exc}"
            return False

    def _store(self, day, counters, now, *, ended):
        path = self.directory / f"{day}.json"
        data = _read(path) or {"date": day, "runs": {}}
        data["runs"][self.run_id] = {
            **{name: max(0, int(counters.get(name, 0)) - self.baseline[name]) for name in COUNTERS},
            "seconds": round(max(0., now - self.day_started), 1),
            "started": self.day_started, "updated": now, "ended": ended}
        _write(path, data)


def daily_summary(directory, *, now=None):
    """Totals for the local day, plus the most recent run; never raises."""
    now = time.time() if now is None else now
    day = _day(now)
    data = _read(Path(directory) / f"{day}.json") or {"runs": {}}
    runs = [row for row in data["runs"].values() if isinstance(row, dict)]
    totals = {name: sum(int(row.get(name, 0)) for row in runs) for name in COUNTERS}
    totals["seconds"] = round(sum(float(row.get("seconds", 0)) for row in runs), 1)
    last = max(runs, key=lambda row: row.get("updated", 0), default=None)
    return {"date": day, "runs": len(runs), **totals, "last_run": last}
