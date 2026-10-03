"""Pure motion-probe tests; no native window or game input is used."""
from dataclasses import replace
import unittest

from bsgo_offline.calibration_evidence import MotionBrakeProbe
from bsgo_offline.input import ActionRecord
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.state import FieldValue, Snapshot


PROFILE = "restart-20261001"
HASHES = {name: "a" * 64 for name in
          ("hud-reader/hud_reader.py", "hud-reader/templates.json",
           "hud-reader/restart-calibration.json")}


def image(sequence, at, speed, *, window_id="42:17", valid=True):
    return Snapshot(sequence, 1, sequence, at, at + .01, PROFILE, (3840, 2160),
        {"speed": FieldValue(speed, at, valid, None if valid else "unreadable")},
        window_id=window_id, capture_started_at=at,
        capture_completed_at=at + .005)


def delivery(sequence, at, kind, key, *, outcome="injected", inserted=1,
             action="probe"):
    request = ActionRecord(sequence, at, kind, "session-1", PROFILE, key=key,
                           reason=action)
    result = DeliveryRecord(sequence, at + .01, kind, outcome, 1, inserted, 0)
    return request, result


def probe():
    context = image(1, 1., 0.).context
    return MotionBrakeProbe(context=context, session_id="session-1", profile=PROFILE,
        source_sha256=HASHES, scope="native", started_at=.5, expires_at=10.,
        run_id="run-1")


def add_acceleration(item, *, outcome="injected"):
    for sequence, at, kind in ((1, 1.30, "key_down"), (2, 1.32, "key_up")):
        request, result = delivery(sequence, at, kind, "PageUp", outcome=outcome)
        item.record_delivery("accelerate", "accelerate-1", request, result)


def add_brake(item):
    for sequence, at, kind in ((3, 1.70, "key_down"), (4, 1.72, "key_up")):
        request, result = delivery(sequence, at, kind, "PageDown")
        item.record_delivery("brake", "brake-1", request, result)


class CalibrationEvidenceTests(unittest.TestCase):
    def test_full_motion_then_two_fresh_stop_images_yields_scoped_certificate(self):
        item = probe()
        item.observe(image(1, 1.0, 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        self.assertEqual(item.evaluate(1.4).reason, "waiting_for_image_observed_motion")
        item.observe(image(3, 1.6, 3.))
        add_brake(item)
        item.observe(image(4, 1.9, 0.))
        self.assertEqual(item.evaluate(2.0).state, "pending")
        item.observe(image(5, 2.1, 0.))
        result = item.evaluate(2.2)
        self.assertEqual(result.state, "proved")
        certificate = result.certificate
        self.assertEqual(certificate.scope, "native")
        self.assertEqual(certificate.result, "observed_motion_and_stop")
        self.assertEqual(certificate.context.window_id, "42:17")
        self.assertEqual(certificate.session_id, "session-1")
        self.assertEqual(certificate.profile, PROFILE)
        self.assertEqual(certificate.run_id, "run-1")
        self.assertEqual(certificate.baseline_observation_sequences, (1, 2))
        self.assertEqual(certificate.moving_observation_sequence, 3)
        self.assertEqual(certificate.stopped_observation_sequences, (4, 5))
        self.assertEqual(certificate.acceleration_request_sequences, (1, 2))
        self.assertEqual(certificate.brake_request_sequences, (3, 4))
        self.assertEqual(certificate.source_sha256["hud-reader/hud_reader.py"], "a" * 64)
        with self.assertRaises(TypeError):
            certificate.source_sha256["new"] = "b" * 64

    def test_insertion_without_observed_motion_never_proves_control(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        item.observe(image(3, 1.6, 0.))
        self.assertEqual(item.evaluate(1.7).state, "pending")
        self.assertEqual(item.evaluate(6.4).reason, "motion_response_timeout")

    def test_braking_must_follow_published_motion_and_show_two_zero_images(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        add_brake(item)
        item.observe(image(3, 1.9, 0.))
        self.assertEqual(item.evaluate(2.).reason, "brake_before_observed_motion")
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        item.observe(image(3, 1.6, 3.))
        add_brake(item)
        item.observe(image(4, 1.9, 0.))
        self.assertEqual(item.evaluate(6.9).reason, "stop_response_timeout")

    def test_failed_delivery_and_context_change_fail_closed(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        request, result = delivery(1, 1.3, "key_down", "PageUp", outcome="failed", inserted=0)
        item.record_delivery("accelerate", "accelerate-1", request, result)
        self.assertEqual(item.evaluate(1.4).reason, "input_not_completely_delivered")
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0., window_id="42:18"))
        self.assertEqual(item.evaluate(1.3).reason, "capture_context_changed")

    def test_stale_invalid_and_future_image_evidence_fail_closed(self):
        item = probe()
        item.observe(replace(image(1, 1., 0.), published_at=4.))
        self.assertEqual(item.evaluate(4.1).reason, "unhealthy_or_late_observation")
        item = probe()
        item.observe(image(1, 1., 0., valid=False))
        self.assertEqual(item.evaluate(1.1).reason, "speed_not_freshly_read_from_image")
        item = probe()
        item.observe(image(1, 2., 0.))
        self.assertEqual(item.evaluate(1.).reason, "future_dated_evidence")

    def test_stopped_baseline_and_probe_expiry_are_required(self):
        item = probe()
        item.observe(image(1, 1., 1.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        self.assertEqual(item.evaluate(1.4).reason, "stopped_baseline_not_observed")
        item = probe()
        self.assertEqual(item.evaluate(10.).reason, "probe_expired")

    def test_atomic_press_pair_may_share_request_timestamp(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(replace(image(2, 1.2, 0.), published_at=1.3))
        down, first = delivery(1, 1.3, "key_down", "PageUp")
        up, second = delivery(2, 1.3, "key_up", "PageUp")
        item.record_delivery("accelerate", "accelerate-1", down, first)
        item.record_delivery("accelerate", "accelerate-1", up,
                             replace(second, at=first.at + .01))
        self.assertEqual(item.evaluate(1.4).reason, "waiting_for_image_observed_motion")

    def test_late_motion_and_ignored_brake_do_not_certify(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        item.observe(image(3, 6.5, 3.))
        self.assertEqual(item.evaluate(6.6).reason, "motion_response_timeout")
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        item.observe(image(3, 1.6, 3.))
        add_brake(item)
        item.observe(image(4, 1.9, 2.))
        item.observe(image(5, 2.1, 1.))
        self.assertEqual(item.evaluate(6.9).reason, "stop_response_timeout")

    def test_native_probe_requires_actual_capture_provenance(self):
        item = probe()
        item.observe(replace(image(1, 1., 0.), capture_started_at=None))
        self.assertEqual(item.evaluate(1.1).reason, "native_image_provenance_missing")

    def test_reappearing_speed_after_first_zero_blocks_stop_proof(self):
        item = probe()
        item.observe(image(1, 1., 0.))
        item.observe(image(2, 1.2, 0.))
        add_acceleration(item)
        item.observe(image(3, 1.6, 3.))
        add_brake(item)
        item.observe(image(4, 1.9, 0.))
        item.observe(image(5, 2.1, 1.))
        item.observe(image(6, 2.3, 0.))
        self.assertEqual(item.evaluate(2.4).reason, "speed_reappeared_after_stop")

    def test_synthetic_scope_is_labeled_and_never_mistaken_for_native(self):
        first = replace(image(1, 1., 0.), window_id="synthetic:probe",
                        capture_started_at=None, capture_completed_at=None)
        item = MotionBrakeProbe(context=first.context, session_id="session-1",
            profile=PROFILE, source_sha256=HASHES, scope="synthetic",
            started_at=.5, expires_at=10.)
        item.observe(first)
        item.observe(replace(image(2, 1.2, 0.), window_id="synthetic:probe",
                             capture_started_at=None, capture_completed_at=None))
        request, result = delivery(1, 1.3, "key_down", "PageUp")
        item.record_delivery("accelerate", "synthetic-1", request, result)
        self.assertEqual(item.evaluate(1.4).reason, "input_not_completely_delivered")
        item = MotionBrakeProbe(context=first.context, session_id="session-1",
            profile=PROFILE, source_sha256=HASHES, scope="synthetic",
            started_at=.5, expires_at=10.)
        for sequence, at, speed in ((1, 1., 0.), (2, 1.2, 0.), (3, 1.6, 3.),
                                    (4, 1.9, 0.), (5, 2.1, 0.)):
            item.observe(replace(image(sequence, at, speed), window_id="synthetic:probe",
                                 capture_started_at=None, capture_completed_at=None))
        for sequence, at, kind, key, operation, action in (
                (1, 1.30, "key_down", "PageUp", "accelerate", "synthetic-1"),
                (2, 1.32, "key_up", "PageUp", "accelerate", "synthetic-1"),
                (3, 1.70, "key_down", "PageDown", "brake", "synthetic-2"),
                (4, 1.72, "key_up", "PageDown", "brake", "synthetic-2")):
            request, result = delivery(sequence, at, kind, key)
            item.record_delivery(operation, action, request, replace(result,
                outcome="received", evidence="synthetic_receiver_reported_effect"))
        self.assertEqual(item.evaluate(2.2).certificate.scope, "synthetic")


if __name__ == "__main__":
    unittest.main()
