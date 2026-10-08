"""One bounded stationary view: propose rocks, verify selection, scan once each."""
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
import time

from PIL import Image

from .artifacts import plain
from .diagnostics import DiagnosticRun, _write_json
from .input import RecordingInputBackend
from .manual_assist import scan_scene_reading, target_reading, same_target
from .recognition import RecognitionWorker
from .selected_scan_vision import locate_selected_marker, classify_selected_marker
from .state import SnapshotStore
from .visible_scan_vision import visible_rock_candidates, view_fingerprint, same_view


def nearby(point, other, radius=55):
    return (point['x']-other['x'])**2 + (point['y']-other['y'])**2 <= radius**2


def marker_matches(scene, candidate):
    box = scene.get('marker')
    return bool(box and nearby({'x': (box[0]+box[2])/2, 'y': (box[1]+box[3])/2}, candidate, 60))


class VisibleSceneRecognizer:
    """Keep pixel proposals paired with their acquisition, outside JSON fields."""
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.lock = Lock()
        self.scenes = deque(maxlen=8)

    def __call__(self, sample):
        result = self.recognizer(sample)
        if callable(getattr(sample, 'to_image', None)):
            image = sample.to_image().convert('RGB')
        else:
            with Image.open(sample.path) as opened:
                image = opened.convert('RGB')
        selected = result.fields.get('asteroid_selected')
        verified = bool(selected and selected.valid and selected.observed_at == sample.acquired_at
                        and selected.value is True)
        location = locate_selected_marker(image, selected_asteroid_verified=verified)
        classification = classify_selected_marker(image, location.box, marker_location_verified=True) if location.box else None
        scene = {'candidates': visible_rock_candidates(image), 'fingerprint': view_fingerprint(image),
                 'marker': location.box, 'scan_state': classification.scan_state if classification else 'unknown',
                 'resource': classification.resource if classification else None}
        with self.lock:
            self.scenes.append((sample.acquisition_sequence, sample.acquired_at, scene))
        return result

    def scene_for(self, snapshot):
        with self.lock:
            return next((scene for sequence, at, scene in reversed(self.scenes)
                         if sequence == snapshot.acquisition_sequence and at == snapshot.acquired_at), None)


def run_visible_scan_pass(output, *, source_factory, recognizer_factory, dispatcher_factory,
                          profile, expected_window_id, frame_size, expected_scene,
                          configuration=None, scope='native', safe_scene_assessed=False,
                          cancel_file=None, scene_recognizer_factory=VisibleSceneRecognizer):
    if safe_scene_assessed is not True or tuple(frame_size) != (2560, 1440) or scope not in ('native','synthetic'):
        raise ValueError('assessed_native_stopped_view_required')
    cancel_path = Path(cancel_file) if cancel_file else None
    diagnostic = DiagnosticRun(output, mode=scope+'_visible_scan_pass',
        configuration={**(configuration or {}), 'maximum_candidates':32, 'scanner_range':4000,
                       'safe_scene_assessed':True, 'navigation_enabled':False}, max_frames=640)
    store = SnapshotStore()
    source = worker = backend = dispatcher = None
    rows, attempted = [], []
    progress = {'rows':rows, 'clicks_sent':0, 'scan_inputs_sent':0, 'scope':'current_stopped_view'}
    result = {'state':'blocked', 'reason':'not_started', 'visible_scan':progress, 'mining_ready':False}
    shutdown = {}
    context = fingerprint = candidate = pin = None
    sequence = 0
    request_at = None
    error = None

    def canceled():
        return bool(cancel_path and cancel_path.exists())

    def current(snapshot):
        if canceled():
            raise RuntimeError('cancel_requested')
        reading = scan_scene_reading(snapshot, time.monotonic(), allow_selected=True)
        scene = recognizer.scene_for(snapshot) if snapshot else None
        if (not reading or not scene or snapshot.window_id != expected_window_id
                or snapshot.frame_size != tuple(frame_size)
                or context is not None and snapshot.context != context
                or expected_scene['context']['layout'] != snapshot.layout
                or expected_scene['hull_max'] != reading['hull_max']):
            raise RuntimeError('fresh_stationary_full_hull_scene_required')
        if fingerprint is not None and not same_view(fingerprint, scene['fingerprint']):
            raise RuntimeError('view_changed_restart_scan')
        return scene

    def next_image(deadline):
        nonlocal sequence
        while time.monotonic() < deadline:
            if canceled():
                raise RuntimeError('cancel_requested')
            snapshot = store.wait_for_sequence(sequence, .05)
            if snapshot:
                sequence = snapshot.sequence
                return snapshot, current(snapshot)
            if worker.end_reason:
                raise RuntimeError('recognition_ended:'+worker.end_reason)
        raise RuntimeError('fresh_image_timeout')

    def guard(record):
        try:
            if record.kind == 'key_up':
                return record.key == 'Z'
            if request_at is None or not 0 <= time.monotonic()-request_at < 1:
                return False
            snapshot = store.latest()
            scene = current(snapshot)
            if record.kind == 'mouse_click':
                return record.x == candidate['x'] and record.y == candidate['y'] and any(
                    nearby(candidate, proposal, 25) for proposal in scene['candidates'])
            reading, reason = target_reading(snapshot, time.monotonic(), for_scan=True)
            return (record.kind == 'key_down' and record.key == 'Z' and reason is None
                    and same_target(pin, reading) and abs(pin['distance']-reading['distance']) <= 5
                    and reading['distance'] <= 4000 and marker_matches(scene, candidate)
                    and energy_available(snapshot))
        except Exception:
            return False

    def energy_available(snapshot):
        field = snapshot.fields.get('ship_telemetry')
        energy = field.value.get('energy_current') if field and isinstance(field.value, Mapping) else None
        return bool(field and field.valid and field.observed_at == snapshot.acquired_at
                    and type(energy) in (int,float) and energy >= 100)

    def publish(phase):
        diagnostic.update_execution(phase=phase, visible_scan=plain(progress),
            progress=f"{len(rows)} candidates checked; {progress['scan_inputs_sent']} scan inputs sent",
            processing='checking visible rock selections and resource colors',
            waiting_for='fresh stopped images and matching selected asteroid marker',
            expected_next_step='finish this view or stop; move the camera before starting another pass')

    def delivered(requests, deadline):
        while time.monotonic() < deadline:
            if canceled():
                raise RuntimeError('cancel_requested')
            indexed = {d.request_sequence:d for d in dispatcher.deliveries()}
            linked = [indexed.get(r.sequence) for r in requests]
            if all(linked):
                if any(d.outcome != 'injected' for d in linked):
                    raise RuntimeError('input_not_delivered')
                return max(d.at for d in linked)
            time.sleep(.025)
        raise RuntimeError('input_delivery_timeout')

    try:
        source = source_factory()
        recognizer = scene_recognizer_factory(recognizer_factory())
        backend = RecordingInputBackend(session_id='visible-'+diagnostic.journal.run_id,
            layout_id=profile, width=frame_size[0], height=frame_size[1], journal=diagnostic.journal)
        backend.set_delivery_contract(guard)
        worker = RecognitionWorker(source, recognizer, store, diagnostics=diagnostic)
        diagnostic.attach(worker=worker, store=store, coordinator=None, backend=backend)
        publish('baseline')
        worker.start()
        snapshot, scene = next_image(time.monotonic()+4)
        context, fingerprint = snapshot.context, scene['fingerprint']
        snapshot, scene = next_image(time.monotonic()+4)
        dispatcher = dispatcher_factory(backend, diagnostic.journal)
        diagnostic.attach_dispatcher(dispatcher)
        dispatcher.start()
        overall = time.monotonic()+240
        while len(attempted) < 32 and time.monotonic() < overall:
            candidate = next((p for p in scene['candidates'] if not any(nearby(p,a) for a in attempted)), None)
            if candidate is None:
                break
            attempted.append(dict(candidate))
            row = {'point':[candidate['x'],candidate['y']], 'status':'selecting', 'resource':None}
            rows.append(row)
            if marker_matches(scene,candidate) and scene['scan_state']=='scanned':
                row.update(status='already_scanned',resource=scene['resource'])
                publish('selecting')
                snapshot, scene = next_image(time.monotonic()+3)
                continue
            publish('selecting')
            request_at = time.monotonic()
            click = backend.click_mouse(candidate['x'],candidate['y'],reason='visible_rock_candidate')
            clicked_at = delivered([click],time.monotonic()+2)
            progress['clicks_sent'] += 1
            deadline, first, pin = time.monotonic()+4, None, None
            while time.monotonic() < deadline:
                snapshot, scene = next_image(deadline+2)
                reading, reason = target_reading(snapshot,time.monotonic(),for_scan=True)
                if snapshot.acquired_at <= clicked_at or reason or not marker_matches(scene,candidate):
                    first = None
                    continue
                if first and same_target(first,reading) and abs(first['distance']-reading['distance']) <= 5:
                    pin = reading
                    break
                first = reading
            if pin is None:
                row['status']='selection_unverified'
            elif pin['distance'] > 4000:
                row.update(status='outside_scanner_range',distance=pin['distance'])
            elif scene['scan_state']=='scanned':
                row.update(status='already_scanned',resource=scene['resource'],distance=pin['distance'])
            elif not energy_available(snapshot):
                row['status']='energy_reserve_unverified_or_low'
                publish('waiting')
                result.update(state='blocked',reason='energy_reserve_unverified_or_low')
                break
            else:
                row.update(status='scanning',distance=pin['distance'])
                publish('scanning')
                request_at = time.monotonic()
                requests = backend.press_key('Z',reason='verified_visible_asteroid')
                scanned_at = delivered(requests,time.monotonic()+2)
                progress['scan_inputs_sent'] += 1
                row['status']='scan_sent_result_unverified'
                deadline = time.monotonic()+6
                while time.monotonic() < deadline:
                    snapshot, scene = next_image(deadline+2)
                    reading, reason = target_reading(snapshot,time.monotonic(),for_scan=True)
                    if reason or not marker_matches(scene,candidate):
                        # Scan animations can temporarily occlude the label.
                        # No further input is requested while it is unreadable.
                        continue
                    if not same_target(pin,reading):
                        raise RuntimeError('selected_scan_target_changed')
                    if snapshot.acquired_at > scanned_at and scene['scan_state']=='scanned':
                        row.update(status='scan_color_observed',resource=scene['resource'])
                        break
            publish('selecting')
            snapshot, scene = next_image(time.monotonic()+3)
        if result['reason']=='not_started':
            result.update(state='recorded',reason='pass_time_limit_reached' if time.monotonic()>=overall else
                          'candidate_limit_reached' if len(attempted)>=32 else 'visible_pass_complete')
    except Exception as exc:
        error = str(exc)
        result.update(state='blocked',reason=error,cancel_requested=canceled())
    finally:
        publish('cleanup')
        if dispatcher:
            try:
                shutdown['dispatcher']=dispatcher.stop(2)
                result['deliveries']=plain(dispatcher.deliveries())
                if dispatcher.error:
                    error=error or str(dispatcher.error)
            except Exception as exc:
                shutdown['dispatcher']=False
                error=error or str(exc)
        else:
            shutdown['dispatcher']=True
        if worker:
            worker.stop()
        shutdown['recognition']=worker.join(2) if worker else True
        shutdown['source']=shutdown['recognition']
        if source and shutdown['recognition']:
            try:
                source.close()
            except Exception as exc:
                shutdown['source']=False
                error=error or str(exc)
        if backend:
            backend.cancel(reason='visible_scan_finished')
            backend.flush_journal()
        shutdown['input_idle']=not backend or (not backend.pending_requests and not backend.held_keys)
    report=diagnostic.close(shutdown=shutdown,result=result,error=error)
    if not all(shutdown.values()) or not report['complete_diagnostics']:
        result.update(state='blocked',reason='cleanup_or_diagnostics_incomplete')
    report['result']=plain(result)
    _write_json(diagnostic.directory/'report.json',report)
    _write_json(diagnostic.directory/'visible-scan.json',plain(result))
    return {**result,'shutdown':shutdown,'complete_diagnostics':report['complete_diagnostics']}
