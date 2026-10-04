"""Manual input leases and existing-probe composition, with no native calls."""
from threading import Event, Thread
from dataclasses import replace
import time
import unittest
from unittest.mock import patch

from bsgo_offline.manual_assist import ManualAssistController, target_reading
from bsgo_offline.manual_routines import GuardedManualRoutines, PinnedRecognizer
from bsgo_offline.state import Snapshot, FieldValue
from test_approach_probe import PixelSample, PixelSource, Ship, recognize, PROFILE
from test_trace import scratch_directory


def snapshot(*, sequence=1, distance=1000, speed=0, identity="asteroid:hp:3900", at=None,
             view=1, track=1):
    at = time.monotonic() if at is None else at
    values = {"speed": speed, "distance": distance, "asteroid_selected": True,
        "target_identity": identity, "target_portrait": (3,) * 144, "target_max_hp": 3900,
        "hull_current": 5005, "hull_max": 5005, "view_revision": view, "selection_track_id": track}
    return Snapshot(sequence, 1, sequence, at, at, PROFILE, (4,3),
        {key: FieldValue(value, at) for key,value in values.items()}, window_id="42:17")


def request(controller, name="approach_current_target"):
    token = controller.actions_snapshot()["assessment_context"]["token"]
    return {"assessment_token": token, "supervisor_assessment": {
        "clear_corridor": True, "aligned_heading": True, "safe_stopped_scene": True,
        "flight_point": True}}


def drained(stop=True):
    return {"state": "blocked", "reason": "fixture_finished", "final_stop_observed": stop,
        "shutdown": {key: True for key in ("input_idle", "dispatcher", "recognition", "source")}}


def wait_finished(controller):
    deadline = time.monotonic()+2
    while time.monotonic() < deadline:
        state = controller.actions_snapshot()
        if state["ownership"]["state"] in ("idle", "faulted"):
            return state
        time.sleep(.01)
    raise AssertionError("action did not finish")


class ManualLeaseTests(unittest.TestCase):
    def test_present_unknown_or_stale_revision_cannot_be_assessed_as_absent(self):
        first=snapshot()
        for revision in (FieldValue(None,first.acquired_at,False,"unknown"),
                         FieldValue(1,first.acquired_at-.1)):
            current=replace(first,fields={**first.fields,"view_revision":revision})
            reading,reason=target_reading(current,time.monotonic())
            self.assertIsNone(reading)
            self.assertEqual(reason,"invalid_or_stale_view_revision")

    def test_navigation_drain_without_stop_retains_faulted_lease(self):
        with scratch_directory() as directory:
            def runner(name, **args):
                args["on_delivery"](None, "injected")
                return drained(False)
            controller = ManualAssistController(directory, snapshot_provider=snapshot, runner=runner)
            self.assertTrue(controller.start_action("approach_current_target", request(controller))["accepted"])
            state = wait_finished(controller)
            self.assertEqual(state["ownership"]["state"], "faulted")
            self.assertTrue(state["app_control_enabled"])
            self.assertFalse(state["decisions_enabled"])
            self.assertFalse(controller.start_action("scan_selected", request(controller))["accepted"])
            self.assertFalse(controller.close())

    def test_atomic_single_owner_and_cancel_waits_for_observed_stop(self):
        with scratch_directory() as directory:
            entered, finish = Event(), Event()
            def runner(name, **args):
                args["on_delivery"](None, "injected")
                entered.set()
                finish.wait(2)
                return {**drained(), "cancel_requested": True}
            controller = ManualAssistController(directory, snapshot_provider=snapshot, runner=runner)
            body = request(controller)
            answers = []
            threads = [Thread(target=lambda: answers.append(controller.start_action("approach_current_target", body))) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(sum(answer["accepted"] for answer in answers), 1)
            self.assertTrue(entered.wait(1))
            controller.stop_action("approach_current_target")
            self.assertEqual(controller.actions_snapshot()["ownership"]["state"], "stopping")
            finish.set()
            state = wait_finished(controller)
            self.assertIsNone(state["ownership"]["owner"])
            self.assertEqual(state["routines"]["approach_current_target"]["state"], "canceled")
            self.assertTrue(controller.close())

    def test_pause_moving_unknown_discovery_and_missing_assessments_reject(self):
        with scratch_directory() as directory:
            calls = []
            controller = ManualAssistController(directory, snapshot_provider=snapshot,
                runner=lambda *args, **kw: calls.append(args))
            state = controller.actions_snapshot()
            self.assertFalse(state["routines"]["scan_nearby_unscanned"]["available"])
            self.assertEqual(controller.start_action("scan_nearby_unscanned")["reason"], "nearby_target_discovery_not_available")
            self.assertFalse(controller.start_action("approach_current_target", {})["accepted"])
            marker = directory/"pause"
            marker.touch()
            controller.pause_file=marker
            self.assertEqual(controller.start_action("scan_selected", request(controller))["reason"], "mining_pause_marker_present")
            self.assertEqual(calls, [])
            self.assertTrue(marker.exists())
            moving=ManualAssistController(directory/"moving", snapshot_provider=lambda:snapshot(speed=5), runner=lambda **kw:None)
            self.assertEqual(moving.start_action("approach_current_target")["reason"], "stationary_ship_required")

    def test_focus_handover_failure_never_starts_and_cancel_keeps_waiting_truth(self):
        with scratch_directory() as directory:
            calls=[]
            controller=ManualAssistController(directory, snapshot_provider=snapshot,
                runner=lambda *args,**kw:calls.append(args), activation_ready=lambda:"game_not_foreground",
                focus_handover=lambda cause:{"accepted":False,"reason":"focus_denied"})
            self.assertTrue(controller.start_action("approach_current_target",request(controller))["accepted"])
            deadline=time.monotonic()+1
            while controller.actions_snapshot()["focus_handover"] is None and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(controller.actions_snapshot()["ownership"]["state"],"arming")
            controller.stop_action("approach_current_target")
            self.assertEqual(wait_finished(controller)["routines"]["approach_current_target"]["state"],"canceled")
            self.assertEqual(calls,[])

    def test_successful_focus_handover_and_changed_view_revalidation(self):
        with scratch_directory() as directory:
            current=[snapshot()]
            calls=[]
            def handover(cause):
                current[0]=snapshot(sequence=2,view=2)
                return {"accepted":True}
            controller=ManualAssistController(directory,snapshot_provider=lambda:current[0],
                runner=lambda *args,**kw:calls.append(args),focus_handover=handover)
            controller.start_action("approach_current_target",request(controller))
            state=wait_finished(controller)
            self.assertEqual(calls,[])
            self.assertIn("requested_target_or_context_changed",state["routines"]["approach_current_target"]["error"])
            second=ManualAssistController(directory/"good",snapshot_provider=snapshot,
                runner=lambda *args,**kw:drained(),focus_handover=lambda cause:{"accepted":True})
            self.assertTrue(second.start_action("approach_current_target",request(second))["accepted"])
            self.assertIsNone(wait_finished(second)["ownership"]["owner"])

    def test_token_expiry_and_partial_delivery_do_not_release_uncertain_navigation(self):
        with scratch_directory() as directory:
            now=[time.monotonic()]
            controller=ManualAssistController(directory,snapshot_provider=lambda:snapshot(at=now[0]),
                clock=lambda:now[0],runner=lambda *args,**kw:drained())
            body=request(controller)
            now[0]+=16
            self.assertEqual(controller.start_action("approach_current_target",body)["reason"],"fresh_target_assessment_token_required")
            def partial(name,**args):
                args["on_delivery"](None,"partial")
                return drained(False)
            controller.runner=partial
            controller.start_action("approach_current_target",request(controller))
            self.assertEqual(wait_finished(controller)["ownership"]["state"],"faulted")


class ManualCompositionTests(unittest.TestCase):
    def runtime(self,source,recognizer=recognize):
        return GuardedManualRoutines(source_factory=lambda interval:source,
            recognizer_factory=lambda:recognizer,dispatcher_factory=lambda *args:None,
            profile=PROFILE,window_id="42:17",frame_size=(4,3),scope="synthetic",
            wheel_point=(1,1),flight_point_assessed=True)

    def invoke(self,runtime,directory,distance=1000):
        target,_=target_reading(snapshot(distance=distance),time.monotonic())
        return runtime("approach_current_target",output=directory/"action",cancel_file=directory/"cancel",
            expected_target=target,assessment={key:True for key in ("clear_corridor","aligned_heading","safe_stopped_scene","flight_point")},
            on_progress=lambda path:None,on_delivery=lambda *args:None,may_deliver=lambda:True)

    def test_composes_proven_legs_with_no_flight_time_cap(self):
        with scratch_directory() as directory:
            cruise={**drained(),"state":"proved","final_stop_distance":1054}
            slow={**drained(),"state":"proved","final_stop_distance":786}
            with patch("bsgo_offline.manual_routines.run_approach_probe",return_value=cruise) as full,patch("bsgo_offline.manual_routines.run_throttle_probe",return_value=slow) as reduced:
                result=self.invoke(self.runtime(None),directory,1800)
            self.assertEqual(full.call_args.kwargs["brake_at"],1300)
            self.assertIsNone(full.call_args.kwargs["max_thrust_seconds"])
            self.assertNotIn("reduced_transition",full.call_args.kwargs)
            self.assertEqual(reduced.call_args.kwargs["brake_at"],800)
            self.assertEqual(reduced.call_args.kwargs["sample_interval"],5)
            self.assertTrue(result["final_stop_observed"])
            self.assertEqual(result["native_composed_approach_acceptance"],"pending")
            self.assertFalse(result["mining_ready"])

    def test_already_in_range_requires_two_new_zero_images_and_no_dispatcher(self):
        with scratch_directory() as directory:
            source=PixelSource(Ship(distance=786))
            runtime=self.runtime(source)
            runtime.dispatcher_factory=lambda *args:(_ for _ in ()).throw(AssertionError("input forbidden"))
            result=self.invoke(runtime,directory,786)
            self.assertEqual(result["reason"],"already_in_range")
            self.assertEqual(len(result["stopped_images"]),2)
            self.assertEqual(result["input_requests"],[])
            self.assertTrue(source.closed)

    def test_missing_or_changed_second_image_cannot_claim_already_in_range(self):
        for options in ({"change_at":2},{"damage_at":2}):
            with self.subTest(options=options),scratch_directory() as directory:
                result=self.invoke(self.runtime(PixelSource(Ship(distance=786),**options)),directory,786)
                self.assertFalse(result["final_stop_observed"])
                self.assertEqual(result["state"],"blocked")

    def test_target_pin_preserves_cleanup_speed_but_geometry_does_not(self):
        target,_=target_reading(snapshot(),time.monotonic())
        sample=PixelSample(1,time.monotonic(),0,1000,hull=4000)
        result=PinnedRecognizer(recognize,target)(sample)
        self.assertTrue(result.healthy)
        self.assertTrue(result.fields["speed"].valid)
        self.assertFalse(result.fields["hull_current"].valid)
        sample.layout="changed"
        result=PinnedRecognizer(recognize,target)(sample)
        self.assertFalse(result.healthy)
        self.assertFalse(result.fields["speed"].valid)


if __name__=="__main__": unittest.main()
