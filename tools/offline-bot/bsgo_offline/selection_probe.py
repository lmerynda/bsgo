"""One supervised stationary click, requiring later changed target images."""
import argparse
import json
import math
from pathlib import Path
import time

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .recognition import RecognitionWorker
from .state import SnapshotStore


def reading(snapshot, now, window_id, frame_size, context=None):
    if (not snapshot.healthy or snapshot.window_id != window_id
            or snapshot.frame_size != tuple(frame_size)
            or (context is not None and snapshot.context != context)
            or not 0 <= now-snapshot.acquired_at < 2):
        raise RuntimeError('invalid_capture_context_or_age')
    speed = snapshot.fields.get('speed')
    if (not speed or not speed.valid or type(speed.value) not in (int,float)
            or speed.value != 0 or speed.observed_at != snapshot.acquired_at
            or not 0 <= now-speed.observed_at < 2):
        raise RuntimeError('fresh_stopped_speed_required')
    values = {}
    for name in ('asteroid_selected', 'target_identity', 'target_max_hp', 'distance'):
        field = snapshot.fields.get(name)
        values[name] = (field.value if field and field.valid
                        and field.observed_at == snapshot.acquired_at
                        and 0 <= now-field.observed_at < 2 else None)
    return {'sequence':snapshot.sequence, 'acquired_at':snapshot.acquired_at, **values}


def baseline_speed_unreadable(snapshot, now):
    """Only missing/rejected speed can be retried before any input is sent."""
    field = snapshot.fields.get('speed')
    return (not field or not field.valid or type(field.value) not in (int,float)
            or not math.isfinite(field.value)
            or field.observed_at != snapshot.acquired_at
            or not 0 <= now-field.observed_at < 2)


def run_selection_probe(output, *, source_factory, recognizer_factory, dispatcher_factory,
                        configuration, profile, window_id, frame_size, x, y,
                        safe_scene_assessed, seconds=4, baseline_seconds=4,
                        scope='native', cancel_file=None):
    if safe_scene_assessed is not True:
        raise ValueError('fresh_safe_scene_assessment_required')
    if (type(seconds) not in (int,float) or not 0 < seconds <= 8
            or type(baseline_seconds) not in (int,float) or not 0 < baseline_seconds <= 8
            or scope not in ('native','synthetic')):
        raise ValueError('invalid_probe_bounds')
    if cancel_file is not None and not str(cancel_file).strip():
        raise ValueError('cancel_file')
    cancel_path = Path(cancel_file).resolve() if cancel_file is not None else None
    if any(type(v) is not int or not 0 <= v < limit for v,limit in zip((x,y),frame_size)):
        raise ValueError('invalid_frame_click')
    diagnostic = DiagnosticRun(output, mode=scope+'_stationary_selection_probe',
        configuration={**configuration, 'x':x, 'y':y, 'seconds':seconds,
                       'baseline_seconds':baseline_seconds,
                       'cancel_file':str(cancel_path) if cancel_path else None,
                       'safe_scene_assessed':True, 'mission_started':False}, max_frames=32)
    store = SnapshotStore()
    source = worker = backend = dispatcher = None
    baseline, after, matched, baseline_rejections = [], [], [], []
    request = None
    requested_at = None
    context = None
    shutdown = {}
    result = {'state':'blocked', 'reason':'not_started', 'mining_ready':False,
              'cancel_requested':False,
              'selection_effect_verified':False, 'baseline':baseline, 'after':after,
              'baseline_rejections':baseline_rejections}
    error = None
    cancel_seen = False
    def canceled():
        nonlocal cancel_seen
        if cancel_path is None:
            return False
        try:
            requested = cancel_path.exists()
        except OSError:
            requested = True
        if requested and not cancel_seen:
            cancel_seen = True
            diagnostic.submit({'event':'selection_probe_cancel_requested',
                               'at':time.monotonic(), 'path':str(cancel_path)})
        return requested
    def guard(record):
        try:
            if canceled():
                return False
            latest = store.latest()
            reading(latest, time.monotonic(), window_id, frame_size, context)
            return (record.kind == 'mouse_click' and requested_at is not None
                    and time.monotonic()-requested_at < 1)
        except Exception:
            return False
    try:
        if canceled():
            raise RuntimeError('cancel_requested')
        source = source_factory()
        backend = RecordingInputBackend(session_id='select-'+diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1], journal=diagnostic.journal)
        backend.set_delivery_contract(guard)
        worker = RecognitionWorker(source, recognizer_factory(), store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        diagnostic.update_execution(phase='baseline', processing='checking stationary selection baseline',
            waiting_for='two fresh stopped images',
            expected_next_step='request the assessed click only if the baseline passes')
        worker.start()
        deadline, sequence = time.monotonic()+baseline_seconds, 0
        while time.monotonic() < deadline:
            if canceled():
                raise RuntimeError('cancel_requested')
            snapshot = store.wait_for_sequence(sequence, .025)
            if not snapshot:
                if worker.end_reason:
                    raise RuntimeError('recognition_ended:'+worker.end_reason)
                continue
            if snapshot.sequence != sequence+1:
                raise RuntimeError('uninspected_observation_sequence')
            sequence = snapshot.sequence
            now = time.monotonic()
            if context is None:
                context = snapshot.context
            if request is None and baseline_speed_unreadable(snapshot, now):
                # Check capture identity/health even when speed OCR is unreadable.
                if (not snapshot.healthy or snapshot.window_id != window_id
                        or snapshot.frame_size != tuple(frame_size)
                        or snapshot.context != context
                        or not 0 <= now-snapshot.acquired_at < 2):
                    raise RuntimeError('invalid_capture_context_or_age')
                baseline.clear()
                diagnostic.update_execution(phase='baseline', reason='unreadable_baseline_speed',
                    processing='retaining unreadable speed evidence without selection input',
                    waiting_for='two fresh images with readable zero speed',
                    expected_next_step='retry the baseline while capture identity and age remain valid')
                baseline_rejections.append({'sequence':snapshot.sequence,
                                            'reason':'unreadable_baseline_speed'})
                diagnostic.submit({'event':'selection_baseline_retry',
                                   'sequence':snapshot.sequence,
                                   'reason':'unreadable_baseline_speed'})
                continue
            row = reading(snapshot, now, window_id, frame_size, context)
            if request is None:
                baseline.append(row)
                if len(baseline) == 2:
                    if canceled():
                        raise RuntimeError('cancel_requested')
                    dispatcher = dispatcher_factory(backend, diagnostic.journal)
                    diagnostic.attach_dispatcher(dispatcher)
                    dispatcher.start()
                    requested_at = time.monotonic()
                    request = backend.click_mouse(x,y,reason='stationary_selection_probe')
                    diagnostic.update_execution(phase='selecting', processing='checking click delivery and target change',
                        waiting_for='two matching changed-target images after delivered click',
                        expected_next_step='finalize the selection proof or blocker', progress='selection click requested')
                    deadline = requested_at+seconds
            else:
                after.append(row)
                deliveries = dispatcher.deliveries()
                delivered = next((d for d in deliveries if d.request_sequence == request.sequence), None)
                changed = (row['asteroid_selected'] is True
                           and isinstance(row['target_identity'],str) and row['target_identity']
                           and all(type(row[n]) in (int,float) and math.isfinite(row[n]) and row[n]>0
                                   for n in ('target_max_hp','distance'))
                           and row['target_identity'] != baseline[-1]['target_identity'])
                if delivered and delivered.outcome == 'injected' and row['acquired_at'] > delivered.at and changed:
                    if matched and row['target_identity'] != matched[-1]['target_identity']:
                        matched.clear()
                    matched.append(row)
                    if len(matched) == 2:
                        result.update(state='proved',reason='changed_target_observed_after_click',
                                      selection_effect_verified=True)
                        diagnostic.update_execution(phase='selecting', progress='two post-click target images matched',
                            confirmed_effect={'effect':'changed_target_observed_after_click',
                                'observation_sequences':[r['sequence'] for r in matched],
                                'request_sequences':[request.sequence]})
                        break
                else:
                    matched.clear()
        if result['state'] != 'proved':
            result['reason'] = ('baseline_timeout' if request is None
                                else 'selection_change_not_observed')
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        result['reason'] = error
    finally:
        diagnostic.update_execution(phase='cleanup', processing='stopping selection workers and flushing evidence',
            waiting_for='dispatcher, recognition and diagnostic writers to stop',
            expected_next_step='publish the checked selection outcome', blocker=error)
        if dispatcher:
            try:
                shutdown['dispatcher'] = dispatcher.stop(2)
            except Exception as exc:
                shutdown['dispatcher'] = False
                error = error or f'dispatcher_stop_failure: {type(exc).__name__}: {exc}'
        else:
            shutdown['dispatcher'] = True
        if worker:
            try:
                worker.stop()
                shutdown['recognition'] = worker.join(2)
            except Exception as exc:
                shutdown['recognition'] = False
                error = error or f'recognition_stop_failure: {type(exc).__name__}: {exc}'
        else:
            shutdown['recognition'] = True
        shutdown['source'] = shutdown['recognition']
        if source and shutdown['recognition']:
            try:
                source.close()
            except Exception as exc:
                shutdown['source'] = False
                error = error or f'source_close_failure: {type(exc).__name__}: {exc}'
        if backend:
            try:
                backend.cancel(reason='selection_probe_finished')
                backend.flush_journal()
            except Exception as exc:
                error = error or f'input_cancel_failure: {type(exc).__name__}: {exc}'
        shutdown['input_idle'] = not backend or (not backend.pending_requests and not backend.held_keys)
    try:
        deliveries = plain(dispatcher.deliveries()) if dispatcher else []
        dispatcher_error = dispatcher.error if dispatcher else None
    except Exception as exc:
        deliveries, dispatcher_error = [], f'delivery_read_failure: {type(exc).__name__}: {exc}'
        shutdown['dispatcher'] = False
    result.update(request=plain(request), deliveries=deliveries,
                  matched_sequences=[r['sequence'] for r in matched],
                  dispatcher_error=dispatcher_error)
    if canceled():
        result.update(state='blocked',reason='cancel_requested',
                      selection_effect_verified=False,cancel_requested=True)
    report = diagnostic.close(shutdown=shutdown,result=result,error=error)
    if not report['complete_diagnostics'] or not all(shutdown.values()) or result['dispatcher_error']:
        result.update(state='blocked',reason='incomplete_probe_evidence',selection_effect_verified=False)
    result.update(run_id=diagnostic.journal.run_id,shutdown=shutdown,
                  complete_diagnostics=report['complete_diagnostics'])
    _write_json(diagnostic.directory/'selection-probe.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('hwnd','pid','x','y'):
        parser.add_argument('--'+name,type=int,required=True)
    parser.add_argument('--profile',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--safe-scene-assessed',action='store_true',required=True)
    parser.add_argument('--cancel-file',type=Path)
    args = parser.parse_args()
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher
    from .mining_recognition import MiningHudRecognizer
    geometry = inspect_window(args.hwnd,expected_pid=args.pid)
    if geometry.title != 'Battlestar Galactica Online' or geometry.client_size != (2560,1440):
        raise ValueError('unexpected_game_window_or_native_size')
    result = run_selection_probe(args.output,
        source_factory=lambda:NativeSource(NativeCapture(args.hwnd,args.pid,layout=args.profile),.4),
        recognizer_factory=lambda:MiningHudRecognizer(args.profile),
        dispatcher_factory=lambda backend,journal:NativeInputDispatcher(backend,args.hwnd,args.pid,
            expected_size=geometry.client_size,expected_origin=geometry.screen_origin,journal=journal,
            minimum_mouse_hold_seconds=.05, minimum_mouse_settle_seconds=.05),
        configuration={'hwnd':args.hwnd,'pid':args.pid,'profile':args.profile,'mouse_hold_ms':50,'mouse_settle_ms':50},
        profile=args.profile,window_id=f'{args.pid}:{args.hwnd}',frame_size=geometry.client_size,
        x=args.x,y=args.y,safe_scene_assessed=True,cancel_file=args.cancel_file)
    print(json.dumps(plain(result),indent=2))
    return 0 if result['state']=='proved' else 1


if __name__ == '__main__':
    raise SystemExit(main())
