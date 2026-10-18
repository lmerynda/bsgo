"""Telemetry durability/backpressure tests; no window or native input access."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from threading import Event
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from bsgo_offline.trace import TraceJournal, _io_path, iter_trace_events


@contextmanager
def scratch_directory():
    # Inherit workspace ACL instead of TemporaryDirectory's mode0700 on Windows.
    base = Path(__file__).resolve().parent
    directory = base / ("trace-scratch-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        checked = directory.resolve()
        if checked.parent != base or not checked.name.startswith("trace-scratch-"):
            raise RuntimeError("Unexpected telemetry test cleanup path")
        shutil.rmtree(_io_path(checked))


class ControlledStream:
    def __init__(self, *, fail=False):
        self.entered = Event()
        self.release = Event()
        self.fail = fail
        self.lines = []
        self.closed = False

    def write(self, data):
        self.entered.set()
        if not self.release.wait(5.):
            raise TimeoutError("Test did not release the simulated blocked disk")
        if self.fail:
            raise OSError("injected disk write failure")
        self.lines.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


@contextmanager
def fake_binary_writer(stream, directory):
    original = Path.open
    def open_file(path, mode="r", *args, **kwargs):
        if path.suffix == ".jsonl" and mode == "xb" and path.parent.samefile(directory):
            return stream
        return original(path, mode, *args, **kwargs)
    with patch.object(Path, "open", open_file):
        yield


class TraceJournalTests(unittest.TestCase):
    def read_records(self, directory, stats):
        return [json.loads(line) for name in stats["segments"]
                for line in (directory / name).read_text(encoding="utf-8").splitlines()]

    def test_long_output_path_preserves_archive_active_events_and_summary(self):
        with scratch_directory() as directory:
            nested = directory
            while len(str(nested)) < 300:
                nested /= "nested-" + "x" * 60
            journal = TraceJournal(nested, package_capacity=2)
            try:
                for ordinal in range(3):
                    self.assertTrue(journal.submit({"event": "sample", "ordinal": ordinal}))
            finally:
                self.assertTrue(journal.close(2.))
            stats = journal.stats()
            self.assertIsNone(stats["error"])
            self.assertTrue(stats["write_complete"])
            self.assertEqual(stats["archive_count"], 1)
            self.assertEqual(stats["archived_events"], 2)
            self.assertEqual(stats["active_package_events"], 1)
            records = list(iter_trace_events(nested, journal.run_id))
            self.assertEqual([row["journal_sequence"] for row in records], [1, 2, 3])
            self.assertEqual([row["payload"]["ordinal"] for row in records], [0, 1, 2])
            summary = json.loads(_io_path(nested / f"trace-{journal.run_id}-summary.json")
                                 .read_text(encoding="utf-8"))
            self.assertTrue(summary["write_complete"])
            self.assertEqual(summary["written"], 3)

    def test_rotation_preserves_order_and_fresh_run_namespace(self):
        with scratch_directory() as directory:
            journal = TraceJournal(directory, queue_capacity=100, max_file_bytes=320)
            try:
                for ordinal in range(25):
                    self.assertTrue(journal.submit({"event": "sample", "ordinal": ordinal,
                                                   "label": "saved observation"}))
            finally:
                self.assertTrue(journal.close(2.))
            first = journal.stats()
            self.assertTrue(first["write_complete"])
            self.assertEqual((first["accepted"], first["written"], first["dropped"]), (25, 25, 0))
            self.assertGreater(len(first["segments"]), 1)
            records = self.read_records(directory, first)
            self.assertEqual([row["journal_sequence"] for row in records], list(range(1, 26)))
            self.assertEqual([row["payload"]["ordinal"] for row in records], list(range(25)))
            self.assertTrue(all(isinstance(row["recorded_at"], (int, float)) for row in records))
            self.assertTrue(all((directory / name).stat().st_size <= 320 for name in first["segments"]))
            archived = {name: (directory / name).read_bytes() for name in first["segments"]}
            second = TraceJournal(directory)
            try:
                self.assertTrue(second.submit({"event": "second_run"}))
            finally:
                self.assertTrue(second.close(2.))
            following = second.stats()
            self.assertNotEqual(first["run_id"], following["run_id"])
            self.assertFalse(set(first["segments"]) & set(following["segments"]))
            self.assertTrue(all(first["run_id"] in name for name in first["segments"]))
            self.assertTrue(all(following["run_id"] in name for name in following["segments"]))
            self.assertEqual(self.read_records(directory, following)[0]["journal_sequence"], 1)
            self.assertEqual({name: (directory / name).read_bytes() for name in archived}, archived)
            summary = json.loads((directory / f"trace-{first['run_id']}-summary.json").read_text())
            self.assertEqual(summary["written"], 25)
            self.assertTrue(summary["write_complete"])

    def test_submitted_payload_is_copied_before_writer_runs(self):
        with scratch_directory() as directory:
            stream = ControlledStream()
            with fake_binary_writer(stream, directory):
                journal = TraceJournal(directory)
                event = {"event": "nested", "values": [{"distance": 1416}], "tags": ["original"]}
                try:
                    self.assertTrue(journal.submit(event))
                    self.assertTrue(stream.entered.wait(1.))
                    event["values"][0]["distance"] = 999
                    event["tags"].append("changed")
                    stream.release.set()
                    self.assertTrue(journal.close(2.))
                finally:
                    stream.release.set()
                    journal.close(2.)
                payload = json.loads(stream.lines[0])["payload"]
                self.assertEqual(payload["values"], [{"distance": 1416}])
                self.assertEqual(payload["tags"], ["original"])

    def test_segment_metadata_is_bounded_while_lifetime_files_remain_readable(self):
        with scratch_directory() as directory:
            journal = TraceJournal(directory, queue_capacity=200, max_file_bytes=180)
            try:
                for ordinal in range(140):
                    self.assertTrue(journal.submit({"event": "many_segments", "ordinal": ordinal,
                                                   "padding": "x" * 40}))
            finally:
                self.assertTrue(journal.close(2.))
            stats = journal.stats()
            files = sorted(directory.glob(f"trace-{journal.run_id}-*.jsonl"))
            self.assertEqual(len(files), 140)
            self.assertEqual(stats["segment_count"], 140)
            self.assertEqual(stats["segments_scope"], "recent_segment_names")
            self.assertEqual(len(stats["segments"]), 128)
            self.assertEqual(stats["first_segment"], files[0].name)
            self.assertEqual(stats["last_segment"], files[-1].name)
            self.assertNotIn(files[0].name, stats["segments"])
            records = [json.loads(line) for path in files
                       for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["payload"]["ordinal"] for row in records], list(range(140)))
            self.assertTrue(stats["write_complete"])

    def test_blocked_disk_has_bounded_backlog_nonblocking_submit_and_bounded_close(self):
        with scratch_directory() as directory:
            stream = ControlledStream()
            with fake_binary_writer(stream, directory):
                journal = TraceJournal(directory, queue_capacity=2)
                try:
                    self.assertTrue(journal.submit({"event": "first"}))
                    self.assertTrue(stream.entered.wait(1.))
                    self.assertTrue(journal.submit({"event": "second"}))
                    self.assertTrue(journal.submit({"event": "third"}))
                    started = time.monotonic()
                    for _ in range(20):
                        self.assertFalse(journal.submit({"event": "overflow"}))
                    self.assertLess(time.monotonic() - started, .5)
                    self.assertEqual(journal.stats()["pending"], 2)
                    started = time.monotonic()
                    self.assertFalse(journal.close(.02))
                    self.assertLess(time.monotonic() - started, .5)
                    self.assertFalse(journal.submit({"event": "after_close"}))
                    self.assertEqual(journal.stats()["pending"], 2)
                    stream.release.set()
                    self.assertTrue(journal.close(2.))
                finally:
                    stream.release.set()
                    journal.close(2.)
                stats = journal.stats()
                self.assertEqual((stats["submitted"], stats["accepted"], stats["written"], stats["dropped"]),
                                 (24, 3, 3, 21))
                self.assertEqual(stats["pending"], 0)
                self.assertTrue(stats["closed"])
                self.assertFalse(stats["write_complete"])
                self.assertEqual([json.loads(line)["journal_sequence"] for line in stream.lines], [1, 2, 3])

    def test_disk_failure_is_visible_and_future_submissions_are_rejected(self):
        with scratch_directory() as directory:
            stream = ControlledStream(fail=True)
            stream.release.set()
            with fake_binary_writer(stream, directory):
                journal = TraceJournal(directory)
                try:
                    self.assertTrue(journal.submit({"event": "cannot_persist"}))
                finally:
                    self.assertTrue(journal.close(2.))
                stats = journal.stats()
                self.assertIn("injected disk write failure", stats["error"])
                self.assertEqual((stats["accepted"], stats["written"]), (1, 0))
                self.assertFalse(stats["write_complete"])
                self.assertFalse(journal.submit({"event": "after_failure"}))
                self.assertEqual(journal.stats()["dropped"], 1)
                self.assertTrue(stream.closed)

    def test_invalid_nonfinite_and_oversized_events_are_rejected_with_sequence_gaps(self):
        with scratch_directory() as directory:
            journal = TraceJournal(directory, max_event_bytes=64)
            circular = {}
            circular["self"] = circular
            invalid = [None, [], {"number": float("nan")}, {"number": float("inf")},
                       {"opaque": object()}, circular, {"text": "x" * 65}, {"text": "界" * 25}]
            try:
                self.assertTrue(journal.submit({"event": "before"}))
                for event in invalid:
                    self.assertFalse(journal.submit(event))
                self.assertTrue(journal.submit({"event": "after"}))
            finally:
                self.assertTrue(journal.close(2.))
            stats = journal.stats()
            self.assertEqual((stats["submitted"], stats["accepted"], stats["written"], stats["dropped"]),
                             (10, 2, 2, 8))
            self.assertFalse(stats["write_complete"])
            self.assertIsNone(stats["error"])
            self.assertEqual([row["journal_sequence"] for row in self.read_records(directory, stats)], [1, 10])

    def test_empty_close_and_configuration_validation(self):
        with scratch_directory() as directory:
            for option in ("queue_capacity", "max_file_bytes", "max_event_bytes", "package_capacity"):
                for invalid in (0, -1, True, 1.5):
                    with self.assertRaises(ValueError):
                        TraceJournal(directory, **{option: invalid})
            journal = TraceJournal(directory)
            for invalid in (-1, float("nan"), float("inf"), True):
                with self.assertRaises(ValueError):
                    journal.close(invalid)
            self.assertTrue(journal.close(2.))
            self.assertTrue(journal.close(2.))
            self.assertTrue(journal.stats()["write_complete"])
            self.assertFalse(journal.submit({"event": "after_close"}))
            self.assertFalse(journal.stats()["write_complete"])


if __name__ == "__main__":
    unittest.main()
