"""Experimental X/G fight loop with a fake dispatcher; no native window or game calls."""
from threading import Event, Lock, Thread
import random
import time
import unittest

from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.npc_fight import run_npc_fight
from bsgo_offline.manual_assist import ManualAssistController
from test_manual_assist import snapshot
from test_trace import scratch_directory


class KeyDispatcher:
    """Inject every permitted record; optionally fail like a lost-focus guard."""
    def __init__(self, backend, *, fail_on=None):
        self.backend, self.fail_on = backend, fail_on
        self.stop_event, self.lock = Event(), Lock()
        self.rows, self.keys, self.held = [], [], set()
        self.error = None

    def start(self):
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def run(self):
        while not self.stop_event.is_set():
            record = self.backend.get_action(timeout=.01)
            if record is None:
                continue
            if record.kind == "key_down" and record.key == self.fail_on:
                with self.lock:
                    self.error = "Target window is not foreground"
                outcome = "skipped_shutdown"
            else:
                permitted = (record.kind == "key_up" and record.key in self.held) or self.backend.delivery_allowed(record)
                outcome = "injected" if permitted else "skipped_dependency"
                if permitted and record.kind == "key_down":
                    self.held.add(record.key)
                    self.keys.append(record.key)
                elif permitted:
                    self.held.discard(record.key)
            with self.lock:
                self.rows.append(DeliveryRecord(record.sequence, time.monotonic(), record.kind, outcome, 1,
                                                1 if outcome == "injected" else 0, 0))
            self.backend.report_delivery(record, outcome)

    def deliveries(self):
        with self.lock:
            return tuple(self.rows)

    def stop(self, timeout):
        self.stop_event.set()
        self.backend.cancel(reason="fixture_stop")
        self.thread.join(timeout)
        return not self.thread.is_alive() and not self.held


class NpcFightTests(unittest.TestCase):
    def run_loop(self, directory, **options):
        made, fail_on = [], options.pop("fail_on", None)
        def factory(backend, journal):
            made.append(KeyDispatcher(backend, fail_on=fail_on))
            return made[0]
        result = run_npc_fight(directory / "run", dispatcher_factory=factory, configuration={"fixture": True},
            profile="largehud-20261003", frame_size=(2560, 1440), cancel_file=directory / "stop.request",
            scope="synthetic", **options)
        return result, made[0]

    def test_cycles_press_x_then_g_with_bounded_random_waits(self):
        waits = []
        class Recording(random.Random):
            def uniform(self, a, b):
                waits.append((a, b))
                return a
        with scratch_directory() as directory:
            started = time.monotonic()
            result, dispatcher = self.run_loop(directory, delay=(.2, .3), max_cycles=2, rng=Recording())
            self.assertEqual(result["state"], "recorded", result)
            self.assertEqual(dispatcher.keys, ["X", "G", "X", "G"])
            self.assertEqual(result["presses"], {"X": 2, "G": 2})
            self.assertTrue(all(window == (.2, .3) for window in waits))
            self.assertGreaterEqual(time.monotonic() - started, .6, "waits after each X and between cycles")
            self.assertTrue(all(result["shutdown"].values()))

    def test_cancel_stops_before_the_next_key(self):
        with scratch_directory() as directory:
            stop = directory / "stop.request"
            def cancel_after_first(cycle):
                stop.write_text("stop", encoding="utf-8")
            result, dispatcher = self.run_loop(directory, delay=(.2, .2), on_cycle=cancel_after_first)
            self.assertEqual(dispatcher.keys, ["X", "G"])
            self.assertTrue(result["cancel_requested"])
            self.assertEqual(result["reason"], "stopped")

    def test_refused_key_ends_the_loop_with_its_cause(self):
        with scratch_directory() as directory:
            result, dispatcher = self.run_loop(directory, delay=(.2, .2), fail_on="G", max_cycles=5)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("Target window is not foreground", result["reason"])
            self.assertEqual(dispatcher.keys, ["X"])

    def test_delay_bounds_are_validated(self):
        with scratch_directory() as directory:
            for delay in ((0, 1), (2, 1), (1, 11), "1-2"):
                with self.assertRaises(ValueError):
                    run_npc_fight(directory / "bad", dispatcher_factory=None, configuration={},
                        profile="p", frame_size=(1, 1), cancel_file=directory / "x", delay=delay)

    def test_controller_starts_without_scene_token_and_respects_pause_and_owner(self):
        with scratch_directory() as directory:
            calls, release = [], Event()
            class Runner:
                supports_npc_fight = True
                def __call__(self, name, **kw):
                    calls.append(name)
                    release.wait(2)
                    return {"state": "recorded", "reason": "stopped",
                            "shutdown": {k: True for k in ("input_idle", "dispatcher", "recognition", "source")}}
            paused = ManualAssistController(directory / "p", snapshot_provider=snapshot, paused=True, runner=Runner())
            self.assertFalse(paused.start_action("npc_fight")["accepted"])
            controller = ManualAssistController(directory / "c", snapshot_provider=lambda: None, paused=False,
                runner=Runner(), activation_ready=lambda: None)
            state = controller.actions_snapshot()["routines"]["npc_fight"]
            self.assertTrue(state["available"] and state["ready"], state)
            self.assertTrue(controller.start_action("npc_fight")["accepted"])
            self.assertEqual(controller.start_action("npc_fight")["reason"], "input_owner_conflict")
            deadline = time.monotonic() + 2
            while not calls and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(calls, ["npc_fight"])
            self.assertTrue(controller.stop_action("npc_fight")["accepted"])
            release.set()
            controller.close()

    def test_unsupported_runner_keeps_npc_fight_unavailable(self):
        with scratch_directory() as directory:
            controller = ManualAssistController(directory, snapshot_provider=snapshot, paused=False,
                runner=lambda *a, **kw: None)
            self.assertFalse(controller.actions_snapshot()["routines"]["npc_fight"]["available"])
            self.assertEqual(controller.start_action("npc_fight")["reason"], "npc_fight_not_available")


if __name__ == "__main__":
    unittest.main()
