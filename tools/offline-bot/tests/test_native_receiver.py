"""Pure receiver-state checks. These tests never launch a native window."""
import json
from pathlib import Path
from contextlib import contextmanager
import shutil
import unittest
from unittest.mock import patch
from uuid import uuid4

from bsgo_offline.native_receiver import (ReceiverJournal, TARGET_RECT, STATE_MARKERS,
                                         client_point, key_name, atomic_json)


@contextmanager
def scratch_directory():
    # Inherit workspace ACL; Windows TemporaryDirectory's explicit mode0700
    # excludes this sandbox identity even though its parent is writable.
    base = Path(__file__).resolve().parent
    directory = base / ("native-receiver-scratch-" + uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        resolved = directory.resolve()
        if resolved.parent != base or not resolved.name.startswith("native-receiver-scratch-"):
            raise RuntimeError("Unexpected scratch directory cleanup target")
        shutil.rmtree(resolved)


class NativeReceiverTests(unittest.TestCase):
    def test_key_messages_have_distinct_receipts_and_visible_effects(self):
        with scratch_directory() as temporary:
            journal = ReceiverJournal(Path(temporary), "key-test")
            journal.key(True, 0x21, extra_info=0x4253474F00000007)
            self.assertEqual(journal.state()["held_keys"], ["PageUp"])
            self.assertEqual(journal.throttle, "maximum")
            journal.key(False, 0x21, extra_info=0x4253474F00000008)
            self.assertEqual(journal.state()["held_keys"], [])
            self.assertEqual(journal.throttle, "maximum")
            journal.key(True, 0x22)
            self.assertEqual(journal.throttle, "idle")
            journal.key(False, 0x22)
            journal.close("test")
            events = [json.loads(line) for line in (Path(temporary) / "receiver-events.jsonl").read_text().splitlines()]
            self.assertEqual([e["kind"] for e in events[:4]], ["key_down", "key_up", "key_down", "key_up"])
            self.assertEqual(events[0]["extra_info"], 0x4253474F00000007)
            self.assertEqual(events[1]["extra_info"], 0x4253474F00000008)

    def test_target_click_and_buttons_are_recorded_in_client_coordinates(self):
        with scratch_directory() as temporary:
            journal = ReceiverJournal(Path(temporary), "mouse-test")
            journal.mouse(True, "right", 240, 190)
            self.assertFalse(journal.selected)
            journal.mouse(False, "right", 240, 190)
            journal.mouse(True, "left", 199, 190)
            self.assertFalse(journal.selected)
            journal.mouse(False, "left", 199, 190)
            journal.mouse(True, "left", 240, 190, extra_info=123)
            self.assertTrue(journal.selected)
            self.assertEqual(journal.state()["held_buttons"], ["left"])
            journal.mouse(False, "left", 240, 190)
            self.assertEqual(journal.state()["held_buttons"], [])
            journal.close("test")
            events = [json.loads(line) for line in (Path(temporary) / "receiver-events.jsonl").read_text().splitlines()]
            self.assertEqual(events[4]["coordinate_space"], "client")
            self.assertEqual((events[4]["x"], events[4]["y"]), (240, 190))
            self.assertEqual(events[4]["extra_info"], 123)

    def test_saturated_log_stays_bounded_but_latest_held_state_keeps_updating(self):
        with scratch_directory() as temporary:
            journal = ReceiverJournal(Path(temporary), "limit-test", limit=4)
            for _ in range(4):
                journal.key(True, 0x41)
                journal.key(False, 0x41)
            journal.key(True, 0x42)
            journal.close("lifetime_expired")
            rows = [json.loads(line) for line in (Path(temporary) / "receiver-events.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[-1]["kind"], "event_limit")
            state = json.loads((Path(temporary) / "receiver-state.json").read_text())
            self.assertTrue(state["limit_reached"])
            self.assertTrue(state["shutdown"])
            self.assertEqual(state["held_keys"], ["B"])
            self.assertEqual(state["shutdown_reason"], "lifetime_expired")

    def test_received_held_state_is_retained_on_shutdown_as_evidence(self):
        with scratch_directory() as temporary:
            journal = ReceiverJournal(Path(temporary), "shutdown-test")
            journal.key(True, 0x41)
            journal.mouse(True, "middle", 400, 400)
            journal.close("owned_window_closed")
            state = json.loads((Path(temporary) / "receiver-state.json").read_text())
            self.assertEqual(state["held_keys"], ["A"])
            self.assertEqual(state["held_buttons"], ["middle"])

    def test_signed_mouse_coordinates_and_named_keys(self):
        self.assertEqual(client_point((0xFFFC << 16) | 0xFFFE), (-2, -4))
        self.assertEqual(client_point((190 << 16) | 240), (240, 190))
        self.assertEqual(key_name(0x21), "PageUp")
        self.assertEqual(key_name(0x22), "PageDown")
        self.assertEqual(key_name(0x7B), "F12")

    def test_atomic_json_and_pixel_contract(self):
        with scratch_directory() as temporary:
            path = Path(temporary) / "state.json"
            atomic_json(path, {"value": 1})
            atomic_json(path, {"value": 2})
            self.assertEqual(json.loads(path.read_text()), {"value": 2})
            self.assertFalse(path.with_name("state.json.tmp").exists())
        self.assertEqual(TARGET_RECT, (200, 150, 280, 230))
        self.assertEqual(STATE_MARKERS["selected"]["client"], [340, 190])
        self.assertEqual(STATE_MARKERS["throttle"]["client"], [420, 190])
        self.assertNotEqual(STATE_MARKERS["throttle"]["rgb"]["idle"], STATE_MARKERS["throttle"]["rgb"]["maximum"])

    def test_atomic_replace_retries_are_bounded(self):
        with scratch_directory() as temporary:
            with patch("bsgo_offline.native_receiver.os.replace", side_effect=PermissionError("reader")) as replacement:
                with patch("bsgo_offline.native_receiver.time.sleep") as pause:
                    with self.assertRaises(PermissionError):
                        atomic_json(Path(temporary) / "state.json", {"value": 2})
                    self.assertEqual(replacement.call_count, 6)
                    self.assertEqual(pause.call_count, 5)


if __name__ == "__main__":
    unittest.main()
