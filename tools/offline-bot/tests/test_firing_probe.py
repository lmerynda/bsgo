"""Synthetic image and delivery seams for one supervised G experiment."""
from threading import Event, Lock, Thread
import time
import unittest

from bsgo_offline.firing_probe import run_firing_probe
from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_scan_probe import PixelSample
from test_trace import scratch_directory


PROFILE = "largehud-20261003"


class Source:
    def __init__(self, *, effect=True, baseline_damage=False, invalid_after=False,
                 change_target=False, stall_after=None, cancel_at=None,
                 cancel_path=None):
        self.sequence = 0
        self.closed = False
        self.effect = effect
        self.baseline_damage = baseline_damage
        self.invalid_after = invalid_after
        self.change_target = change_target
        self.stall_after = stall_after
        self.cancel_at, self.cancel_path = cancel_at, cancel_path
        self.delivery_sequence = None

    def next_sample(self, stop):
        if self.stall_after is not None and self.sequence >= self.stall_after:
            stop.wait(2)
            return None
        if stop.wait(.025):
            return None
        self.sequence += 1
        if self.sequence == self.cancel_at:
            self.cancel_path.touch()
        after = (self.delivery_sequence is not None
                 and self.sequence > self.delivery_sequence)
        hp = 4500
        if self.baseline_damage and self.sequence >= 3:
            hp = 4490
        if after and self.effect:
            hp = 4500 - 10 * (self.sequence - self.delivery_sequence)
        values = {"speed": 0., "asteroid_selected": True, "distance": 677.,
            "target_identity": "asteroid:hp:4500", "target_portrait": tuple([1] * 144),
            "target_max_hp": 4500, "target_current_hp": hp}
        if after and self.invalid_after:
            values["target_current_hp"] = None
        if after and self.change_target:
            values["target_max_hp"] = 4600
        sample = PixelSample(self.sequence, time.monotonic(), values)
        sample.layout = PROFILE
        return sample

    def close(self):
        self.closed = True


def recognize(sample):
    return RecognitionResult((4, 3), {name: FieldValue(value, sample.acquired_at,
        valid=value is not None, reason="fixture_rejection" if value is None else None)
        for name, value in sample.values.items()})


class Dispatcher:
    def __init__(self, backend, source, *, ignore=False, cancel_on_G=None):
        self.backend, self.source, self.ignore = backend, source, ignore
        self.cancel_on_G = cancel_on_G
        self._stop, self._lock = Event(), Lock()
        self._thread = None
        self._deliveries = []
        self._held = set()
        self.error = None

    def start(self):
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            request = self.backend.get_action(timeout=.005)
            if request is None:
                continue
            permitted = (request.kind == "key_up" and request.key in self._held)
            permitted = (permitted or self.backend.delivery_allowed(request)) and not self.ignore
            outcome = "injected" if permitted else "skipped_dependency"
            if permitted and request.kind == "key_down":
                self._held.add(request.key)
            elif permitted and request.kind == "key_up":
                self._held.discard(request.key)
                self.source.delivery_sequence = self.source.sequence
                if self.cancel_on_G:
                    self.cancel_on_G.touch()
            delivery = DeliveryRecord(request.sequence, time.monotonic(), request.kind,
                outcome, 1, 1 if permitted else 0, 0)
            with self._lock:
                self._deliveries.append(delivery)
            self.backend.report_delivery(request, outcome)

    def deliveries(self):
        with self._lock:
            return tuple(self._deliveries)

    def stop(self, timeout):
        self._stop.set()
        self.backend.cancel(reason="fake_dispatcher_stop")
        self._thread.join(timeout)
        return not self._thread.is_alive() and not self._held


class FiringProbeTests(unittest.TestCase):
    def run_probe(self, directory, source, *, ignore=False, after_seconds=.3,
                  cancel_file=None, cancel_on_G=False):
        dispatchers = []
        def factory(backend, journal):
            dispatcher = Dispatcher(backend, source, ignore=ignore,
                cancel_on_G=cancel_file if cancel_on_G else None)
            dispatchers.append(dispatcher)
            return dispatcher
        result = run_firing_probe(directory / "run", source_factory=lambda: source,
            recognizer_factory=lambda: recognize, dispatcher_factory=factory,
            configuration={"fixture": True}, profile=PROFILE,
            expected_window_id="42:17", frame_size=(4, 3),
            safe_stopped_scene_assessed=True, wanted_resource_assessed="Tylium",
            no_preexisting_firing_assessed=True, scope="synthetic",
            baseline_seconds=.5, stable_seconds=.075,
            after_seconds=after_seconds, cancel_file=cancel_file)
        return result, dispatchers

    def test_two_distinct_post_delivery_declines_prove_narrow_effect(self):
        with scratch_directory() as directory:
            source = Source()
            result, dispatchers = self.run_probe(directory, source)
            self.assertEqual(result["state"], "proved", result)
            self.assertTrue(result["firing_effect_verified"])
            self.assertFalse(result["mining_ready"])
            self.assertEqual([r["key"] for r in result["requests"]], ["G", "G"])
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()], ["injected"] * 2)
            self.assertEqual(len(result["falling"]), 2)
            self.assertGreater(result["falling"][0]["target_current_hp"],
                               result["falling"][1]["target_current_hp"])
            self.assertTrue(result["complete_diagnostics"])
            self.assertTrue(all(result["shutdown"].values()))
            self.assertTrue(source.closed)
            self.assertIn("autofire may continue", result["warning"])

    def test_no_G_if_hp_changes_during_baseline(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(baseline_damage=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("preexisting_target_hp_change", result["reason"])
            self.assertEqual(dispatchers, [])
            self.assertEqual(result["requests"], [])

    def test_ignored_G_does_not_prove_firing(self):
        with scratch_directory() as directory:
            result, dispatchers = self.run_probe(directory, Source(), ignore=True)
            self.assertEqual(result["state"], "blocked")
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(all(d.outcome != "injected" for d in dispatchers[0].deliveries()))

    def test_no_hp_effect_does_not_prove_firing(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(effect=False), after_seconds=.18)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("two_post_delivery_hp_declines_not_observed", result["reason"])

    def test_invalid_hp_after_G_blocks_and_retains_failed_image(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(invalid_after=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("invalid_or_stale_target_current_hp", result["reason"])
            self.assertGreaterEqual(len(list((directory / "run" / "failed-recognition").glob("*.png"))), 1)
            self.assertGreaterEqual(len(list((directory / "run" / "failed-recognition").glob("*.json"))), 1)

    def test_selected_target_change_blocks(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(change_target=True))
            self.assertEqual(result["state"], "blocked")
            self.assertIn("selected_target_changed", result["reason"])

    def test_stalled_image_stream_cannot_prove_effect(self):
        with scratch_directory() as directory:
            result, _ = self.run_probe(directory, Source(stall_after=5), after_seconds=.15)
            self.assertEqual(result["state"], "blocked")
            self.assertFalse(result["firing_effect_verified"])
            self.assertTrue(all(result["shutdown"].values()))

    def test_existing_cancel_marker_prevents_G(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            marker.touch()
            result, dispatchers = self.run_probe(directory, Source(), cancel_file=marker)
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])
            self.assertTrue(result["cancel_requested"])
            self.assertTrue(marker.exists())

    def test_cancel_during_baseline_prevents_G(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, dispatchers = self.run_probe(directory,
                Source(cancel_at=2, cancel_path=marker), cancel_file=marker)
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertEqual(result["requests"], [])
            self.assertEqual(dispatchers, [])

    def test_cancel_after_G_does_not_claim_firing_or_ceasefire(self):
        with scratch_directory() as directory:
            marker = directory / "stop.request"
            result, dispatchers = self.run_probe(directory, Source(),
                cancel_file=marker, cancel_on_G=True)
            self.assertEqual(result["state"], "blocked")
            self.assertEqual(result["reason"], "cancel_requested")
            self.assertFalse(result["firing_effect_verified"])
            self.assertEqual(result["firing_state_after_probe"],
                             "unverified_supervisor_retains_control")
            self.assertEqual([d.outcome for d in dispatchers[0].deliveries()],
                             ["injected"] * 2)
            self.assertTrue(all(result["shutdown"].values()))


if __name__ == "__main__":
    unittest.main()
