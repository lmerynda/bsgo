"""Run saved-pixel recognition or a synthetic input receiver, with no game access."""
import argparse, json, time
from pathlib import Path
from .artifacts import plain, save_report
from .coordinator import Coordinator, DelayedPlanner
from .input import RecordingInputBackend
from .policy import ApproachPolicy
from .recognition import HudRecognizer, RecognitionWorker
from .replay import ReplaySource
from .simulation import SimulationClock, SyntheticSource, VirtualReceiver, recognize_synthetic
from .state import SnapshotStore
from .trace import TraceJournal

ROOT = Path(__file__).resolve().parents[3]

def simulation(output, *, ignore_brake=False, stall_after=None, fault_after=None,
               planner_delay=.4, speedup=20.0):
    clock = SimulationClock(speedup)
    journal = TraceJournal(Path(output)/'trace')
    store = SnapshotStore()
    backend = RecordingInputBackend(session_id='simulation-1',layout_id='synthetic-original',clock=clock,
                                    journal=journal)
    receiver = VirtualReceiver(backend,clock,ignore_brake=ignore_brake,journal=journal)
    source = SyntheticSource(receiver,stall_after=stall_after,fault_after=fault_after)
    worker = RecognitionWorker(source,recognize_synthetic,store,clock=clock)
    planner = DelayedPlanner(delay=planner_delay,ttl=20)
    coordinator = Coordinator(store,backend,clock=clock,planner=planner,journal=journal)
    receiver.start()
    try:
        backend.click_mouse(1200,600,reason='select_synthetic_target')
        deadline = time.monotonic()+1
        while not receiver.sample()[3]['asteroid_selected']:
            if time.monotonic() >= deadline: raise RuntimeError('Receiver did not acknowledge target click')
            time.sleep(.005)
        worker.start()
        result = coordinator.run(timeout=4)
        # Drain bounded cleanup requests to the receiver before ending the trace.
        until = time.monotonic()+.1
        while time.monotonic()<until: time.sleep(.005)
    finally:
        worker.stop()
        recognition_stopped = worker.join(timeout=2)
        backend.cancel(reason='harness_shutdown')
        receiver_stopped = receiver.stop(timeout=2)
        planner_stopped = planner.close()
        backend.flush_journal()
        journal_stopped = journal.close()
    report = {'mode':'synthetic_receiver','physics':'invented test model; not BSGO dynamics',
              'clock_speedup':speedup,'planner_delay_real_seconds':planner_delay,
              'result':result,'recognition_processed':worker.processed,'recognition_end_reason':worker.end_reason,
              'latest_snapshot':store.latest(),'input_requests':backend.records(),
              'discarded_sequences':backend.discarded_sequences,'receiver_effects':receiver.observed_effects(),
              'history_scope':'recent_in_memory_history; backend and control event trace in trace/',
              'input_history_stats':backend.history_stats(),'receiver_telemetry_stats':receiver.telemetry_stats(),
              'coordinator_telemetry_stats':coordinator.telemetry_stats(),'trace_stats':journal.stats(),
              'shutdown':{'recognition':recognition_stopped,'receiver':receiver_stopped,
                          'planner':planner_stopped,'trace_writer':journal_stopped}}
    report['complete_event_trace'] = (all(report['shutdown'].values()) and
        report['trace_stats']['write_complete'] and not report['input_history_stats']['logging_gaps'] and
        not report['receiver_telemetry_stats']['journal_dropped'] and
        not report['coordinator_telemetry_stats']['journal_dropped'])
    save_report(output,report)
    backend.export_jsonl(Path(output)/'input.jsonl')
    return report

def replay(output, *, source_run=None, limit=None, interval=.03, profile='original', consumer_delay=.3,
           mining_observations=False):
    store = SnapshotStore()
    source = ReplaySource(ROOT/'test-data/vision/v1/manifest.json',source_run=source_run,
                         interval=interval,limit=limit,labeled_only=True,layout=profile)
    if mining_observations:
        from .mining_recognition import MiningHudRecognizer
        recognizer = MiningHudRecognizer(profile)
    else:
        recognizer = HudRecognizer(profile=profile)
    worker = RecognitionWorker(source,recognizer,store)
    worker.start()
    observations=[]
    first = store.wait_for_sequence(0,timeout=2)
    before = first.sequence if first else 0
    # Deliberately delay the consumer; it must not stall recognition.
    time.sleep(consumer_delay)
    after_snapshot = store.latest()
    after = after_snapshot.sequence if after_snapshot else 0
    if after_snapshot: observations.append(after_snapshot)
    deadline = time.monotonic()+15
    last = after
    try:
        while worker.running and time.monotonic()<deadline:
            snapshot=store.wait_for_sequence(last,timeout=.1)
            if snapshot:
                observations.append(snapshot);last=snapshot.sequence
    finally:
        worker.stop();stopped=worker.join(timeout=2)
    report={'mode':'saved_pixel_replay','timing':'Synthetic replay acquisitions; not original game frame timestamps',
            'profile':profile,'source_run':source_run,'mining_observations':mining_observations,
            'recognition_processed':worker.processed,
            'recognition_end_reason':worker.end_reason,'recognition_error':worker.error,
            'consumer_delay_real_seconds':consumer_delay,'sequence_before_delay':before,'sequence_after_delay':after,
            'advanced_during_delay':after>before,'latest_snapshot':store.latest(),'consumer_observations':observations,
            'input_requests':[],'receiver_effects':[],'shutdown':{'recognition':stopped}}
    save_report(output,report)
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['simulate','replay','simulate-mining'])
    parser.add_argument('--output',type=Path,default=ROOT/'logs/offline-bot/demo')
    parser.add_argument('--ignore-brake',action='store_true')
    parser.add_argument('--stall-after',type=int)
    parser.add_argument('--fault-after',type=int)
    parser.add_argument('--planner-delay',type=float,default=.4)
    parser.add_argument('--speedup',type=float,default=20)
    parser.add_argument('--source-run',default='sol-tylium-live-2321')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--interval',type=float,default=.03)
    parser.add_argument('--profile',choices=['original','restart-20261001'],default='original')
    parser.add_argument('--consumer-delay',type=float,default=.3)
    parser.add_argument('--no-planner', action='store_true')
    parser.add_argument('--scan-missing', action='store_true')
    parser.add_argument('--ineligible', action='store_true')
    parser.add_argument('--mining-ignored', action='store_true')
    parser.add_argument('--reward-missing', action='store_true')
    parser.add_argument('--inventory-conflict', action='store_true')
    parser.add_argument('--mining-observations', action='store_true')
    args=parser.parse_args()
    if args.mode=='simulate-mining':
        from .mining_harness import simulate_mining
        report=simulate_mining(args.output, ignore_brake=args.ignore_brake,
            stall_after=args.stall_after, fault_after=args.fault_after,
            planner_delay=None if args.no_planner else args.planner_delay, speedup=args.speedup,
            scan_missing=args.scan_missing, eligible=not args.ineligible,
            mining_ignored=args.mining_ignored, reward_missing=args.reward_missing,
            inventory_conflict=args.inventory_conflict)
    elif args.mode=='simulate':
        report=simulation(args.output,ignore_brake=args.ignore_brake,stall_after=args.stall_after,
            fault_after=args.fault_after,planner_delay=args.planner_delay,speedup=args.speedup)
    else:
        report=replay(args.output,source_run=args.source_run,limit=args.limit,interval=args.interval,
            profile=args.profile,consumer_delay=args.consumer_delay,mining_observations=args.mining_observations)
    summary={key:plain(report[key]) for key in ['mode','recognition_processed','recognition_end_reason','shutdown']}
    if 'result' in report: summary['result']=plain(report['result'])
    if 'advanced_during_delay' in report: summary['advanced_during_delay']=report['advanced_during_delay']
    summary['report']=str(args.output.resolve()/'report.json')
    print(json.dumps(summary,indent=2))

if __name__=='__main__': main()
