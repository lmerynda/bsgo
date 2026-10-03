"""Recording-only threaded integration harness for a complete mining cycle."""
from pathlib import Path
import hashlib
import subprocess
import time

from .artifacts import save_report
from .execution import SYNTHETIC_BINDINGS
from .input import RecordingInputBackend
from .mining_simulation import MiningReceiver, MiningSource
from .mission import MiningCoordinator, RouteAssessment
from .planning import AdvicePlanner
from .recognition import RecognitionWorker
from .simulation import SimulationClock, recognize_synthetic
from .state import SnapshotStore
from .trace import TraceJournal


def simulate_mining(output, *, ignore_brake=False, stall_after=None, fault_after=None,
                    planner_delay=None, speedup=20, scan_missing=False, eligible=True,
                    mining_ignored=False, reward_missing=False, inventory_conflict=False):
    """Only fake surfaces are constructed here. No fallback to native adapters."""
    clock = SimulationClock(speedup)
    store = SnapshotStore()
    journal = TraceJournal(Path(output) / "trace")
    backend = RecordingInputBackend(session_id="synthetic-mining-1",
                                    layout_id="synthetic-original", clock=clock, journal=journal)
    receiver = MiningReceiver(backend, clock, ignore_brake=ignore_brake, scan_missing=scan_missing,
        eligible=eligible, mining_ignored=mining_ignored, reward_missing=reward_missing,
        inventory_conflict=inventory_conflict, journal=journal)
    source = MiningSource(receiver, stall_after=stall_after, fault_after=fault_after)
    worker = RecognitionWorker(source, recognize_synthetic, store, clock=clock)
    planner = AdvicePlanner(planner_delay) if planner_delay is not None else None
    coordinator = None
    receiver.start()
    try:
        worker.start()
        first = store.wait_for_sequence(0, 2)
        if first is None:
            raise RuntimeError("synthetic_recognition_unavailable")
        route = RouteAssessment(first.context, 1, first.acquired_at, first.acquired_at + 120, "synthetic")
        coordinator = MiningCoordinator(store, backend, clock=clock, route=route,
            equipment={"configured": True, "resource": "Water"}, bindings=SYNTHETIC_BINDINGS,
            planner=planner, journal=journal)
        result = coordinator.run(real_timeout=12)
    finally:
        backend.cancel(reason="harness_shutdown")
        # Allow the independent receiver to consume reserved release requests.
        drain_deadline = time.monotonic() + .5
        while backend.pending_requests and time.monotonic() < drain_deadline:
            time.sleep(.005)
        worker.stop()
        recognition_stopped = worker.join(2)
        receiver_stopped = receiver.stop(2)
        planner_stopped = planner.close() if planner else True
        backend.flush_journal()
        journal_stopped = journal.close()
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                  timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "unknown"
    source_digest = hashlib.sha256()
    for module in sorted(Path(__file__).resolve().parent.glob("*.py")):
        source_digest.update(module.name.encode("utf-8"))
        source_digest.update(module.read_bytes())
    report = {"mode": "synthetic_mining", "physics": "invented; not measured BSGO dynamics",
        "runtime_source_sha256": source_digest.hexdigest(),
        "code_revision": revision, "working_tree": "source edits may be uncommitted",
        "configuration": {"speedup": speedup, "planner_delay_real_seconds": planner_delay,
            "ignore_brake": ignore_brake, "stall_after": stall_after, "fault_after": fault_after,
            "scan_missing": scan_missing, "eligible": eligible, "mining_ignored": mining_ignored,
            "reward_missing": reward_missing, "inventory_conflict": inventory_conflict},
        "result": result, "status": coordinator.read_status(),
        "transitions": tuple(coordinator.transitions), "transitions_total": coordinator.transitions_total,
        "transitions_evicted": coordinator.transitions_total - len(coordinator.transitions),
        "action_statuses": coordinator.executor.statuses(),
        "observation_events": store.read(), "latest_snapshot": store.latest(),
        "selection_track": coordinator.tracker.track,
        "recognition_processed": worker.processed, "recognition_end_reason": worker.end_reason,
        "recognition_error": worker.error, "input_requests": backend.records(),
        "discarded_sequences": backend.discarded_sequences, "receiver_effects": receiver.observed_effects(),
        "input_history_stats": backend.history_stats(), "receiver_telemetry_stats": receiver.telemetry_stats(),
        "trace_stats": journal.stats(), "journal_dropped": coordinator.journal_dropped,
        "shutdown": {"recognition": recognition_stopped, "receiver": receiver_stopped,
                     "planner": planner_stopped, "trace_writer": journal_stopped}}
    report["complete_event_trace"] = (all(report["shutdown"].values())
        and report["trace_stats"]["write_complete"] and not coordinator.journal_dropped
        and not backend.history_stats()["logging_gaps"] and not receiver.journal_dropped)
    save_report(output, report)
    backend.export_jsonl(Path(output) / "input.jsonl")
    return report
