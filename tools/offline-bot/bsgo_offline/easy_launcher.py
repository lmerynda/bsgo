"""One-command Manual dashboard startup; discovering a window never sends input."""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock, Thread
import time
from urllib.request import urlopen
from uuid import uuid4
import webbrowser

from .artifacts import plain
from .manual_assist_session import (native_binding,run_manual_assist_session,
    SelectionEvidenceRecognizer)
from .manual_routines import GuardedManualRoutines
from .observer_ui import UnattachedManualActions,make_server


PROTOCOL="bsgo-manual-assist-launcher-v1"
SERVICE_KIND="manual_assist_launcher"
GAME_TITLE="Battlestar Galactica Online"
PROFILE="largehud-20261003"
SIZE=(2560,1440)
FLIGHT_DESCRIPTION="upper-center flight view; inspect for overlays"


def discover_game_windows():
    """Read-only exact-title enumeration, including unsupported-size candidates."""
    from .native_capture import _get_api
    api=_get_api()
    candidates=[]
    callback_type=ctypes.WINFUNCTYPE(ctypes.c_int32,ctypes.c_void_p,ctypes.c_void_p)
    def visit(hwnd,param):
        if not api.user32.IsWindowVisible(hwnd):
            return 1
        length=api.user32.GetWindowTextLengthW(hwnd)
        if not 0 < length <= 512:
            return 1
        title=ctypes.create_unicode_buffer(length+1)
        api.user32.GetWindowTextW(hwnd,title,len(title))
        if title.value != GAME_TITLE:
            return 1
        try:
            with api.dpi_context():
                candidates.append(api.inspect_window(hwnd,None))
        except Exception as exc:
            candidates.append({"hwnd":int(hwnd),"title":GAME_TITLE,"reason":str(exc)})
        return 1
    callback=callback_type(visit)
    function=api.user32.EnumWindows
    function.argtypes,function.restype=[callback_type,ctypes.c_void_p],ctypes.c_int32
    if not function(callback,None):
        raise RuntimeError("game_window_enumeration_failed")
    return candidates


def _marker_reader(sample,result):
    from .selected_scan_vision import locate_selected_marker,classify_selected_marker
    selected=result.fields.get("asteroid_selected")
    if not selected or not selected.valid or selected.value is not True:
        return "unknown","selected_marker_not_verified"
    image=sample.to_image()
    location=locate_selected_marker(image,selected_asteroid_verified=True)
    if location.box is None:
        return "unknown",location.reason
    classified=classify_selected_marker(image,location.box,marker_location_verified=True)
    return classified.scan_state,classified.reason


class EasyLauncher:
    """Persistent UI supervisor with at most one drained, pinned game session."""
    def __init__(self,workspace,runs_root,*,pause_file,discover=discover_game_windows,
                 binding=native_binding,session_runner=run_manual_assist_session,synthetic=False):
        self.workspace,self.runs_root=Path(workspace).resolve(),Path(runs_root).resolve()
        self.runs_root.mkdir(parents=True,exist_ok=True)
        self.pause_file=Path(pause_file).resolve()
        self.discover,self.binding,self.session_runner=discover,binding,session_runner
        self.synthetic=synthetic
        self.instance_id=uuid4().hex
        self._lock=Lock()
        self._phase="waiting_for_game"
        self._reason="Open the game, then choose Retry connection. Manual actions start paused."
        self._candidates=[]
        self._game=None
        self._controller=None
        self._thread=None
        self._cancel=None
        self._closed=False
        self._last_report=None
        self._cleanup_unresolved=False
        self._unattached=UnattachedManualActions()

    def _current_controller(self):
        with self._lock:
            return self._controller

    def action_control_snapshot(self):
        controller=self._current_controller()
        return controller.action_control_snapshot() if controller else {
            "enabled":False,"paused":True,"pause_reason":"game_connection_not_attached",
            "global_pause_present":self.pause_file.exists(),"pause_fingerprint":None,
            "acknowledgment_token":None,"acknowledgment_required":True,
            "resume_starts_routine":False,"legacy_pause_file_preserved":True}

    def startup_snapshot(self):
        with self._lock:
            phase,reason,game,candidates=self._phase,self._reason,self._game,list(self._candidates)
            closed=self._closed
            cleanup_unresolved=self._cleanup_unresolved
            session_running=bool(self._thread and self._thread.is_alive())
        controller=self._current_controller()
        observation=controller.actions_snapshot().get("current_observation") if controller else None
        if game:
            game={**game,"window_attached":phase=="connected",
                "observation_healthy":bool(observation and observation["healthy"]
                    and 0 <= observation["age_seconds"] < 2) if observation else None,
                "observation_age_seconds":observation.get("age_seconds") if observation else None,
                "observation_reason":None if observation and observation["healthy"]
                    and 0 <= observation["age_seconds"] < 2 else "fresh_healthy_image_not_available"}
        return {"protocol":PROTOCOL,"service_kind":SERVICE_KIND,"instance_id":self.instance_id,
            "workspace":str(self.workspace),"runs_root":str(self.runs_root),"synthetic":self.synthetic,
            "phase":phase,"reason":reason,"game":plain(game),"candidates":plain(candidates),
            "retry_available":not closed and not session_running and not cleanup_unresolved,
            "cleanup_unresolved":cleanup_unresolved,
            "action_control":self.action_control_snapshot(),
            "focus_handover":controller.focus_handover_snapshot() if controller else
                {"enabled":False,"available":False,"reason":"game_connection_not_attached"}}

    def actions_snapshot(self):
        controller=self._current_controller()
        state=controller.actions_snapshot() if controller else self._unattached.actions_snapshot()
        return {**state,"action_control":self.action_control_snapshot()}

    def start_action(self,name,request=None):
        controller=self._current_controller()
        if not controller:
            return {"accepted":False,"reason":"game_connection_not_attached","state":self.actions_snapshot()}
        return controller.start_action(name,request)

    def stop_action(self,name):
        controller=self._current_controller()
        if not controller:
            return {"accepted":False,"reason":"game_connection_not_attached","state":self.actions_snapshot()}
        return controller.stop_action(name)

    def resume_actions(self,*,acknowledgment_token=None):
        controller=self._current_controller()
        if not controller:
            return {"accepted":False,"reason":"game_connection_not_attached","state":self.startup_snapshot()}
        outcome=controller.resume_actions(acknowledgment_token=acknowledgment_token)
        return {**outcome,"state":self.startup_snapshot()}

    def pause_actions(self):
        controller=self._current_controller()
        if controller:
            outcome=controller.pause_actions()
        else:
            outcome={"accepted":True,"reason":"manual_actions_already_paused"}
        return {**outcome,"state":self.startup_snapshot()}

    def set_focus_handover(self,enabled):
        controller=self._current_controller()
        outcome=controller.set_focus_handover(enabled) if controller else {
            "accepted":False,"reason":"game_connection_not_attached"}
        return {**outcome,"state":self.startup_snapshot()}

    def retry_connection(self):
        with self._lock:
            reason="launcher_closed" if self._closed else None
            if self._thread and self._thread.is_alive():
                reason=reason or "connection_or_session_already_active"
            if self._cleanup_unresolved:
                reason=reason or "previous_session_shutdown_unresolved"
            controller=self._controller
            if controller and controller.actions_snapshot()["ownership"]["owner"] is not None:
                reason=reason or "previous_action_cleanup_unresolved"
            if reason is None:
                self._phase,self._reason="connecting","Discovering the unique supported game window; no focus or input."
                self._thread=Thread(target=self._connect,daemon=True,name="manual-dashboard-session")
                self._thread.start()
        return {"accepted":reason is None,"reason":reason or "connection_retry_started",
                "state":self.startup_snapshot()}

    def _connect(self):
        session_entered=False
        try:
            candidates=list(self.discover())
            with self._lock:
                self._candidates=[plain(candidate) for candidate in candidates]
            if not candidates:
                raise ValueError("No visible game window found. Open or restore the game, then Retry connection.")
            if len(candidates)!=1:
                raise ValueError("Multiple game windows found. Keep one game window open, then Retry connection.")
            candidate=candidates[0]
            if isinstance(candidate,dict):
                raise ValueError("Game window is unavailable: "+candidate.get("reason","unknown_window_state")+". Restore it, then Retry connection.")
            if candidate.title!=GAME_TITLE or candidate.client_size!=SIZE:
                raise ValueError("Game layout unsupported. Use a2560x1440 game client with the enlarged HUD, then Retry connection.")
            geometry,source,recognizer,dispatcher,ready,handover=self.binding(
                candidate.hwnd,candidate.pid,PROFILE,allow_focus_handover=True)
            if geometry!=candidate:
                raise ValueError("Game window changed during connection. Retry connection.")
            connection_id=uuid4().hex
            name="manual-dashboard-"+datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-")+connection_id[:8]
            output=self.runs_root/name
            cancel=self.runs_root/(name+"-stop.request")
            wheel_point=(geometry.client_size[0]//2,geometry.client_size[1]//3)
            configuration={"profile":PROFILE,"hwnd":geometry.hwnd,"expected_pid":geometry.pid,
                "client_size":geometry.client_size,"screen_origin":geometry.screen_origin,
                "connection_id":connection_id,"launcher_instance_id":self.instance_id,
                "capture_mode":"visible_client_rgb","foreground_required_for_observation":False,
                "foreground_required_for_input":True,"minimum_key_hold_seconds":.05,
                "wheel_point":wheel_point,"flight_point_assessed":False,
                "flight_point_description":FLIGHT_DESCRIPTION,"focus_handover_enabled":False}
            reader=SelectionEvidenceRecognizer(recognizer(),marker_reader=_marker_reader)
            runner=GuardedManualRoutines(source_factory=source,recognizer_factory=recognizer,
                dispatcher_factory=dispatcher,profile=PROFILE,window_id=f"{geometry.pid}:{geometry.hwnd}",
                frame_size=geometry.client_size,configuration=configuration,
                scope="synthetic" if self.synthetic else "native",wheel_point=wheel_point,
                flight_point_assessed=False)
            def connected(controller,server):
                with self._lock:
                    self._controller=controller
                    self._game={**plain(geometry),"connection_id":connection_id,"output":str(output),
                        "cancel_file":str(cancel),"profile":PROFILE}
                    self._phase,self._reason="connected","Manual observation connected. Enable actions explicitly when ready."
                    if self._closed:
                        cancel.write_text("launcher closed",encoding="utf-8")
            with self._lock:
                if self._closed:
                    return
                self._cancel=cancel
            session_entered=True
            report=self.session_runner(output,source_factory=lambda:source(.5,action=False),
                recognizer_factory=lambda:reader,selection_track_provider=reader.track_for,runner=runner,
                configuration=configuration,pause_file=self.pause_file,cancel_file=cancel,
                activation_ready=ready,focus_handover=None,focus_handover_capability=handover,
                wheel_point=wheel_point,wheel_point_description=FLIGHT_DESCRIPTION,
                action_output_root=self.runs_root,actions_paused=True,synthetic=self.synthetic,on_ready=connected)
            with self._lock:
                self._last_report=report
                shutdown=report.get("shutdown",{})
                self._cleanup_unresolved=(not all(value is True for value in shutdown.values())
                    or not all(shutdown.get(name) is True for name in
                        ("manual_actions","observer_server","recognition","source")))
                self._phase="blocked" if report.get("error") or self._cleanup_unresolved else "disconnected"
                self._reason=("Previous session workers did not stop. Retry is blocked until cleanup is resolved."
                    if self._cleanup_unresolved else report.get("error") or
                    "Observation session ended. Retry connection when the game is ready.")
        except Exception as exc:
            with self._lock:
                if session_entered:
                    self._cleanup_unresolved=True
                self._phase,self._reason="blocked",str(exc)

    def close(self,timeout=45.):
        with self._lock:
            self._closed=True
            cancel,thread=self._cancel,self._thread
        if cancel:
            cancel.write_text("explicit dashboard shutdown",encoding="utf-8")
        if thread:
            thread.join(timeout)
        controller=self._current_controller()
        return (not thread or not thread.is_alive()) and not self._cleanup_unresolved and (not controller or controller.actions_snapshot()["ownership"]["owner"] is None)


def compatible_listener(metadata,workspace,runs_root,*,fetch=None):
    """GET-only reuse of our nonce-identified workspace listener, never a kill."""
    try:
        row=json.loads(Path(metadata).read_text(encoding="utf-8"))
        if not isinstance(row.get("instance_id"),str) or len(row["instance_id"])!=32:
            return None
        url=row["url"]
        from urllib.parse import urlsplit
        parsed=urlsplit(url)
        if (parsed.scheme!="http" or parsed.hostname!="127.0.0.1" or parsed.username
                or parsed.password or parsed.path not in ("","/") or parsed.query or parsed.fragment):
            return None
        if fetch:
            state=fetch(url+"api/startup")
        else:
            with urlopen(url+"api/startup",timeout=.5) as response:
                data=response.read(65537)
                if len(data)>65536:
                    return None
                state=json.loads(data)
        if (state.get("protocol")==PROTOCOL and state.get("service_kind")==SERVICE_KIND
                and state.get("instance_id")==row.get("instance_id")
                and Path(state["workspace"]).resolve()==Path(workspace).resolve()
                and Path(state["runs_root"]).resolve()==Path(runs_root).resolve()):
            return url
    except (OSError,ValueError,KeyError,TypeError):
        pass
    return None


class WorkspaceLease:
    """OS-released lock prevents two launchers owning inputs in one workspace."""
    def __init__(self,path):
        self.path=Path(path)
        self.stream=None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open("a+b")
        if stream.tell()==0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            stream.close()
            return False
        self.stream=stream
        return True

    def close(self):
        if self.stream:
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(),msvcrt.LK_UNLCK,1)
            self.stream.close()
            self.stream=None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",type=Path,default=Path(__file__).resolve().parents[3])
    parser.add_argument("--runs-root",type=Path)
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be0..65535")
    workspace=args.workspace.resolve()
    runs_root=args.runs_root.resolve() if args.runs_root else workspace/"logs/mining/live-20261003"
    local=workspace/"logs/mining/dashboard-launcher"
    local.mkdir(parents=True,exist_ok=True)
    metadata=local/"listener.json"
    existing=compatible_listener(metadata,workspace,runs_root)
    if existing:
        webbrowser.open(existing)
        return 0
    lease=WorkspaceLease(local/"workspace.lock")
    if not lease.acquire():
        for _ in range(20):
            existing=compatible_listener(metadata,workspace,runs_root)
            if existing:
                webbrowser.open(existing)
                return 0
            time.sleep(.1)
        print("Another dashboard startup owns this workspace. Retry shortly; no process was stopped.",flush=True)
        return 1
    launcher=EasyLauncher(workspace,runs_root,
        pause_file=workspace/"logs/mining/live-20261003/continuous-stop.request")
    server=thread=None
    try:
        try:
            server=make_server(runs_root,args.port,action_adapter=launcher,startup_adapter=launcher)
        except OSError:
            # An unknown/legacy listener is left alone; the new dashboard gets
            # a free loopback port rather than replacing someone else's server.
            server=make_server(runs_root,0,action_adapter=launcher,startup_adapter=launcher)
        url=f"http://127.0.0.1:{server.server_port}/"
        temporary=metadata.with_suffix(".tmp")
        temporary.write_text(json.dumps({"url":url,"instance_id":launcher.instance_id,
            "pid":os.getpid(),"workspace":str(workspace),"runs_root":str(runs_root)}),encoding="utf-8")
        temporary.replace(metadata)
        thread=Thread(target=lambda:server.serve_forever(poll_interval=.2),daemon=True,name="manual-dashboard-http")
        thread.start()
        print(f"Mining dashboard: {url}",flush=True)
        webbrowser.open(url)
        launcher.retry_connection()
        while thread.is_alive():
            thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        clean=launcher.close()
        if server:
            if thread and thread.ident is not None:
                server.shutdown()
            server.server_close()
        if thread and thread.ident is not None:
            thread.join(2.)
        lease.close()
        if not clean:
            print("Manual action cleanup is unresolved; the app cannot certify that the ship stopped.",flush=True)
    return 0 if clean else 1


if __name__=="__main__":
    raise SystemExit(main())
