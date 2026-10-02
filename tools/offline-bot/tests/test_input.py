"""Tests prove recording semantics, not operating-system input delivery."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from bsgo_offline.input import InputOverflow, InputRejected, RecordingInputBackend


class RecordingInputTests(unittest.TestCase):
    def backend(self, **kwargs):
        return RecordingInputBackend(session_id="test", layout_id="hud-v1", **kwargs)

    def test_sequence_click_coordinates_and_request_evidence(self):
        backend = self.backend(clock=lambda: 12.5)
        backend.press_key("PageUp", reason="accelerate")
        backend.move_mouse(2559, 1439)
        click = backend.click_mouse(1200, 600, button="right", reason="select",
                                    expected_session="test", expected_layout="hud-v1")
        actions = backend.records()
        self.assertEqual([r.sequence for r in actions], [1, 2, 3, 4])
        self.assertEqual([r.kind for r in actions],
                         ["key_down", "key_up", "mouse_move", "mouse_click"])
        self.assertEqual((click.x, click.y, click.button), (1200, 600, "right"))
        self.assertTrue(all(r.at == 12.5 and r.coordinate_space == "frame" for r in actions))
        self.assertTrue(all(r.evidence == "recorded_request" for r in actions))
        self.assertEqual(backend.drain(), actions)
        self.assertEqual(backend.drain(), ())
        self.assertEqual(backend.records(), actions)

    def test_records_and_metadata_are_immutable_snapshots(self):
        backend = self.backend()
        metadata = {"purpose": "scan"}
        record = backend.click_mouse(1, 2, metadata=metadata)
        metadata["purpose"] = "changed"
        self.assertEqual(record.metadata, (("purpose", "scan"),))
        with self.assertRaises(FrozenInstanceError):
            record.x = 999
        with self.assertRaises(InputRejected):
            backend.click_mouse(1, 2, metadata={"nested": [1]})
        self.assertTrue(backend.canceled)

    def test_hold_release_and_cancel_cleanup(self):
        backend = self.backend()
        backend.key_down("w")
        backend.key_down("Shift")
        self.assertEqual(backend.held_keys, ("Shift", "W"))
        released = backend.cancel(reason="stop")
        self.assertEqual({r.key for r in released}, {"W", "Shift"})
        self.assertTrue(all(r.kind == "key_up" for r in released))
        self.assertTrue(all(dict(r.metadata)["cleanup"] for r in released))
        self.assertEqual(backend.held_keys, ())
        self.assertEqual(backend.cancel(), ())
        with self.assertRaises(InputRejected):
            backend.press_key("PageUp")
        backend.drain()
        backend.new_session("second", layout_id="hud-v2")
        self.assertFalse(backend.canceled)
        backend.press_key("PageDown", expected_session="second", expected_layout="hud-v2")

    def test_release_all_does_not_cancel(self):
        backend = self.backend()
        backend.key_down("A")
        backend.release_all()
        self.assertFalse(backend.canceled)
        backend.press_key("A")
        self.assertEqual(backend.held_keys, ())

    def test_session_and_layout_mismatch_cancel_and_release(self):
        for context in ({"expected_session": "old"}, {"expected_layout": "old"}):
            with self.subTest(context=context):
                backend = self.backend()
                backend.key_down("W")
                with self.assertRaises(InputRejected):
                    backend.click_mouse(10, 10, **context)
                self.assertEqual([r.kind for r in backend.records()], ["key_down", "key_up"])
                self.assertEqual(backend.held_keys, ())
                self.assertTrue(backend.canceled)

    def test_invalid_inputs_release_held_keys(self):
        invalid = [lambda b: b.press_key("not-a-key"),
                   lambda b: b.click_mouse(-1, 20),
                   lambda b: b.click_mouse(2560, 20),
                   lambda b: b.click_mouse(10.0, 20),
                   lambda b: b.click_mouse(True, 20),
                   lambda b: b.click_mouse(10, 20, button="extra"),
                   lambda b: b.press_key("A", expected_session_id="wrong-keyword")]
        for request in invalid:
            backend = self.backend()
            backend.key_down("W")
            with self.assertRaises(InputRejected):
                request(backend)
            self.assertEqual(backend.held_keys, ())
            self.assertEqual(backend.records()[-1].kind, "key_up")

    def test_queue_overflow_is_atomic_and_reserves_cleanup(self):
        backend = self.backend(queue_capacity=2)
        backend.key_down("W")
        with self.assertRaises(InputOverflow):
            backend.press_key("PageUp")
        self.assertTrue(backend.canceled)
        self.assertEqual([(r.kind, r.key) for r in backend.records()],
                         [("key_down", "W"), ("key_up", "W")])
        self.assertEqual(backend.held_keys, ())
        self.assertLessEqual(len(backend.drain()), 2 + len(backend.supported_keys))

    def test_full_queue_cancellation_keeps_release_requests(self):
        backend = self.backend(queue_capacity=1)
        backend.key_down("W")
        backend.cancel()
        self.assertEqual([r.kind for r in backend.drain()], ["key_up"])
        self.assertEqual(backend.discarded_sequences, (1,))

    def test_cancel_preempts_queued_acceleration_and_click_but_preserves_history(self):
        backend = self.backend()
        backend.press_key("PageUp")
        backend.click_mouse(1200, 600)
        before = backend.records()
        backend.cancel()
        self.assertEqual(backend.records(), before)
        self.assertEqual(backend.discarded_sequences, (1, 2, 3))
        self.assertEqual(backend.drain(), ())
        self.assertIsNone(backend.get_action(timeout=0))

    def test_cancel_releases_key_already_dequeued_from_press_pair(self):
        backend = self.backend()
        down, up = backend.press_key("PageUp")
        self.assertEqual(backend.get_action(timeout=0), down)
        cleanup = backend.cancel()
        self.assertEqual(backend.discarded_sequences, (up.sequence,))
        self.assertEqual(len(cleanup), 1)
        self.assertEqual((cleanup[0].kind, cleanup[0].key), ("key_up", "PageUp"))
        self.assertEqual(backend.cancel(), ())
        self.assertEqual(backend.drain(), cleanup)
        backend.new_session("second")

    def test_cleanup_stop_is_delivered_after_cancel_and_is_idempotent(self):
        backend = self.backend(queue_capacity=2, record_capacity=2)
        backend.press_key("PageUp")
        backend.cancel()
        stop = backend.request_cleanup_stop(reason="coordinator canceled")
        self.assertEqual(backend.request_cleanup_stop(), stop)
        self.assertEqual([(r.kind, r.key) for r in backend.drain()],
                         [("key_down", "PageDown"), ("key_up", "PageDown")])
        self.assertTrue(all(dict(r.metadata).get("cleanup") is True for r in stop))
        self.assertEqual(backend.request_cleanup_stop(), stop)
        self.assertEqual(backend.drain(), ())
        self.assertTrue(backend.canceled)
        with self.assertRaises(InputRejected):
            backend.press_key("PageUp")

    def test_cleanup_channel_cannot_record_acceleration_or_accept_stale_context(self):
        for kwargs in ({"key": "PageUp"}, {"expected_session": "old"},
                       {"expected_layout": "old"}):
            backend = self.backend()
            with self.assertRaises(InputRejected):
                backend.request_cleanup_stop(**kwargs)
            self.assertTrue(backend.canceled)
            self.assertEqual(backend.records(), ())
        backend = self.backend()
        with self.assertRaises(InputRejected):
            backend.press_key("PageUp", metadata={"cleanup": True})
        self.assertEqual(backend.records(), ())

    def test_receiver_backlog_overflow_preempts_existing_ordinary_requests(self):
        backend = self.backend(queue_capacity=3)
        backend.press_key("PageUp")
        backend.click_mouse(1, 2)
        with self.assertRaises(InputOverflow):
            backend.click_mouse(3, 4)
        self.assertEqual(backend.discarded_sequences, (1, 2, 3))
        self.assertEqual(backend.drain(), ())
        backend.request_cleanup_stop()
        self.assertEqual([r.key for r in backend.drain()], ["PageDown", "PageDown"])

    def test_repeated_sessions_keep_rolling_cleanup_history_and_lifetime_totals(self):
        backend = self.backend(record_capacity=1)
        for index in range(100):
            backend.request_cleanup_stop()
            backend.drain()
            backend.new_session(f"session-{index}")
        self.assertEqual(len(backend.records()), 1)
        self.assertEqual(backend.records()[0].sequence, 200)
        self.assertEqual(backend.history_stats()["total_records"], 200)
        self.assertEqual(backend.history_stats()["evicted_records"], 199)
        backend.new_session("fresh", clear_records=True)
        self.assertEqual(backend.discarded_sequences, ())
        self.assertEqual(backend.history_stats()["total_records"], 200)
        self.assertEqual(backend.history_stats()["evicted_records"], 200)
        self.assertIsNone(backend.history_stats()["first_retained_sequence"])
        backend.press_key("A")
        self.assertEqual(backend.records()[0].sequence, 202)

    def test_rolling_history_never_cancels_and_clear_does_not_reset_sequence(self):
        backend = self.backend(record_capacity=1)
        backend.key_down("W")
        backend.drain()
        backend.key_up("W")
        self.assertEqual(len(backend.records()), 1)
        self.assertEqual(backend.records()[0].kind, "key_up")
        self.assertFalse(backend.canceled)
        backend.drain()
        backend.new_session("second", clear_records=True)
        self.assertEqual(backend.records(), ())
        self.assertEqual(backend.key_down("A").sequence, 3)
        self.assertEqual(backend.history_stats()["total_records"], 3)

    def test_long_run_exceeds_old_lifetime_cap_with_bounded_memory(self):
        backend = self.backend(record_capacity=16, queue_capacity=2)
        for index in range(6000):
            records = backend.press_key("A")
            self.assertEqual(backend.drain(), records)
            self.assertFalse(backend.canceled)
        stats = backend.history_stats()
        self.assertEqual(stats["total_records"], 12000)
        self.assertEqual(stats["retained_records"], 16)
        self.assertEqual(stats["evicted_records"], 11984)
        self.assertEqual(stats["first_retained_sequence"], 11985)
        self.assertEqual(stats["last_retained_sequence"], 12000)
        self.assertLessEqual(len(backend._dequeued_sequences), 16)
        self.assertEqual(backend._discarded_sequences, set())

    def test_default_64000_history_rolls_during_paced_70000_record_run(self):
        backend = self.backend(queue_capacity=2)
        self.assertEqual(backend.record_capacity, 64000)
        for index in range(35000):
            records = backend.press_key("A")
            self.assertEqual(backend.drain(), records)
            if index % 1000 == 0:
                # Yield between batches; pending ordinary input is always drained.
                time.sleep(0)
        self.assertFalse(backend.canceled)
        self.assertEqual(backend.history_stats()["total_records"], 70000)
        self.assertEqual(backend.history_stats()["retained_records"], 64000)
        self.assertEqual(backend.history_stats()["evicted_records"], 6000)
        self.assertEqual(backend.history_stats()["first_retained_sequence"], 6001)
        self.assertEqual(backend.history_stats()["last_retained_sequence"], 70000)
        self.assertEqual(len(backend._dequeued_sequences), 64000)
        self.assertNotIn(6000, backend._dequeued_sequences)
        self.assertIn(6001, backend._dequeued_sequences)
        backend.key_down("B")
        backend.get_action(timeout=0)
        pending = backend.move_mouse(100, 100)
        release = backend.cancel(reason="post_64000_rollover_cleanup")
        stop = backend.request_cleanup_stop()
        self.assertEqual(backend.drain(), release + stop)
        self.assertEqual(backend.held_keys, ())
        self.assertEqual(backend._dequeued_held, set())
        self.assertEqual(backend.discarded_sequences, (pending.sequence,))
        self.assertEqual(backend.history_stats()["total_records"], 70005)
        self.assertEqual(backend.history_stats()["retained_records"], 64000)
        self.assertLessEqual(len(backend._discarded_sequences | backend._dequeued_sequences), 64000)

    def test_history_eviction_does_not_remove_pending_requests_or_hold_cleanup(self):
        class Sink:
            def __init__(self):
                self.events = []
            def submit(self, event):
                self.events.append(event)
                return True

        sink = Sink()
        backend = self.backend(record_capacity=1, journal=sink)
        down, up = backend.press_key("PageUp")
        self.assertEqual(backend.get_action(timeout=0), down)
        click = backend.click_mouse(10, 20)
        self.assertEqual(backend.records(), (click,))
        cleanup = backend.cancel()
        self.assertEqual([(r.kind, r.key) for r in cleanup], [("key_up", "PageUp")])
        self.assertEqual(backend.drain(), cleanup)
        self.assertFalse(backend._dequeued_held)
        dispositions = {event["sequence"]: event["disposition"] for event in sink.events
                        if event["event"] == "input_disposition"}
        self.assertEqual(dispositions[down.sequence], "dequeued_for_receiver")
        self.assertEqual(dispositions[up.sequence], "discarded_before_receive")
        self.assertEqual(dispositions[click.sequence], "discarded_before_receive")
        self.assertEqual(dispositions[cleanup[0].sequence], "dequeued_for_receiver")
        self.assertLessEqual(len(backend._dequeued_sequences | backend._discarded_sequences), 1)

    def test_pending_queue_fidelity_independent_of_recent_history_size(self):
        backend = self.backend(record_capacity=1, queue_capacity=4)
        first = backend.press_key("PageUp")
        second = backend.press_key("PageDown")
        self.assertEqual(backend.records(), (second[-1],))
        self.assertEqual(backend.drain(), first + second)
        self.assertFalse(backend.canceled)

    def test_session_cannot_cross_outstanding_queue(self):
        backend = self.backend()
        backend.key_down("W")
        with self.assertRaises(InputRejected):
            backend.new_session("second")
        self.assertEqual(backend.session_id, "test")
        self.assertTrue(backend.canceled)
        self.assertEqual(backend.held_keys, ())
        backend.drain()
        backend.new_session("second")
        with self.assertRaises(InputRejected):
            backend.new_session("third", layout_id="")
        self.assertEqual(backend.session_id, "second")

    def test_clock_failure_still_records_cleanup(self):
        calls = iter((1.0, float("nan")))
        backend = self.backend(clock=lambda: next(calls))
        backend.key_down("W")
        with self.assertRaises(InputRejected):
            backend.click_mouse(1, 2)
        self.assertEqual(backend.held_keys, ())
        self.assertEqual(backend.records()[-1].kind, "key_up")
        self.assertEqual(dict(backend.records()[-1].metadata)["clock_fallback"],
                         "wall_monotonic")

    def test_successful_cleanup_uses_injected_clock_domain(self):
        backend = self.backend(clock=lambda: 20.25)
        backend.key_down("W")
        released = backend.cancel()
        stop = backend.request_cleanup_stop()
        self.assertTrue(all(record.at == 20.25 for record in released + stop))
        self.assertTrue(all("clock_fallback" not in dict(record.metadata)
                            for record in released + stop))

    def test_cleanup_clock_exception_is_annotated(self):
        calls = iter((20.25,))
        backend = self.backend(clock=lambda: next(calls))
        backend.key_down("W")
        released = backend.cancel()
        self.assertEqual(dict(released[0].metadata)["clock_fallback"], "wall_monotonic")
        self.assertEqual(backend.held_keys, ())

    def test_threads_produce_serialized_atomic_key_pairs(self):
        backend = self.backend(queue_capacity=1000)
        threads = [threading.Thread(target=lambda: [backend.press_key("A") for _ in range(20)])
                   for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        records = backend.records()
        self.assertEqual(len(records), 200)
        self.assertEqual([r.sequence for r in records], list(range(1, 201)))
        for index in range(0, 200, 2):
            self.assertEqual([records[index].kind, records[index + 1].kind], ["key_down", "key_up"])

    def test_receiver_wait_and_timeout(self):
        backend = self.backend()
        self.assertIsNone(backend.get_action(timeout=0))
        action = backend.click_mouse(1, 2)
        self.assertEqual(backend.get_action(timeout=0.01), action)
        self.assertIsNone(backend.get_action(timeout=0.01))

    def test_json_export_round_trip(self):
        backend = self.backend()
        backend.press_key("PageUp")
        path = Path(__file__).parent / f"requests-{uuid.uuid4().hex}.jsonl"
        try:
            stats = backend.export_jsonl(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["kind"] for row in rows], ["key_down", "key_up"])
            self.assertTrue(all(row["evidence"] == "recorded_request" for row in rows))
            scope = json.loads(Path(str(path) + ".stats.json").read_text())
            self.assertFalse(scope["complete_lifetime_trace"])
            self.assertEqual(scope["history_stats"], stats)
        finally:
            path.unlink(missing_ok=True)
            Path(str(path) + ".stats.json").unlink(missing_ok=True)

    def test_export_includes_discarded_request_evidence(self):
        backend = self.backend()
        backend.press_key("PageUp")
        backend.cancel()
        backend.request_cleanup_stop()
        backend.drain()
        path = Path(__file__).parent / f"requests-{uuid.uuid4().hex}.jsonl"
        try:
            backend.export_jsonl(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["disposition"] for row in rows],
                             ["discarded_before_receive", "discarded_before_receive",
                              "dequeued_for_receiver", "dequeued_for_receiver"])
        finally:
            path.unlink(missing_ok=True)
            Path(str(path) + ".stats.json").unlink(missing_ok=True)

    def test_export_is_recent_only_and_statistics_disclose_eviction(self):
        backend = self.backend(record_capacity=1)
        backend.press_key("A")
        backend.drain()
        path = Path(__file__).parent / f"requests-{uuid.uuid4().hex}.jsonl"
        try:
            stats = backend.export_jsonl(path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sequence"], 2)
            self.assertEqual(rows[0]["disposition"], "dequeued_for_receiver")
            self.assertEqual(stats["total_records"], 2)
            self.assertEqual(stats["evicted_records"], 1)
        finally:
            path.unlink(missing_ok=True)
            Path(str(path) + ".stats.json").unlink(missing_ok=True)

    def test_journal_receives_all_requests_and_dispositions_despite_history_eviction(self):
        class Sink:
            def __init__(self):
                self.events = []
            def submit(self, event):
                self.events.append(event)
                return True
        sink = Sink()
        backend = self.backend(record_capacity=2, journal=sink)
        for _ in range(20):
            backend.press_key("A")
            backend.drain()
        requests = [event["record"]["sequence"] for event in sink.events
                    if event["event"] == "input_request"]
        dispositions = [event["sequence"] for event in sink.events
                        if event["event"] == "input_disposition"]
        self.assertEqual(requests, list(range(1, 41)))
        self.assertEqual(dispositions, list(range(1, 41)))
        self.assertEqual(backend.history_stats()["logging_gaps"], 0)
        self.assertEqual(backend.history_stats()["pending_journal_events"], 0)
        self.assertEqual(len(backend.records()), 2)

    def test_journal_failure_or_drop_is_telemetry_gap_and_never_cancels(self):
        class Sink:
            def __init__(self, raises):
                self.raises = raises
            def submit(self, event):
                if self.raises:
                    raise OSError("disk telemetry unavailable")
                return False
        for raises in (True, False):
            backend = self.backend(record_capacity=1, journal=Sink(raises))
            for _ in range(10):
                backend.press_key("A")
                backend.drain()
            self.assertFalse(backend.canceled)
            self.assertEqual(backend.history_stats()["submit_failures"], 40)
            self.assertEqual(backend.history_stats()["logging_gaps"], 40)
            backend.key_down("B")
            backend.cancel()
            self.assertEqual(backend.held_keys, ())

    def test_staged_journal_backlog_is_bounded_and_cleanup_ignores_logging_gaps(self):
        class Sink:
            def __init__(self):
                self.events = []
            def submit(self, event):
                self.events.append(event)
                return True

        sink = Sink()
        backend = self.backend(record_capacity=4, queue_capacity=2, journal=sink)
        backend._journal_flush_lock.acquire()
        try:
            for _ in range(200):
                backend.press_key("A")
                backend.drain()
            self.assertFalse(backend.canceled)
            self.assertEqual(len(backend._journal_pending), backend._journal_pending_capacity)
            self.assertGreater(backend.history_stats()["logging_gaps"], 0)
            backend.key_down("B")
            released = backend.cancel()
            self.assertEqual([(record.kind, record.key) for record in released], [("key_up", "B")])
            self.assertEqual(backend.held_keys, ())
        finally:
            backend._journal_flush_lock.release()
        backend.flush_journal()
        self.assertEqual(backend.history_stats()["pending_journal_events"], 0)
        self.assertEqual(len(sink.events), backend._journal_pending_capacity)
        self.assertEqual(backend.history_stats()["submit_failures"], 0)
        self.assertGreater(backend.history_stats()["logging_gaps"], 0)

    def test_sink_submit_is_outside_backend_lock_and_other_inputs_continue(self):
        entered, unblock = threading.Event(), threading.Event()

        class SlowSink:
            def submit(self, event):
                entered.set()
                unblock.wait(timeout=2)
                return True

        backend = self.backend(journal=SlowSink())
        submitter = threading.Thread(target=backend.press_key, args=("A",))
        submitter.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            # A concurrently blocked sink cannot lock out queue/control cleanup.
            completed = threading.Event()
            def cancel():
                backend.cancel()
                completed.set()
            caller = threading.Thread(target=cancel)
            caller.start()
            self.assertTrue(completed.wait(timeout=.5))
            caller.join(timeout=1)
            self.assertTrue(backend.canceled)
        finally:
            unblock.set()
            submitter.join(timeout=2)
        self.assertFalse(submitter.is_alive())
        backend.flush_journal()
        self.assertEqual(backend.history_stats()["logging_gaps"], 0)

    def test_slow_export_does_not_hold_input_lock(self):
        backend = self.backend()
        backend.press_key("A")
        writing, unblock = threading.Event(), threading.Event()

        class SlowFile:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def write(self, value):
                writing.set()
                if not unblock.wait(timeout=2):
                    raise RuntimeError("test export timed out")

        with patch.object(Path, "open", return_value=SlowFile()):
            exporter = threading.Thread(target=backend.export_jsonl, args=("ignored",))
            exporter.start()
            try:
                self.assertTrue(writing.wait(timeout=1))
                completed = threading.Event()
                def record():
                    backend.press_key("B")
                    completed.set()
                caller = threading.Thread(target=record)
                caller.start()
                self.assertTrue(completed.wait(timeout=0.5))
                caller.join(timeout=1)
            finally:
                unblock.set()
                exporter.join(timeout=2)
            self.assertFalse(exporter.is_alive())


if __name__ == "__main__":
    unittest.main()
