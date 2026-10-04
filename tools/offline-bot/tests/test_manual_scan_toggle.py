"""Selected-target toggle execution and scanner lease cancellation."""
from dataclasses import replace
import json
from threading import Event,Thread
import time
import unittest

from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.state import FieldValue
from bsgo_offline.tracking import SelectionTrack
from test_manual_assist import snapshot,request,drained
from test_trace import scratch_directory


def track(current):
    if current is None:
        return None
    fields=current.fields
    return SelectionTrack(fields["selection_track_id"].value,current.context,current.acquired_at,
        current.acquired_at,fields["target_identity"].value,fields["target_portrait"].value,None)


def wait_for(predicate):
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        value=predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("condition not observed")


class ManualSelectedToggleTests(unittest.TestCase):
    def test_driver_failure_survives_owned_scan_cleanup_as_blocked(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            entered=Event()
            count=[0]
            def provider(value):
                count[0]+=1
                if count[0]>1:
                    entered.wait(1)
                    raise RuntimeError("fixture_track_provider_failure")
                return track(value)
            def runner(name,**kwargs):
                kwargs["on_delivery"](None,"injected")
                entered.set()
                deadline=time.monotonic()+1
                while kwargs["may_deliver"]() and time.monotonic()<deadline:time.sleep(.01)
                return drained()
            controller=ManualAssistController(directory,snapshot_provider=lambda:current[0],
                runner=runner,selection_track_provider=provider)
            controller.start_action("auto_scan_selected",request(controller))
            wait_for(lambda:controller.actions_snapshot()["routines"]["auto_scan_selected"].get("error"))
            wait_for(lambda:controller.actions_snapshot()["ownership"]["owner"] is None)
            state=controller.actions_snapshot()["routines"]["auto_scan_selected"]
            self.assertEqual(state["state"],"blocked")
            self.assertIn("fixture_track_provider_failure",state["error"])
            self.assertIn("fixture_track_provider_failure",state["result"]["reason"])
            self.assertTrue(controller.close())

    def test_persistent_snapshot_failure_stays_unknown_and_cancellation_remains_callable(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            controller=self.controller(directory,current,lambda name,**kw:drained())
            controller.start_action("auto_scan_selected",request(controller))
            def broken():
                raise RuntimeError("fixture_snapshot_provider_failure")
            controller.snapshot_provider=broken
            wait_for(lambda:controller.actions_snapshot()["routines"]["auto_scan_selected"].get("error"))
            state=controller.actions_snapshot()
            self.assertIsNone(state["current_observation"])
            self.assertIsNone(state["assessment_context"])
            self.assertIn("snapshot_provider_failure",state["routines"]["approach_current_target"]["reason"])
            self.assertTrue(controller.stop_action("auto_scan_selected")["accepted"])
            self.assertTrue(controller.close())

    def test_stop_waits_for_old_provider_and_fences_its_late_decision(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            entered,release=Event(),Event()
            calls=[]
            def provider(value):
                entered.set()
                release.wait(2)
                return track(value)
            controller=ManualAssistController(directory,snapshot_provider=lambda:current[0],
                runner=lambda name,**kw:calls.append(name) or drained(),selection_track_provider=provider)
            controller.start_action("auto_scan_selected",request(controller))
            self.assertTrue(entered.wait(1))
            controller.stop_action("auto_scan_selected")
            self.assertEqual(controller.start_action("auto_scan_selected",request(controller))["reason"],
                             "selected_scan_toggle_stopping")
            self.assertFalse(controller.actions_snapshot()["routines"]["auto_scan_selected"]["ready"])
            release.set()
            wait_for(lambda:not controller._toggle_thread.is_alive())
            self.assertEqual(calls,[])
            self.assertTrue(controller.close())

    def controller(self,directory,current,runner,**kwargs):
        return ManualAssistController(directory,snapshot_provider=lambda:current[0],runner=runner,
            selection_track_provider=track,**kwargs)

    def test_initial_moving_admission_rejected_then_enabled_waits_without_braking(self):
        with scratch_directory() as directory:
            current=[snapshot(speed=5)]
            calls=[]
            controller=self.controller(directory,current,lambda name,**kw:calls.append(name) or drained())
            self.assertEqual(controller.start_action("auto_scan_selected",{})["reason"],"stationary_ship_required")
            current[0]=snapshot()
            self.assertTrue(controller.start_action("auto_scan_selected",request(controller))["accepted"])
            current[0]=snapshot(sequence=2,speed=5)
            wait_for(lambda:controller.actions_snapshot()["routines"]["auto_scan_selected"]["waiting_for"]=="stationary_ship_required")
            state=controller.actions_snapshot()
            self.assertTrue(state["routines"]["auto_scan_selected"]["active"])
            self.assertIsNone(state["ownership"]["owner"])
            self.assertFalse(state["app_control_enabled"])
            self.assertEqual(calls,[])
            self.assertFalse(controller.start_action("approach_current_target",{})["accepted"])
            current[0]=snapshot(sequence=3)
            wait_for(lambda:len(calls)==1)
            time.sleep(.25)
            self.assertEqual(calls,["scan_selected"])
            self.assertFalse(controller.actions_snapshot()["selected_scan_toggle"]["last_result"]["scan_effect_verified"])
            self.assertTrue(controller.stop_action("auto_scan_selected")["accepted"])
            wait_for(lambda:not controller._toggle_thread.is_alive())
            self.assertFalse(controller.actions_snapshot()["routines"]["auto_scan_selected"]["active"])
            self.assertTrue(controller.close())

    def test_same_target_new_track_gap_does_not_repeat_but_observed_change_can(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            calls=[]
            controller=self.controller(directory,current,lambda name,**kw:calls.append(name) or drained())
            controller.start_action("auto_scan_selected",request(controller))
            wait_for(lambda:len(calls)==1)
            current[0]=snapshot(sequence=2,track=2)
            wait_for(lambda:controller.actions_snapshot()["selected_scan_toggle"]["continuity_blocker"])
            self.assertEqual(len(calls),1)
            current[0]=snapshot(sequence=3,track=3,identity="asteroid:hp:4100")
            wait_for(lambda:len(calls)==2)
            controller.stop_action("auto_scan_selected")
            self.assertTrue(controller.close())

    def test_known_scanned_skips_and_cancel_retains_pending_until_real_cleanup(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            first=current[0]
            current[0]=replace(first,fields={**first.fields,
                "selected_marker_scan_state":FieldValue("scanned",first.acquired_at)})
            entered,finish=Event(),Event()
            def runner(name,**kwargs):
                kwargs["on_delivery"](None,"injected")
                entered.set()
                finish.wait(2)
                return drained()
            controller=self.controller(directory,current,runner)
            controller.start_action("auto_scan_selected",request(controller))
            wait_for(lambda:controller.actions_snapshot()["routines"]["auto_scan_selected"]["waiting_for"]=="selected_marker_image_known_scanned")
            self.assertFalse(entered.is_set())
            current[0]=snapshot(sequence=2,track=2,identity="asteroid:hp:4100")
            self.assertTrue(entered.wait(1))
            controller.stop_action("auto_scan_selected")
            state=controller.actions_snapshot()
            self.assertEqual(state["ownership"]["state"],"stopping")
            self.assertIsNotNone(state["selected_scan_toggle"]["pending"])
            self.assertFalse(controller.start_action("approach_current_target",request(controller))["accepted"])
            finish.set()
            wait_for(lambda:controller.actions_snapshot()["ownership"]["owner"] is None)
            self.assertIsNone(controller.actions_snapshot()["selected_scan_toggle"]["pending"])
            self.assertTrue(controller.close())

    def test_atomic_toggle_and_approach_compete_without_queue(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            finish=Event()
            def runner(name,**kw):
                finish.wait(1)
                return drained()
            controller=self.controller(directory,current,runner)
            body=request(controller)
            responses=[]
            threads=[Thread(target=lambda name=name:responses.append(controller.start_action(name,body)))
                for name in ("auto_scan_selected","approach_current_target")]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            self.assertEqual(sum(row["accepted"] for row in responses),1)
            controller.stop_action("auto_scan_selected" if controller.actions_snapshot()["selected_scan_toggle"]["enabled"] else "approach_current_target")
            finish.set()
            self.assertTrue(controller.close())

    def test_completed_execution_references_remain_reviewable(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            def runner(name,**kw):
                path=directory/"actual-probe"
                path.mkdir()
                (path/"status.json").write_text(json.dumps({"execution":{
                    "last_input_intent":{"sequence":1},"last_input_delivery":{"outcome":"injected"},
                    "last_confirmed_effect":None}}))
                kw["on_progress"](path)
                return drained()
            controller=self.controller(directory,current,runner)
            controller.start_action("scan_selected",request(controller))
            wait_for(lambda:controller.actions_snapshot()["ownership"]["owner"] is None)
            state=controller.actions_snapshot()
            self.assertEqual(state["last_input_intent"]["sequence"],1)
            self.assertEqual(state["last_input_delivery"]["outcome"],"injected")
            self.assertIsNone(state["last_confirmed_effect"])
            self.assertEqual(state["action_artifacts"]["directory"],str(directory/"actual-probe"))


if __name__=="__main__":unittest.main()
