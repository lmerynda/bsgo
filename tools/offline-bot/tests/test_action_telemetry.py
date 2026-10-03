import unittest
from dataclasses import replace
from collections import deque
from runtime_fixture import MissionFixture


class Journal:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail
    def submit(self, event):
        if self.fail:
            raise OSError("injected telemetry fault")
        self.events.append(event)
        return True


class ActionTelemetryTests(unittest.TestCase):
    def test_requests_delivery_and_releases_survive_current_status_overwrites(self):
        journal = Journal()
        h = MissionFixture(journal=journal)
        self.assertTrue(h.complete().completed)
        events = [event["record"] for event in journal.events if event["event"] == "action_lifecycle"]
        scan = [event for event in events if (event.get("status", {}).get("action_id") or
                event.get("intent", {}).get("action_id") or event.get("action_id")) == "1:scan:scan"]
        self.assertEqual([event["status"]["state"] for event in scan if event["kind"] == "status"],
                         ["requested", "dispatched"])
        self.assertEqual([event["input_kind"] for event in scan if event["kind"] == "delivery"],
                         ["key_down", "key_up"])
        self.assertTrue(all(event["observation_sequence"] is not None for event in events))
        self.assertTrue(all(event["context"]["window_id"].startswith("synthetic:") for event in events))
        self.assertEqual(h.coordinator.executor.telemetry_stats()["journal_dropped"], 0)

    def test_telemetry_fault_does_not_change_control_result(self):
        h = MissionFixture(journal=Journal(fail=True))
        self.assertTrue(h.complete().completed)
        self.assertGreater(h.coordinator.executor.telemetry_stats()["journal_dropped"], 0)
        self.assertFalse(h.backend.held_keys)

    def test_cancellation_and_expiration_retained(self):
        h = MissionFixture()
        h.tick(deliver=False)
        h.tick(deliver=False)
        h.clock.now += 3
        h.deliver()
        history = h.coordinator.executor.history()
        self.assertIn("expired", [event["status"]["state"] for event in history if event["kind"] == "status"])
        h = MissionFixture()
        h.tick(deliver=False)
        h.tick(deliver=False)
        h.coordinator.cancel("user_stop")
        states = [event["status"]["state"] for event in h.coordinator.executor.history() if event["kind"] == "status"]
        self.assertEqual(states, ["requested", "cancelled"])

    def test_bounded_history_and_collision_rejection_keep_original_status(self):
        h = MissionFixture()
        h.coordinator.executor._history = deque(maxlen=2)
        h.tick(deliver=False)
        h.tick(deliver=False)
        executor = h.coordinator.executor
        original = next(iter(executor._intents.values()))
        executor.submit(replace(original, operation="idle_throttle"))
        self.assertEqual(executor.statuses()[0].state, "requested")
        self.assertEqual(executor.history()[-1]["status"]["reason"], "action_id_collision")
        self.assertGreater(executor.telemetry_stats()["history_evicted"], 0)


if __name__ == "__main__":
    unittest.main()
