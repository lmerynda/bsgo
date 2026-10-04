import math
import unittest
from dataclasses import replace

from bsgo_offline.policy import ApproachPolicy, PolicyError
from bsgo_offline.state import FieldValue, Snapshot


def frame(time=10.0, distance=1150, speed=0, *, epoch=0, layout="original",
          sequence=None, changes=None):
    values = {"distance": distance, "speed": speed, "asteroid_selected": True,
              "target_identity": "hp-signature", "target_portrait": tuple([0] * 144)}
    fields = {name: FieldValue(value=value, observed_at=time) for name, value in values.items()}
    fields.update(changes or {})
    return Snapshot(sequence=int(time) if sequence is None else sequence,
                    session_epoch=epoch, acquisition_sequence=int(time), acquired_at=time,
                    published_at=time, layout=layout, frame_size=(2560, 1440), fields=fields)


def begin_braking(policy=None):
    policy = policy or ApproachPolicy()
    for time, distance, speed, action in (
            (10, 1150, 0, "accelerate"), (12, 1100, 44, "continue"),
            (14, 1020, 44, "continue"), (16, 940, 44, "continue"),
            (18, 860, 44, "brake")):
        assert policy.decide(frame(time, distance, speed), time) == action
    return policy


class ApproachTests(unittest.TestCase):
    def test_success_requires_observed_deceleration_and_new_zero(self):
        policy = begin_braking()
        self.assertEqual(policy.decide(frame(20, 790, 30), 20), "continue")
        self.assertEqual(policy.decide(frame(22, 750, 15), 22), "continue")
        self.assertEqual(policy.decide(frame(24, 730, 0), 24), "complete")
        self.assertEqual(policy.final.distance, 730)
        self.assertEqual(policy.phase, "complete")

    def test_corrupt_or_unknown_fields_cannot_authorize_thrust(self):
        cases = [("distance", None), ("distance", math.inf), ("distance", -1),
                 ("distance", True), ("speed", None), ("speed", 44.2), ("speed", -1),
                 ("asteroid_selected", False), ("target_identity", ""),
                 ("target_portrait", (0,) * 143), ("target_portrait", (True,) * 144)]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                sample = frame(changes={name: FieldValue(value, 10)})
                with self.assertRaises(PolicyError):
                    ApproachPolicy().decide(sample, 10)
        with self.assertRaises(PolicyError):
            ApproachPolicy().decide(frame(changes={"distance": FieldValue(1150, 10, valid=False)}), 10)

    def test_unhealthy_recognition_rejects_previous_values(self):
        with self.assertRaisesRegex(PolicyError, "Unhealthy"):
            ApproachPolicy().decide(replace(frame(), healthy=False, reason="reader failed"), 10)

    def test_snapshot_and_each_field_have_observation_freshness(self):
        with self.assertRaisesRegex(PolicyError, "Stale"):
            ApproachPolicy().decide(frame(), 12.001)
        for name in ("distance", "speed", "asteroid_selected", "target_identity", "target_portrait"):
            sample = frame()
            with self.subTest(name=name):
                sample = frame(changes={name: replace(sample.fields[name], observed_at=7.9)})
                with self.assertRaisesRegex(PolicyError, "Stale"):
                    ApproachPolicy().decide(sample, 10)
        with self.assertRaises(PolicyError):
            ApproachPolicy().decide(frame(changes={"speed": FieldValue(0, 10.1)}), 10)

    def test_initial_stopped_and_clearance_are_required(self):
        for distance, speed in ((1200, 0.1), (1149, 0)):
            with self.assertRaises(PolicyError):
                ApproachPolicy().decide(frame(distance=distance, speed=speed), 10)

    def test_explicit_short_clearance_keeps_distance_driven_braking(self):
        policy = ApproachPolicy(brake_at=800, launch_clearance=75,
                                max_observation_gap=7)
        for time, distance, speed, action in (
                (10, 880, 0, "accelerate"), (15, 855, 10.9, "continue"),
                (20, 769, 23.2, "brake"), (25, 710, 10, "continue"),
                (30, 680, 0, "complete")):
            self.assertEqual(policy.decide(frame(time, distance, speed), time), action)
        for value in (74.9, 250.1, True, math.nan, math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(PolicyError, "Launch clearance"):
                ApproachPolicy(launch_clearance=value)

    def test_target_identity_and_portrait_changes_reject(self):
        for name, value in (("target_identity", "different-hp-signature"),
                            ("target_portrait", (1,) * 144)):
            policy = ApproachPolicy()
            policy.decide(frame(), 10)
            with self.assertRaisesRegex(PolicyError, "target"):
                policy.decide(frame(12, 1100, 20, changes={name: FieldValue(value, 12)}), 12)

    def test_context_changes_invalidate_approach(self):
        for sample in (frame(12, 1100, 20, epoch=1),
                       frame(12, 1100, 20, layout="restart"),
                       replace(frame(12, 1100, 20), frame_size=(1920, 1080))):
            policy = ApproachPolicy()
            policy.decide(frame(), 10)
            with self.assertRaisesRegex(PolicyError, "changed"):
                policy.decide(sample, 12)

    def test_validation_rejects_changed_context_without_advancing_policy(self):
        policy = ApproachPolicy()
        policy.decide(frame(), 10)
        previous = policy.last
        with self.assertRaisesRegex(PolicyError, "changed"):
            policy.validate(frame(12, 1100, 20, epoch=1), 12)
        self.assertIs(policy.last, previous)
        self.assertEqual(policy.phase, "approaching")

    def test_validation_checks_finite_now_and_future_timestamps(self):
        for now in (math.inf, math.nan, True, 9.9):
            with self.subTest(now=now):
                with self.assertRaises(PolicyError):
                    ApproachPolicy().validate(frame(), now)
        with self.assertRaises(PolicyError):
            ApproachPolicy().validate(replace(frame(), published_at=10.1), 10)

    def test_acquisition_clock_controls_gaps_and_plausibility(self):
        for sample, now in ((frame(15, 1100, 20), 15),
                            (replace(frame(12, 1100, 20), acquired_at=10), 12),
                            (replace(frame(12, 1100, 20), acquisition_sequence=10), 12),
                            (frame(12, 900, 20), 12), (frame(12, 1154, 20), 12)):
            policy = ApproachPolicy()
            policy.decide(frame(), 10)
            with self.subTest(sample=sample):
                with self.assertRaises(PolicyError):
                    policy.decide(sample, now)

    def test_publication_sequence_change_is_not_target_change(self):
        policy = ApproachPolicy()
        policy.decide(frame(sequence=100), 10)
        self.assertEqual(policy.decide(frame(12, 1100, 20, sequence=1000), 12), "continue")

    def test_configured_five_second_cadence_accepts_closure_and_rejects_missed_sample(self):
        policy = ApproachPolicy(max_frame_age=7, max_observation_gap=7)
        self.assertEqual(policy.decide(frame(10, 1800, 0), 10), "accelerate")
        self.assertEqual(policy.decide(frame(15, 1650, 33), 15), "continue")
        self.assertEqual(policy.decide(frame(20, 1485, 33), 20), "continue")
        with self.assertRaisesRegex(PolicyError, "Observation gap"):
            policy.decide(frame(28, 1221, 33), 28)

    def test_ten_second_cadence_does_not_create_elapsed_flight_limit(self):
        policy = ApproachPolicy(max_frame_age=12, max_observation_gap=12)
        self.assertEqual(policy.decide(frame(10, 8000, 0), 10), "accelerate")
        for at in range(20, 230, 10):
            self.assertEqual(policy.decide(frame(at, 8000 - (at - 10) * 33, 33), at),
                             "continue")
        self.assertEqual(policy.decide(frame(230, 740, 33), 230), "brake")

    def test_fast_ship_requires_measured_closing(self):
        policy = ApproachPolicy()
        policy.decide(frame(), 10)
        policy.decide(frame(12, 1100, 20), 12)
        with self.assertRaisesRegex(PolicyError, "stalled"):
            policy.decide(frame(14, 1099, 20), 14)

    def test_high_rate_recognition_must_not_blindly_drive_policy_cadence(self):
        policy = ApproachPolicy()
        policy.decide(frame(), 10)
        policy.decide(frame(12, 1100, 20), 12)
        # A 20 Hz recognition loop sees legitimate one-metre progress, but the
        # retained prototype guard assumes roughly two-second policy samples.
        sample = replace(frame(12.05, 1099, 20), acquisition_sequence=13)
        self.assertEqual(policy.validate(sample, 12.05).distance, 1099)
        self.assertEqual(policy.last.acquired_at, 12)
        with self.assertRaisesRegex(PolicyError, "stalled"):
            policy.decide(sample, 12.05)

    def test_two_samples_without_brake_acknowledgement_fail(self):
        policy = begin_braking()
        self.assertEqual(policy.decide(frame(20, 780, 44), 20), "continue")
        with self.assertRaisesRegex(PolicyError, "not acknowledged"):
            policy.decide(frame(22, 700, 44), 22)

    def test_braking_increase_or_stalled_deceleration_fail(self):
        policy = begin_braking()
        policy.decide(frame(20, 790, 40), 20)
        with self.assertRaisesRegex(PolicyError, "increased"):
            policy.decide(frame(22, 720, 40.31), 22)
        policy = begin_braking()
        policy.decide(frame(20, 790, 40), 20)
        policy.decide(frame(22, 720, 40), 22)
        with self.assertRaisesRegex(PolicyError, "stalled"):
            policy.decide(frame(24, 650, 40), 24)

    def test_braking_limit_is_thirty_seconds_without_verified_zero(self):
        policy = begin_braking()
        self.assertEqual(policy.max_braking, 30)
        for step in range(1, 15):
            time = 18 + step * 2
            policy.decide(frame(time, 860 - step * 10, 44 - step), time)
        with self.assertRaisesRegex(PolicyError, "time limit"):
            policy.decide(frame(48, 710, 29), 48)

    def test_cached_zero_cannot_confirm_stop(self):
        policy = begin_braking()
        # Within the generic two-second freshness limit, but before braking.
        with self.assertRaisesRegex(PolicyError, "after the brake"):
            policy.decide(frame(19, 820, 0, changes={"speed": FieldValue(0, 18)}), 19)
        self.assertEqual(policy.phase, "braking")
        self.assertIsNone(policy.final)

    def test_repeated_acquisition_cannot_confirm_stop(self):
        policy = begin_braking()
        with self.assertRaises(PolicyError):
            policy.decide(frame(18, 860, 0), 18)
        self.assertEqual(policy.phase, "braking")

    def test_policy_defaults_match_current_prototype(self):
        policy = ApproachPolicy()
        self.assertEqual((policy.brake_at, policy.max_speed, policy.max_frame_age,
                          policy.brake_ack, policy.max_braking), (900, 44, 2, 4, 30))


if __name__ == "__main__":
    unittest.main()
