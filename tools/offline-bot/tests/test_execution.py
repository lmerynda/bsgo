from dataclasses import replace
import unittest
from bsgo_offline.execution import ActionIntent, InputExecutor, SYNTHETIC_BINDINGS
from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.events import EventEvidence
from bsgo_offline.state import SnapshotStore
from bsgo_offline.tracking import SelectionTracker
from runtime_fixture import observation, Clock


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1)
        self.store = SnapshotStore()
        self.store.publish(observation())
        self.tracker = SelectionTracker()
        self.track = self.tracker.update(self.store.latest(), 1)
        self.backend = RecordingInputBackend(clock=self.clock)
        self.executor = InputExecutor(self.store, self.backend, self.tracker, self.clock,
                                      bindings=SYNTHETIC_BINDINGS)
        self.intent = ActionIntent("1:approach:maximum_throttle", 1, self.store.latest().context,
            self.track.track_id, "maximum_throttle", 1, 3, 0, {})

    def test_duplicate_tick_requests_one_press(self):
        first = self.executor.submit(self.intent)
        self.assertEqual(first.state, "requested")
        self.assertEqual(first, self.executor.submit(self.intent))
        self.assertEqual(len(self.backend.records()), 2)

    def test_expired_after_dequeue_cannot_deliver(self):
        self.executor.submit(self.intent)
        record = self.backend.get_action(0)
        self.clock.now = 3
        self.assertFalse(self.backend.delivery_allowed(record))
        self.assertEqual(self.executor.statuses()[0].state, "expired")

    def test_release_of_delivered_press_is_allowed_after_expiry(self):
        self.executor.submit(self.intent)
        down, up = self.backend.drain()
        self.assertTrue(self.backend.delivery_allowed(down))
        self.backend.report_delivery(down, "received")
        self.clock.now = 3
        self.assertTrue(self.backend.delivery_allowed(up))
        self.assertEqual(self.executor.statuses()[0].state, "dispatched")

    def test_delivery_revalidates_context_ownership_and_visible_selection(self):
        for changed in (observation(2, 1.2, ownership_generation=2),
                        observation(2, 1.2, values={"owned": False}),
                        observation(2, 1.2, values={"selection_revision": 2}),
                        observation(2, 1.2, values={"asteroid_selected": False})):
            self.setUp()
            self.executor.submit(self.intent)
            record = self.backend.get_action(0)
            self.clock.now = 1.2
            self.store.publish(changed)
            self.assertFalse(self.backend.delivery_allowed(record))

    def test_hidden_loss_between_reads_is_retained_for_delivery_guard(self):
        self.executor.submit(self.intent)
        record = self.backend.get_action(0)
        self.clock.now = 1.4
        self.store.publish(observation(2, 1.2), (EventEvidence("selected_target_disappeared", 1.2, {}),))
        self.store.publish(observation(3, 1.4))
        self.assertFalse(self.backend.delivery_allowed(record))

    def test_cancel_before_submission_records_no_acceleration(self):
        self.executor.cancel("race")
        self.assertEqual(self.executor.submit(self.intent).state, "cancelled")
        self.assertFalse(any(r.key == "PageUp" for r in self.backend.records()))

    def test_synthetic_bindings_reject_other_surface(self):
        self.clock.now = 1.2
        snap = observation(2, 1.2, window_id="real:window")
        self.store.publish(snap)
        track = self.tracker.update(snap, 1.2)
        intent = replace(self.intent, action_id="1:scan:scan", operation="scan", context=snap.context,
                         track_id=track.track_id)
        self.assertEqual(self.executor.submit(intent).reason, "synthetic_binding_on_other_surface")
        self.assertFalse(self.backend.records())

    def test_missing_binding_and_mining_in_motion_are_rejected(self):
        self.assertEqual(self.executor.submit(replace(self.intent, operation="unbound")).reason, "missing_binding")
        self.setUp()
        self.clock.now = 1.2
        self.store.publish(observation(2, 1.2, values={"speed": 5}))
        self.assertEqual(self.executor.submit(replace(self.intent, operation="start_mining")).reason, "mining_requires_stop")

    def test_replayed_or_forged_backend_record_is_rejected(self):
        self.executor.submit(self.intent)
        down, up = self.backend.drain()
        self.assertTrue(self.backend.delivery_allowed(down))
        self.backend.report_delivery(down, "received")
        self.assertFalse(self.backend.delivery_allowed(down))
        self.assertFalse(self.backend.delivery_allowed(replace(up, key="X")))
        self.assertFalse(self.backend.delivery_allowed(replace(up, sequence=100)))
        self.assertTrue(self.backend.delivery_allowed(up))

    def test_queued_acceleration_requires_current_distance(self):
        self.executor.submit(self.intent)
        down = self.backend.get_action(0)
        self.clock.now = 1.2
        self.store.publish(observation(2, 1.2, values={"distance": None}))
        self.assertFalse(self.backend.delivery_allowed(down))

    def test_queued_acceleration_rechecks_stopped_start_and_minimum_range(self):
        for changed in ({"speed": 10}, {"distance": 900}):
            self.setUp()
            intent = replace(self.intent, parameters={"minimum_distance": 1150})
            self.executor.submit(intent)
            down = self.backend.get_action(0)
            self.clock.now = 1.2
            self.store.publish(observation(2, 1.2, values=changed))
            self.assertFalse(self.backend.delivery_allowed(down))

    def test_identity_collision_and_invalid_event_cursor_reject(self):
        self.executor.submit(self.intent)
        self.assertEqual(self.executor.submit(replace(self.intent, operation="scan")).reason, "action_id_collision")
        self.assertEqual(self.executor.submit(replace(self.intent, action_id="1:new", event_cursor=99)).reason,
                         "invalid_event_cursor")


if __name__ == "__main__": unittest.main()
