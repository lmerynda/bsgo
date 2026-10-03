from dataclasses import FrozenInstanceError, replace
import unittest

from bsgo_offline.events import EventEvidence
from bsgo_offline.mission import RouteAssessment
from bsgo_offline.state import FieldValue
from runtime_fixture import MissionFixture


class MissionTests(unittest.TestCase):
    def test_complete_cycle_requires_scan_activation_stop_and_resource_change(self):
        h = MissionFixture()
        result = h.complete()
        self.assertTrue(result.completed, result)
        self.assertEqual(result.reward["delta"], 25)
        phases = [t["phase"] for t in h.coordinator.transitions if t["kind"] == "phase"]
        self.assertEqual(phases, ["scan", "assess", "approach", "verify_stop", "mine", "confirm_outcome"])
        self.assertEqual(h.receiver.speed, 0)
        self.assertFalse(h.backend.held_keys)
        before = len(h.backend.records())
        for _ in range(5): self.assertEqual(h.tick(), result)
        self.assertEqual(before, len(h.backend.records()))
        status = h.coordinator.read_status()
        with self.assertRaises(FrozenInstanceError): status.phase = "running"

    def test_missing_scan_and_ineligible_resource_do_not_approach(self):
        for config, reason in (({"scan_missing": True}, "scan_timeout"),
                               ({"eligible": False}, "resource_ineligible")):
            h = MissionFixture(**config)
            result = h.complete()
            self.assertFalse(result.completed)
            self.assertEqual(result.reason, reason)
            self.assertFalse(any(r.key == "PageUp" for r in h.backend.records()))

    def test_depletion_without_reward_does_not_succeed(self):
        h = MissionFixture(reward_missing=True)
        result = h.complete()
        self.assertFalse(result.completed)
        self.assertEqual(result.reason, "confirm_outcome_timeout")
        self.assertTrue(any(e.kind == "selected_target_disappeared" for e in h.store.read().events.events))

    def test_mining_requires_observed_activation(self):
        h = MissionFixture(mining_ignored=True)
        result = h.complete()
        self.assertEqual(result.reason, "mine_timeout")
        self.assertFalse(result.completed)

    def test_conflicting_inventory_activity_prevents_reward_attribution(self):
        h = MissionFixture(inventory_conflict=True)
        self.assertEqual(h.complete().reason, "reward_attribution_uncertain")

    def test_lost_selection_before_mining_blocks_mining(self):
        h = MissionFixture()
        h.run_until(lambda: h.coordinator.phase == "verify_stop")
        h.receiver.selected = False
        result = h.complete()
        self.assertFalse(result.completed)
        self.assertFalse(any(r.key == "M" for r in h.backend.records()))

    def test_identical_reselection_invalidates_work(self):
        h = MissionFixture()
        h.run_until(lambda: h.coordinator.phase == "approach")
        h.receiver.reselect()
        result = h.complete()
        self.assertEqual(result.reason, "selection_changed")
        self.assertFalse(any(r.key == "M" for r in h.backend.records()))

    def test_old_or_other_resource_event_cannot_confirm(self):
        h = MissionFixture(reward_missing=True)
        h.run_until(lambda: h.coordinator.phase == "confirm_outcome")
        h.clock.now += .2
        h.sequence += 1
        previous = h.store.latest()
        snap = replace(previous, sequence=h.sequence, acquisition_sequence=h.sequence,
                       acquired_at=h.clock(), published_at=h.clock(),
                       fields={name: replace(field, observed_at=h.clock()) for name, field in previous.fields.items()})
        h.store.publish(snap, (
            EventEvidence("resource_change", h.coordinator.mining_started_at - .1,
                {"resource": "Water", "delta": 25, "selection_revision": 1}),
            EventEvidence("resource_change", h.clock(),
                {"resource": "Tylium", "delta": 25, "selection_revision": 1}),
        ))
        self.assertIsNone(h.coordinator.step())
        self.assertFalse(h.complete().completed)

    def test_overflow_cannot_silently_confirm_reward(self):
        h = MissionFixture(event_capacity=2)
        h.run_until(lambda: h.coordinator.phase == "mine")
        h.clock.now += .2
        h.sequence += 1
        snap = replace(h.store.latest(), sequence=h.sequence, acquisition_sequence=h.sequence,
                       acquired_at=h.clock(), published_at=h.clock())
        h.store.publish(snap, tuple(EventEvidence("noise", h.clock(), {}) for _ in range(4)))
        h.coordinator.step()
        result = h.complete()
        self.assertEqual(result.reason, "observation_event_gap")

    def test_missing_route_and_equipment_fail_preflight(self):
        h = MissionFixture()
        h.coordinator.route = None
        self.assertEqual(h.complete().reason, "route_evidence_unavailable")
        h = MissionFixture()
        h.coordinator.equipment = {}
        self.assertEqual(h.complete().reason, "equipment_unavailable")

    def test_diagnostic_failure_does_not_stop_control(self):
        class Journal:
            def submit(self, event): raise OSError("disk failure")
        h = MissionFixture(journal=Journal())
        self.assertTrue(h.complete().completed)
        self.assertGreater(h.coordinator.journal_dropped, 0)

    def test_journal_receives_serializable_complete_transition_payloads(self):
        import json
        class Journal:
            def __init__(self): self.rows = []
            def submit(self, event):
                self.rows.append(json.loads(json.dumps(event, allow_nan=False)))
                return True
        journal = Journal()
        h = MissionFixture(journal=journal)
        self.assertTrue(h.complete().completed)
        self.assertEqual(h.coordinator.journal_dropped, 0)
        transitions = [row for row in journal.rows if row["event"] == "mining_transition"]
        self.assertEqual(len(transitions), h.coordinator.transitions_total)
        self.assertEqual(journal.rows[-1]["record"]["reason"], "reward_observed")

    def test_dispatch_failure_is_not_observed_completion(self):
        h = MissionFixture()
        h.tick()
        h.tick(deliver=False)
        for action in h.backend.drain(): h.backend.report_delivery(action, "failed")
        self.assertEqual(h.complete().reason, "action_rejected")

    def test_loss_and_reacquisition_between_ticks_abort_existing_mission(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        h.clock.now += .1
        h.receiver.selected = False
        h.publish()
        h.clock.now += .1
        h.receiver.selected = True
        h.publish()
        h.coordinator.step()
        self.assertFalse(h.complete().completed)

    def test_route_expiring_before_approach_cannot_authorize_thrust(self):
        h = MissionFixture()
        h.coordinator.route = replace(h.route, expires_at=.8)
        result = h.complete()
        self.assertEqual(result.reason, "route_evidence_expired")
        self.assertFalse(any(r.key == "PageUp" for r in h.backend.records()))

    def test_transient_inventory_conflict_is_retained(self):
        h = MissionFixture()
        h.run_until(lambda: h.coordinator.phase == "mine" and h.receiver.mining_active)
        h.receiver.inventory_conflict = True
        h.clock.now += .2
        h.publish()
        h.receiver.inventory_conflict = False
        result = h.complete()
        self.assertEqual(result.reason, "reward_attribution_uncertain")

    def test_motion_during_mining_cannot_complete_reward(self):
        h = MissionFixture()
        h.run_until(lambda: h.coordinator.phase == "mine" and h.receiver.mining_active)
        h.receiver.throttle = 1
        self.assertEqual(h.complete().reason, "mining_requires_stop")


if __name__ == "__main__": unittest.main()
