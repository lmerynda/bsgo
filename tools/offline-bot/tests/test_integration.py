"""Exercise the real worker, coordinator and recording receiver together."""
import math, threading, time, unittest
from dataclasses import replace
from bsgo_offline.coordinator import Coordinator, DelayedPlanner, Proposal, validate_proposal
from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.policy import ApproachPolicy
from bsgo_offline.recognition import RecognitionWorker
from bsgo_offline.simulation import SimulationClock, SyntheticSource, VirtualReceiver, recognize_synthetic
from bsgo_offline.state import Snapshot, SnapshotStore, FieldValue

class Harness:
    def __init__(self, *, ignore_brake=False, stall_after=None, fault_after=None, planner_delay=.2, journal=None,
                 history_capacity=64000):
        self.clock=SimulationClock(20)
        self.backend=RecordingInputBackend(session_id='test-1',layout_id='synthetic-original',clock=self.clock)
        self.receiver=VirtualReceiver(self.backend,self.clock,ignore_brake=ignore_brake,journal=journal,
                                      history_capacity=history_capacity)
        self.store=SnapshotStore()
        self.source=SyntheticSource(self.receiver,stall_after=stall_after,fault_after=fault_after)
        self.worker=RecognitionWorker(self.source,recognize_synthetic,self.store,clock=self.clock)
        self.planner=DelayedPlanner(planner_delay,ttl=20)
        self.coordinator=Coordinator(self.store,self.backend,clock=self.clock,planner=self.planner,journal=journal,
                                     history_capacity=history_capacity)

    def __enter__(self):
        self.receiver.start()
        self.backend.click_mouse(1200,600,reason='test_selection')
        until=time.monotonic()+1
        while not self.receiver.sample()[3]['asteroid_selected']:
            if time.monotonic()>until: raise AssertionError('Receiver did not consume click')
            time.sleep(.005)
        self.worker.start()
        return self

    def __exit__(self,*args):
        self.worker.stop()
        assert self.worker.join(2)
        self.backend.cancel(reason='test_shutdown')
        assert self.receiver.stop(2)
        assert self.planner.close()

def snapshot(sequence=1, acquired=1.0, epoch=1, portrait=None, identity='same-signature'):
    fields={'distance':FieldValue(1400,acquired),'speed':FieldValue(0,acquired),
            'asteroid_selected':FieldValue(True,acquired),'target_identity':FieldValue(identity,acquired),
            'target_portrait':FieldValue(tuple([2]*144) if portrait is None else portrait,acquired)}
    return Snapshot(sequence,epoch,sequence,acquired,acquired,'original',(2560,1440),fields)

class IntegrationTests(unittest.TestCase):
    def test_recent_history_defaults_are_64000_and_small_configuration_is_supported(self):
        backend=RecordingInputBackend()
        coordinator=Coordinator(SnapshotStore(),backend)
        receiver=VirtualReceiver(backend,time.monotonic)
        self.assertEqual(coordinator.events.maxlen,64000)
        self.assertEqual(receiver.effects.maxlen,64000)
        small=Coordinator(SnapshotStore(),backend,history_capacity=3)
        for index in range(5):
            self.assertTrue(small._record('example',index=index))
        self.assertEqual([event['index'] for event in small.events],[2,3,4])
        self.assertEqual(small.telemetry_stats(),{'events_total':5,'events_retained':3,
            'events_evicted':2,'journal_dropped':0})
        self.assertEqual(VirtualReceiver(backend,time.monotonic,history_capacity=7).effects.maxlen,7)
        for invalid in (0,-1,True,1.5,None):
            with self.subTest(history_capacity=invalid):
                with self.assertRaises(ValueError):
                    Coordinator(SnapshotStore(),backend,history_capacity=invalid)
                with self.assertRaises(ValueError):
                    VirtualReceiver(backend,time.monotonic,history_capacity=invalid)

    def test_nominal_click_acceleration_brake_and_observed_stop_while_planner_delayed(self):
        with Harness() as h:
            result=h.coordinator.run(4)
            self.assertTrue(result.completed,result.reason)
            self.assertTrue(result.stop_observed)
            self.assertGreater(h.worker.processed,20)
            clicks=[r for r in h.backend.records() if r.kind=='mouse_click']
            self.assertEqual((clicks[0].x,clicks[0].y),(1200,600))
            self.assertTrue(any(r.key=='PageUp' and r.kind=='key_down' for r in h.backend.records()))
            self.assertTrue(any(r.key=='PageDown' and r.kind=='key_down' for r in h.backend.records()))
            planner=next(e for e in result.events if e['kind']=='planner_completed')
            self.assertTrue(planner['accepted'])
            self.assertGreater(planner['current_sequence'],planner['source_sequence']+5)
            self.assertEqual(h.store.latest().fields['speed'].value,0)

    def test_ignored_brake_does_not_become_stop_acknowledgement(self):
        with Harness(ignore_brake=True) as h:
            result=h.coordinator.run(4)
            self.assertFalse(result.completed)
            self.assertFalse(result.stop_observed)
            self.assertIn('Brake not acknowledged',result.reason)
            self.assertTrue(result.stop_requested)
            self.assertGreater(h.receiver.sample()[3]['speed'],0)

    def test_stalled_recognition_ages_out_unchanged_snapshot(self):
        with Harness(stall_after=8) as h:
            result=h.coordinator.run(2)
            self.assertFalse(result.completed)
            self.assertIn('Stale',result.reason)
            self.assertTrue(result.stop_requested)
            self.assertTrue(h.store.latest().healthy)

    def test_capture_fault_publishes_unknown_and_requests_cleanup(self):
        with Harness(fault_after=8) as h:
            result=h.coordinator.run(2)
            self.assertFalse(result.completed)
            self.assertIn('Unhealthy recognition',result.reason)
            self.assertTrue(result.stop_requested)
            self.assertFalse(h.store.latest().healthy)
            self.assertIsNone(h.store.latest().fields['speed'].value)

    def test_cancel_at_decision_submission_boundary_never_records_acceleration(self):
        store=SnapshotStore();store.publish(snapshot())
        backend=RecordingInputBackend()
        coordinator=Coordinator(store,backend,clock=lambda:1.0)
        class CancellingPolicy(ApproachPolicy):
            def decide(self,snapshot,now):
                action=super().decide(snapshot,now)
                coordinator.cancel()
                return action
        coordinator.policy=CancellingPolicy()
        result=coordinator.run(.2)
        self.assertFalse(result.completed)
        self.assertFalse(any(r.key=='PageUp' for r in backend.records()))
        self.assertTrue(any(r.key=='PageDown' for r in backend.records()))

    def test_full_recent_telemetry_keeps_running_and_records_history_evictions(self):
        with Harness(history_capacity=2048) as h:
            for index in range(2048):
                self.assertTrue(h.coordinator._record('history_filler', index=index))
            result=h.coordinator.run(4)
            self.assertTrue(result.completed,result.reason)
            self.assertTrue(result.stop_observed)
            self.assertEqual(len(result.events),2048)
            self.assertGreater(result.events_total,2048)
            self.assertEqual(result.events_evicted,result.events_total-2048)
            self.assertEqual(result.reason,'fresh_stop_observed')
            self.assertTrue(any(r.key=='PageUp' for r in h.backend.records()))

    def test_real_fault_still_cleans_up_when_recent_telemetry_is_full(self):
        store=SnapshotStore();store.publish(replace(snapshot(),healthy=False,reason='injected failure'))
        backend=RecordingInputBackend();coordinator=Coordinator(store,backend,clock=lambda:1,history_capacity=2048)
        for index in range(2048):
            coordinator._record('history_filler',index=index)
        result=coordinator.run(.2)
        self.assertIn('Unhealthy recognition',result.reason)
        self.assertTrue(backend.canceled)
        self.assertTrue(result.stop_requested)
        self.assertGreater(result.events_evicted,0)
        self.assertEqual(result.events[-1]['kind'],'fault')
        self.assertFalse(any(r.key=='PageUp' for r in backend.records()))

    def test_dropped_or_failed_journal_does_not_stop_control(self):
        class DroppingJournal:
            def submit(self,event): return False
        with Harness(journal=DroppingJournal()) as h:
            result=h.coordinator.run(4)
            self.assertTrue(result.completed,result.reason)
            self.assertTrue(result.stop_observed)
            self.assertEqual(result.journal_dropped,result.events_total)
            self.assertGreater(h.receiver.telemetry_stats()['journal_dropped'],0)
        class FailedJournal:
            def submit(self,event): raise OSError('injected disk failure')
        coordinator=Coordinator(SnapshotStore(),RecordingInputBackend(),journal=FailedJournal())
        self.assertTrue(coordinator._record('example'))
        self.assertEqual(coordinator.telemetry_stats()['journal_dropped'],1)

    def test_receiver_recent_effects_roll_but_later_input_still_has_effect(self):
        backend=RecordingInputBackend(queue_capacity=8192,record_capacity=8192,clock=lambda:10)
        class DroppingJournal:
            def submit(self,event): return False
        receiver=VirtualReceiver(backend,lambda:10,journal=DroppingJournal(),history_capacity=4096)
        for _ in range(4200):
            backend.move_mouse(0,0)
        backend.press_key('PageUp')
        receiver.start()
        try:
            until=time.monotonic()+2
            while receiver.telemetry_stats()['effects_total']<4202:
                if time.monotonic()>until: self.fail('Receiver did not consume bounded test backlog')
                time.sleep(.005)
        finally:
            self.assertTrue(receiver.stop())
        effects=receiver.observed_effects()
        self.assertEqual(len(effects),4096)
        self.assertEqual(effects[-2]['effect'],'maximum_throttle_requested')
        self.assertEqual(receiver.throttle,1)
        self.assertFalse(backend.canceled)
        self.assertEqual(receiver.telemetry_stats(),{'effects_total':4202,'effects_retained':4096,
            'effects_evicted':106,'journal_dropped':4202})

    def test_optional_journal_receives_coordinator_and_receiver_envelopes(self):
        class Journal:
            def __init__(self): self.events=[]
            def submit(self,event): self.events.append(event);return True
        journal=Journal()
        coordinator=Coordinator(SnapshotStore(),RecordingInputBackend(),journal=journal)
        coordinator._record('example',value=3)
        backend=RecordingInputBackend()
        backend.press_key('PageUp')
        receiver=VirtualReceiver(backend,time.monotonic,journal=journal)
        receiver.start()
        try:
            until=time.monotonic()+1
            while len(journal.events)<3:
                if time.monotonic()>until:self.fail('No receiver journal receipts')
                time.sleep(.005)
        finally:self.assertTrue(receiver.stop())
        self.assertEqual(journal.events[0]['event'],'coordinator_event')
        self.assertEqual(journal.events[0]['record']['value'],3)
        self.assertEqual(journal.events[-1]['event'],'receiver_effect')
        self.assertIn('action_sequence',journal.events[-1]['record'])

    def test_policy_cadence_cannot_silently_follow_recognition_rate(self):
        with self.assertRaises(ValueError): Coordinator(SnapshotStore(),RecordingInputBackend(),decision_interval=.05)

    def test_reconnected_session_faults_during_actual_coordinator_run(self):
        with Harness() as h:
            results=[]
            control=threading.Thread(target=lambda:results.append(h.coordinator.run(3)))
            control.start()
            try:
                deadline=time.monotonic()+1
                while not any(r.key=='PageUp' for r in h.backend.records()):
                    if time.monotonic()>deadline: self.fail('No initial acceleration')
                    time.sleep(.005)
                h.receiver.reconnect()
                control.join(2)
                self.assertFalse(control.is_alive())
                self.assertFalse(results[0].completed)
                self.assertIn('Session, layout',results[0].reason)
                self.assertTrue(results[0].stop_requested)
            finally:
                h.coordinator.cancel();control.join(2)

    def test_newer_sequence_does_not_invalidate_relevant_planner_dependencies(self):
        proposal=Proposal(1,'original','same-signature',1,1,10,target_portrait=(2,)*144)
        accepted,reason=validate_proposal(proposal,snapshot(sequence=8,acquired=2),2.1)
        self.assertTrue(accepted,reason)

    def test_same_hp_signature_but_replaced_portrait_invalidates_proposal(self):
        proposal=Proposal(1,'original','same-signature',1,1,10,target_portrait=(2,)*144)
        accepted,reason=validate_proposal(proposal,snapshot(sequence=8,acquired=2,portrait=(7,)*144),2.1)
        self.assertFalse(accepted)
        self.assertEqual(reason,'portrait_changed')

    def test_session_change_expiry_and_nonfinite_times_reject_proposals(self):
        proposal=Proposal(1,'original','same-signature',1,1,10,target_portrait=(2,)*144)
        self.assertFalse(validate_proposal(proposal,snapshot(epoch=2),1.1)[0])
        self.assertFalse(validate_proposal(proposal,snapshot(acquired=11),11)[0])
        self.assertFalse(validate_proposal(proposal,snapshot(),math.nan)[0])
        invalid=Proposal(1,'original','same-signature',1,1,math.nan,target_portrait=(2,)*144)
        self.assertFalse(validate_proposal(invalid,snapshot(),1.1)[0])
        self.assertFalse(validate_proposal(proposal,snapshot(acquired=2),1.5)[0])

    def test_cancelled_queued_click_does_not_select_receiver_target(self):
        backend=RecordingInputBackend()
        backend.click_mouse(1200,600)
        backend.cancel(reason='test_cancel')
        receiver=VirtualReceiver(backend,time.monotonic)
        receiver.start()
        try:
            time.sleep(.05)
            self.assertFalse(receiver.sample()[3]['asteroid_selected'])
            self.assertTrue(backend.discarded_sequences)
        finally: self.assertTrue(receiver.stop())

    def test_synthetic_model_rejects_invalid_rates(self):
        with self.assertRaises(ValueError): VirtualReceiver(RecordingInputBackend(),time.monotonic,deceleration=0)
        with self.assertRaises(ValueError): DelayedPlanner(math.nan)

if __name__=='__main__': unittest.main()
