"""Invented mining receiver. No operating-system input, capture, or model APIs."""
from dataclasses import dataclass
from types import MappingProxyType

from .simulation import VirtualReceiver, SyntheticSample, SyntheticSource


@dataclass(frozen=True)
class MiningSample(SyntheticSample):
    window_id: str = "synthetic:mining"
    ownership_generation: int = 1


class MiningReceiver(VirtualReceiver):
    def __init__(self, backend, clock, *, scan_missing=False, eligible=True,
                 mining_ignored=False, reward_missing=False, inventory_conflict=False,
                 scan_delay=1, mining_delay=3, reward_delay=1, **kwargs):
        import math
        for duration in (scan_delay, mining_delay, reward_delay):
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("simulation_duration")
        super().__init__(backend, clock, **kwargs)
        self.selected = True
        self.selection_revision = 1
        self.owned = True
        self.ownership_generation = 1
        self.scan_missing, self.eligible = scan_missing, eligible
        self.mining_ignored, self.reward_missing = mining_ignored, reward_missing
        self.inventory_conflict = inventory_conflict
        self.scan_delay, self.mining_delay, self.reward_delay = scan_delay, mining_delay, reward_delay
        self.scan_due = self.deplete_due = self.reward_due = None
        self.scan_id = 0
        self.scan_result = None
        self.mining_active = False
        self.inventory = {"Water": 100, "Tylium": 50}

    def _extra_action(self, action, now):
        if action.kind == "key_down" and action.key == "S":
            if self.selected and not self.scan_missing:
                self.scan_due = now + self.scan_delay
                return "scan_started"
            return "scan_ignored"
        if action.kind == "key_down" and action.key == "M":
            if self.selected and self.speed == 0 and self.eligible and not self.mining_ignored:
                self.mining_active = True
                self.deplete_due = now + self.mining_delay
                return "mining_started"
            return "mining_ignored"
        if action.kind == "key_up" and action.key == "M":
            self.mining_active = False
            if self.selected:
                self.deplete_due = None
            return "mining_released"
        return "no_state_change"

    def sample(self):
        with self._lock:
            # The base receiver uses a non-reentrant lock, so sample directly.
            now = self.clock()
            self._advance(now)
            if self.scan_due is not None and now >= self.scan_due:
                self.scan_id += 1
                self.scan_result = {"scan_id": self.scan_id, "resource": "Water" if self.eligible else "empty",
                                    "eligible": self.eligible}
                self.scan_due = None
            if self.deplete_due is not None and now >= self.deplete_due:
                self.selected = False
                self.mining_active = False
                self.deplete_due = None
                if not self.reward_missing:
                    self.reward_due = now + self.reward_delay
            if self.reward_due is not None and now >= self.reward_due:
                self.inventory = {**self.inventory, "Water": self.inventory["Water"] + 25}
                self.reward_due = None
            values = {"distance": self.distance if self.selected else None,
                "speed": self.speed, "asteroid_selected": self.selected,
                "target_identity": self.identity if self.selected else None,
                "target_portrait": self.portrait if self.selected else None,
                "selection_revision": self.selection_revision, "owned": self.owned,
                "scan_result": self.scan_result, "mining_active": self.mining_active,
                "inventory": self.inventory, "inventory_conflict": self.inventory_conflict}
            return now, self.session_epoch, self.layout, MappingProxyType(values)

    def reselect(self):
        with self._lock:
            self.selection_revision += 1
            self.selected = True
            self.scan_result = None


class MiningSource(SyntheticSource):
    def next_sample(self, stop_event):
        sample = super().next_sample(stop_event)
        if sample is None:
            return None
        return MiningSample(sample.acquisition_sequence, sample.session_epoch, sample.layout,
                            sample.acquired_at, sample.values,
                            ownership_generation=self.receiver.ownership_generation)
