from dataclasses import replace
import unittest
from bsgo_offline.tracking import SelectionTracker
from runtime_fixture import observation


class TrackingTests(unittest.TestCase):
    def test_continuous_evidence_keeps_local_track(self):
        tracker = SelectionTracker()
        first = tracker.update(observation(), 1)
        second = tracker.update(observation(2, 1.2), 1.2)
        self.assertEqual(first.track_id, second.track_id)
        self.assertEqual(second.continuity, "confirmed")

    def test_identical_target_after_loss_gets_new_track(self):
        tracker = SelectionTracker()
        first = tracker.update(observation(), 1)
        tracker.update(observation(2, 1.2, values={"asteroid_selected": False}), 1.2)
        second = tracker.update(observation(3, 1.4), 1.4)
        self.assertGreater(second.track_id, first.track_id)

    def test_gap_unknown_context_revision_and_explicit_selection_break_continuity(self):
        for changed in (observation(2, 4), observation(2, 1.2, session_epoch=2),
                        observation(2, 1.2, layout="other"),
                        observation(2, 1.2, ownership_generation=2),
                        observation(2, 1.2, values={"selection_revision": 2})):
            tracker = SelectionTracker()
            first = tracker.update(observation(), 1)
            self.assertNotEqual(first.track_id, tracker.update(changed, changed.acquired_at).track_id)
        tracker = SelectionTracker()
        first = tracker.update(observation(), 1)
        tracker.update(observation(2, 1.2, values={"target_portrait": None}), 1.2)
        self.assertEqual(tracker.track.continuity, "uncertain")
        self.assertNotEqual(first.track_id, tracker.update(observation(3, 1.4), 1.4).track_id)
        tracker.invalidate()
        self.assertEqual(tracker.update(observation(3, 1.4), 1.4).continuity, "lost")
        self.assertNotEqual(first.track_id, tracker.update(observation(4, 1.6), 1.6).track_id)

    def test_stale_frame_cannot_reacquire(self):
        tracker = SelectionTracker()
        tracker.update(observation(), 1)
        tracker.update(observation(), 4)
        self.assertEqual(tracker.track.continuity, "uncertain")


if __name__ == "__main__": unittest.main()
