"""Exercise native backends against a receiver owned by this test, never the game."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
import uuid

from .artifacts import plain
from .input import InputRejected, RecordingInputBackend
from .recognition import RecognitionResult, RecognitionWorker
from .state import FieldValue, SnapshotStore
from .trace import TraceJournal


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(plain(value), indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def wait_for(predicate, label, timeout=5.):
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result = predicate()
            if result:
                return result
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        time.sleep(.02)
    raise TimeoutError(label)


class ReceiverPixelRecognizer:
    """Tiny test-fixture classifier; its state comes from pixels, not event files."""
    def __init__(self, ready):
        self.ready = ready
        self._lock = threading.Lock()
        self._last_frame = None
        self._observations = []

    def __call__(self, frame):
        image = frame.to_image()
        valid = image.size == tuple(self.ready['client_size'])
        for mark in self.ready['fiducials']:
            point = tuple(mark['client'])
            valid = valid and image.getpixel(point) == tuple(mark['rgb'])
        fields = {}
        fields['pixel_evidence']=FieldValue({
            'fiducials':[list(image.getpixel(tuple(mark['client']))) for mark in self.ready['fiducials']],
            'state_markers':{name:list(image.getpixel(tuple(marker['client'])))
                             for name,marker in self.ready['state_markers'].items()}},frame.acquired_at)
        for name, marker in self.ready['state_markers'].items():
            observed = image.getpixel(tuple(marker['client']))
            matches = [value for value, rgb in marker['rgb'].items() if observed == tuple(rgb)]
            known = valid and len(matches)==1
            value = matches[0] if known else None
            if name=='selected' and known:
                value = value=='true'
            fields[name] = FieldValue(value, frame.acquired_at, known,
                                      None if known else 'fixture_pixel_mismatch')
        with self._lock:
            self._last_frame = frame
            if len(self._observations)<512:
                self._observations.append({'acquisition_sequence':frame.acquisition_sequence,
                    'acquired_at':frame.acquired_at,'fields':fields})
        return RecognitionResult(image.size, fields, valid,
                                 None if valid else 'fixture_identity_pixels_mismatch')

    def evidence(self):
        with self._lock:
            return self._last_frame, tuple(self._observations)


def verify_received(requests, deliveries, events):
    """Require independently tagged receipts for each successfully inserted action."""
    by_sequence={request.sequence:request for request in requests}
    matched=[]
    delivered_sequences={delivery.request_sequence for delivery in deliveries if delivery.request_sequence is not None}
    if set(by_sequence)!=delivered_sequences:
        raise AssertionError('Requests and native delivery evidence do not cover the same sequences')
    for delivery in deliveries:
        if delivery.request_sequence is None:
            continue
        if delivery.outcome not in ('injected','skipped_not_injected'):
            raise AssertionError(f'Nominal input did not fully inject: {delivery}')
        if delivery.outcome=='skipped_not_injected':
            if by_sequence[delivery.request_sequence].kind!='key_up':
                raise AssertionError('A required press or click was skipped')
            continue
        request=by_sequence[delivery.request_sequence]
        received=[event for event in events if event.get('extra_info')==delivery.extra_info]
        if request.kind in ('key_down','key_up'):
            accepted=[event for event in received if event['kind']==request.kind and event.get('key')==request.key]
            if not accepted:
                raise AssertionError(f'No tagged receiver receipt for {request.kind} {request.key}')
        elif request.kind=='mouse_click':
            for kind in ('mouse_down','mouse_up'):
                if not any(event['kind']==kind and event.get('button')==request.button and
                           abs(event['x']-request.x)<=1 and abs(event['y']-request.y)<=1 for event in received):
                    raise AssertionError(f'No tagged receiver {kind} at requested client point')
        else:
            raise AssertionError('Unsupported action in native receipt check')
        matched.append(request.sequence)
    return {'matched_request_sequences':matched,'matched_count':len(matched),
            'coordinate_tolerance_pixels':1,'evidence':'independent_received_window_messages'}


def run_test(output, *, focus_timeout=30.):
    if os.name!='nt':
        raise RuntimeError('The native receiver experiment requires Windows')
    if not 0<focus_timeout<=120:
        raise ValueError('focus timeout must be within (0,120] seconds')
    from .native_capture import NativeCapture, NativeSource, inspect_window
    from .native_input import NativeInputDispatcher

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    receiver_dir = output/token
    receiver_dir.mkdir()
    # The base interpreter avoids Windows venv redirector PID indirection.
    process = subprocess.Popen([sys._base_executable, '-m', 'bsgo_offline.native_receiver',
        '--output', str(receiver_dir), '--token', token, '--lifetime', str(focus_timeout+30),'--activate'],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    worker = capture = dispatcher = backend = recognizer = journal = None
    receiver_stopped = False
    stages = []
    report = {'passed':False,'token':token,'receiver_directory':str(receiver_dir),
              'boundary':'Owned native receiver only; no game, global hooks, model, or GPU capture validation.'}

    def state():
        return read_json(receiver_dir/'receiver-state.json')

    try:
        journal = TraceJournal(receiver_dir/'trace')
        ready = wait_for(lambda: read_json(receiver_dir/'receiver-ready.json'), 'Receiver did not start')
        if ready['token']!=token or ready['pid']!=process.pid or ready['title']!='BSGO native input test - '+token:
            raise RuntimeError('Receiver identity does not match the owned child process')
        geometry = inspect_window(ready['hwnd'], expected_pid=ready['pid'])
        if geometry.title!=ready['title'] or geometry.client_size!=tuple(ready['client_size']):
            raise RuntimeError('Receiver window identity or client geometry changed at startup')
        report['target'] = ready
        print(json.dumps({'receiver_ready':ready,'waiting_for_foreground_seconds':focus_timeout}), flush=True)
        recognizer = ReceiverPixelRecognizer(ready)

        def foreground_capture():
            nonlocal capture
            candidate = None
            try:
                candidate = NativeCapture(ready['hwnd'], ready['pid'])
                frame=candidate.capture()
                observed=recognizer(frame)
                if not observed.healthy or not all(observed.fields[name].valid for name in ready['state_markers']):
                    raise RuntimeError('Receiver pixels are not yet fully painted or unobscured')
                capture = candidate
                return True
            except Exception as error:
                if candidate:
                    candidate.close()
                report['last_startup_capture_rejection']=str(error)
                if process.poll() is not None:
                    raise RuntimeError('Receiver exited before foreground capture') from error
                return False

        wait_for(foreground_capture, 'Owned receiver must be foreground and unobscured', focus_timeout)
        recognizer = ReceiverPixelRecognizer(ready)
        backend = RecordingInputBackend(session_id=token,layout_id='native-client-rgb',
            width=geometry.client_size[0],height=geometry.client_size[1],journal=journal)
        dispatcher = NativeInputDispatcher(backend,ready['hwnd'],ready['pid'],
            expected_size=geometry.client_size,expected_origin=geometry.screen_origin,journal=journal)
        store = SnapshotStore()
        worker = RecognitionWorker(NativeSource(capture, interval=.05),recognizer,store)
        worker.start()
        dispatcher.start()

        def seen(name, value):
            snapshot = store.latest()
            if snapshot is None or not snapshot.healthy:
                if worker.error:
                    raise RuntimeError(worker.error)
                return False
            field = snapshot.fields.get(name)
            return field is not None and field.valid and field.value==value

        wait_for(lambda: seen('throttle','idle'), 'Initial idle pixels not recognized')
        before = store.latest().sequence
        time.sleep(.5)  # A slow decision consumer must not trigger or stall capture.
        after = store.latest().sequence
        if after<=before:
            raise AssertionError('Native recognition did not progress during consumer delay')
        stages.append({'stage':'independent_native_capture','sequence_before':before,'sequence_after':after,
                       'consumer_delay_seconds':.5})
        last_frame, _ = recognizer.evidence()
        last_frame.to_image().save(receiver_dir/'native-before.png')

        x0,y0,x1,y1=ready['target_rect']
        target=((x0+x1)//2,(y0+y1)//2)
        backend.click_mouse(*target,reason='owned_receiver_target')
        wait_for(lambda: state()['selected'] and seen('selected',True), 'Click effect not received and observed')
        backend.press_key('PageUp',reason='owned_receiver_maximum')
        wait_for(lambda: state()['throttle']=='maximum' and seen('throttle','maximum'), 'PageUp effect not received and observed')
        backend.press_key('A',reason='owned_receiver_letter')
        for button, point in [('right',(300,250)),('middle',(320,250))]:
            backend.click_mouse(*point,button=button,reason='owned_receiver_'+button)
        wait_for(lambda: len(dispatcher.deliveries())>=len(backend.records()), 'Requested native actions not dispatched')
        # Deliberately hold one key, then verify cancellation produces received release.
        backend.key_down('B',reason='owned_receiver_hold')
        wait_for(lambda: 'B' in state()['held_keys'], 'Held key not received')
        backend.cancel(reason='native_test_cancel')
        backend.request_cleanup_stop(reason='native_test_cleanup')
        wait_for(lambda: state()['held_keys']==[] and state()['held_buttons']==[] and
                 state()['throttle']=='idle' and seen('throttle','idle'), 'Cancellation cleanup not received and observed')
        try:
            backend.click_mouse(*target,reason='must_reject_after_cancel')
        except InputRejected:
            pass
        else:
            raise AssertionError('Canceled native queue accepted an ordinary click')
        last_frame, _ = recognizer.evidence()
        last_frame.to_image().save(receiver_dir/'native-after.png')
        stages.append({'stage':'received_and_observed','selected':True,'final_throttle':'idle','holds_released':True})

        native_events=[json.loads(line) for line in (receiver_dir/'receiver-events.jsonl').read_text().splitlines()]
        report['receipt_verification']=verify_received(backend.records(),dispatcher.deliveries(),native_events)

        # Receiver changes its own geometry via a fixture command, not fake mouse/key messages.
        write_json(receiver_dir/'receiver-command.json',{'id':'geometry-test','command':'resize'})
        wait_for(lambda: inspect_window(ready['hwnd'],expected_pid=ready['pid']).client_size!=geometry.client_size,
                 'Receiver resize command not applied')
        wait_for(lambda: store.latest() is not None and not store.latest().healthy,
                 'Changed native geometry did not invalidate published recognition')
        faulty = store.latest()
        if any(field.valid for field in faulty.fields.values()):
            raise AssertionError('Capture fault retained a current known value')
        backend2=RecordingInputBackend(session_id=token+'-geometry',layout_id=backend.layout_id,
            width=backend.width,height=backend.height,journal=journal)
        rejected=NativeInputDispatcher(backend2,ready['hwnd'],ready['pid'],
            expected_size=geometry.client_size,expected_origin=geometry.screen_origin,journal=journal)
        try:
            backend2.press_key('A',reason='reject_changed_geometry')
            rejected.dispatch(backend2.get_action(timeout=0))
            wait_for(lambda: rejected.error is not None, 'Changed geometry did not reject native input')
            if any(row.outcome=='injected' for row in rejected.deliveries()):
                raise AssertionError('Stale geometry allowed injected input')
            stages.append({'stage':'geometry_rejection','capture_error':worker.error,
                           'input_error':rejected.error,'deliveries':rejected.deliveries()})
        finally:
            rejected.stop(timeout=2)
            backend2.flush_journal()
            report['geometry_input_history_stats']=backend2.history_stats()
            report['geometry_native_delivery_stats']=rejected.delivery_stats()
        report['passed']=True
    except Exception as error:
        report['error']=f'{type(error).__name__}: {error}'
    finally:
        recognition_stopped = True
        input_stopped = True
        if worker:
            worker.stop(); recognition_stopped=worker.join(timeout=2)
        if dispatcher:
            input_stopped=dispatcher.stop(timeout=2)
        if capture and recognition_stopped:
            capture.close()
        (receiver_dir/'receiver-exit').write_text('test complete',encoding='utf-8')
        try:
            process.wait(timeout=5)
            receiver_stopped=True
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=3)
            report['receiver_forced_termination']=True
        stderr=process.stderr.read().decode('utf-8',errors='replace')
        if stderr:
            report['receiver_stderr']=stderr
        report['stages']=stages
        report['shutdown']={'recognition':recognition_stopped,'input':input_stopped,'receiver':receiver_stopped}
        if not all(report['shutdown'].values()):
            report['passed']=False
        if backend:
            backend.flush_journal()
            backend.export_jsonl(receiver_dir/'native-input.jsonl')
            report['input_requests']=backend.records()
            report['input_history_stats']=backend.history_stats()
            report['discarded_sequences']=backend.discarded_sequences
        if dispatcher:
            report['native_deliveries']=dispatcher.deliveries()
            report['native_delivery_stats']=dispatcher.delivery_stats()
            report['native_input_error']=dispatcher.error
            if dispatcher.error:
                report['passed']=False
        if recognizer:
            last_frame,observations=recognizer.evidence()
            if last_frame:
                last_frame.to_image().save(receiver_dir/'native-last.png')
            report['pixel_observations']=observations
            report['recognition_error']=worker.error if worker else None
            if len(observations)>1:
                gaps=[b['acquired_at']-a['acquired_at'] for a,b in zip(observations,observations[1:])]
                report['observed_capture_interval_seconds']={'median':statistics.median(gaps),'max':max(gaps)}
        if (receiver_dir/'receiver-events.jsonl').exists():
            report['receiver_events']=[json.loads(line) for line in (receiver_dir/'receiver-events.jsonl').read_text().splitlines()]
        if (receiver_dir/'receiver-state.json').exists():
            report['receiver_final_state']=state()
        if journal:
            report['shutdown']['trace_writer']=journal.close()
            report['trace_stats']=journal.stats()
            report['history_scope']='recent_in_memory_history; backend event trace in trace/'
            report['complete_backend_trace']=(all(report['shutdown'].values()) and
                report['trace_stats']['write_complete'] and backend is not None and dispatcher is not None and
                not backend.history_stats()['logging_gaps'] and not dispatcher.delivery_stats()['logging_gaps'] and
                not report.get('geometry_input_history_stats',{}).get('logging_gaps',0) and
                not report.get('geometry_native_delivery_stats',{}).get('logging_gaps',0))
            if not report['complete_backend_trace']:
                report['passed']=False
        write_json(receiver_dir/'native-report.json',report)
        write_json(output/'native-report.json',report)
    return plain(report)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('logs/offline-bot/native'))
    parser.add_argument('--focus-timeout',type=float,default=30.)
    args=parser.parse_args()
    report=run_test(args.output,focus_timeout=args.focus_timeout)
    print(json.dumps({key:report.get(key) for key in ['passed','error','stages','shutdown']},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
