from dataclasses import replace
import unittest

from bsgo_offline.execution import SYNTHETIC_BINDINGS
from bsgo_offline.readiness import CYCLE_FIELDS, assess_preflight
from bsgo_offline.state import FieldValue
from runtime_fixture import MissionFixture, observation


class ReadinessTests(unittest.TestCase):
    def test_all_blockers_reported_without_input_or_tracking_changes(self):
        h = MissionFixture()
        h.coordinator.route = None
        h.coordinator.equipment = {}
        h.publish({"owned": FieldValue(None, 0, False, "ownership_not_validated"),
                   "speed": FieldValue(None, 0, False, "unreadable_speed")})
        report = h.coordinator.read_preflight()
        blockers = {check.name: check.reason for check in report.blockers}
        self.assertEqual(blockers["ownership"], "ownership_not_validated")
        self.assertEqual(blockers["stopped"], "unreadable_speed")
        self.assertIn("route", blockers)
        self.assertIn("equipment", blockers)
        self.assertIn("recognizer:scan_result", blockers)
        self.assertFalse(h.backend.records())
        self.assertIsNone(h.coordinator.tracker.track)

    def test_unknown_ownership_differs_from_lost_and_geometry_health(self):
        h = MissionFixture()
        h.publish({"owned": FieldValue(None, 0, False, "not_validated")})
        status = h.coordinator.read_status()
        self.assertIsNone(status.owned)
        self.assertTrue(status.observation_healthy)
        self.assertFalse(status.recognition_healthy)
        self.assertEqual(status.field_rejections["owned"], "not_validated")
        h.publish({"owned": FieldValue(False, 0)})
        self.assertIs(h.coordinator.read_status().owned, False)
        h.clock.now = 3
        status = h.coordinator.read_status()
        self.assertIsNone(status.owned)
        self.assertFalse(status.observation_healthy)
        self.assertFalse(status.recognition_healthy)

    def test_ready_requires_explicit_capabilities_and_native_bindings(self):
        h = MissionFixture()
        self.assertFalse(h.coordinator.read_preflight().ready)
        capabilities = dict.fromkeys(CYCLE_FIELDS, True)
        self.assertTrue(h.coordinator.read_preflight(capabilities).ready)
        native = replace(h.store.latest(), window_id="123:456")
        report = assess_preflight(native, 0, route=replace(h.route, context=native.context, source="human"),
            equipment=h.coordinator.equipment, bindings=SYNTHETIC_BINDINGS, capabilities=capabilities)
        blockers = {check.name: check.reason for check in report.blockers}
        self.assertEqual(blockers["binding:scan"], "synthetic_binding_on_other_surface")
        self.assertEqual(blockers["binding:start_mining"], "synthetic_binding_on_other_surface")

    def test_multiple_bad_values_stale_route_and_event_gap(self):
        h = MissionFixture()
        snapshot = observation(values={"speed": True, "distance": -1, "selection_revision": -1,
                                        "asteroid_selected": False})
        report = assess_preflight(snapshot, 1, route=replace(h.route, expires_at=.5),
            equipment={}, bindings={}, capabilities={"scan_result": False}, event_gap=True)
        blockers = {check.name for check in report.blockers}
        self.assertTrue({"stopped", "distance", "selection", "selection_revision", "route",
                         "event_history", "binding:scan", "recognizer:scan_result"} <= blockers)

    def test_missing_snapshot_does_not_throw_or_claim_readiness(self):
        report = assess_preflight(None, 0)
        self.assertFalse(report.ready)
        self.assertEqual(report.checks[0].state, "unknown")


if __name__ == "__main__":
    unittest.main()
