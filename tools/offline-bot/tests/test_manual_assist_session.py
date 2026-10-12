"""Session wiring and exact focus guards, entirely injected and synthetic."""
from pathlib import Path
from contextlib import redirect_stdout
import io
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch, Mock

from bsgo_offline.manual_assist_session import (run_manual_assist_session,
    SelectionEvidenceRecognizer, ExactWindowHandover, native_binding, main)
from bsgo_offline.manual_routines import GuardedManualRoutines
from bsgo_offline.scan_probe import run_scan_probe
from bsgo_offline.native_capture import WindowGeometry
from test_approach_probe import Ship, PixelSource, recognize, FakeDispatcher, PROFILE
from test_throttle_probe import WheelDispatcher
from test_manual_assist import request, drained
from test_trace import scratch_directory


class ManualSessionTests(unittest.TestCase):
    def test_selected_toggle_runs_actual_stationary_z_probe_and_retains_unknown_effect(self):
        with scratch_directory() as directory:
            ship=Ship(distance=1200)
            passive=PixelSource(ship)
            reader=SelectionEvidenceRecognizer(recognize)
            dispatchers=[]
            def dispatcher(backend,journal):
                instance=FakeDispatcher(backend,ship)
                dispatchers.append(instance)
                return instance
            runner=GuardedManualRoutines(source_factory=lambda interval:PixelSource(ship),
                recognizer_factory=lambda:recognize,dispatcher_factory=dispatcher,
                profile=PROFILE,window_id="42:17",frame_size=(4,3),scope="synthetic")
            outcomes=[]
            thread_holder=[]
            def ready(controller,server):
                def exercise():
                    deadline=time.monotonic()+2
                    while not controller.actions_snapshot()["assessment_context"] and time.monotonic()<deadline:time.sleep(.01)
                    outcomes.append(controller.start_action("auto_scan_selected",request(controller)))
                    deadline=time.monotonic()+4
                    while controller.actions_snapshot()["selected_scan_toggle"]["last_result"] is None and time.monotonic()<deadline:time.sleep(.01)
                    outcomes.append(controller.actions_snapshot())
                    controller.stop_action("auto_scan_selected")
                    (directory/"stop").touch()
                thread=Thread(target=exercise,daemon=True)
                thread_holder.append(thread)
                thread.start()
            def short_probe(path,**kwargs):
                return run_scan_probe(path,**kwargs,baseline_seconds=.5,after_seconds=.3)
            with patch("bsgo_offline.manual_routines.run_scan_probe",side_effect=short_probe):
                report=run_manual_assist_session(directory/"run",source_factory=lambda:passive,
                    recognizer_factory=lambda:reader,selection_track_provider=reader.track_for,
                    runner=runner,configuration={"fixture":True},pause_file=directory/"pause",
                    cancel_file=directory/"stop",on_ready=ready,synthetic=True,
                    actions_paused=False,
                    action_output_root=directory/"runs")
            thread_holder[0].join(1)
            self.assertTrue(outcomes[0]["accepted"])
            state=outcomes[1]
            self.assertIsNone(state["ownership"]["owner"])
            evidence=state["routines"]["auto_scan_selected"]["result"]["probe_evidence"]
            self.assertEqual(evidence["state"],"recorded")
            self.assertFalse(evidence["scan_effect_verified"])
            self.assertTrue(state["selected_scan_toggle"]["last_result"]["delivered"])
            self.assertEqual([item.key for item in dispatchers[0].backend.records()],["Z","Z"])
            self.assertEqual(ship.speed,0)
            self.assertEqual(Path(state["action_artifacts"]["directory"]).parent,directory/"runs")
            self.assertTrue(all(report["shutdown"].values()))

    def test_cli_connects_server_actions_and_explicit_session_identity(self):
        with scratch_directory() as directory:
            geometry=WindowGeometry(17,42,"Battlestar Galactica Online",(2560,1440),(0,0))
            source=Mock()
            report={"result":{},"shutdown":{"manual_actions":True},"error":None}
            argv=["manual_assist_session","--hwnd","17","--pid","42","--profile",PROFILE,
                "--output",str(directory/"run"),"--pause-file",str(directory/"pause"),
                "--cancel-file",str(directory/"stop"),"--wheel-x","1500","--wheel-y","700",
                "--flight-point-assessed"]
            with patch("sys.argv",argv),patch("bsgo_offline.manual_assist_session.native_binding",
                    return_value=(geometry,source,Mock(),Mock(),Mock(),None)),\
                    patch("bsgo_offline.manual_assist_session.run_manual_assist_session",return_value=report) as session,\
                    patch("bsgo_offline.observer_ui.make_server") as server,redirect_stdout(io.StringIO()):
                self.assertEqual(main(),0)
                options=session.call_args.kwargs
                options["source_factory"]()
                source.assert_called_once_with(.5,action=False)
                controller=Mock()
                options["server_factory"](controller)
                self.assertIs(server.call_args.kwargs["action_adapter"],controller)
                self.assertNotIn("mode_controller",server.call_args.kwargs)
                self.assertEqual(options["runner"].window_id,"42:17")
                self.assertEqual(options["runner"].wheel_point,(1500,700))
                self.assertIsNone(options["focus_handover"])

    def test_session_observes_under_pause_without_constructing_any_input(self):
        with scratch_directory() as directory:
            marker=directory/"mining-paused"
            marker.touch()
            calls=[]
            source=PixelSource(Ship())
            def ready(controller,server):
                deadline=time.monotonic()+1
                while not controller.actions_snapshot()["assessment_context"] and time.monotonic()<deadline:
                    time.sleep(.01)
                calls.append(controller.start_action("scan_selected",request(controller)))
                (directory/"session-stop").touch()
            report=run_manual_assist_session(directory/"run",source_factory=lambda:source,
                recognizer_factory=lambda:SelectionEvidenceRecognizer(recognize),runner=Mock(side_effect=AssertionError("input forbidden")),
                configuration={"fixture":True},pause_file=marker,cancel_file=directory/"session-stop",
                on_ready=ready,synthetic=True)
            self.assertEqual(calls[0]["reason"],"session_paused")
            self.assertTrue(report["result"]["manual_actions"]["action_control"]["global_pause_present"])
            self.assertTrue(marker.exists())
            self.assertTrue(all(report["shutdown"].values()))
            self.assertFalse(report["control"]["app_control_enabled"])
            self.assertFalse(report["control"]["decisions_enabled"])

    def test_session_runs_real_guarded_far_and_slow_probes_without_native_calls(self):
        with scratch_directory() as directory:
            ship=Ship(distance=1650)
            passive=PixelSource(ship)
            dispatchers=[]
            sources=[]
            def dispatch(backend,journal):
                # Wheel dispatcher also handles PageUp through the shared
                # fake dispatcher; no native methods or OS calls exist here.
                dispatcher=FakeDispatcher(backend,ship,reduced_wheel_speed=5)
                dispatchers.append(dispatcher)
                return dispatcher
            def capture(interval):
                source=PixelSource(ship)
                sources.append(source)
                return source
            runner=GuardedManualRoutines(source_factory=capture,recognizer_factory=lambda:recognize,
                dispatcher_factory=dispatch,profile=PROFILE,window_id="42:17",frame_size=(4,3),
                configuration={"fixture":True},scope="synthetic",wheel_point=(1,1),flight_point_assessed=True)
            output=[]
            observer_thread=[]
            def ready(controller,server):
                def action():
                    deadline=time.monotonic()+2
                    while not controller.actions_snapshot()["assessment_context"] and time.monotonic()<deadline: time.sleep(.01)
                    response=controller.start_action("approach_current_target",request(controller))
                    output.append(response)
                    deadline=time.monotonic()+12
                    while controller.actions_snapshot()["ownership"]["state"] not in ("idle","faulted") and time.monotonic()<deadline: time.sleep(.025)
                    output.append(controller.actions_snapshot())
                    (directory/"stop").touch()
                thread=Thread(target=action,daemon=True)
                observer_thread.append(thread)
                thread.start()
            report=run_manual_assist_session(directory/"run",source_factory=lambda:passive,
                recognizer_factory=lambda:SelectionEvidenceRecognizer(recognize),runner=runner,
                configuration={"fixture":True},pause_file=directory/"pause",cancel_file=directory/"stop",
                wheel_point=(1,1),on_ready=ready,synthetic=True,actions_paused=False)
            observer_thread[0].join(1)
            self.assertTrue(output[0]["accepted"],output)
            self.assertIsNone(output[1]["ownership"]["owner"],output[1]["routines"]["approach_current_target"]["error"])
            evidence=output[1]["routines"]["approach_current_target"]["result"]["probe_evidence"]
            self.assertEqual(len(evidence["legs"]),2,evidence)
            self.assertTrue(evidence["final_stop_observed"],evidence)
            self.assertLessEqual(evidence["final_stop_distance"],800)
            self.assertEqual(ship.speed,0)
            self.assertTrue(all(source.closed for source in sources))
            self.assertEqual(len(dispatchers),2)
            self.assertTrue(all(report["shutdown"].values()))

    def test_session_shutdown_does_not_hide_unresolved_navigation_owner(self):
        with scratch_directory() as directory:
            stop=directory/"stop"
            def runner(name,**args):
                args["on_delivery"](None,"injected")
                stop.touch()
                return drained(False)
            def ready(controller,server):
                deadline=time.monotonic()+1
                while not controller.actions_snapshot()["assessment_context"] and time.monotonic()<deadline: time.sleep(.01)
                controller.start_action("approach_current_target",request(controller))
            report=run_manual_assist_session(directory/"run",source_factory=lambda:PixelSource(Ship()),
                recognizer_factory=lambda:SelectionEvidenceRecognizer(recognize),runner=runner,
                configuration={"fixture":True},pause_file=directory/"pause",cancel_file=stop,
                on_ready=ready,synthetic=True,shutdown_timeout=1.,actions_paused=False)
            self.assertFalse(report["shutdown"]["manual_actions"])
            self.assertEqual(report["result"]["state"],"blocked")
            self.assertIn("human intervention required",report["error"])
            self.assertTrue(report["control"]["app_control_enabled"])
            self.assertEqual(report["control"]["routine_ownership"]["state"],"faulted")

    def test_native_factories_preserve_foreground_input_guard_and_50ms_timing(self):
        geometry=WindowGeometry(17,42,"Battlestar Galactica Online",(2560,1440),(20,30))
        capture=Mock(geometry=geometry)
        api=Mock()
        api.foreground_hwnd.return_value=17
        with patch("bsgo_offline.native_capture.inspect_window",return_value=geometry),\
                patch("bsgo_offline.native_capture._get_api",return_value=api),\
                patch("bsgo_offline.native_capture.NativeCapture",return_value=capture) as capture_class,\
                patch("bsgo_offline.native_capture.NativeSource") as source_class,\
                patch("bsgo_offline.native_input.NativeInputDispatcher") as dispatch_class:
            bound,source,recognizer,dispatcher,ready,handover=native_binding(17,42,PROFILE)
            self.assertIsNone(handover)
            in_dpi=[]
            api.dpi_context.return_value.__enter__=lambda *a:in_dpi.append(True)
            api.dpi_context.return_value.__exit__=lambda *a:in_dpi.append(False)
            api.ensure_unoccluded.side_effect=lambda g:self.assertEqual(in_dpi[-1:],[True],
                "occlusion hit-test must run in the physical-pixel DPI context")
            self.assertIsNone(ready())
            api.ensure_unoccluded.assert_called_once_with(geometry)
            source(.5,action=False)
            self.assertFalse(capture_class.call_args.kwargs["require_foreground"])
            self.assertTrue(capture_class.call_args.kwargs["background_window"])
            source(5.)
            self.assertTrue(capture_class.call_args.kwargs["require_foreground"])
            self.assertFalse(capture_class.call_args.kwargs["background_window"])
            dispatcher(Mock(),Mock())
            self.assertEqual(dispatch_class.call_args.kwargs["minimum_key_hold_seconds"],.05)
            self.assertEqual(dispatch_class.call_args.kwargs["keyboard_mode"],"virtual_key")
            self.assertEqual(dispatch_class.call_args.kwargs["expected_origin"],(20,30))
            # No focus request during binding, source creation or readiness.
            api.user32.SetForegroundWindow.assert_not_called()

    def test_exact_focus_failure_success_and_changed_geometry(self):
        geometry=WindowGeometry(17,42,"game",(4,3),(0,0))
        current=[0]
        focus=Mock(side_effect=lambda hwnd:current.__setitem__(0,hwnd) or True)
        handover=ExactWindowHandover(geometry,inspect=lambda h,p:geometry,
            foreground=lambda:current[0],focus=focus)
        self.assertEqual(handover.ready(),"waiting_for_game_focus")
        focus.assert_not_called()
        self.assertTrue(handover("start")["accepted"])
        focus.assert_called_once_with(17)
        current[0]=0
        focus.side_effect=None
        focus.return_value=False
        self.assertFalse(handover("cancel")["accepted"])
        handover.inspect=lambda h,p:WindowGeometry(17,42,"game",(4,3),(1,0))
        focus.reset_mock()
        self.assertFalse(handover("start")["accepted"])
        focus.assert_not_called()

    def test_focus_handover_raises_an_occluded_game_and_ready_still_guards_occlusion(self):
        geometry=WindowGeometry(17,42,"game",(4,3),(0,0))
        current,covered=[0],[True]
        def focus(hwnd):
            current[0]=hwnd
            covered[0]=False
            return True
        def unoccluded(actual):
            if covered[0]:
                raise ValueError("occluded")
        handover=ExactWindowHandover(geometry,inspect=lambda h,p:geometry,
            foreground=lambda:current[0],focus=focus,unoccluded=unoccluded)
        self.assertIn("occluded",handover.ready())
        self.assertTrue(handover("start")["accepted"])
        self.assertIsNone(handover.ready())
        covered[0]=True
        self.assertIn("occluded",handover.ready(),"input stays blocked while covered")


if __name__=="__main__": unittest.main()
