"""Explicit native Manual observer session with opt-in guarded action routines.

Starting the session only observes. HTTP Start needs fresh image-bound human
assessments; a persistent mining pause marker blocks action inputs. No mission
planner or Autopilot is attached.
"""
import argparse
from collections import deque
import json
from pathlib import Path
from threading import Thread, Lock
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, recognition_failures
from .manual_assist import ManualAssistController
from .live_frame import LiveFrameStore, LiveFrameSource, LiveFrameDiagnostics
from .manual_routines import GuardedManualRoutines
from .recognition import RecognitionResult, RecognitionWorker
from .state import FieldValue, Snapshot, SnapshotStore
from .tracking import SelectionTracker
from .game_observation import GameObservationFeed


class SelectionEvidenceRecognizer:
    """Publish local continuous selection evidence, never a world identity."""
    def __init__(self, recognizer, *, marker_reader=None):
        self.recognizer, self.marker_reader = recognizer, marker_reader
        self.tracker = SelectionTracker()
        self._tracks = deque(maxlen=8)
        self._track_lock = Lock()

    def track_for(self, snapshot):
        if snapshot is None:
            return None
        with self._track_lock:
            return next((track for context, at, sequence, track in self._tracks
                if context == snapshot.context and at == snapshot.acquired_at
                and sequence == snapshot.acquisition_sequence), None)

    def __call__(self, sample):
        result = self.recognizer(sample)
        features = result.fields.get('features')
        if features and features.valid and features.value.get('mining', {}).get('enabled') is False:
            return result
        try:
            return self._selection_evidence(sample, result)
        except Exception as exc:
            reason = 'mining_selection_recognition_failed: ' + type(exc).__name__
            fields = {**result.fields, **{name: FieldValue(None, sample.acquired_at, False, reason)
                for name in ('selection_track_id', 'selected_marker_scan_state', 'selected_scan_resource')}}
            return RecognitionResult(result.frame_size, fields, result.healthy, result.reason or reason)

    def _selection_evidence(self, sample, result):
        geometry = getattr(sample, "geometry", None)
        window = f"{geometry.pid}:{geometry.hwnd}" if geometry else sample.window_id
        snapshot = Snapshot(sample.acquisition_sequence, sample.session_epoch,
            sample.acquisition_sequence, sample.acquired_at, time.monotonic(), sample.layout,
            result.frame_size, result.fields, result.healthy, result.reason, window_id=window)
        from dataclasses import replace
        from .stationary_scan import scanner_target_values
        scanner = scanner_target_values(snapshot, time.monotonic())
        if scanner:
            local = dict(snapshot.fields)
            for name in ('target_identity','target_max_hp'):
                if name in scanner and (not local.get(name) or not local[name].valid):
                    local[name] = FieldValue(scanner[name], sample.acquired_at)
            snapshot = replace(snapshot, fields=local)
        track = self.tracker.update(snapshot, time.monotonic())
        with self._track_lock:
            self._tracks.append((snapshot.context,snapshot.acquired_at,snapshot.acquisition_sequence,track))
        confirmed = bool(track and track.continuity == "confirmed"
                         and track.last_evidence_at == sample.acquired_at)
        fields = {**result.fields, "selection_track_id": FieldValue(
            track.track_id if confirmed else None, sample.acquired_at, confirmed,
            None if confirmed else "local_selection_continuity_unverified")}
        if self.marker_reader:
            from .selected_scan_vision import MarkerClassification
            marker = self.marker_reader(sample, result)
            if isinstance(marker, MarkerClassification):
                state, reason = marker.scan_state, marker.reason
                resource, resource_reason = marker.resource, marker.resource_reason
            else:
                state, reason = marker
                resource, resource_reason = None, "resource_color_unverified"
            fields["selected_marker_scan_state"] = FieldValue(state if state == "scanned" else None,
                sample.acquired_at, state == "scanned", reason)
            valid_resource = state == "scanned" and resource in ("Water", "Titanium", "Tylium", "empty")
            fields["selected_scan_resource"] = FieldValue(resource if valid_resource else None,
                sample.acquired_at, valid_resource, resource_reason)
        return RecognitionResult(result.frame_size, fields, result.healthy, result.reason)


class ExactWindowHandover:
    """Opt-in focus requests are exact-window actions with before/after guards."""
    def __init__(self, geometry, *, inspect, foreground, focus, unoccluded=lambda geometry:None):
        self.geometry, self.inspect = geometry, inspect
        self.foreground, self.focus, self.unoccluded = foreground, focus, unoccluded

    def ready(self):
        try:
            actual = self.inspect(self.geometry.hwnd, self.geometry.pid)
            if actual != self.geometry:
                return "exact_game_window_geometry_or_title_changed"
            self.unoccluded(actual)
            if self.foreground() != self.geometry.hwnd:
                return "waiting_for_game_focus"
        except Exception as exc:
            return "game_window_guard_failed: " + str(exc)
        return None

    def __call__(self, cause):
        try:
            actual = self.inspect(self.geometry.hwnd, self.geometry.pid)
            if actual != self.geometry:
                raise ValueError("exact_game_window_geometry_or_title_changed")
            # Raising the game is how an overlapping window stops covering it;
            # ready() still requires an unoccluded client before any input.
            if self.foreground() != self.geometry.hwnd:
                if not self.focus(self.geometry.hwnd):
                    raise ValueError("exact_game_window_focus_request_failed")
            reason = self.ready()
            return {"accepted": reason is None, "reason": reason, "cause": cause,
                    "hwnd": self.geometry.hwnd, "pid": self.geometry.pid}
        except Exception as exc:
            return {"accepted": False, "reason": str(exc), "cause": cause,
                    "hwnd": self.geometry.hwnd, "pid": self.geometry.pid}


def run_manual_assist_session(output, *, source_factory, recognizer_factory, runner,
                             configuration, pause_file, cancel_file=None,
                             activation_ready=None, focus_handover=None, wheel_point=None,
                             selection_track_provider=None,
                             action_output_root=None,
                             actions_paused=True, focus_handover_capability=None,
                             wheel_point_description=None,
                             server_factory=None, on_ready=None, synthetic=False,
                             shutdown_timeout=40.):
    """Observation has no time cap. Controls start only from explicit actions."""
    if not 0 < shutdown_timeout <= 45:
        raise ValueError("shutdown_timeout")
    cancel = Path(cancel_file).resolve() if cancel_file else None
    observation_feed = GameObservationFeed()
    store = observation_feed.store
    diagnostic = DiagnosticRun(output,
        mode="synthetic_manual_assist_passive_observer" if synthetic else "native_manual_assist_passive_observer",
        configuration={**configuration, "synthetic": synthetic, "capture_interval_real_seconds": .5,
            "maximum_image_age": 2., "total_time_limit": None,
            "mission_decisions_owner": "human", "autopilot_attached": False,
            "routine_frames": 64, "routine_frame_retention": "first64; later routine images budget-skipped",
            "pause_file": str(Path(pause_file).resolve()) if pause_file else None,
            "actions_paused_at_start":actions_paused,
            "cancel_file": str(cancel) if cancel else None}, max_frames=64)
    controller = ManualAssistController(Path(output)/"actions", snapshot_provider=observation_feed.latest,
        runner=runner, pause_file=pause_file, activation_ready=activation_ready,
        paused=actions_paused, focus_handover_capability=focus_handover_capability,
        wheel_point_description=wheel_point_description,
        focus_handover=focus_handover, wheel_point=wheel_point,
        selection_track_provider=selection_track_provider, action_output_root=action_output_root)
    live_frames = observation_feed.frames
    controller.observation_feed = observation_feed
    # Routines that judge their own effects (Auto-fight) read the same feed.
    if runner is not None and hasattr(runner, "snapshot_provider") and runner.snapshot_provider is None:
        runner.snapshot_provider = observation_feed.latest
    controller.live_frame_snapshot = live_frames.snapshot
    controller.live_frame_image = live_frames.image
    source = worker = server = server_thread = None
    error = None
    shutdown = {}
    result = {"state": "recorded", "reason": "not_started", "mining_ready": False,
        "actual_mode": "manual", "mission_decisions_owner": "human"}
    try:
        if cancel and cancel.exists():
            result.update(state="canceled", reason="session_cancel_requested")
        else:
            source = LiveFrameSource(source_factory(), live_frames)
            worker = RecognitionWorker(source, recognizer_factory(), store,
                diagnostics=LiveFrameDiagnostics(diagnostic, live_frames))
            diagnostic.attach(worker=worker, store=store, coordinator=None, backend=None)
            diagnostic.update_execution(phase="observing", processing="reading HUD; Manual mission ownership",
                waiting_for="an image or an explicit Manual action request",
                expected_next_step="publish readings; input requires a separately accepted action")
            worker.start()
            if server_factory:
                server = server_factory(controller)
                server_thread = Thread(target=lambda:server.serve_forever(poll_interval=.1),
                                       daemon=True, name="manual-assist-observer-http")
                server_thread.start()
            if on_ready:
                on_ready(controller, server)
            sequence = 0
            while True:
                if cancel and cancel.exists():
                    result.update(state="canceled", reason="session_cancel_requested")
                    break
                snapshot = store.wait_for_sequence(sequence, .1)
                state = controller.actions_snapshot()
                diagnostic.set_control_status({"actual_mode":"manual", "requested_mode":"manual",
                    "pending":False,"app_control_enabled":state["app_control_enabled"],
                    "decisions_enabled":False,"mission_decisions_owner":"human",
                    "routine_decisions_enabled":state["routine_decisions_enabled"],
                    "routine_ownership":state["ownership"],"attached_controller":"manual_assist",
                    "autopilot_available":False,"reason":"manual_assist_only"})
                if snapshot:
                    sequence = snapshot.sequence
                    reasons = recognition_failures(snapshot)
                    why = snapshot.reason or "; ".join(f"{key}: {value}" for key,value in reasons.items()) or None
                    diagnostic.update_execution(phase="observing", processing="reading HUD; mission decisions belong to the human",
                        waiting_for="the next image or an explicitly assessed Manual action",
                        expected_next_step="publish readings and actual temporary routine ownership",
                        reason=why, blocker=why,progress=f"current observation {snapshot.sequence}")
                if worker.end_reason:
                    error = worker.error
                    result.update(state="blocked" if error else "recorded",reason=error or worker.end_reason)
                    break
                time.sleep(.1)
    except KeyboardInterrupt:
        result.update(state="canceled",reason="supervisor_interrupt")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result.update(state="blocked",reason=error)
    finally:
        live_frames.disconnect(error or result["reason"])
        # Keep recognition available while the canceled action verifies its
        # actual stop. Shutdown never automatically takes game focus.
        shutdown["manual_actions"] = controller.close(shutdown_timeout)
        observation_feed.connected = False
        result["manual_actions"] = controller.actions_snapshot()
        if not shutdown["manual_actions"]:
            error = error or "manual_action_ownership_unresolved; human intervention required"
            result.update(state="blocked",reason=error)
        if server:
            if server_thread and server_thread.ident is not None:
                server.shutdown()
            server.server_close()
            if server_thread and server_thread.ident is not None:
                server_thread.join(2.)
        shutdown["observer_server"] = server_thread is None or not server_thread.is_alive()
        if worker:
            worker.stop()
        shutdown["recognition"] = worker.join(2.) if worker else True
        shutdown["source"] = shutdown["recognition"]
        if source and shutdown["recognition"] and callable(getattr(source,"close",None)):
            try:
                source.close()
            except Exception as exc:
                shutdown["source"] = False
                error = error or str(exc)
        final = controller.snapshot()
        diagnostic.set_control_status({**final,"mission_decisions_owner":"human",
            "routine_ownership":controller.actions_snapshot()["ownership"]})
    return diagnostic.close(shutdown=shutdown,result=result,error=error,timeout=2.)


def native_binding(hwnd, pid, profile, *, allow_focus_handover=False):
    """Create explicit guarded factories. Calling this requires a real window."""
    from .native_capture import NativeCapture, NativeSource, inspect_window, _get_api
    from .native_input import NativeInputDispatcher
    from .startup_layout import StartupLayout, StartupHudRecognizer
    geometry = inspect_window(hwnd, expected_pid=pid)
    if geometry.title != "Battlestar Galactica Online":
        raise ValueError("unexpected_game_window_title")
    if profile != "largehud-20261003":
        raise ValueError("manual_assist_requires_explicit_large_hud_profile")
    layout = StartupLayout(geometry.client_size)
    api = _get_api()
    def focus(window):
        import ctypes
        function = api.user32.SetForegroundWindow
        function.argtypes, function.restype = [ctypes.c_void_p], ctypes.c_int32
        return bool(function(window))
    def unoccluded(actual):
        # Geometry is in physical pixels; hit-testing must use the same space.
        with api.dpi_context():
            api.ensure_unoccluded(actual)
    handover = ExactWindowHandover(geometry,inspect=lambda h,p:inspect_window(h,expected_pid=p),
        foreground=api.foreground_hwnd,focus=focus,unoccluded=unoccluded)
    def source(interval, *, action=True):
        if action and not layout.control_supported:
            raise ValueError(layout.control_reason)
        if inspect_window(hwnd,expected_pid=pid) != geometry:
            raise ValueError("pinned_game_window_changed")
        capture = NativeCapture(hwnd,pid,layout=profile,require_foreground=action,
            background_window=not action)
        if capture.geometry != geometry:
            capture.close()
            raise ValueError("pinned_game_window_changed")
        return NativeSource(capture,interval)
    def dispatcher(backend,journal):
        if not layout.control_supported:
            raise ValueError(layout.control_reason)
        return NativeInputDispatcher(backend,hwnd,pid,expected_size=geometry.client_size,
            expected_origin=geometry.screen_origin,journal=journal,keyboard_mode="virtual_key",
            minimum_key_hold_seconds=.05,minimum_mouse_hold_seconds=.05,minimum_mouse_settle_seconds=.05)
    return (geometry,source,lambda:StartupHudRecognizer(geometry.client_size,profile=profile),dispatcher,
            handover.ready if layout.control_supported else lambda:layout.control_reason,
            handover if allow_focus_handover and layout.control_supported else None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd",type=int,required=True)
    parser.add_argument("--pid",type=int,required=True)
    parser.add_argument("--profile",choices=["largehud-20261003"],required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--runs-root",type=Path,default=Path("logs/mining/live-20261003"))
    parser.add_argument("--pause-file",type=Path,required=True)
    parser.add_argument("--cancel-file",type=Path,required=True)
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--wheel-x",type=int,required=True)
    parser.add_argument("--wheel-y",type=int,required=True)
    parser.add_argument("--flight-point-assessed",action="store_true")
    parser.add_argument("--allow-action-focus-handover",action="store_true")
    args=parser.parse_args()
    if args.hwnd <= 0 or args.pid <= 0 or not 0 <= args.port <= 65535:
        parser.error("positive HWND/PID and port0..65535 required")
    if args.wheel_x < 0 or args.wheel_y < 0:
        parser.error("wheel point coordinates must be nonnegative")
    if args.pause_file.resolve() == args.cancel_file.resolve():
        parser.error("session cancellation and persistent mining pause must use separate files")
    from .observer_ui import make_server
    from .selected_scan_vision import locate_selected_marker,classify_selected_marker
    geometry,source,recognizer,dispatcher,ready,handover=native_binding(args.hwnd,args.pid,args.profile,
        allow_focus_handover=args.allow_action_focus_handover)
    if not args.wheel_x < geometry.client_size[0] or not args.wheel_y < geometry.client_size[1]:
        parser.error("wheel point must be inside the discovered native game client")
    def marker_reader(sample,result):
        selected=result.fields.get("asteroid_selected")
        if not selected or not selected.valid or selected.value is not True:
            return "unknown","selected_marker_not_verified"
        image=sample.to_image()
        location=locate_selected_marker(image,selected_asteroid_verified=True)
        if location.box is None:
            return "unknown",location.reason
        classification=classify_selected_marker(image,location.box,marker_location_verified=True)
        return classification.scan_state,classification.reason
    configuration={"hwnd":args.hwnd,"expected_pid":args.pid,"profile":args.profile,
        "client_size":geometry.client_size,"screen_origin":geometry.screen_origin,
        "capture_mode":"window_graphics_capture","foreground_required_for_observation":False,
        "foreground_required_for_input":True,"minimum_key_hold_seconds":.05,
        "focus_handover_enabled":args.allow_action_focus_handover,
        "flight_point_assessed":args.flight_point_assessed,
        "wheel_point":[args.wheel_x,args.wheel_y]}
    from .startup_layout import StartupLayout
    configuration["startup_layout"]=StartupLayout(geometry.client_size).metadata()
    runner=GuardedManualRoutines(source_factory=source,recognizer_factory=recognizer,
        dispatcher_factory=dispatcher,profile=args.profile,window_id=f"{args.pid}:{args.hwnd}",
        frame_size=geometry.client_size,configuration=configuration,wheel_point=(args.wheel_x,args.wheel_y),
        flight_point_assessed=args.flight_point_assessed)
    continuous_recognizer=SelectionEvidenceRecognizer(recognizer(),marker_reader=marker_reader)
    report=run_manual_assist_session(args.output,source_factory=lambda:source(.5,action=False),
        recognizer_factory=lambda:continuous_recognizer, selection_track_provider=continuous_recognizer.track_for,
        runner=runner,configuration=configuration,pause_file=args.pause_file,cancel_file=args.cancel_file,
        activation_ready=ready,focus_handover=handover,wheel_point=(args.wheel_x,args.wheel_y),
        action_output_root=args.runs_root,
        server_factory=lambda controller:make_server(args.runs_root,args.port,action_adapter=controller),
        on_ready=lambda controller,server:print(f"Manual session: http://127.0.0.1:{server.server_port}/",flush=True))
    print(json.dumps({"output":str(args.output.resolve()),"result":report["result"],
        "shutdown":report["shutdown"],"error":report["error"]},indent=2))
    return 1 if report["error"] or not all(report["shutdown"].values()) else 0


if __name__=="__main__":
    raise SystemExit(main())
