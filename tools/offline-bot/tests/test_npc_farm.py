from dataclasses import replace
import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bsgo_offline.npc_farm import NpcFarm, Observation, Target
from bsgo_offline.observer_ui import make_server
from test_trace import scratch_directory


A = Target('a', 'Raider', 1800, 600, True, True)
B = Target('b', 'Raider', 900, 600, True, True)
PLAYER = Target('player', 'Player', 100, 600, False, True)


class NpcFarmTests(unittest.TestCase):
    def setUp(self):
        self.engine = NpcFarm(uniform=lambda a,b: 5.)
        self.now, self.seq = 10., 0
        self.engine.start(self.now)

    def observe(self, target, **kwargs):
        self.now += .2
        self.seq += 1
        ack = self.engine.pending['id'] if self.engine.pending else None
        obs = Observation(self.seq, self.now, 'session', target, selection_ack=ack)
        return self.engine.tick(replace(obs, **kwargs), self.now)

    def pass_three(self):
        for target in (A, B, PLAYER, A, B):
            self.observe(target)

    def test_complete_cycle_chooses_nearest_npc_and_returns_before_waiting(self):
        self.pass_three()
        self.assertEqual(self.engine.phase, 'waiting')
        self.assertEqual(self.engine.intended, 'b')
        self.assertEqual(self.engine.selected, 'b')
        self.assertEqual(len(self.engine.targets), 3)
        self.assertAlmostEqual(self.engine.next_pass_at-self.now, 5)
        self.assertEqual(self.engine.steps, 4)
        self.assertIsNone(self.observe(A))  # no X between passes

    def test_retains_intended_even_when_another_enemy_becomes_closer(self):
        self.pass_three()
        self.now = self.engine.next_pass_at
        for target in (replace(B, distance=3000), PLAYER, replace(A, distance=50), replace(B, distance=3000)):
            self.observe(target)
        self.assertEqual(self.engine.intended, 'b')
        self.assertEqual(self.engine.phase, 'waiting')

    def test_unknown_identity_is_not_cycle_completion(self):
        self.observe(A)
        self.observe(replace(B, identity_confirmed=False))
        self.assertEqual(self.engine.phase, 'blocked')
        self.assertIsNone(self.engine.intended)

    def test_stale_unacknowledged_or_repeated_frames_do_not_advance(self):
        intent = self.observe(A)
        self.observe(B, selection_ack=None)
        self.observe(B, at=self.now-5)
        self.observe(B, sequence=1)
        self.assertEqual(self.engine.steps, 1)
        self.assertEqual(self.engine.pending['id'], intent['id'])
        self.assertEqual(list(self.engine.targets), ['a'])

    def test_unreadable_and_unconfirmed_selection_times_out_without_more_input(self):
        self.observe(A)
        self.now += 4
        self.assertIsNone(self.observe(B, readable=False))
        self.assertEqual(self.engine.phase, 'blocked')
        self.assertEqual(self.engine.steps, 1)

    def test_single_target_and_empty_cycle(self):
        self.observe(A)
        self.observe(A)
        self.assertEqual(self.engine.phase, 'waiting')
        self.assertEqual(self.engine.intended, 'a')
        self.engine = NpcFarm()
        self.engine.start(self.now, repeat=False)
        self.observe(None)
        self.observe(None)
        self.assertEqual(self.engine.phase, 'complete')
        self.assertIsNone(self.engine.intended)

    def test_target_lost_during_return_never_claims_success(self):
        for target in (A, B, PLAYER, A, PLAYER, A):
            self.observe(target)
        self.assertEqual(self.engine.phase, 'blocked')
        self.assertNotEqual(self.engine.selected, self.engine.intended)

    def test_step_budget_session_change_and_pause_clear_pending(self):
        self.engine.max_steps = 1
        self.observe(A)
        self.observe(B)
        self.assertEqual(self.engine.phase, 'blocked')
        self.engine.start(self.now)
        self.observe(A, session='other')
        self.assertEqual(self.engine.phase, 'blocked')
        self.engine.pause()
        self.assertIsNone(self.engine.pending)
        self.assertIsNone(self.observe(B))

    def test_unknown_range_cannot_claim_closest(self):
        for target in (A, replace(B, distance=None), A):
            self.observe(target)
        self.assertEqual(self.engine.phase, 'blocked')

    def test_new_closest_is_chosen_if_prior_target_is_missing_next_pass(self):
        self.pass_three()
        self.now = self.engine.next_pass_at
        for target in (PLAYER, A, PLAYER, A):
            self.observe(target)
        self.assertEqual(self.engine.phase, 'waiting')
        self.assertEqual(self.engine.intended, 'a')

    def test_dead_target_on_return_and_non_anchor_repetition_are_blocked(self):
        for target in (A, B, PLAYER, A, replace(B, hp=0)):
            self.observe(target)
        self.assertEqual(self.engine.phase, 'blocked')
        self.engine = NpcFarm()
        self.engine.start(self.now)
        for target in (A, B, PLAYER, B):
            self.observe(target)
        self.assertEqual(self.engine.phase, 'blocked')

    def test_pass_deadline_includes_unreadable_initial_observation(self):
        self.now += 46
        self.assertIsNone(self.engine.tick(None, self.now))
        self.assertEqual(self.engine.phase, 'blocked')
        self.assertEqual(self.engine.steps, 0)

    def test_no_confirmation_at_request_timestamp(self):
        self.observe(A)
        self.observe(B, at=self.engine.pending['issued_at'])
        self.assertEqual(self.engine.steps, 1)

    def test_invalid_intervals_rejected(self):
        for interval in ((8,2),(0,8),(2,float('nan')),(2,61)):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                NpcFarm(interval=interval)


class NpcFarmApiTests(unittest.TestCase):
    def test_offline_commands_are_isolated_and_same_origin_guarded(self):
        class NoActions:
            def start_action(self, *args):
                raise AssertionError('demo must never reach live actions')
        with scratch_directory() as directory:
            server = make_server(directory, 0, action_adapter=NoActions())
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def post(data, origin=base):
                request = Request(base+'/api/npc-farm', data=json.dumps(data).encode(),
                    headers={'Origin':origin,'Content-Type':'application/json'})
                try:
                    with urlopen(request, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base+'/api/npc-farm') as response:
                    self.assertEqual(json.load(response)['phase'], 'idle')
                self.assertEqual(post({'action':'start'}, 'https://elsewhere.test')[0], 403)
                self.assertEqual(post({'action':'start','live':True})[0], 409)
                self.assertEqual(post({'action':'start','interval':[8,2]})[0], 409)
                code, result = post({'action':'start','repeat':False})
                self.assertEqual(code, 200)
                self.assertFalse(result['state']['input_authorized'])
                self.assertEqual(result['state']['environment'], 'simulation')
                self.assertEqual(post({'action':'pause'})[1]['state']['phase'], 'paused')
                self.assertEqual(post({'action':'reset'})[1]['state']['phase'], 'idle')
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)
            self.assertFalse(server.npc_demo.thread.is_alive())
