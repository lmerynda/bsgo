"""Easy launch admission, ownership and cleanup with fake native bindings."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
from threading import Event
import time
import unittest
from unittest.mock import Mock,patch

from bsgo_offline.easy_launcher import (EasyLauncher,compatible_listener,PROTOCOL,
    SERVICE_KIND,PROFILE,GAME_TITLE,main)
from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.native_capture import WindowGeometry
from bsgo_offline.state import FieldValue
from bsgo_offline.startup_layout import StartupLayout
from test_manual_assist import snapshot,request,drained
from test_trace import scratch_directory


GAME=WindowGeometry(17,42,GAME_TITLE,(2560,1440),(20,30))


def wait_done(launcher):
    deadline=time.monotonic()+2
    while launcher._thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
    if launcher._thread.is_alive():raise AssertionError("launcher worker did not finish")
    return launcher.startup_snapshot()


class EasyLauncherTests(unittest.TestCase):
    def create(self,directory,**options):
        return EasyLauncher(directory,directory/"runs",pause_file=directory/"global-stop",**options)

    def test_missing_ambiguous_and_unsupported_game_are_visible_retryable_without_binding(self):
        for candidates,message in (([],"No visible game"),([GAME,GAME],"Multiple game"),
                ([WindowGeometry(17,42,"Another game",(1920,1080),(0,0))],"Unexpected game window title"),
                ([GAME,WindowGeometry(18,43,GAME_TITLE,(1920,1080),(0,0))],"Multiple game")):
            with self.subTest(message=message),scratch_directory() as directory:
                binding=Mock(side_effect=AssertionError("must not bind"))
                launcher=self.create(directory,discover=lambda:candidates,binding=binding)
                self.assertTrue(launcher.retry_connection()["accepted"])
                state=wait_done(launcher)
                self.assertIn(message,state["reason"])
                self.assertFalse(state["action_control"]["enabled"])
                self.assertTrue(state["retry_available"])
                binding.assert_not_called()
                self.assertTrue(launcher.close())

    def test_detected_dimensions_configure_observation_and_block_uncalibrated_actions(self):
        for size in ((1920,1080),(3840,2160),(1920,1200)):
            with self.subTest(size=size),scratch_directory() as directory:
                game=WindowGeometry(17,42,GAME_TITLE,size,(20,30))
                runner=Mock(side_effect=AssertionError("must not run an action"))
                resume=Mock(side_effect=AssertionError("must not enable uncalibrated input"))
                configs=[]
                def calibrated_snapshot():
                    observed=snapshot()
                    return replace(observed,fields={**observed.fields,"ocr_evidence":FieldValue(
                        {"startup_calibration":{"status":"registered","input_authorized":False}},observed.acquired_at)})
                def session(output,**args):
                    configs.append(args["configuration"])
                    controller=ManualAssistController(output,snapshot_provider=calibrated_snapshot,runner=runner,paused=True)
                    controller.resume_actions=resume
                    args["on_ready"](controller,None)
                    controller.close()
                    return {"shutdown":dict.fromkeys(("recognition","source","manual_actions","observer_server"),True),"error":None}
                launcher=self.create(directory,discover=lambda:[game],
                    binding=lambda *args,**kw:(game,Mock(),lambda:Mock(),Mock(),Mock(),None),session_runner=session)
                launcher.retry_connection()
                state=wait_done(launcher)
                self.assertEqual(state["game"]["client_size"],list(size))
                self.assertEqual(configs[0]["wheel_point"],(size[0]//2,size[1]//3))
                self.assertFalse(state["game"]["startup_layout"]["control_supported"])
                self.assertEqual(state["game"]["startup_layout"]["calibration"]["status"],"pending")
                self.assertEqual(launcher.actions_snapshot()["current_observation"]["layout_calibration"]["status"],"registered")
                self.assertFalse(launcher.resume_actions()["accepted"])
                self.assertFalse(launcher.start_action("scan_selected")["accepted"])
                self.assertIsNone(launcher.actions_snapshot()["assessment_context"])
                resume.assert_not_called()
                runner.assert_not_called()
                self.assertTrue(launcher.close())

    def test_calibration_status_requires_attached_fresh_observation(self):
        with scratch_directory() as directory:
            launcher=self.create(directory)
            observed=snapshot(at=20.)
            observed=replace(observed,fields={**observed.fields,"ocr_evidence":FieldValue(
                {"startup_calibration":{"status":"registered","input_authorized":False}},20.)})
            clock=[20.]
            controller=ManualAssistController(directory/"session",snapshot_provider=lambda:observed,
                clock=lambda:clock[0],paused=True)
            launcher._controller=controller
            launcher._phase="connected"
            launcher._game={"startup_layout":StartupLayout((1920,1200)).metadata()}
            def status():
                return launcher.startup_snapshot()["game"]["startup_layout"]["calibration"]["status"]
            self.assertEqual(status(),"registered")
            clock[0]=23.
            self.assertEqual(status(),"pending")
            clock[0]=20.
            launcher._phase="disconnected"
            self.assertEqual(status(),"pending")
            controller.close()
            self.assertTrue(launcher.close())

    def test_retry_discovers_a_new_size_instead_of_reusing_previous_layout(self):
        with scratch_directory() as directory:
            game=[GAME]
            configurations=[]
            def session(output,**args):
                configurations.append(args["configuration"])
                controller=ManualAssistController(output,snapshot_provider=snapshot,paused=True)
                args["on_ready"](controller,None)
                controller.close()
                return {"shutdown":dict.fromkeys(("recognition","source","manual_actions","observer_server"),True),"error":"window_resized"}
            launcher=self.create(directory,discover=lambda:game,
                binding=lambda *args,**kw:(game[0],Mock(),lambda:Mock(),Mock(),Mock(),None),session_runner=session)
            launcher.retry_connection()
            first=wait_done(launcher)
            game[0]=WindowGeometry(17,42,GAME_TITLE,(3840,2160),(20,30))
            launcher.retry_connection()
            second=wait_done(launcher)
            self.assertEqual(configurations[1]["client_size"],(3840,2160))
            self.assertEqual(second["game"]["startup_layout"]["mode"],"auto_calibration")
            self.assertNotEqual(first["game"]["connection_id"],second["game"]["connection_id"])
            self.assertFalse(second["action_control"]["enabled"])
            self.assertTrue(launcher.close())

    def test_attach_is_paused_unassessed_and_does_not_activate_game(self):
        with scratch_directory() as directory:
            ready,finish=Event(),Event()
            callbacks=[]
            bound=Mock(return_value=(GAME,Mock(),lambda:Mock(),Mock(),lambda:None,
                lambda cause:callbacks.append(cause) or {"accepted":True}))
            def session(output,**args):
                controller=ManualAssistController(Path(output)/"actions",snapshot_provider=snapshot,
                    runner=lambda *args,**kw:drained(),paused=args["actions_paused"],pause_file=args["pause_file"],
                    focus_handover_capability=args["focus_handover_capability"],
                    wheel_point=args["wheel_point"],wheel_point_description=args["wheel_point_description"])
                args["on_ready"](controller,None)
                ready.set()
                finish.wait(2)
                controller.close()
                return {"shutdown":{"recognition":True,"source":True,"manual_actions":True,"observer_server":True},"error":None}
            launcher=self.create(directory,discover=lambda:[GAME],binding=bound,session_runner=session)
            launcher.retry_connection()
            self.assertTrue(ready.wait(1))
            state=launcher.startup_snapshot()
            self.assertEqual(state["phase"],"connected")
            self.assertTrue(state["game"]["window_attached"])
            self.assertTrue(state["game"]["observation_healthy"])
            self.assertFalse(state["action_control"]["enabled"])
            self.assertFalse(state["focus_handover"]["enabled"])
            self.assertEqual(callbacks,[])
            context=launcher.actions_snapshot()["assessment_context"]
            self.assertFalse(context["flight_point_assessed"])
            self.assertIn("upper-center",context["flight_point_description"])
            token=state["action_control"]["acknowledgment_token"]
            self.assertTrue(launcher.resume_actions(acknowledgment_token=token)["accepted"])
            self.assertTrue(launcher.set_focus_handover(True)["accepted"])
            self.assertEqual(callbacks,[])
            self.assertFalse(launcher.retry_connection()["accepted"])
            finish.set()
            wait_done(launcher)
            self.assertFalse(launcher.startup_snapshot()["action_control"]["enabled"])
            self.assertTrue(launcher.close())

    def test_fit_game_window_resizes_unsupported_client_then_reconnects_paused(self):
        with scratch_directory() as directory:
            windows=[WindowGeometry(17,42,GAME_TITLE,(3192,1806),(20,30))]
            fitted=[]
            def fit(hwnd,size):
                fitted.append((hwnd,size))
                windows[0]=WindowGeometry(17,42,GAME_TITLE,size,(20,30))
                return size
            sessions=[]
            def session(output,**args):
                sessions.append(args["configuration"]["client_size"])
                controller=ManualAssistController(output,snapshot_provider=snapshot,paused=True)
                args["on_ready"](controller,None)
                cancel=Path(args["cancel_file"])
                deadline=time.monotonic()+2
                while not cancel.exists() and time.monotonic()<deadline:time.sleep(.01)
                controller.close()
                return {"shutdown":{"recognition":True,"source":True,"manual_actions":True,"observer_server":True},"error":None}
            launcher=self.create(directory,discover=lambda:list(windows),fit_window=fit,
                binding=lambda hwnd,*args,**kw:(windows[0],Mock(),lambda:Mock(),Mock(),lambda:None,None),
                session_runner=session)
            launcher.retry_connection()
            deadline=time.monotonic()+2
            while launcher.startup_snapshot()["phase"]!="connected" and time.monotonic()<deadline:time.sleep(.01)
            self.assertFalse(launcher.startup_snapshot()["game"]["startup_layout"]["control_supported"])
            outcome=launcher.fit_game_window()
            self.assertTrue(outcome["accepted"],outcome)
            self.assertEqual(fitted,[(17,(2560,1440))])
            deadline=time.monotonic()+3
            while len(sessions)<2 and time.monotonic()<deadline:time.sleep(.01)
            while launcher.startup_snapshot()["phase"]!="connected" and time.monotonic()<deadline:time.sleep(.01)
            state=launcher.startup_snapshot()
            self.assertEqual(sessions,[(3192,1806),(2560,1440)])
            self.assertTrue(state["game"]["startup_layout"]["control_supported"])
            self.assertFalse(state["action_control"]["enabled"],"fitting never enables actions")
            self.assertEqual(launcher.fit_game_window()["reason"],"game_window_already_validated_size")
            self.assertEqual(len(fitted),1)
            Path(state["game"]["cancel_file"]).write_text("done",encoding="utf-8")
            wait_done(launcher)
            self.assertTrue(launcher.close())

    def test_fit_game_window_refusals_keep_session_and_never_resize(self):
        with scratch_directory() as directory:
            fit=Mock(side_effect=ValueError("game_monitor_too_small_for_validated_size"))
            small=WindowGeometry(17,42,GAME_TITLE,(1920,1080),(0,0))
            for candidates,reason in (([],"game_window_not_found"),([small,small],"game_window_not_unique")):
                launcher=self.create(directory,discover=lambda:candidates,fit_window=fit)
                self.assertEqual(launcher.fit_game_window()["reason"],reason)
                fit.assert_not_called()
            launcher=self.create(directory,discover=lambda:[small],fit_window=fit)
            outcome=launcher.fit_game_window()
            self.assertFalse(outcome["accepted"])
            self.assertEqual(outcome["reason"],"game_monitor_too_small_for_validated_size")
            launcher=self.create(directory,discover=lambda:[small],fit_window=lambda hwnd,size:(1920,1080))
            self.assertEqual(launcher.fit_game_window()["reason"],"game_window_size_not_applied")

    def test_failed_worker_shutdown_blocks_retry_even_without_input_owner(self):
        with scratch_directory() as directory:
            binding=Mock(return_value=(GAME,Mock(),lambda:Mock(),Mock(),lambda:None,None))
            session=Mock(return_value={"shutdown":{"recognition":False,"source":False,"manual_actions":True},"error":None})
            launcher=self.create(directory,discover=lambda:[GAME],binding=binding,session_runner=session)
            launcher.retry_connection()
            state=wait_done(launcher)
            self.assertTrue(state["cleanup_unresolved"])
            self.assertFalse(state["retry_available"])
            self.assertEqual(launcher.retry_connection()["reason"],"previous_session_shutdown_unresolved")
            self.assertEqual(session.call_count,1)
            self.assertFalse(launcher.close())

    def test_unknown_cleanup_on_session_exception_or_missing_flags_blocks_retry(self):
        for result in (RuntimeError("fixture_failure_after_source_start"),
                       {"shutdown":{"manual_actions":True},"error":None}):
            with self.subTest(result=result),scratch_directory() as directory:
                session=Mock(side_effect=result) if isinstance(result,Exception) else Mock(return_value=result)
                launcher=self.create(directory,discover=lambda:[GAME],
                    binding=lambda *args,**kw:(GAME,Mock(),lambda:Mock(),Mock(),lambda:None,None),
                    session_runner=session)
                launcher.retry_connection()
                self.assertTrue(wait_done(launcher)["cleanup_unresolved"])
                self.assertFalse(launcher.retry_connection()["accepted"])
                self.assertEqual(session.call_count,1)
                self.assertFalse(launcher.close())

    def test_retry_revalidates_changed_window_and_never_reuses_controller_assessments(self):
        with scratch_directory() as directory:
            generation=[0]
            def session(output,**args):
                generation[0]+=1
                controller=ManualAssistController(output,snapshot_provider=snapshot,paused=True)
                args["on_ready"](controller,None)
                controller.close()
                return {"shutdown":{"recognition":True,"source":True,"manual_actions":True,"observer_server":True},"error":"capture_failure"}
            launcher=self.create(directory,discover=lambda:[GAME],
                binding=lambda *args,**kw:(GAME,Mock(),lambda:Mock(),Mock(),lambda:None,None),session_runner=session)
            launcher.retry_connection()
            first=wait_done(launcher)
            launcher.retry_connection()
            second=wait_done(launcher)
            self.assertNotEqual(first["game"]["connection_id"],second["game"]["connection_id"])
            self.assertNotEqual(first["game"]["cancel_file"],second["game"]["cancel_file"])
            self.assertFalse(second["action_control"]["enabled"])
            self.assertEqual(generation[0],2)
            self.assertTrue(launcher.close())

    def test_listener_reuse_requires_owned_nonce_workspace_and_attached_kind(self):
        with scratch_directory() as directory:
            metadata=directory/"listener.json"
            identity="a"*32
            metadata.write_text(json.dumps({"url":"http://127.0.0.1:9000/","instance_id":identity}))
            state={"protocol":PROTOCOL,"service_kind":SERVICE_KIND,"instance_id":identity,
                "workspace":str(directory),"runs_root":str(directory/"runs")}
            self.assertEqual(compatible_listener(metadata,directory,directory/"runs",fetch=lambda url:state),"http://127.0.0.1:9000/")
            for altered in ({**state,"instance_id":"b"*32},{**state,"service_kind":"unattached"},
                            {**state,"workspace":str(directory/"other")}):
                self.assertIsNone(compatible_listener(metadata,directory,directory/"runs",fetch=lambda url:altered))

    def test_nonobject_metadata_and_listener_response_are_ignored(self):
        with scratch_directory() as directory:
            metadata=directory/"listener.json"
            metadata.write_text("[]")
            fetch=Mock(side_effect=AssertionError("malformed metadata must not fetch"))
            self.assertIsNone(compatible_listener(metadata,directory,directory/"runs",fetch=fetch))
            fetch.assert_not_called()
            metadata.write_text(json.dumps({"url":"http://127.0.0.1:9000/","instance_id":"a"*32}))
            self.assertIsNone(compatible_listener(metadata,directory,directory/"runs",fetch=lambda url:[]))

    def test_bind_failure_before_serve_never_deadlocks_shutdown_or_starts_capture(self):
        with scratch_directory() as directory:
            server=Mock(server_port=9000)
            with patch("sys.argv",["easy_launcher","--workspace",str(directory)]),\
                    patch("bsgo_offline.easy_launcher.compatible_listener",return_value=None),\
                    patch("bsgo_offline.easy_launcher.WorkspaceLease") as lease,\
                    patch("bsgo_offline.easy_launcher.make_server",return_value=server),\
                    patch("pathlib.Path.replace",side_effect=OSError("fixture_metadata_failure")),\
                    patch("bsgo_offline.easy_launcher.discover_game_windows") as discover,\
                    patch("bsgo_offline.easy_launcher.SidePanel") as panel,\
                    patch("bsgo_offline.easy_launcher.webbrowser.open") as browser:
                lease.return_value.acquire.return_value=True
                with self.assertRaisesRegex(OSError,"fixture_metadata_failure"):
                    main()
                server.shutdown.assert_not_called()
                server.server_close.assert_called_once()
                lease.return_value.close.assert_called_once()
                discover.assert_not_called()
                browser.assert_not_called()
                panel.return_value.open.assert_not_called()
                panel.return_value.close.assert_called_once()

    def test_unknown_occupied_port_gets_new_server_without_killing_listener(self):
        # The side panel opens instead of the dashboard; the dashboard is the fallback.
        for opened,browser_calls in ((True,0),(False,1)):
            with self.subTest(panel_opened=opened),scratch_directory() as directory:
                server=Mock(server_port=9001)
                thread=Mock(ident=1)
                thread.is_alive.return_value=False
                launcher=Mock(instance_id="a"*32)
                launcher.close.return_value=True
                with patch("sys.argv",["easy_launcher","--workspace",str(directory)]),\
                        patch("bsgo_offline.easy_launcher.compatible_listener",return_value=None),\
                        patch("bsgo_offline.easy_launcher.WorkspaceLease") as lease,\
                        patch("bsgo_offline.easy_launcher.EasyLauncher",return_value=launcher),\
                        patch("bsgo_offline.easy_launcher.make_server",side_effect=[OSError("occupied"),server]) as make,\
                        patch("bsgo_offline.easy_launcher.Thread",return_value=thread),\
                        patch("bsgo_offline.easy_launcher.SidePanel") as panel,\
                        patch("bsgo_offline.easy_launcher.webbrowser.open") as browser,redirect_stdout(io.StringIO()):
                    lease.return_value.acquire.return_value=True
                    panel.return_value.open.return_value=opened
                    panel.return_value.start_hotkeys.return_value={"F8":True}
                    self.assertEqual(main(),0)
                    self.assertEqual(make.call_args_list[0].args[1],8765)
                    self.assertEqual(make.call_args_list[1].args[1],0)
                    self.assertIs(make.call_args_list[1].kwargs["dock_adapter"],panel.return_value)
                    panel.return_value.start_hotkeys.assert_called_once_with(server.hotkey_pause)
                    panel.return_value.open.assert_called_once_with("http://127.0.0.1:9001/")
                    self.assertEqual(browser.call_count,browser_calls)
                    launcher.retry_connection.assert_called_once()
                    panel.return_value.close.assert_called_once()


if __name__=="__main__":unittest.main()
