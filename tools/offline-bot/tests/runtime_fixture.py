"""Deterministic control-time fixture; no workers, OS input or real sleeps."""
from bsgo_offline.events import EventDetector
from bsgo_offline.execution import SYNTHETIC_BINDINGS
from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.mining_simulation import MiningReceiver, MiningSample
from bsgo_offline.mission import MiningCoordinator, RouteAssessment
from bsgo_offline.simulation import recognize_synthetic
from bsgo_offline.state import FieldValue, Snapshot, SnapshotStore


def observation(sequence=1, at=1.0, **changes):
    values = {"distance": 1400., "speed": 0., "asteroid_selected": True,
        "target_identity": "same-hp", "target_portrait": (2,) * 144,
        "selection_revision": 1, "owned": True, "mining_active": False,
        "inventory": {"Water": 100}, "inventory_conflict": False}
    values.update(changes.pop("values", {}))
    fields = {key: FieldValue(value, at, value is not None) for key, value in values.items()}
    fields.update(changes.pop("fields", {}))
    return Snapshot(sequence, changes.pop("session_epoch", 1), sequence, at, at,
        changes.pop("layout", "synthetic-original"), (2560, 1440), fields,
        window_id=changes.pop("window_id", "synthetic:mining"),
        ownership_generation=changes.pop("ownership_generation", 1), **changes)


class Clock:
    def __init__(self, now=0):
        self.now = now
    def __call__(self):
        return self.now


class MissionFixture:
    def __init__(self, *, event_capacity=256, planner=None, journal=None, **faults):
        self.clock = Clock()
        self.store = SnapshotStore(event_capacity)
        self.backend = RecordingInputBackend(session_id="fixture-1", clock=self.clock)
        self.receiver = MiningReceiver(self.backend, self.clock, **faults)
        self.detector = EventDetector()
        self.sequence = 0
        self.publish()
        first = self.store.latest()
        self.route = RouteAssessment(first.context, 1, first.acquired_at, 120, "synthetic")
        self.coordinator = MiningCoordinator(self.store, self.backend, clock=self.clock,
            route=self.route, equipment={"configured": True, "resource": "Water"},
            bindings=SYNTHETIC_BINDINGS, planner=planner, journal=journal)

    def publish(self, fields=None):
        self.sequence += 1
        now, epoch, layout, values = self.receiver.sample()
        sample = MiningSample(self.sequence, epoch, layout, now, values,
                              ownership_generation=self.receiver.ownership_generation)
        result = recognize_synthetic(sample)
        observations = dict(result.fields)
        observations.update(fields or {})
        snapshot = Snapshot(self.sequence, epoch, self.sequence, now, now, layout,
            result.frame_size, observations, window_id=sample.window_id,
            ownership_generation=sample.ownership_generation)
        self.store.publish(snapshot, self.detector.update(snapshot))
        return snapshot

    def deliver(self):
        for action in self.backend.drain():
            if not self.backend.delivery_allowed(action):
                self.backend.report_delivery(action, "rejected")
                continue
            self.receiver._advance(self.clock())
            if action.kind == "key_down" and action.key == "PageUp":
                self.receiver.throttle = 1
            elif action.kind == "key_down" and action.key == "PageDown":
                if not self.receiver.ignore_brake:
                    self.receiver.throttle = 0
            else:
                self.receiver._extra_action(action, self.clock())
            self.backend.report_delivery(action, "received")

    def tick(self, delta=.2, *, publish=True, deliver=True, fields=None):
        self.clock.now += delta
        if publish:
            self.publish(fields)
        result = self.coordinator.step()
        if deliver:
            self.deliver()
        return result

    def run_until(self, predicate, limit=1000):
        for _ in range(limit):
            if predicate():
                return
            self.tick()
        raise AssertionError("fixture never reached expected state")

    def complete(self):
        self.run_until(lambda: self.coordinator.result is not None)
        return self.coordinator.result
