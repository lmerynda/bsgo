"""Exercise the Wingman side panel's inline script in Node with a fake DOM and fake fetch."""
from pathlib import Path
import shutil
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
DOCK_HTML = HERE.parents[0] / "bsgo_offline" / "dock.html"
CHECKS = HERE / "dock_ui_checks.js"


@unittest.skipUnless(shutil.which("node"), "Node unavailable")
class DockUiTests(unittest.TestCase):
    def run_scenario(self, name):
        result = subprocess.run(["node", str(CHECKS), str(DOCK_HTML), name],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn(f"ok {name}", result.stdout)

    # Status line and state action
    def test_not_connected_offers_find_game_window(self):
        self.run_scenario("status_not_connected")

    def test_connecting_shows_progress_without_action(self):
        self.run_scenario("status_connecting")

    def test_unsupported_layout_is_watching_only_without_enable(self):
        self.run_scenario("status_unsupported_layout")

    def test_paused_offers_enable_with_token_and_never_auto_enables(self):
        self.run_scenario("status_paused_and_enable")

    def test_ready_and_active_routine_labels_and_stop(self):
        self.run_scenario("status_ready_and_active")

    def test_routine_failure_and_focus_wait_are_reported(self):
        self.run_scenario("routine_failure_is_reported_not_silent")

    def test_npc_mode_toggle_hides_mining_and_ignores_mining_keys(self):
        self.run_scenario("npc_mode_toggle_hides_mining_and_ignores_mining_keys")

    def test_unreachable_app_is_reported(self):
        self.run_scenario("status_app_down")

    # Start gating
    def test_start_refused_until_enabled_checked_and_fresh(self):
        self.run_scenario("start_refused_until_checked")

    def test_accepted_start_posts_token_and_assessment_then_clears(self):
        self.run_scenario("start_scan_posts_exact_body")

    def test_approach_needs_three_checks_and_wheel_point(self):
        self.run_scenario("start_approach_requires_all_checks_and_point")

    def test_check_clears_on_scene_change_and_expiry(self):
        self.run_scenario("check_clears_on_scene_change_and_expiry")

    # Hotkeys and pause
    def test_hotkeys_never_replay_and_route_like_buttons(self):
        self.run_scenario("hotkeys_never_replay_and_route")

    def test_pause_button_posts_pause(self):
        self.run_scenario("pause_button")

    # Readings
    def test_unknown_readings_show_dash_not_zero(self):
        self.run_scenario("readings_unknown_show_dash")


if __name__ == "__main__":
    unittest.main()
