"""Bounded enemy discovery and return-to-target decisions, with no input backend.

An adapter must provide confirmed, session-local target identities and explicit
selection acknowledgments. Names, HP and distance are not identity keys. The
current screen reader cannot satisfy this contract; live execution is unavailable.
"""
from dataclasses import asdict, dataclass
import math
import random


@dataclass(frozen=True)
class Target:
    identity: str
    name: str
    distance: float | None
    hp: float
    npc: bool
    hostile: bool
    identity_confirmed: bool = True


@dataclass(frozen=True)
class Observation:
    sequence: int
    at: float
    session: str
    target: Target | None
    readable: bool = True
    selection_ack: int | None = None


class NpcFarm:
    def __init__(self, *, interval=(2., 8.), max_steps=64, pass_timeout=45.,
                 selection_timeout=3., uniform=random.uniform):
        if (len(interval) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in interval)
                or not 1 <= interval[0] <= interval[1] <= 60):
            raise ValueError('interval_must_be_ordered_between_1_and_60_seconds')
        if type(max_steps) is not int or not 1 <= max_steps <= 256:
            raise ValueError('invalid_step_budget')
        if not 0 < selection_timeout <= pass_timeout <= 120:
            raise ValueError('invalid_time_budget')
        self.interval, self.max_steps = tuple(interval), max_steps
        self.pass_timeout, self.selection_timeout, self.uniform = pass_timeout, selection_timeout, uniform
        self.phase, self.reason = 'idle', 'Ready for an offline rehearsal'
        self.active = False
        self.intended = self.selected = self.pending = self.next_pass_at = None
        self.targets = {}
        self.events = []
        self.passes = self.steps = self.request_id = 0
        self.session = None

    def log(self, message):
        self.events = (self.events + [message])[-16:]

    def start(self, now, *, repeat=True):
        if self.active:
            return False
        self.active, self.repeat = True, repeat
        self._begin(now)
        return True

    def _begin(self, now):
        self.phase, self.reason = 'discovering', 'Reading the first target'
        self.started_at, self.steps = now, 0
        self.anchor = self.pending = self.next_pass_at = None
        self.targets, self.return_seen = {}, set()
        self.last_sequence = -1
        self.passes += 1
        self.log(f'Pass {self.passes}: discover the enemy cycle')

    def pause(self, reason='Paused by you'):
        self.active, self.phase, self.reason = False, 'paused', reason
        self.pending = self.next_pass_at = None
        self.log(reason)

    def fail(self, reason):
        self.pause(reason)
        self.phase = 'blocked'

    def _finish(self, now, reason):
        self.reason = reason
        self.pending = None
        self.log(reason)
        if self.repeat:
            self.phase = 'waiting'
            self.next_pass_at = now + self.uniform(*self.interval)
        else:
            self.phase, self.active = 'complete', False

    def _next(self, obs, now):
        if self.steps >= self.max_steps:
            self.fail('Selection budget exhausted; cycle or return could not be verified')
            return None
        self.steps += 1
        self.request_id += 1
        self.pending = {'id': self.request_id, 'key': 'x', 'kind': 'select_next_enemy',
                        'issued_at': now, 'after_sequence': obs.sequence}
        self.reason = 'Waiting for the next confirmed selection'
        self.log(f'X {self.steps}: {self.phase}')
        return dict(self.pending)

    def _choose(self, obs, now):
        eligible = [t for t in self.targets.values() if t.npc is True and t.hostile is True and t.hp > 0]
        previous = next((t for t in eligible if t.identity == self.intended), None)
        if previous:
            chosen = previous
        elif any(type(t.distance) not in (int, float) or not math.isfinite(t.distance)
                 or t.distance < 0 for t in eligible):
            self.fail('Cannot choose closest: an eligible NPC range is unreadable')
            return None
        else:
            chosen = min(eligible, key=lambda t: (t.distance, t.identity), default=None)
        if chosen is None:
            self.intended = None
            self._finish(now, 'Cycle complete; no eligible NPC found')
            return None
        retained = chosen.identity == self.intended
        self.intended = chosen.identity
        self.log(('Retain ' if retained else 'Choose closest: ') + chosen.name)
        self.phase = 'returning'
        self.reason = 'Cycling back to the intended target'
        if self.selected == self.intended:
            self._finish(now, 'Intended target selected and verified')
            return None
        self.return_seen.add(self.selected)
        return self._next(obs, now)

    def tick(self, obs, now):
        """Consume fresh evidence; return at most one selection intent per call."""
        if not self.active:
            return None
        if self.phase == 'waiting':
            if now < self.next_pass_at:
                return None
            self._begin(now)
        if now-self.started_at >= self.pass_timeout:
            self.fail('Detection pass timed out')
            return None
        if self.pending and now-self.pending['issued_at'] >= self.selection_timeout:
            self.fail('Selection was not confirmed; stopped without retrying X')
            return None
        if obs is None or not obs.readable or not 0 <= now-obs.at < 2:
            self.reason = 'Waiting for a fresh, readable target observation'
            return None
        if self.session is not None and self.session != obs.session:
            self.fail('Game session changed; target continuity lost')
            return None
        self.session = obs.session
        if obs.sequence <= self.last_sequence:
            return None
        if self.pending and (obs.selection_ack != self.pending['id']
                or obs.sequence <= self.pending['after_sequence'] or obs.at <= self.pending['issued_at']):
            return None
        acknowledged = self.pending is not None
        self.pending = None
        self.last_sequence = obs.sequence
        target = obs.target
        if target is not None and (not target.identity_confirmed or not target.identity
                or type(target.hp) not in (int, float) or not math.isfinite(target.hp) or target.hp < 0
                or type(target.npc) is not bool or type(target.hostile) is not bool):
            self.fail('Target identity is ambiguous; a complete cycle cannot be established')
            return None
        self.selected = target.identity if target else None
        if target is None:
            if self.anchor is not None:
                self.fail('Selection disappeared during the pass')
            elif acknowledged:
                self.intended = None
                self._finish(now, 'No enemies selected after X')
            else:
                return self._next(obs, now)
            return None
        if self.phase == 'returning':
            if self.selected == self.intended:
                if not target.npc or not target.hostile or target.hp <= 0:
                    self.fail('Intended target is no longer an eligible NPC')
                    return None
                self.targets[self.selected] = target
                self._finish(now, 'Intended target selected and verified')
                return None
            if self.selected in self.return_seen:
                self.fail('Intended target disappeared or the cycle changed during return')
                return None
            self.return_seen.add(self.selected)
            return self._next(obs, now)
        if self.anchor is None:
            self.anchor = target.identity
        elif target.identity == self.anchor and acknowledged:
            self.targets[target.identity] = target
            self.log(f'Cycle closed: {len(self.targets)} distinct targets')
            return self._choose(obs, now)
        elif target.identity in self.targets:
            self.fail('Cycle repeated before returning to its first target')
            return None
        self.targets[target.identity] = target
        return self._next(obs, now)

    def snapshot(self, now):
        return {'phase': self.phase, 'reason': self.reason, 'active': self.active,
                'pass_number': self.passes, 'steps': self.steps, 'max_steps': self.max_steps,
                'intended': self.intended, 'selected': self.selected,
                'next_pass_seconds': max(0., self.next_pass_at-now) if self.next_pass_at is not None else None,
                'interval': self.interval, 'targets': [asdict(t) for t in self.targets.values()],
                'events': list(self.events), 'pending': dict(self.pending) if self.pending else None}
