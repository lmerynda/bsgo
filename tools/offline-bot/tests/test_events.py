from dataclasses import FrozenInstanceError, replace
import threading
import unittest
from bsgo_offline.events import EventDetector, EventEvidence
from bsgo_offline.state import FieldValue, SnapshotStore
from runtime_fixture import observation


class EventTests(unittest.TestCase):
    def test_transient_event_survives_snapshot_replacement_and_deduplicates(self):
        store, detector = SnapshotStore(), EventDetector()
        for index, values in enumerate(({}, {"scan_result": {"scan_id": 1, "resource": "Water", "eligible": True}},
                                      {"scan_result": {"scan_id": 1, "resource": "Water", "eligible": True}}, {}), 1):
            snap = observation(index, index / 10, values=values)
            store.publish(snap, detector.update(snap))
        view = store.read()
        self.assertEqual(view.snapshot.sequence, 4)
        self.assertEqual(len(view.events.events), 1)
        self.assertEqual(view.events.events[0].acquisition_sequence, 2)
        self.assertEqual(store.read(view.events.next_cursor).events.events, ())
        with self.assertRaises(TypeError): view.events.events[0].payload["resource"] = "Tylium"

    def test_gap_and_independent_cursors(self):
        store = SnapshotStore(2)
        for i in range(1, 5):
            store.publish(observation(i, i), (EventEvidence("test", i, {}),))
        self.assertTrue(store.read(0).events.gap)
        self.assertFalse(store.read(2).events.gap)
        self.assertEqual(store.read(3).events.events[0].sequence, 4)
        with self.assertRaises(ValueError): store.read(5)

    def test_invalid_inventory_breaks_delta_continuity(self):
        detector = EventDetector()
        self.assertEqual(detector.update(observation()), ())
        detector.update(observation(2, 1.2, values={"inventory": None}))
        events = detector.update(observation(3, 1.4, values={"inventory": {"Water": 150}}))
        self.assertFalse(any(e.kind == "resource_change" for e in events))
        events = detector.update(observation(4, 1.6, values={"inventory": {"Water": 175}}))
        self.assertEqual(events[0].payload["delta"], 25)

    def test_context_change_cannot_create_reward_delta(self):
        detector = EventDetector()
        detector.update(observation())
        self.assertEqual(detector.update(observation(2, 1.2, session_epoch=2,
                         values={"inventory": {"Water": 200}})), ())

    def test_concurrent_read_is_consistent_with_event_watermark(self):
        store = SnapshotStore(1024)
        errors = []
        def publish():
            for i in range(1, 300):
                store.publish(observation(i, i), (EventEvidence("test", i, {}),))
        thread = threading.Thread(target=publish)
        thread.start()
        while thread.is_alive():
            view = store.read()
            if view.snapshot and view.snapshot.sequence != view.events.next_cursor:
                errors.append(view)
        thread.join(2)
        self.assertFalse(errors)
        self.assertEqual(store.read().snapshot.sequence, store.read().events.next_cursor)

    def test_future_event_rejected_before_publication(self):
        store = SnapshotStore()
        with self.assertRaises(ValueError):
            store.publish(observation(), (EventEvidence("test", 2, {}),))
        self.assertIsNone(store.latest())


if __name__ == "__main__": unittest.main()
