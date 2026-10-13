"""Side panel placement, hotkey routing, and dashboard routes without real windows."""
import json
from urllib.error import HTTPError
from threading import Thread
import unittest
from unittest.mock import Mock
from urllib.request import Request, urlopen

from bsgo_offline.dock_window import DockFollower, PANEL_TITLE, GAME_TITLE, dock_placement, open_panel
from bsgo_offline.hotkeys import GlobalHotkeys, HotkeyEvents
from bsgo_offline.observer_ui import DOCK_HTML, make_server
from bsgo_offline.side_panel import SidePanel
from test_trace import scratch_directory


LEFT = (0, 0, 2560, 1392)
RIGHT = (2560, 0, 5120, 1392)


class PlacementTests(unittest.TestCase):
    def test_panel_takes_the_right_edge_of_the_games_screen(self):
        # Windowed game on the left screen: the panel stays on that screen, full work height.
        self.assertEqual(dock_placement((100, 50, 2000, 1200), [LEFT, RIGHT], 300),
                         (2260, 0, 300, 1392, "beside_right"))
        # Game on the right screen: never jump to the neighbouring screen.
        self.assertEqual(dock_placement((2560, 0, 5120, 1440), [LEFT, RIGHT], 300),
                         (4820, 0, 300, 1392, "over_game"))

    def test_a_game_reaching_the_edge_is_overlapped_and_height_never_exceeds_the_screen(self):
        x, y, width, height, mode = dock_placement((0, 0, 2560, 1440), [LEFT], 300)
        self.assertEqual((x, y, width, height, mode), (2260, 0, 300, 1392, "over_game"))
        self.assertLessEqual(y + height, LEFT[3])

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
        # 320 CSS px at 150% is 480 physical px, on the game's own screen.
        self.assertEqual(win32.placed, [(7, (2080, 0, 480, 1392))])
        self.assertEqual(state["mode"], "over_game")
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

    def test_open_panel_converts_physical_placement_to_scaled_browser_units(self):
        # 150% scaling: the 2088 px tall work area must not open 3132 px tall.
        with scratch_directory() as directory:
            popen = Mock(return_value="process")
            open_panel("http://127.0.0.1:1/dock", directory / "profile", (3360, 0, 480, 2088, "beside_right"),
                       browser="edge.exe", popen=popen, scale=1.5)
            arguments = popen.call_args.args[0]
            self.assertIn("--window-position=2240,0", arguments)
            self.assertIn("--window-size=320,1392", arguments)

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

    def test_each_press_is_claimed_by_exactly_one_panel(self):
        now = [10.]
        events = HotkeyEvents(clock=lambda: now[0])
        self.assertEqual(events.claim(1)["reason"], "hotkey_press_superseded")
        events.record("F9")
        self.assertEqual(events.claim(1), {"accepted": True, "reason": "hotkey_press_claimed", "action": "scan_view"})
        self.assertEqual(events.claim(1)["reason"], "hotkey_press_already_handled")
        events.record("F10")
        self.assertEqual(events.claim(1)["reason"], "hotkey_press_superseded")
        now[0] = 14.
        self.assertEqual(events.claim(2)["reason"], "hotkey_press_expired")
        for bad in (True, "2", None):
            self.assertFalse(events.claim(bad)["accepted"])

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
                panel.events.record("F9")
                def claim(body):
                    request = Request(base + "/api/dock", data=json.dumps(body).encode(), method="POST",
                        headers={"Content-Type": "application/json", "Origin": base})
                    try:
                        with urlopen(request, timeout=3) as response:
                            return json.load(response)
                    except HTTPError as error:
                        return json.load(error)
                self.assertTrue(claim({"action": "claim_hotkey", "sequence": 1})["accepted"])
                self.assertFalse(claim({"action": "claim_hotkey", "sequence": 1})["accepted"])
                self.assertEqual(claim({"action": "claim_hotkey", "sequence": "1"}), {"error": "invalid_dock_request"})
                self.assertTrue(server.hotkey_pause()["accepted"])
                launcher.pause_actions.assert_called_once_with()
            finally:
                server.shutdown()
                server.server_close()

    def test_panel_title_matches_the_window_finder(self):
        self.assertIn(f"<title>{PANEL_TITLE}</title>", DOCK_HTML.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
