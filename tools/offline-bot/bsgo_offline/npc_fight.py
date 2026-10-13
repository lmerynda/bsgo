"""Experimental NPC fight loop: X (next enemy), G on, brief random hold, G off.

Toggling the guns per burst fires them fresh at each target. With a target-card
reader in the feed, the loop then stays on that target, bursting every 1-2 s
while its HP keeps dropping, and presses X only once it dies, disappears,
switches, leaves range, stops taking damage for `stall_seconds`, or its HP stays
unreadable. Without a card reader it bursts once per X.
With an observation feed, each X is followed by a check of an image captured
after it: G fires only when a target card shows a readable distance within
`max_range` and the target is not an asteroid. NPCs and players are not told
apart; the game decides what X selects.

No image recognition gates this loop. It is a supervised experiment for a safe
spot: the game decides what X selects and what G fires at. The native
dispatcher still requires the exact game window in the foreground for every
key, and stop, F8 pause, focus loss or a refused key end the loop before the
next press. A stop between the two G presses still sends the second G, so the
guns are never left on by a stop.
"""
from collections.abc import Mapping
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
                  rng=None, delivery_timeout=1.5, on_cycle=None, guns_state=None,
                  observe=None, max_range=2500, confirm_timeout=1.5, settle=.15, stall_seconds=8.):
    """Repeat X, G, hold `fire` s, G, pause `delay` s until canceled."""
    fire_lo, fire_hi = _window(fire, "fire", .05, 5)
    lo, hi = _window(delay, "delay", .2, 10)
    if type(max_range) not in (int, float) or not 0 < max_range <= 20000:
        raise ValueError("max_range")
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
                       "recognition_gated": observe is not None, "max_range": max_range},
        frame_capacity=1, max_frames=1)
    backend = RecordingInputBackend(session_id="npc-" + diagnostic.journal.run_id,
        layout_id=profile, width=frame_size[0], height=frame_size[1], journal=diagnostic.journal)
    dispatcher = None
    shutdown, error = {}, None
    result = {"state": "blocked", "reason": "not_started", "cycles": 0,
              "presses": {key: 0 for key in KEYS}, "guns_left_on": False, "cancel_requested": False,
              "fired": 0, "skipped": {}, "last_target": None, "kills": 0, "outcomes": {},
              "engaged": None, "last_outcome": None,
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
                return done[up.sequence].at
            time.sleep(.01)
        raise RuntimeError(f"{key}_delivery_timeout")

    def fresh(after):
        """The first healthy observation captured after `after`; never an older one."""
        deadline = time.monotonic() + confirm_timeout
        while time.monotonic() < deadline:
            if canceled():
                return "stopped"
            try:
                snapshot = observe()
            except Exception:
                snapshot = None
            if snapshot is not None and snapshot.healthy and snapshot.acquired_at > after + settle:
                return snapshot
            time.sleep(.05)
        return None

    def field(snapshot, name):
        value = snapshot.fields.get(name)
        return value.value if value and value.valid and value.observed_at == snapshot.acquired_at else None

    def card(snapshot):
        """The selected-target card reading, or None where this feed has no card reader."""
        value = field(snapshot, "selected_target")
        return dict(value) if isinstance(value, Mapping) else None  # snapshots hold read-only proxies

    def confirm(after):
        """Judge the first healthy image captured after X."""
        snapshot = fresh(after)
        if snapshot == "stopped":
            return {"fire": False, "reason": "stopped"}
        if snapshot is None:
            return {"fire": False, "reason": "no_fresh_image"}
        target = card(snapshot) or {}
        distance, asteroid = field(snapshot, "distance"), field(snapshot, "asteroid_selected")
        distance = distance if distance is not None else target.get("distance")
        row = {"sequence": snapshot.sequence, "distance": distance, "hp": target.get("current_hp"),
               "max_hp": target.get("max_hp"), "card": "selected_target" in snapshot.fields}
        if asteroid is True or target.get("kind") == "asteroid":
            return {**row, "fire": False, "reason": "asteroid_selected"}
        if type(distance) not in (int, float) or not distance > 0:
            return {**row, "fire": False, "reason": "no_target"}
        if distance > max_range:
            return {**row, "fire": False, "reason": "out_of_range"}
        return {**row, "fire": True, "reason": "target_in_range"}

    def judge(snapshot, engaged):
        """Keep firing at one target while its HP drops; otherwise say why to move on."""
        now = time.monotonic()
        stalled = now - engaged["drop_at"] > stall_seconds
        if snapshot is None:
            engaged["unreadable"] += 1
            return "unreadable" if engaged["unreadable"] >= 4 else "no_damage" if stalled else "keep"
        target = card(snapshot) or {}
        distance = target.get("distance") if target.get("distance") is not None else field(snapshot, "distance")
        if target.get("present") is not True and distance is None:
            return "gone"
        if target.get("kind") == "asteroid":
            return "switched"
        if distance is not None and distance > max_range:
            return "out_of_range"
        hp, maximum = target.get("current_hp"), target.get("max_hp")
        if hp is None or maximum is None:
            engaged["unreadable"] += 1
            # Never seen this target's HP: fall back to one burst per X quickly.
            limit = 2 if engaged["max"] is None else 4
            return "unreadable" if engaged["unreadable"] >= limit else "no_damage" if stalled else "keep"
        engaged["unreadable"] = 0
        engaged["hp"] = hp
        if engaged["max"] is None:
            engaged.update(max=maximum, best=hp, drop_at=now)
        elif maximum != engaged["max"]:
            return "switched"
        if hp == 0:
            return "killed"
        if hp < engaged["best"]:
            engaged.update(best=hp, drop_at=now)
        return "no_damage" if now - engaged["drop_at"] > stall_seconds else "keep"

    def publish(step, verdict=None):
        """One status update per step, so no field is wiped by a later one."""
        if verdict is not None:
            result["last_target"] = verdict
        last = result["last_target"]
        where = f"{int(last['distance']):,} m · " if last and last.get("distance") else ""
        what = "" if not last else "fired" if last["fire"] else {"no_target": "no target",
            "out_of_range": "out of range", "asteroid_selected": "asteroid, skipped",
            "no_fresh_image": "no fresh image"}.get(last["reason"], last["reason"])
        skipped = sum(result["skipped"].values())
        diagnostic.update_execution(phase="fighting", processing=f"Last: {where}{what}" if last else None,
            loop={"step": step, "cycles": result["cycles"], "fired": result["fired"], "skipped": skipped,
                  "checked": observe is not None, "last": last,
                  "fire_ms": [round(fire_lo * 1000), round(fire_hi * 1000)], "pause_s": [lo, hi],
                  "max_range": max_range, "kills": result["kills"], "bursts": result["cycles"],
                  "engaged": result["engaged"], "last_outcome": result["last_outcome"],
                  "stall_s": stall_seconds})

    try:
        diagnostic.attach(worker=None, store=None, coordinator=None, backend=backend)
        dispatcher = dispatcher_factory(backend, diagnostic.journal)
        diagnostic.attach_dispatcher(dispatcher)
        dispatcher.start()
        def burst():
            """G on, brief hold, G off. True when the cycle limit is reached."""
            nonlocal guns_on
            guns_on = guns_state["on"] = True
            press("G")
            time.sleep(rng.uniform(fire_lo, fire_hi))  # short; a stop waits for the off press
            ended = press("G")
            guns_on = guns_state["on"] = False
            result["cycles"] += 1
            if on_cycle:
                on_cycle(result["cycles"])
            return ended, max_cycles is not None and result["cycles"] >= max_cycles

        done = False
        while not canceled() and not done:
            publish("select")
            x_at = press("X")
            if canceled():
                break
            verdict = None
            if observe is not None:
                publish("check")
                verdict = confirm(x_at)
                if verdict["reason"] == "stopped":
                    break
                if not verdict["fire"]:
                    result["skipped"][verdict["reason"]] = result["skipped"].get(verdict["reason"], 0) + 1
                    publish("pause", verdict)
                    if wait(rng.uniform(lo, hi)):
                        break
                    continue
                result["fired"] += 1
                publish("fire", verdict)
            else:
                publish("fire")
            ended, done = burst()
            if not done and verdict is not None and verdict["card"]:
                # Stay on this target while its HP drops; X only once it is done.
                engaged = {"max": verdict["max_hp"], "best": verdict["hp"], "hp": verdict["hp"],
                           "drop_at": time.monotonic(), "unreadable": 0}
                if engaged["best"] is None:
                    engaged["max"] = None
                while True:
                    result["engaged"] = dict(engaged)
                    publish("watch")
                    if wait(rng.uniform(lo, hi)):
                        done = True
                        break
                    snapshot = fresh(ended)
                    if snapshot == "stopped":
                        done = True
                        break
                    outcome = judge(snapshot, engaged)
                    result["engaged"] = dict(engaged)
                    if outcome != "keep":
                        result["outcomes"][outcome] = result["outcomes"].get(outcome, 0) + 1
                        if outcome == "killed" or outcome == "gone" and engaged["max"] is not None \
                                and engaged["best"] is not None and engaged["best"] < engaged["max"]:
                            result["kills"] += 1
                        result["last_outcome"] = outcome
                        break
                    publish("fire")
                    ended, done = burst()
                    if done:
                        break
                result["engaged"] = None
                continue
            if done:
                break
            publish("pause")
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
