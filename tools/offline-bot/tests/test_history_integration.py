"""Rolling-history integration soak: real disk journal, fake native API only.

Run directly with --output to save a larger evidence run. Every native operation
uses FakeNativeApi; no Windows API, desktop input, receiver, or game is involved.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import time
import unittest
import uuid

from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.native_input import NativeInputDispatcher
from bsgo_offline.trace import TraceJournal, iter_trace_events
from test_native_input import FakeNativeApi


ROOT = Path(__file__).resolve().parents[3]


@contextmanager
def scratch_directory():
    # Inherit workspace ACL instead of TemporaryDirectory's Windows mode0700.
    base = Path(__file__).resolve().parent
    directory = base / ("history-scratch-" + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        checked = directory.resolve()
        if (checked.parent != base or not checked.is_relative_to(ROOT)
                or not checked.name.startswith("history-scratch-")):
            raise RuntimeError("Unexpected history test cleanup target")
        shutil.rmtree(checked)


def run_soak(output: Path, *, cycles: int = 24000, batch_cycles: int = 25,
             history_capacity: int = 64000, native_history_capacity: int = 64000,
             package_capacity: int = 64000) -> dict:
    if type(cycles) is not int or cycles < 1 or type(batch_cycles) is not int or batch_cycles < 1:
        raise ValueError("cycles and batch_cycles must be positive integers")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    journal = TraceJournal(output, package_capacity=package_capacity)
    backend = RecordingInputBackend(session_id="history-soak", layout_id="fake-native",
                                    width=640, height=480, queue_capacity=128,
                                    record_capacity=history_capacity, journal=journal)
    api = FakeNativeApi()
    dispatcher = NativeInputDispatcher(backend, 100, 200, expected_size=(640, 480),
                                       expected_origin=(20, 40), history_capacity=native_history_capacity,
                                       journal=journal, _api=api)
    observed_maximum = {"recent_requests": 0, "recent_deliveries": 0,
                        "disposition_entries": 0, "fake_native_batches": 0,
                        "sampled_journal_pending": 0}
    start = time.monotonic()
    completed = False

    def consume():
        for record in backend.drain():
            delivery = dispatcher.dispatch(record)
            if delivery.outcome != "injected" or delivery.inserted_count != delivery.requested_count:
                raise AssertionError(f"unexpected fake delivery: {delivery}")

    def pace():
        # Let the asynchronous real writer catch up at finite batch boundaries.
        # This is a retention soak, not a producer-overload experiment.
        deadline = time.monotonic() + 5
        while journal.stats()["pending"] > 128:
            if time.monotonic() >= deadline:
                raise TimeoutError("trace writer did not catch up")
            time.sleep(.001)

    def observe_bounds():
        observed_maximum["recent_requests"] = max(observed_maximum["recent_requests"],
                                                   backend.history_stats()['retained_records'])
        observed_maximum["recent_deliveries"] = max(observed_maximum["recent_deliveries"],
                                                     dispatcher.delivery_stats()['retained'])
        observed_maximum["disposition_entries"] = max(observed_maximum["disposition_entries"],
            len(backend._discarded_sequences) + len(backend._dequeued_sequences))
        observed_maximum["fake_native_batches"] = max(observed_maximum["fake_native_batches"],
                                                       len(api.sent))
        observed_maximum["sampled_journal_pending"] = max(
            observed_maximum["sampled_journal_pending"], journal.stats()["pending"])

    try:
        for cycle in range(cycles):
            backend.press_key("A", reason="retention_soak_key")
            backend.click_mouse(120 + cycle % 20, 160, reason="retention_soak_click")
            consume()
            if backend.canceled or dispatcher.error:
                raise AssertionError("history eviction stopped ordinary control")
            if (cycle + 1) % batch_cycles == 0:
                observe_bounds()
                api.sent.clear()
                pace()
        observe_bounds()
        api.sent.clear()
        before_cleanup = {"input": backend.history_stats(),
                          "native": dispatcher.delivery_stats(),
                          "canceled": backend.canceled, "native_error": dispatcher.error}

        # A hold inserted after many evictions must still be released. Intentional
        # cancellation here is separate from the uninterrupted ordinary soak.
        held = backend.key_down("B", reason="post_rollover_hold")
        consume()
        if dispatcher.injected_held_keys != ("B",):
            raise AssertionError("post-rollover fake hold was not tracked")
        releases = backend.cancel(reason="intentional_post_rollover_cleanup")
        stop_pair = backend.request_cleanup_stop(reason="intentional_post_rollover_stop")
        consume()
        if dispatcher.injected_held_keys or dispatcher.injected_held_buttons or backend.held_keys:
            raise AssertionError("post-rollover cleanup leaked a tracked hold")
        if dispatcher.error:
            raise AssertionError(dispatcher.error)
        observe_bounds()
        backend.flush_journal()
        if not dispatcher.stop():
            raise AssertionError("fake native dispatcher did not shut down cleanly")
        recent = backend.export_jsonl(output / "recent-input.jsonl")
        if not journal.close(timeout=5):
            raise AssertionError("journal writer did not stop")
        journal_stats = journal.stats()
        if not journal_stats["write_complete"] or journal_stats["dropped"] or journal_stats["error"]:
            raise AssertionError(f"incomplete trace: {journal_stats}")

        kinds = Counter()
        requests, dispositions, deliveries = [], [], []
        native_outcomes = Counter()
        # Completed packages live in ZIPs, while the partial package stays raw.
        for row in iter_trace_events(output,journal.run_id):
            payload = row["payload"]
            kinds[payload["event"]] += 1
            if payload["event"] == "input_request":
                requests.append(payload["record"]["sequence"])
            elif payload["event"] == "input_disposition":
                if payload["disposition"] != "dequeued_for_receiver":
                    raise AssertionError("ordinary requests were unexpectedly discarded")
                dispositions.append(payload["sequence"])
            elif payload["event"] == "native_delivery":
                delivery = payload["record"]
                deliveries.append(delivery["request_sequence"])
                native_outcomes[delivery["outcome"]] += 1
        expected = list(range(1, cycles * 3 + 5))
        if requests != expected or dispositions != expected or deliveries != expected:
            raise AssertionError("JSONL lifetime request/disposition/delivery sequences are incomplete")
        if native_outcomes != Counter({"injected": len(expected)}):
            raise AssertionError(f"native outcome gaps: {native_outcomes}")
        if (observed_maximum["recent_requests"] > history_capacity or
                observed_maximum["recent_deliveries"] > native_history_capacity):
            raise AssertionError("recent record bounds exceeded")
        if observed_maximum["disposition_entries"] > history_capacity:
            raise AssertionError("drained disposition metadata was not bounded to recent history")
        if recent["logging_gaps"] or dispatcher.delivery_stats()["logging_gaps"]:
            raise AssertionError("component journal submission had logging gaps")

        report = {"passed": True,
            "boundary": "Real rotating disk journal with FakeNativeApi only; no desktop input or receiver observation.",
            "cycles": cycles, "ordinary_requests": cycles * 3,
            "batch_cycles": batch_cycles, "wall_seconds": time.monotonic() - start,
            "before_intentional_cleanup": before_cleanup,
            "cleanup": {"intentional_cancel": True, "held_request_sequence": held.sequence,
                        "release_sequences": [record.sequence for record in releases],
                        "stop_sequences": [record.sequence for record in stop_pair],
                        "injected_held_keys": dispatcher.injected_held_keys,
                        "injected_held_buttons": dispatcher.injected_held_buttons},
            "recent_input": recent, "recent_native": dispatcher.delivery_stats(),
            "observed_bounds": observed_maximum,
            "jsonl_counts": dict(kinds), "jsonl_native_outcomes": dict(native_outcomes),
            "audited_segment_files": journal_stats['segment_count'],
            "audited_archive_files": journal_stats['archive_count'],
            "jsonl_contiguous_sequence_range": [expected[0], expected[-1]],
            "journal": journal_stats}
        (output / "soak-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        completed = True
        return report
    finally:
        if not completed:
            dispatcher.stop()
            backend.flush_journal()
            journal.close(timeout=5)


class HistoryIntegrationTests(unittest.TestCase):
    def test_full_journal_survives_eviction_and_cleanup_beyond_old_lifetime_cap(self):
        with scratch_directory() as output:
            report = run_soak(output, cycles=1600,history_capacity=16,
                              native_history_capacity=32,package_capacity=1000)
        self.assertEqual(report["ordinary_requests"], 4800)
        self.assertEqual(report["jsonl_counts"], {"input_request": 4804,
                                                "input_disposition": 4804,
                                                "native_delivery": 4804})
        self.assertFalse(report["before_intentional_cleanup"]["canceled"])
        self.assertIsNone(report["before_intentional_cleanup"]["native_error"])
        self.assertEqual(report["recent_input"]["retained_records"], 16)
        self.assertEqual(report["recent_native"]["retained"], 32)
        self.assertEqual(report['audited_archive_files'],14)


if __name__ == "__main__":
    import sys
    if "--output" in sys.argv:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--cycles", type=int, default=24000)
        parser.add_argument("--batch-cycles", type=int, default=25)
        args = parser.parse_args()
        result = run_soak(args.output, cycles=args.cycles, batch_cycles=args.batch_cycles)
        print(json.dumps({key: result[key] for key in ("passed", "ordinary_requests", "wall_seconds",
                                                       "jsonl_counts", "observed_bounds")}, indent=2))
    else:
        unittest.main()
