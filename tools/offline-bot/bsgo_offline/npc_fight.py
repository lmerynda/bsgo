"""Experimental NPC fight loop: X (next enemy), G on, brief random hold, G off, pause.

Toggling the guns per target fires them fresh at each newly selected enemy.

No image recognition gates this loop. It is a supervised experiment for a safe
spot: the game decides what X selects and what G fires at. The native
dispatcher still requires the exact game window in the foreground for every
key, and stop, F8 pause, focus loss or a refused key end the loop before the
next press. A stop between the two G presses still sends the second G, so the
guns are never left on by a stop.
"""
import math
from pathlib import Path
import random
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend


KEYS = ("X", "G")


def _window(value, name, low, high):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or not low <= value[0] <= value[1] <= high):
        raise ValueError(name)
    return float(value[0]), float(value[1])


def run_npc_fight(output, *, dispatcher_factory, configuration, profile, frame_size,
                  cancel_file, scope="native", fire=(.1, .3), delay=(1., 2.), max_cycles=None,
                  rng=None, delivery_timeout=1.5, on_cycle=None, guns_state=None):
    """Repeat X, G, hold `fire` s, G, pause `delay` s until canceled."""
    fire_lo, fire_hi = _window(fire, "fire", .05, 5)
    lo, hi = _window(delay, "delay", .2, 10)
    if scope not in ("native", "synthetic"):
        raise ValueError("scope")
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
        raise ValueError("max_cycles")
    cancel_path = Path(cancel_file).resolve()
    rng = rng or random.Random()
    diagnostic = DiagnosticRun(output, mode=scope + "_npc_fight_loop",
        configuration={**configuration, "keys": list(KEYS), "fire_seconds": [fire_lo, fire_hi],
                       "delay_seconds": [lo, hi],
                       "max_cycles": max_cycles, "cancel_file": str(cancel_path),
                       "recognition_gated": False},
        frame_capacity=1, max_frames=1)
    backend = RecordingInputBackend(session_id="npc-" + diagnostic.journal.run_id,
        layout_id=profile, width=frame_size[0], height=frame_size[1], journal=diagnostic.journal)
    dispatcher = None
    shutdown, error = {}, None
    result = {"state": "blocked", "reason": "not_started", "cycles": 0,
              "presses": {key: 0 for key in KEYS}, "guns_left_on": False, "cancel_requested": False,
              "unverified": ["target_kind", "target_hostility", "hits", "kills", "rewards"]}

    def canceled():
        try:
            return cancel_path.exists()
        except OSError:
            return True

    guns_on = False
    guns_state = guns_state if guns_state is not None else {}
    guns_state["on"] = False
    # After G turned the guns on, the off G is allowed even once a stop arrives.
    backend.set_delivery_contract(lambda record: record.key in KEYS
        and (not canceled() or record.key == "G" and guns_on))

    def wait(seconds):
        """Sleep in short steps; True when the loop must end."""
        until = time.monotonic() + seconds
        while (remaining := until - time.monotonic()) > 0:
            if canceled() or dispatcher.error:
                return True
            time.sleep(min(.05, remaining))
        return canceled() or bool(dispatcher.error)

    def press(key):
        down, up = backend.press_key(key, reason="npc_fight_" + key.lower())
        deadline = time.monotonic() + delivery_timeout
        while time.monotonic() < deadline:
            done = {row.request_sequence: row for row in dispatcher.deliveries()}
            if dispatcher.error:
                raise RuntimeError(dispatcher.error)
            if down.sequence in done and up.sequence in done:
                if done[down.sequence].outcome != "injected":
                    raise RuntimeError(f"{key}_not_delivered: {done[down.sequence].outcome}")
                result["presses"][key] += 1
                return
            time.sleep(.01)
        raise RuntimeError(f"{key}_delivery_timeout")

    try:
        diagnostic.attach(worker=None, store=None, coordinator=None, backend=backend)
        dispatcher = dispatcher_factory(backend, diagnostic.journal)
        diagnostic.attach_dispatcher(dispatcher)
        dispatcher.start()
        diagnostic.update_execution(phase="fighting", processing="X, G on, brief hold, G off, pause",
            waiting_for=None, expected_next_step="repeat until stopped")
        while not canceled():
            press("X")
            if canceled():
                break
            guns_on = guns_state["on"] = True
            press("G")
            time.sleep(rng.uniform(fire_lo, fire_hi))  # short; a stop waits for the off press
            press("G")
            guns_on = guns_state["on"] = False
            result["cycles"] += 1
            diagnostic.update_execution(progress=f"{result['cycles']} cycles")
            if on_cycle:
                on_cycle(result["cycles"])
            if max_cycles is not None and result["cycles"] >= max_cycles:
                break
            if wait(rng.uniform(lo, hi)):
                break
        if dispatcher.error:
            raise RuntimeError(dispatcher.error)
        result.update(state="recorded", reason="stopped" if canceled() else "cycle_limit_reached")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked", reason=error)
    finally:
        # Recorded so the panel can say the guns may still be firing.
        result["guns_left_on"] = result["presses"]["G"] % 2 == 1
        if dispatcher:
            try:
                shutdown["dispatcher"] = dispatcher.stop(2)
            except Exception as exc:
                shutdown["dispatcher"] = False
                error = error or f"dispatcher_stop_failure: {type(exc).__name__}: {exc}"
        else:
            shutdown["dispatcher"] = True
        try:
            backend.cancel(reason="npc_fight_finished")
            backend.flush_journal()
        except Exception as exc:
            error = error or f"input_cancel_failure: {type(exc).__name__}: {exc}"
        shutdown["input_idle"] = not backend.pending_requests and not backend.held_keys
        shutdown["recognition"] = shutdown["source"] = True  # this loop owns neither
    result["cancel_requested"] = canceled()
    report = diagnostic.close(shutdown=shutdown, result=result, error=error)
    report["result"] = plain(result)
    _write_json(diagnostic.directory / "report.json", report)
    return {**result, "run_id": diagnostic.journal.run_id, "shutdown": shutdown,
            "complete_diagnostics": report["complete_diagnostics"]}
