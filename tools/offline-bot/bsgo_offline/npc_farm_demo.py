"""Offline adapter for the real NPC decision loop. Never imports native input."""
from dataclasses import replace
from threading import Event, Lock, Thread
import time

from .npc_farm import NpcFarm, Observation, Target


SCENARIOS = ('normal', 'empty', 'ambiguous', 'unreadable', 'target_lost')


class NpcFarmDemo:
    def __init__(self):
        self.lock, self.stop = Lock(), Event()
        self.thread = None
        self.engine = NpcFarm()
        self.scenario = 'normal'
        self.rows = []
        self.index = self.sequence = 0
        self.ack = self.delivery = None

    def _snapshot(self):
        return {**self.engine.snapshot(time.monotonic()), 'environment': 'simulation',
                'live_available': False, 'input_authorized': False,
                'live_reason': 'Live NPC identity and enemy-cycle input are not verified',
                'scenario': self.scenario, 'scenarios': SCENARIOS,
                'simulated_selected': self.rows[self.index].name if self.rows else None}

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def command(self, data):
        with self.lock:
            if not isinstance(data, dict) or data.get('action') not in ('start', 'pause', 'reset'):
                return {'accepted': False, 'reason': 'invalid_npc_command', 'state': self._snapshot()}
            action = data['action']
            allowed = {'action', 'scenario', 'interval', 'repeat'} if action == 'start' else {'action'}
            if set(data)-allowed:
                return {'accepted': False, 'reason': 'unexpected_npc_command_fields', 'state': self._snapshot()}
            if action == 'start':
                if self.engine.active:
                    return {'accepted': False, 'reason': 'pause_before_restarting', 'state': self._snapshot()}
                scenario, repeat = data.get('scenario', 'normal'), data.get('repeat', True)
                if scenario not in SCENARIOS or type(repeat) is not bool:
                    return {'accepted': False, 'reason': 'invalid_simulation_settings', 'state': self._snapshot()}
                try:
                    engine = NpcFarm(interval=data.get('interval', (2, 8)))
                except (ValueError, TypeError):
                    return {'accepted': False, 'reason': 'invalid_interval', 'state': self._snapshot()}
                self.engine, self.scenario = engine, scenario
                self.rows = [] if scenario == 'empty' else [
                    Target('demo-a', 'Raider A', 1800, 640, True, True),
                    Target('demo-b', 'Raider B', 950, 640, True, True),
                    Target('demo-player', 'Enemy player', 300, 1200, False, True),
                    Target('demo-c', 'Escort', 2300, 880, True, True)]
                if scenario == 'ambiguous':
                    self.rows[1] = replace(self.rows[1], name='Raider A', identity_confirmed=False)
                self.index = self.sequence = 0
                self.ack = self.delivery = None
                self.engine.start(time.monotonic(), repeat=repeat)
                if self.thread is None:
                    self.thread = Thread(target=self._run, name='npc-offline-rehearsal', daemon=True)
                    self.thread.start()
            elif action == 'pause':
                self.engine.pause()
                self.delivery = None
            else:
                self.engine = NpcFarm()
                self.rows, self.delivery, self.ack = [], None, None
            return {'accepted': True, 'reason': 'simulation_'+action, 'state': self._snapshot()}

    def _run(self):
        while not self.stop.wait(.1):
            with self.lock:
                if not self.engine.active:
                    continue
                now = time.monotonic()
                if self.delivery and now >= self.delivery['at']:
                    if self.scenario == 'target_lost' and self.engine.phase == 'returning':
                        selected = self.rows[self.index].identity if self.rows else None
                        self.rows = [t for t in self.rows if t.identity != self.engine.intended]
                        self.index = next((i for i, t in enumerate(self.rows) if t.identity == selected), 0)
                    if self.rows:
                        self.index = (self.index+1) % len(self.rows)
                    self.ack, self.delivery = self.delivery['id'], None
                self.sequence += 1
                obs = Observation(self.sequence, now, 'offline-demo',
                    self.rows[self.index] if self.rows else None,
                    readable=not (self.scenario == 'unreadable' and self.ack is not None),
                    selection_ack=self.ack)
                intent = self.engine.tick(obs, now)
                if intent:
                    self.delivery = {'id': intent['id'], 'at': now+.45}

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(2)
