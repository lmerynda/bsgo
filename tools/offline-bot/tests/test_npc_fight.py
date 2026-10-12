"""Experimental X/G fight loop with a fake dispatcher; no native window or game calls."""
from threading import Event, Lock, Thread
import random
import time
import unittest

from bsgo_offline.native_input import DeliveryRecord
from bsgo_offline.npc_fight import run_npc_fight
from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.state import FieldValue, Snapshot
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

    def test_cycles_press_x_g_hold_g_with_bounded_random_waits(self):
        waits = []
        class Recording(random.Random):
            def uniform(self, a, b):
                waits.append((a, b))
                return a
        with scratch_directory() as directory:
            started = time.monotonic()
            result, dispatcher = self.run_loop(directory, fire=(.1, .3), delay=(.2, .3), max_cycles=2, rng=Recording())
            self.assertEqual(result["state"], "recorded", result)
            self.assertEqual(dispatcher.keys, ["X", "G", "G", "X", "G", "G"])
            self.assertEqual(result["presses"], {"X": 2, "G": 4})
            self.assertFalse(result["guns_left_on"])
            self.assertEqual(waits, [(.1, .3), (.2, .3), (.1, .3)], "hold per burst, pause between cycles")
            self.assertGreaterEqual(time.monotonic() - started, .4)
            self.assertTrue(all(result["shutdown"].values()))

    def test_cancel_stops_before_the_next_key(self):
        with scratch_directory() as directory:
            stop = directory / "stop.request"
            def cancel_after_first(cycle):
                stop.write_text("stop", encoding="utf-8")
            result, dispatcher = self.run_loop(directory, delay=(.2, .2), on_cycle=cancel_after_first)
            self.assertEqual(dispatcher.keys, ["X", "G", "G"])
            self.assertTrue(result["cancel_requested"])
            self.assertEqual(result["reason"], "stopped")

    def test_stop_during_the_burst_still_turns_the_guns_off(self):
        with scratch_directory() as directory:
            stop = directory / "stop.request"
            class StopMidBurst(random.Random):
                def uniform(self, a, b):
                    if (a, b) == (.1, .3):
                        stop.write_text("stop", encoding="utf-8")
                    return a
            result, dispatcher = self.run_loop(directory, fire=(.1, .3), delay=(.2, .2), rng=StopMidBurst())
            self.assertEqual(dispatcher.keys, ["X", "G", "G"])
            self.assertFalse(result["guns_left_on"])
            self.assertTrue(result["cancel_requested"])

    def test_controller_pause_after_guns_on_still_delivers_the_off_press(self):
        from bsgo_offline.manual_routines import GuardedManualRoutines
        with scratch_directory() as directory:
            made, paused = [], [False]
            cancel = directory / "cancel"
            def factory(backend, journal):
                made.append(KeyDispatcher(backend))
                return made[0]
            def delivered(record, outcome):
                if record.key == "G" and record.kind == "key_down" and outcome == "injected":
                    paused[0] = True  # F8 lands while the guns are on
                    cancel.write_text("paused", encoding="utf-8")
            routines = GuardedManualRoutines(source_factory=None, recognizer_factory=None,
                dispatcher_factory=factory, profile="largehud-20261003", window_id="1:2",
                frame_size=(2560, 1440), scope="synthetic")
            result = routines("npc_fight", output=directory / "run", cancel_file=cancel,
                expected_target=None, assessment={}, on_progress=lambda path: None,
                on_delivery=delivered, may_deliver=lambda: not paused[0])
            self.assertEqual(made[0].keys, ["X", "G", "G"])
            self.assertFalse(result["guns_left_on"])
            self.assertTrue(result["cancel_requested"])

    def test_refused_key_ends_the_loop_with_its_cause(self):
        with scratch_directory() as directory:
            result, dispatcher = self.run_loop(directory, delay=(.2, .2), fail_on="G", max_cycles=5)
            self.assertEqual(result["state"], "blocked")
            self.assertIn("Target window is not foreground", result["reason"])
            self.assertEqual(dispatcher.keys, ["X"])

    def test_target_check_after_x_decides_whether_to_fire(self):
        # Each X gets the next scene from this list, captured after the press.
        scenes = [{"distance": 886, "asteroid_selected": False},
                  {"distance": None, "asteroid_selected": False},
                  {"distance": 3100, "asteroid_selected": False},
                  {"distance": 700, "asteroid_selected": True},
                  "stale",
                  {"distance": 1449, "asteroid_selected": False}]
        current = {"scene": None, "x_at": 0.}
        sequence = [0]
        def observe():
            scene = current["scene"]
            sequence[0] += 1
            at = current["x_at"] - 1 if scene == "stale" else time.monotonic()
            values = {} if scene == "stale" else scene
            fields = {name: FieldValue(value, at, value is not None, None if value is not None else "unreadable")
                      for name, value in values.items()}
            return Snapshot(sequence[0], 1, sequence[0], at, at, "largehud-20261003", (2560, 1440), fields)
        with scratch_directory() as directory:
            made = []
            def factory(backend, journal):
                dispatcher = KeyDispatcher(backend)
                def run():
                    # Advance the scene whenever X is delivered.
                    while not dispatcher.stop_event.is_set():
                        record = backend.get_action(timeout=.01)
                        if record is None:
                            continue
                        permitted = (record.kind == "key_up" and record.key in dispatcher.held) or backend.delivery_allowed(record)
                        outcome = "injected" if permitted else "skipped_dependency"
                        if permitted and record.kind == "key_down":
                            dispatcher.held.add(record.key)
                            dispatcher.keys.append(record.key)
                            if record.key == "X":
                                current["scene"] = scenes[dispatcher.keys.count("X") - 1]
                                current["x_at"] = time.monotonic()
                        elif permitted:
                            dispatcher.held.discard(record.key)
                        with dispatcher.lock:
                            dispatcher.rows.append(DeliveryRecord(record.sequence, time.monotonic(), record.kind,
                                outcome, 1, 1 if permitted else 0, 0))
                        backend.report_delivery(record, outcome)
                dispatcher.run = run
                made.append(dispatcher)
                return dispatcher
            stop = directory / "stop.request"
            def after_x_count():
                if made and made[0].keys.count("X") >= len(scenes):
                    stop.write_text("done", encoding="utf-8")
            result = run_npc_fight(directory / "run", dispatcher_factory=factory, configuration={},
                profile="largehud-20261003", frame_size=(2560, 1440), cancel_file=stop, scope="synthetic",
                delay=(.2, .2), observe=observe, confirm_timeout=.6,
                max_range=2500, on_cycle=lambda n: after_x_count())
            keys = made[0].keys
            self.assertEqual(keys[:3], ["X", "G", "G"], "886 m fires")
            self.assertEqual(keys.count("X"), len(scenes))
            self.assertEqual(result["fired"], 2, result)
            self.assertEqual(result["skipped"], {"no_target": 1, "out_of_range": 1,
                                                 "asteroid_selected": 1, "no_fresh_image": 1})
            self.assertEqual(result["presses"]["G"], 4)
            self.assertEqual(result["last_target"]["distance"], 1449)
            self.assertFalse(result["guns_left_on"])
            import json
            loop = json.loads((directory / "run" / "status.json").read_text(encoding="utf-8"))["execution"]["loop"]
            self.assertEqual((loop["fired"], loop["skipped"], loop["checked"]), (2, 4, True))
            self.assertIn(loop["step"], ("select", "check", "fire", "pause"))

    def test_delay_bounds_are_validated(self):
        with scratch_directory() as directory:
            for options in ({"delay": (0, 1)}, {"delay": (2, 1)}, {"delay": (1, 11)}, {"delay": "1-2"},
                            {"fire": (0, .3)}, {"fire": (.3, .1)}, {"fire": (1, 6)}):
                with self.assertRaises(ValueError):
                    run_npc_fight(directory / "bad", dispatcher_factory=None, configuration={},
                        profile="p", frame_size=(1, 1), cancel_file=directory / "x", **options)

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
