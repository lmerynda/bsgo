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
from .live_frame import LiveImageError
from .manual_assist_session import (native_binding,run_manual_assist_session,
    SelectionEvidenceRecognizer)
from .manual_routines import GuardedManualRoutines
from .observer_ui import UnattachedManualActions,make_server
from .side_panel import SidePanel
from .dock_window import DockFollower,open_panel
from .startup_layout import CALIBRATED_SIZE,PROFILE,StartupLayout
from .game_observation import game_state_view


PROTOCOL="bsgo-manual-assist-launcher-v1"
SERVICE_KIND="manual_assist_launcher"
GAME_TITLE="Battlestar Galactica Online"
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


def fit_game_client(hwnd,client_size):
    """Resize the window so its physical client area is client_size.

    A window-manager resize only: no focus change, activation or game input.
    The window keeps its position unless it would leave its monitor's work area.
    """
    from ctypes import wintypes
    from .native_capture import _get_api
    user32=ctypes.WinDLL("user32",use_last_error=True)
    handle=ctypes.c_void_p
    class MONITORINFO(ctypes.Structure):
        _fields_=[("cbSize",wintypes.DWORD),("rcMonitor",wintypes.RECT),
                  ("rcWork",wintypes.RECT),("dwFlags",wintypes.DWORD)]
    for name,result,args in [
            ("GetWindowRect",wintypes.BOOL,[handle,ctypes.POINTER(wintypes.RECT)]),
            ("GetClientRect",wintypes.BOOL,[handle,ctypes.POINTER(wintypes.RECT)]),
            ("IsZoomed",wintypes.BOOL,[handle]),("IsIconic",wintypes.BOOL,[handle]),
            ("MonitorFromWindow",handle,[handle,wintypes.DWORD]),
            ("GetMonitorInfoW",wintypes.BOOL,[handle,ctypes.POINTER(MONITORINFO)]),
            ("SetWindowPos",wintypes.BOOL,[handle,handle,ctypes.c_int,ctypes.c_int,
                ctypes.c_int,ctypes.c_int,wintypes.UINT])]:
        function=getattr(user32,name)
        function.restype,function.argtypes=result,args
    width,height=client_size
    with _get_api().dpi_context():
        if user32.IsIconic(hwnd):
            raise ValueError("game_window_minimized")
        if user32.IsZoomed(hwnd):
            raise ValueError("game_window_maximized")
        outer,inner=wintypes.RECT(),wintypes.RECT()
        if not user32.GetWindowRect(hwnd,ctypes.byref(outer)) or not user32.GetClientRect(hwnd,ctypes.byref(inner)):
            raise ValueError("game_window_geometry_unreadable")
        frame_w=(outer.right-outer.left)-inner.right
        frame_h=(outer.bottom-outer.top)-inner.bottom
        total_w,total_h=width+frame_w,height+frame_h
        info=MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd,2),ctypes.byref(info)):
            raise ValueError("game_monitor_unreadable")
        work=info.rcWork
        if total_w>work.right-work.left or total_h>work.bottom-work.top:
            raise ValueError("game_monitor_too_small_for_validated_size")
        left=min(max(outer.left,work.left),work.right-total_w)
        top=min(max(outer.top,work.top),work.bottom-total_h)
        # SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER
        if not user32.SetWindowPos(hwnd,None,left,top,total_w,total_h,0x0004|0x0010|0x0200):
            raise ValueError("game_window_resize_refused")
        if not user32.GetClientRect(hwnd,ctypes.byref(inner)):
            raise ValueError("game_window_geometry_unreadable")
    return (inner.right,inner.bottom)


def _marker_reader(sample,result):
    from .selected_scan_vision import locate_selected_marker,classify_selected_marker
    selected=result.fields.get("asteroid_selected")
    if (not selected or not selected.valid or selected.value is not True
            or selected.observed_at != sample.acquired_at):
        return "unknown","selected_marker_not_verified"
    image=sample.to_image()
    if image.size != result.frame_size:
        return "unknown","selected_marker_image_geometry_mismatch"
    location=locate_selected_marker(image,selected_asteroid_verified=True)
    if location.box is None:
        return "unknown",location.reason
    classified=classify_selected_marker(image,location.box,marker_location_verified=True)
    return classified


class EasyLauncher:
    """Persistent UI supervisor with at most one drained, pinned game session."""
    def __init__(self,workspace,runs_root,*,pause_file,discover=discover_game_windows,
                 binding=native_binding,session_runner=run_manual_assist_session,synthetic=False,
                 fit_window=fit_game_client):
        self.workspace,self.runs_root=Path(workspace).resolve(),Path(runs_root).resolve()
        self.runs_root.mkdir(parents=True,exist_ok=True)
        self.pause_file=Path(pause_file).resolve()
        self.discover,self.binding,self.session_runner=discover,binding,session_runner
        self.fit_window=fit_window
        self.synthetic=synthetic
        self.instance_id=uuid4().hex
        self._lock=Lock()
        self._phase="waiting_for_game"
        self._reason="Open the game, then choose Retry connection. Manual actions start paused."
        self._candidates=[]
        self._game=None
        self._controller=None
        self._observation_feed=None
        self._mining_enabled=True
        self._thread=None
        self._cancel=None
        self._closed=False
        self._last_report=None
        self._cleanup_unresolved=False
        self._unattached=UnattachedManualActions()

    def _current_controller(self):
        with self._lock:
            return self._controller

    def mining_enabled(self):
        with self._lock:
            return self._mining_enabled

    def game_state_snapshot(self):
        with self._lock:
            feed = self._observation_feed
        state = feed.game_state_snapshot() if feed else game_state_view(None, time.monotonic())
        return {**state, 'features': self.features_snapshot()}

    def features_snapshot(self):
        return {'mining': {'enabled': self.mining_enabled(), 'switchable': True},
                'game_observation': {'enabled': True, 'switchable': False}}

    def set_feature(self, name, enabled):
        if name != 'mining' or type(enabled) is not bool:
            return {'accepted': False, 'reason': 'invalid_feature_setting', 'state': self.features_snapshot()}
        controller = self._current_controller()
        if controller:
            state = controller.actions_snapshot()
            if state.get('ownership', {}).get('owner') is not None:
                return {'accepted': False, 'reason': 'stop_active_routine_before_feature_change',
                        'state': self.features_snapshot()}
            # Enabling a feature only makes its UI/recognition available. Every
            # change clears assessments and leaves input explicitly paused.
            controller.pause_actions()
        with self._lock:
            self._mining_enabled = enabled
        return {'accepted': True, 'reason': 'feature_enabled_actions_paused' if enabled else 'feature_disabled',
                'state': self.features_snapshot()}

    def live_frame_snapshot(self):
        with self._lock:
            controller, phase, reason = self._controller, self._phase, self._reason
            feed = self._observation_feed
            game = self._game
        provider = feed.frames.snapshot if feed else getattr(controller, "live_frame_snapshot", None)
        if not callable(provider):
            return {"available": False, "identity": None, "version": None, "image_url": None,
                "status": "waiting", "fresh": False, "connected": False, "reason": reason,
                "acquired_at": None, "age_seconds": None, "frame_size": None}
        state = provider()
        connected = phase == "connected" and state["connected"]
        fresh = connected and state["fresh"]
        return {**state, "connected": connected, "fresh": fresh,
            "status": "current" if fresh else "last_known" if state["available"] else "waiting",
            "reason": state["reason"] if connected else reason,
            "connection_id": game.get("connection_id") if game else None,
            "launcher_instance_id": self.instance_id}

    def live_frame_image(self, identity, version):
        with self._lock:
            feed = self._observation_feed
        if feed:
            return feed.frames.image(identity, version)
        controller = self._current_controller()
        provider = getattr(controller, "live_frame_image", None)
        if not callable(provider):
            raise LiveImageError(404, "live_image_unavailable")
        return provider(identity, version)

    def action_control_snapshot(self):
        controller=self._current_controller()
        state=controller.action_control_snapshot() if controller else {
            "enabled":False,"paused":True,"pause_reason":"game_connection_not_attached",
            "global_pause_present":self.pause_file.exists(),"pause_fingerprint":None,
            "acknowledgment_token":None,"acknowledgment_required":True,
            "resume_starts_routine":False,"legacy_pause_file_preserved":True}
        with self._lock:
            layout=self._game.get("startup_layout") if self._game else None
        if layout and not layout["control_supported"]:
            state={**state,"enabled":False,"paused":True,"pause_reason":layout["control_reason"]}
        if not self.mining_enabled():
            state={**state,"enabled":False,"paused":True,"pause_reason":"mining_feature_disabled"}
        return state

    def startup_snapshot(self):
        with self._lock:
            phase,reason,game,candidates=self._phase,self._reason,self._game,list(self._candidates)
            closed=self._closed
            cleanup_unresolved=self._cleanup_unresolved
            session_running=bool(self._thread and self._thread.is_alive())
        controller=self._current_controller()
        with self._lock:
            feed = self._observation_feed
        observation=(feed.snapshot() if feed else
            controller.actions_snapshot().get("current_observation") if controller else None)
        if game:
            calibration=observation.get("layout_calibration") if observation else None
            if calibration:
                calibration=dict(calibration)
                if phase != "connected" or not 0 <= observation["age_seconds"] < 2:
                    calibration.update(status="pending",reason="fresh_calibration_capture_required")
                game={**game,"startup_layout":{**game["startup_layout"],"calibration":calibration}}
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
            "features":self.features_snapshot(),
            "action_control":self.action_control_snapshot(),
            "focus_handover":controller.focus_handover_snapshot() if controller else
                {"enabled":False,"available":False,"reason":"game_connection_not_attached"}}

    def actions_snapshot(self):
        controller=self._current_controller()
        state=controller.actions_snapshot() if controller else self._unattached.actions_snapshot()
        with self._lock:
            layout=self._game.get("startup_layout") if self._game else None
        if layout and not layout["control_supported"]:
            state={**state,"paused":True,"pause_reason":layout["control_reason"],"assessment_context":None,
                "routines":{name:{**item,"ready":False,"start_allowed":False,"reason":layout["control_reason"]}
                            for name,item in state.get("routines",{}).items()}}
        if not self.mining_enabled():
            state={**state,"paused":True,"pause_reason":"mining_feature_disabled",
                "assessment_context":None,"visible_assessment_context":None,
                "routines":{name:{**item,"available":False,"ready":False,"start_allowed":False,
                    "reason":"mining_feature_disabled"} for name,item in state.get("routines",{}).items()}}
        return {**state,"action_control":self.action_control_snapshot()}

    def start_action(self,name,request=None):
        if not self.mining_enabled():
            return {"accepted":False,"reason":"mining_feature_disabled","state":self.actions_snapshot()}
        with self._lock:
            layout=self._game.get("startup_layout") if self._game else None
        if layout and not layout["control_supported"]:
            return {"accepted":False,"reason":layout["control_reason"],"state":self.actions_snapshot()}
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
        if not self.mining_enabled():
            return {"accepted":False,"reason":"mining_feature_disabled","state":self.startup_snapshot()}
        with self._lock:
            layout=self._game.get("startup_layout") if self._game else None
        if layout and not layout["control_supported"]:
            return {"accepted":False,"reason":layout["control_reason"],"state":self.startup_snapshot()}
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

    def fit_game_window(self):
        """Resize the game client to the validated size, then reconnect."""
        with self._lock:
            reason="launcher_closed" if self._closed else None
            if self._cleanup_unresolved:
                reason=reason or "previous_session_shutdown_unresolved"
            controller,thread,cancel=self._controller,self._thread,self._cancel
        if controller and controller.actions_snapshot()["ownership"]["owner"] is not None:
            reason=reason or "input_owner_busy"
        if reason:
            return {"accepted":False,"reason":reason,"state":self.startup_snapshot()}
        try:
            candidates=list(self.discover())
        except Exception as exc:
            return {"accepted":False,"reason":"game_window_discovery_failed: "+type(exc).__name__,
                    "state":self.startup_snapshot()}
        windows=[candidate for candidate in candidates if not isinstance(candidate,dict)]
        if len(candidates)!=1 or len(windows)!=1:
            return {"accepted":False,"reason":"game_window_not_unique" if candidates else "game_window_not_found",
                    "state":self.startup_snapshot()}
        game=windows[0]
        if tuple(game.client_size)!=CALIBRATED_SIZE:
            try:
                fitted=tuple(self.fit_window(game.hwnd,CALIBRATED_SIZE))
            except Exception as exc:
                return {"accepted":False,"reason":str(exc) if isinstance(exc,ValueError) else
                        "game_window_resize_failed: "+type(exc).__name__,"state":self.startup_snapshot()}
            if fitted!=CALIBRATED_SIZE:
                return {"accepted":False,"reason":"game_window_size_not_applied","state":self.startup_snapshot()}
        elif self.startup_snapshot()["phase"]=="connected":
            return {"accepted":True,"reason":"game_window_already_validated_size","state":self.startup_snapshot()}
        # The resize invalidates the pinned capture session; end it and reconnect
        # at the new size off the request thread.
        if thread and thread.is_alive() and cancel:
            cancel.write_text("game window fitted to validated size",encoding="utf-8")
        with self._lock:
            self._phase,self._reason="connecting","Game window fitted; reconnecting at 2560x1440."
        def reconnect():
            if thread:
                thread.join(15.)
            self.retry_connection()
        Thread(target=reconnect,daemon=True,name="manual-dashboard-fit").start()
        return {"accepted":True,"reason":"game_window_fitted","state":self.startup_snapshot()}

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
            if candidate.title!=GAME_TITLE:
                raise ValueError("Unexpected game window title. Retry connection.")
            layout=StartupLayout(candidate.client_size)
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
                "capture_mode":"window_graphics_capture","foreground_required_for_observation":False,
                "foreground_required_for_input":True,"minimum_key_hold_seconds":.05,
                "wheel_point":wheel_point,"flight_point_assessed":False,
                "flight_point_description":FLIGHT_DESCRIPTION,"focus_handover_enabled":False}
            configuration["startup_layout"]=layout.metadata()
            general_reader=recognizer()
            general_reader.mining_enabled_provider=self.mining_enabled
            reader=SelectionEvidenceRecognizer(general_reader,marker_reader=_marker_reader)
            runner=GuardedManualRoutines(source_factory=source,recognizer_factory=recognizer,
                dispatcher_factory=dispatcher,profile=PROFILE,window_id=f"{geometry.pid}:{geometry.hwnd}",
                frame_size=geometry.client_size,configuration=configuration,
                scope="synthetic" if self.synthetic else "native",wheel_point=wheel_point,
                flight_point_assessed=False)
            def connected(controller,server):
                with self._lock:
                    self._controller=controller
                    self._observation_feed=getattr(controller,'observation_feed',None)
                    self._game={**plain(geometry),"connection_id":connection_id,"output":str(output),
                        "cancel_file":str(cancel),"profile":PROFILE,"startup_layout":layout.metadata()}
                    self._phase="connected"
                    self._reason=("Manual observation connected. Enable actions explicitly when ready."
                        if layout.control_supported else
                        f"Detected {geometry.client_size[0]}x{geometry.client_size[1]} client. "
                        + "Automatic HUD calibration; actions unavailable until this layout is validated.")
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
        if not isinstance(row,dict):
            return None
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
        if not isinstance(state,dict):
            return None
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
    parser.add_argument("--no-browser",action="store_true",
        help="Start or reuse the observer without opening the side panel, a browser, or taking desktop focus")
    parser.add_argument("--dashboard",action="store_true",
        help="Also open the full dashboard in the default browser")
    args=parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be0..65535")
    workspace=args.workspace.resolve()
    runs_root=args.runs_root.resolve() if args.runs_root else workspace/"logs/mining/live-20261003"
    local=workspace/"logs/mining/dashboard-launcher"
    local.mkdir(parents=True,exist_ok=True)
    metadata=local/"listener.json"
    def show(url):
        if args.no_browser:
            return
        # A running instance keeps the panel docked; a repeat launch only reopens it.
        placement,scale=DockFollower().placement()
        opened=open_panel(url+"dock",local/"side-panel-profile",placement,scale=scale)
        if args.dashboard or not opened:
            webbrowser.open(url)

    existing=compatible_listener(metadata,workspace,runs_root)
    if existing:
        show(existing)
        return 0
    lease=WorkspaceLease(local/"workspace.lock")
    if not lease.acquire():
        for _ in range(20):
            existing=compatible_listener(metadata,workspace,runs_root)
            if existing:
                show(existing)
                return 0
            time.sleep(.1)
        print("Another dashboard startup owns this workspace. Retry shortly; no process was stopped.",flush=True)
        return 1
    launcher=EasyLauncher(workspace,runs_root,
        pause_file=workspace/"logs/mining/live-20261003/continuous-stop.request")
    server=thread=None
    panel=SidePanel(local/"side-panel-profile")
    try:
        try:
            server=make_server(runs_root,args.port,action_adapter=launcher,startup_adapter=launcher,
                dock_adapter=panel)
        except OSError:
            # An unknown/legacy listener is left alone; the new dashboard gets
            # a free loopback port rather than replacing someone else's server.
            server=make_server(runs_root,0,action_adapter=launcher,startup_adapter=launcher,
                dock_adapter=panel)
        url=f"http://127.0.0.1:{server.server_port}/"
        temporary=metadata.with_suffix(".tmp")
        temporary.write_text(json.dumps({"url":url,"instance_id":launcher.instance_id,
            "pid":os.getpid(),"workspace":str(workspace),"runs_root":str(runs_root)}),encoding="utf-8")
        temporary.replace(metadata)
        thread=Thread(target=lambda:server.serve_forever(poll_interval=.2),daemon=True,name="manual-dashboard-http")
        thread.start()
        print(f"Mining dashboard: {url}",flush=True)
        # Hotkeys never send game input: F8 pauses, F9-F11 are requests the panel checks.
        registered=panel.start_hotkeys(server.hotkey_pause)
        # Snap any Wingman window to the game's screen, even one opened by a repeat launch.
        panel.follower.start()
        print("Hotkeys: "+", ".join(f"{key} {'ready' if ok else 'unavailable'}" for key,ok in registered.items()),flush=True)
        if not args.no_browser:
            opened=panel.open(url)
            if args.dashboard or not opened:
                webbrowser.open(url)
        launcher.retry_connection()
        while thread.is_alive():
            thread.join(.5)
    except KeyboardInterrupt:
        pass
    finally:
        panel.close()
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
