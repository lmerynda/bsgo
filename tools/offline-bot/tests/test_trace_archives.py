"""Archive preservation tests using small telemetry packages, never native input."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from bsgo_offline import trace


@contextmanager
def scratch_directory():
    base = Path(__file__).resolve().parent
    directory = base / ("trace-archives-scratch-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        checked = directory.resolve()
        if checked.parent != base or not checked.name.startswith("trace-archives-scratch-"):
            raise RuntimeError("Unexpected archive test cleanup target")
        shutil.rmtree(trace._io_path(checked))


class TraceArchiveTests(unittest.TestCase):
    def archives(self, directory, journal):
        return sorted(directory.glob(f"trace-{journal.run_id}-package-*.zip"))

    def raw_records(self, directory, journal):
        return [json.loads(line) for path in sorted(directory.glob(f"trace-{journal.run_id}-*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines()]

    def inspect_archive(self, path, run_id, package_index):
        with zipfile.ZipFile(path) as archive:
            self.assertIsNone(archive.testzip())
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["run_id"], run_id)
            self.assertEqual(manifest["package_index"], package_index)
            names = [segment["name"] for segment in manifest["segments"]]
            self.assertEqual(set(archive.namelist()), {"manifest.json", *names})
            self.assertEqual(len(archive.namelist()), 1 + len(names))
            records = []
            for segment in manifest["segments"]:
                info = archive.getinfo(segment["name"])
                self.assertEqual(info.file_size, segment["bytes"])
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                records.extend(json.loads(line) for line in archive.read(segment["name"]).splitlines())
            self.assertEqual(manifest["event_count"], len(records))
            self.assertEqual(manifest["first_journal_sequence"], records[0]["journal_sequence"])
            self.assertEqual(manifest["last_journal_sequence"], records[-1]["journal_sequence"])
            return manifest, records

    def test_multiple_full_packages_and_final_partial_preserve_complete_order(self):
        for capacity, total in ((3, 8), (5, 12)):
            with self.subTest(capacity=capacity), scratch_directory() as directory:
                journal = trace.TraceJournal(directory, package_capacity=capacity,
                                             queue_capacity=100, max_file_bytes=250)
                try:
                    for ordinal in range(total):
                        self.assertTrue(journal.submit({"event": "observation", "ordinal": ordinal,
                                                       "padding": "x" * 30}))
                finally:
                    self.assertTrue(journal.close(3.))
                stats = journal.stats()
                archives = self.archives(directory, journal)
                self.assertEqual(len(archives), total // capacity)
                all_records = []
                for index, path in enumerate(archives, 1):
                    manifest, records = self.inspect_archive(path, journal.run_id, index)
                    self.assertEqual(manifest["event_count"], capacity)
                    self.assertTrue(all(not (directory / segment["name"]).exists()
                                        for segment in manifest["segments"]))
                    self.assertEqual([row["payload"]["ordinal"] for row in records],
                                     list(range((index - 1) * capacity, index * capacity)))
                    all_records.extend(records)
                loose = self.raw_records(directory, journal)
                self.assertEqual(len(loose), total % capacity)
                all_records.extend(loose)
                self.assertEqual([row["journal_sequence"] for row in all_records], list(range(1, total + 1)))
                self.assertEqual([row["payload"]["ordinal"] for row in all_records], list(range(total)))
                self.assertEqual((stats["archive_count"], stats["archived_events"],
                                  stats["active_package_events"]),
                                 (total // capacity, total - total % capacity, total % capacity))
                self.assertTrue(stats["write_complete"])
                self.assertEqual(list(trace.iter_trace_events(directory, journal.run_id)), all_records)

    def test_package_capacity_counts_written_events_not_submission_sequence(self):
        with scratch_directory() as directory:
            journal = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100)
            try:
                self.assertTrue(journal.submit({"ordinal": 0}))
                self.assertFalse(journal.submit({"invalid": float("nan")}))
                self.assertTrue(journal.submit({"ordinal": 1}))
                self.assertTrue(journal.submit({"ordinal": 2}))
                self.assertTrue(journal.submit({"ordinal": 3}))
            finally:
                self.assertTrue(journal.close(3.))
            paths = self.archives(directory, journal)
            self.assertEqual(len(paths), 1)
            manifest, records = self.inspect_archive(paths[0], journal.run_id, 1)
            self.assertEqual([row["journal_sequence"] for row in records], [1, 3, 4])
            self.assertEqual(manifest["event_count"], 3)
            self.assertEqual([row["journal_sequence"] for row in self.raw_records(directory, journal)], [5])
            self.assertEqual(journal.stats()["active_package_events"], 1)
            self.assertFalse(journal.stats()["write_complete"])

    def test_zip_write_failure_preserves_every_raw_event(self):
        with scratch_directory() as directory:
            with patch.object(zipfile.ZipFile, "write", side_effect=OSError("injected ZIP write failure")):
                journal = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100,
                                             max_file_bytes=150)
                try:
                    for ordinal in range(3):
                        self.assertTrue(journal.submit({"ordinal": ordinal}))
                finally:
                    self.assertTrue(journal.close(3.))
            stats = journal.stats()
            self.assertIn("injected ZIP write failure", stats["error"])
            self.assertFalse(stats["write_complete"])
            self.assertEqual((stats["archive_count"], stats["archived_events"]), (0, 0))
            self.assertEqual([row["payload"]["ordinal"] for row in self.raw_records(directory, journal)], [0, 1, 2])
            self.assertFalse(self.archives(directory, journal))

    def test_verification_failure_preserves_raw_and_does_not_publish_archive(self):
        with scratch_directory() as directory:
            with patch.object(trace.TraceJournal, "_verify_archive",
                              side_effect=OSError("injected archive verification failure")):
                journal = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100)
                try:
                    for ordinal in range(3):
                        self.assertTrue(journal.submit({"ordinal": ordinal}))
                finally:
                    self.assertTrue(journal.close(3.))
            stats = journal.stats()
            self.assertIn("injected archive verification failure", stats["error"])
            self.assertFalse(stats["write_complete"])
            self.assertEqual((stats["archive_count"], stats["archived_events"]), (0, 0))
            self.assertEqual([row["payload"]["ordinal"] for row in self.raw_records(directory, journal)], [0, 1, 2])
            self.assertFalse(self.archives(directory, journal))

    def test_new_run_preserves_existing_archives_and_loose_partial_files(self):
        with scratch_directory() as directory:
            first = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100)
            try:
                for ordinal in range(4):
                    self.assertTrue(first.submit({"run": "first", "ordinal": ordinal}))
            finally:
                self.assertTrue(first.close(3.))
            saved = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
            second = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100)
            try:
                for ordinal in range(3):
                    self.assertTrue(second.submit({"run": "second", "ordinal": ordinal}))
            finally:
                self.assertTrue(second.close(3.))
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertEqual({name: (directory / name).read_bytes() for name in saved}, saved)
            self.assertEqual(len(self.archives(directory, first)), 1)
            self.assertEqual(len(self.archives(directory, second)), 1)
            self.assertEqual(len(self.raw_records(directory, first)), 1)
            self.assertFalse(self.raw_records(directory, second))
            self.assertTrue(all(row["payload"]["run"] == "first"
                                for row in trace.iter_trace_events(directory, first.run_id)))
            self.assertTrue(all(row["payload"]["run"] == "second"
                                for row in trace.iter_trace_events(directory, second.run_id)))

    def test_verified_archive_deduplicates_raw_left_by_cleanup_failure(self):
        with scratch_directory() as directory:
            original_unlink = Path.unlink
            def blocked_unlink(path, *args, **kwargs):
                if path.suffix == ".jsonl" and path.parent.samefile(directory):
                    raise PermissionError("injected raw cleanup failure")
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, "unlink", blocked_unlink):
                journal = trace.TraceJournal(directory, package_capacity=3, queue_capacity=100)
                try:
                    for ordinal in range(3):
                        self.assertTrue(journal.submit({"ordinal": ordinal}))
                finally:
                    self.assertTrue(journal.close(3.))
            stats = journal.stats()
            self.assertIn("injected raw cleanup failure", stats["error"])
            self.assertFalse(stats["write_complete"])
            self.assertEqual((stats["archive_count"], stats["archived_events"]), (1, 3))
            self.assertEqual(stats['active_package_events'],0)
            paths = self.archives(directory, journal)
            self.assertEqual(len(paths), 1)
            _, archived = self.inspect_archive(paths[0], journal.run_id, 1)
            self.assertEqual(self.raw_records(directory, journal), archived)
            self.assertEqual(list(trace.iter_trace_events(directory, journal.run_id)), archived)

    def test_archive_metadata_rolls_without_removing_prior_archives(self):
        with scratch_directory() as directory:
            journal = trace.TraceJournal(directory, package_capacity=1, queue_capacity=200)
            try:
                for ordinal in range(130):
                    self.assertTrue(journal.submit({"ordinal": ordinal}))
            finally:
                self.assertTrue(journal.close(5.))
            stats = journal.stats()
            paths = self.archives(directory, journal)
            self.assertEqual(len(paths), 130)
            self.assertEqual((stats["archive_count"], stats["archived_events"]), (130, 130))
            self.assertEqual(len(stats["archives"]), 128)
            self.assertEqual(stats["archives_scope"], "recent_archive_names")
            self.assertNotIn(paths[0].name, stats["archives"])
            self.assertFalse(self.raw_records(directory, journal))
            self.assertEqual([row["payload"]["ordinal"]
                              for row in trace.iter_trace_events(directory, journal.run_id)], list(range(130)))
            self.assertTrue(stats["write_complete"])

    def test_package_capacity_is_positive_integer(self):
        with scratch_directory() as directory:
            for invalid in (0, -1, True, 1.5):
                with self.assertRaises(ValueError):
                    trace.TraceJournal(directory, package_capacity=invalid)


if __name__ == "__main__":
    unittest.main()
