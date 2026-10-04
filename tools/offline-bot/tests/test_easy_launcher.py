"""Easy launch admission, ownership and cleanup with fake native bindings."""
from contextlib import redirect_stdout
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
                ([WindowGeometry(17,42,GAME_TITLE,(1920,1080),(0,0))],"layout unsupported"),
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
                    patch("bsgo_offline.easy_launcher.webbrowser.open") as browser:
                lease.return_value.acquire.return_value=True
                with self.assertRaisesRegex(OSError,"fixture_metadata_failure"):
                    main()
                server.shutdown.assert_not_called()
                server.server_close.assert_called_once()
                lease.return_value.close.assert_called_once()
                discover.assert_not_called()
                browser.assert_not_called()

    def test_unknown_occupied_port_gets_new_server_without_killing_listener(self):
        with scratch_directory() as directory:
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
                    patch("bsgo_offline.easy_launcher.webbrowser.open") as browser,redirect_stdout(io.StringIO()):
                lease.return_value.acquire.return_value=True
                self.assertEqual(main(),0)
                self.assertEqual(make.call_args_list[0].args[1],8765)
                self.assertEqual(make.call_args_list[1].args[1],0)
                browser.assert_called_once_with("http://127.0.0.1:9001/")
                launcher.retry_connection.assert_called_once()


if __name__=="__main__":unittest.main()
