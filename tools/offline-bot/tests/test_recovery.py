from dataclasses import replace
import unittest
from bsgo_offline.mission import RouteAssessment
from bsgo_offline.state import FieldValue
from runtime_fixture import MissionFixture


class RecoveryTests(unittest.TestCase):
    def test_lost_target_does_not_prevent_observed_cleanup_stop(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        h.receiver.selected = False
        result = h.complete()
        self.assertFalse(result.completed)
        self.assertEqual(result.cleanup, "stopped")
        self.assertTrue(result.stop_observed)
        self.assertFalse(h.backend.held_keys)

    def test_ignored_brake_is_bounded_and_preserves_original_failure(self):
        h = MissionFixture(ignore_brake=True)
        result = h.complete()
        self.assertFalse(result.completed)
        self.assertEqual(result.reason, "approach_evidence_rejected")
        self.assertEqual(result.cleanup, "stop_unverified")

    def test_stalled_recognition_and_old_zero_do_not_confirm_cleanup(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        for _ in range(300):
            if h.tick(publish=False): break
        self.assertEqual(h.coordinator.result.cleanup, "stop_unverified")
        self.assertEqual(h.coordinator.result.reason, "observation_unavailable")
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        h.coordinator.cancel()
        h.tick()
        old_at = h.coordinator._cleanup_started_at - 1
        for _ in range(300):
            if h.tick(fields={"speed": FieldValue(0, old_at)}): break
        self.assertEqual(h.coordinator.result.cleanup, "stop_unverified")

    def test_loss_of_ownership_prevents_new_ordinary_input(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        h.receiver.owned = False
        result = h.complete()
        self.assertEqual(result.cleanup, "control_unavailable")
        self.assertFalse(any(r.key == "M" for r in h.backend.records()))

    def test_context_change_prevents_cleanup_on_new_surface(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        h.receiver.session_epoch += 1
        result = h.complete()
        self.assertEqual(result.cleanup, "control_unavailable")

    def test_cancel_each_phase_is_bounded_and_releases_mining_hold(self):
        for phase in ("preflight", "scan", "approach", "verify_stop", "mine", "confirm_outcome"):
            with self.subTest(phase=phase):
                h = MissionFixture()
                h.run_until(lambda: h.coordinator.phase == phase)
                if phase == "mine": h.tick()
                h.coordinator.cancel()
                result = h.complete()
                self.assertFalse(result.completed)
                self.assertEqual(result.reason, "cancelled")
                self.assertFalse(h.backend.held_keys)
                self.assertFalse(h.receiver.mining_active)

    def test_pause_resume_requires_new_session_and_fresh_preflight(self):
        h = MissionFixture()
        h.run_until(lambda: h.receiver.speed > 20)
        old_generation = h.coordinator.executor.generation
        h.coordinator.pause()
        result = h.complete()
        self.assertEqual(result.cleanup, "stopped")
        self.assertEqual(h.coordinator.read_status().phase, "paused")
        with self.assertRaises(RuntimeError):
            h.coordinator.resume(route=h.route, equipment={"configured": True, "resource": "Water"})
        h.deliver()
        h.backend.new_session("fixture-2")
        h.receiver.ownership_generation += 1
        h.clock.now += .2
        snap = h.publish()
        route = RouteAssessment(snap.context, 1, snap.acquired_at, snap.acquired_at + 120, "synthetic")
        h.coordinator.resume(route=route, equipment={"configured": True, "resource": "Water"})
        self.assertGreater(h.coordinator.executor.generation, old_generation)
        h.tick()
        self.assertEqual(h.coordinator.phase, "scan")
        # Resuming does not replay the prior approach command.
        self.assertFalse(any(r.session_id == "fixture-2" and r.key == "PageUp" for r in h.backend.records()))


if __name__ == "__main__": unittest.main()
