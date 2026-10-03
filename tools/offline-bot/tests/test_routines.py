from dataclasses import replace
import unittest
from bsgo_offline.events import EventEvidence
from bsgo_offline.routines import RoutineContext, ScanRoutine, StopRoutine
from bsgo_offline.state import FieldValue, SnapshotStore
from runtime_fixture import observation


class RoutineTests(unittest.TestCase):
    def test_stop_uses_fresh_speed_without_selected_target(self):
        store = SnapshotStore()
        snapshot = observation(2, 2, values={"asteroid_selected": False, "target_identity": None,
                                           "target_portrait": None})
        store.publish(snapshot)
        routine = StopRoutine(after=1)
        routine.start(RoutineContext(snapshot.context, None, 1, 0), 1)
        self.assertEqual(routine.update(store.read(), (), 2).status, "succeeded")

    def test_old_or_invalid_zero_cannot_satisfy_stop(self):
        for field in (FieldValue(0, 1), FieldValue(None, 2, False)):
            store = SnapshotStore()
            snap = observation(2, 2, fields={"speed": field})
            store.publish(snap)
            routine = StopRoutine(after=1, timeout=4)
            routine.start(RoutineContext(snap.context, None, 1, 0), 1)
            self.assertEqual(routine.update(store.read(), (), 2).status, "running")
            self.assertEqual(routine.update(store.read(), (), 5).status, "failed")

    def test_scan_cannot_use_old_event_or_other_selection(self):
        store = SnapshotStore()
        store.publish(observation(), (EventEvidence("scan_result", 1,
            {"selection_revision": 1, "resource": "Water", "eligible": True}),))
        routine = ScanRoutine()
        routine.start(RoutineContext(store.latest().context, 1, 1, 1, 1), 1)
        self.assertEqual(len(routine.update(store.read(1), (), 1).intents), 1)
        store.publish(observation(2, 1.2), (EventEvidence("scan_result", 1.2,
            {"selection_revision": 2, "resource": "Water", "eligible": True}),))
        self.assertEqual(routine.update(store.read(1), (), 1.2).status, "running")

    def test_cancel_is_terminal_and_repeated_updates_do_not_emit_input(self):
        store = SnapshotStore()
        store.publish(observation())
        routine = ScanRoutine()
        context = RoutineContext(store.latest().context, 1, 1, 0, 1)
        routine.start(context, 1)
        routine.cancel("cancelled", 1)
        for _ in range(3):
            update = routine.update(store.read(), (), 1)
            self.assertEqual(update.status, "cancelled")
            self.assertFalse(update.intents)
        with self.assertRaises(RuntimeError): routine.start(context, 1)


if __name__ == "__main__": unittest.main()
