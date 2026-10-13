"""Auto-fight run and daily statistics on disk; no game or input."""
from datetime import datetime
import json
import unittest

from bsgo_offline.npc_stats import RunRecorder, daily_summary
from test_trace import scratch_directory


def epoch(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


class Clock:
    def __init__(self, text):
        self.now = epoch(text)

    def __call__(self):
        return self.now


class NpcStatsTests(unittest.TestCase):
    def test_runs_add_up_for_the_day_and_the_latest_run_is_reported(self):
        with scratch_directory() as directory:
            clock = Clock("2026-10-12 10:00:00")
            first = RunRecorder(directory, "run-a", clock=clock)
            clock.now += 600
            first.record({"kills": 7, "bursts": 42, "engaged": 9, "skipped": 3}, force=True, ended=True)
            second = RunRecorder(directory, "run-b", clock=clock)
            clock.now += 300
            second.record({"kills": 2, "bursts": 10, "engaged": 3, "skipped": 0}, force=True)
            summary = daily_summary(directory, now=clock.now)
            self.assertEqual((summary["runs"], summary["kills"], summary["bursts"], summary["engaged"],
                              summary["skipped"], summary["seconds"]), (2, 9, 52, 12, 3, 900.0))
            self.assertEqual(summary["last_run"]["kills"], 2)
            self.assertFalse(summary["last_run"]["ended"])

    def test_writes_are_throttled_but_forced_and_final_writes_land(self):
        with scratch_directory() as directory:
            clock = Clock("2026-10-12 10:00:00")
            recorder = RunRecorder(directory, "run", clock=clock, interval=2.)
            self.assertTrue(recorder.record({"kills": 1}))
            clock.now += 1
            self.assertFalse(recorder.record({"kills": 2}), "within the interval")
            self.assertEqual(daily_summary(directory, now=clock.now)["kills"], 1)
            self.assertTrue(recorder.record({"kills": 3}, force=True, ended=True))
            self.assertEqual(daily_summary(directory, now=clock.now)["kills"], 3)

    def test_a_run_across_midnight_is_split_between_the_two_days(self):
        with scratch_directory() as directory:
            clock = Clock("2026-10-12 23:50:00")
            recorder = RunRecorder(directory, "run", clock=clock)
            clock.now += 300
            recorder.record({"kills": 5, "bursts": 20}, force=True)
            clock.now += 900  # 00:10 next day
            recorder.record({"kills": 8, "bursts": 31}, force=True, ended=True)
            yesterday = daily_summary(directory, now=epoch("2026-10-12 23:59:00"))
            today = daily_summary(directory, now=clock.now)
            self.assertEqual((yesterday["kills"], yesterday["bursts"]), (5, 20))
            self.assertTrue(json.loads((directory / "2026-10-12.json").read_text())["runs"]["run"]["ended"])
            self.assertEqual((today["kills"], today["bursts"], today["runs"]), (3, 11, 1))

    def test_missing_or_corrupt_files_read_as_an_empty_day(self):
        with scratch_directory() as directory:
            now = epoch("2026-10-12 12:00:00")
            self.assertEqual(daily_summary(directory, now=now)["runs"], 0)
            (directory / "2026-10-12.json").write_text("{not json", encoding="utf-8")
            summary = daily_summary(directory, now=now)
            self.assertEqual((summary["runs"], summary["kills"], summary["last_run"]), (0, 0, None))

    def test_the_fight_loop_records_its_run(self):
        from test_npc_fight import NpcFightTests
        with scratch_directory() as stats:
            case = NpcFightTests()
            result, _ = case.run_loop(stats, delay=(.2, .2), max_cycles=2, stats_directory=stats / "npc-stats")
            self.assertEqual(result["state"], "recorded", result)
            summary = daily_summary(stats / "npc-stats")
            self.assertEqual((summary["runs"], summary["bursts"]), (1, 2))
            self.assertTrue(summary["last_run"]["ended"])


if __name__ == "__main__":
    unittest.main()
