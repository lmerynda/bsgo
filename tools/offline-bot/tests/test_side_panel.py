"""Side panel placement, hotkey routing, and dashboard routes without real windows."""
import json
from threading import Thread
import unittest
from unittest.mock import Mock
from urllib.request import urlopen

from bsgo_offline.dock_window import DockFollower, PANEL_TITLE, GAME_TITLE, dock_placement, open_panel
from bsgo_offline.hotkeys import GlobalHotkeys, HotkeyEvents
from bsgo_offline.observer_ui import DOCK_HTML, make_server
from bsgo_offline.side_panel import SidePanel
from test_trace import scratch_directory


LEFT = (0, 0, 2560, 1392)
RIGHT = (2560, 0, 5120, 1392)


class PlacementTests(unittest.TestCase):
    def test_panel_sits_on_the_monitor_touching_the_game(self):
        self.assertEqual(dock_placement((0, 0, 2560, 1440), [LEFT, RIGHT], 300),
                         (2560, 0, 300, 1392, "beside_right"))
        self.assertEqual(dock_placement((2560, 0, 5120, 1440), [LEFT, RIGHT], 300),
                         (2260, 0, 300, 1392, "beside_left"))

    def test_single_monitor_overlays_the_game_right_edge(self):
        self.assertEqual(dock_placement((100, 50, 2000, 1200), [LEFT], 300),
                         (1700, 50, 300, 1150, "overlay"))

    def test_without_a_game_the_panel_waits_on_the_primary_monitor(self):
        self.assertEqual(dock_placement(None, [LEFT, RIGHT], 300), (2260, 0, 300, 1392, "waiting"))
        with self.assertRaises(ValueError):
            dock_placement(None, [], 300)


class FakeWin32:
    def __init__(self, panel=7, game_rect=(0, 0, 2560, 1440), scale=1.5):
        self.panel, self.game_rect, self.factor = panel, game_rect, scale
        self.placed = []

    def find(self, title):
        return {PANEL_TITLE: self.panel, GAME_TITLE: 9 if self.game_rect else None}[title]

    def rect(self, hwnd):
        return self.game_rect if hwnd == 9 else None

    def monitors(self):
        return [LEFT, RIGHT]

    def scale(self, hwnd):
        return self.factor

    def place(self, hwnd, *rect):
        self.placed.append((hwnd, rect))


class FollowerTests(unittest.TestCase):
    def test_tick_snaps_panel_with_display_scaling(self):
        win32 = FakeWin32()
        state = DockFollower(win32=win32, width=320).tick()
        self.assertEqual(win32.placed, [(7, (2560, 0, 480, 1392))])
        self.assertEqual(state["mode"], "beside_right")
        self.assertTrue(state["game"])

    def test_missing_panel_is_never_placed(self):
        win32 = FakeWin32(panel=None)
        self.assertFalse(DockFollower(win32=win32).tick()["panel"])
        self.assertEqual(win32.placed, [])

    def test_open_panel_uses_an_isolated_app_window(self):
        with scratch_directory() as directory:
            popen = Mock(return_value="process")
            self.assertEqual(open_panel("http://127.0.0.1:1/dock", directory / "profile",
                (10, 20, 320, 900, "waiting"), browser="edge.exe", popen=popen), "process")
            arguments = popen.call_args.args[0]
            self.assertIn("--app=http://127.0.0.1:1/dock", arguments)
            self.assertIn(f"--user-data-dir={directory / 'profile'}", arguments)
            self.assertIn("--window-position=10,20", arguments)

    def test_missing_browser_is_reported(self):
        from bsgo_offline.dock_window import find_browser
        self.assertIsNone(find_browser({}))


class HotkeyTests(unittest.TestCase):
    def test_f8_pauses_in_the_core_and_other_keys_are_only_requests(self):
        events, pause = HotkeyEvents(clock=lambda: 5.), Mock(return_value={"reason": "manual_actions_paused"})
        hotkeys = GlobalHotkeys(events, pause, user32=object())
        self.assertEqual(hotkeys.handle("F8")["outcome"], "manual_actions_paused")
        pause.assert_called_once_with()
        last = hotkeys.handle("F9")
        self.assertEqual((last["sequence"], last["action"], last["outcome"]), (2, "scan_view", None))
        self.assertEqual(pause.call_count, 1)
        self.assertEqual(events.snapshot()["sequence"], 2)

    def test_failing_pause_is_reported_not_raised(self):
        hotkeys = GlobalHotkeys(HotkeyEvents(), Mock(side_effect=RuntimeError("x")), user32=object())
        self.assertEqual(hotkeys.handle("F8")["outcome"], "pause_failed:RuntimeError")


class RouteTests(unittest.TestCase):
    def test_dock_page_and_state_are_served_and_hotkey_pause_uses_startup(self):
        with scratch_directory() as directory:
            panel = SidePanel(directory / "profile", follower=Mock(last={"mode": "beside_right"}))
            launcher = Mock()
            launcher.pause_actions.return_value = {"accepted": True, "reason": "manual_actions_paused"}
            server = make_server(directory, 0, startup_adapter=launcher, dock_adapter=panel)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/dock", timeout=3) as response:
                    self.assertEqual(response.read(), DOCK_HTML.read_bytes())
                with urlopen(base + "/api/dock", timeout=3) as response:
                    state = json.load(response)
                self.assertEqual(state["hotkeys"]["sequence"], 0)
                self.assertEqual(state["window"]["mode"], "beside_right")
                self.assertTrue(server.hotkey_pause()["accepted"])
                launcher.pause_actions.assert_called_once_with()
            finally:
                server.shutdown()
                server.server_close()

    def test_panel_title_matches_the_window_finder(self):
        self.assertIn(f"<title>{PANEL_TITLE}</title>", DOCK_HTML.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
