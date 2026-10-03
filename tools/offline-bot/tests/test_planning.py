from dataclasses import replace
import unittest
from bsgo_offline.planning import AdvicePlanner, AdviceProposal, AdviceRequest, validate_advice
from bsgo_offline.tracking import SelectionTracker
from runtime_fixture import MissionFixture, observation


class PlanningTests(unittest.TestCase):
    def test_newer_sequence_is_valid_but_changed_dependencies_are_not(self):
        tracker = SelectionTracker()
        track = tracker.update(observation(), 1)
        request = AdviceRequest("request", 1, observation().context, track.track_id, 1, 10, ("candidate",))
        proposal = AdviceProposal("request", 1)
        snap = observation(8, 2)
        track = tracker.update(snap, 2)
        self.assertTrue(validate_advice(proposal, request, snap, track, 1, 2)[0])
        self.assertFalse(validate_advice(proposal, request, snap, track, 2, 2)[0])
        self.assertFalse(validate_advice(proposal, request, snap, track, 1, 11)[0])
        self.assertFalse(validate_advice(proposal, request, replace(snap, session_epoch=2), track, 1, 2)[0])
        tracker.invalidate()
        self.assertFalse(validate_advice(proposal, request, snap, tracker.track, 1, 2)[0])
        self.assertFalse(validate_advice({"action": "observe"}, request, snap, track, 1, 2)[0])
        self.assertFalse(validate_advice(replace(proposal, action="execute"), request, snap, track, 1, 2)[0])
        self.assertTrue(validate_advice(replace(proposal, action="choose_candidate", candidate_id="candidate"),
                                       request, snap, track, 1, 2)[0])

    def test_failed_planner_does_not_stall_mission(self):
        class FailedPlanner:
            def request(self, request): raise RuntimeError("unavailable")
        h = MissionFixture(planner=FailedPlanner())
        self.assertTrue(h.complete().completed)
        self.assertTrue(any(t["kind"] == "planner_unavailable" for t in h.coordinator.transitions))

    def test_fake_planner_rejects_concurrent_request_and_closes(self):
        request = AdviceRequest("request", 1, observation().context, 1, 1, 10)
        planner = AdvicePlanner(10)
        try:
            planner.request(request)
            with self.assertRaises(RuntimeError): planner.request(request)
        finally:
            self.assertTrue(planner.close(.5))

    def test_cancelled_planner_can_be_reused_and_late_result_is_not_returned(self):
        request = AdviceRequest("request", 1, observation().context, 1, 1, 10)
        planner = AdvicePlanner(10)
        planner.request(request)
        self.assertTrue(planner.close(.5))
        self.assertIsNone(planner.poll())
        planner.request(replace(request, request_id="new", generation=2))
        self.assertTrue(planner.close(.5))



if __name__ == "__main__": unittest.main()
