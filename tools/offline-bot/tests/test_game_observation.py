"""Core observations survive disabled or failed mining and action providers."""
from dataclasses import replace
import json
from threading import Thread
import time
import unittest
from unittest.mock import Mock
from urllib.request import Request, urlopen

from bsgo_offline.easy_launcher import EasyLauncher
from bsgo_offline.game_observation import GameObservationFeed
from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.manual_assist_session import SelectionEvidenceRecognizer
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.observer_ui import make_server
from bsgo_offline.startup_layout import StartupHudRecognizer, PROFILE
from bsgo_offline.state import Snapshot
from test_ship_telemetry import fixture
from test_trace import scratch_directory


def frame():
    image = fixture(0)
    return NativeFrame(1, 1, PROFILE, time.monotonic(), *image.size, image.tobytes(),
        WindowGeometry(1, 2, 'numeric-only fixture', image.size, (0, 0)))


def publish(feed, sample, result):
    feed.store.publish(Snapshot(sample.acquisition_sequence, sample.session_epoch,
        sample.acquisition_sequence, sample.acquired_at, time.monotonic(), PROFILE,
        sample.frame_size, result.fields, result.healthy, result.reason))


class GameObservationTests(unittest.TestCase):
    def test_mining_off_skips_feature_while_ui_and_decisions_share_fresh_state(self):
        sample = frame()
        reader = StartupHudRecognizer(sample.frame_size, mining_enabled_provider=lambda: False)
        feature = Mock(side_effect=RuntimeError('mining must not run'))
        reader.reader = feature
        marker = Mock(side_effect=RuntimeError('marker must not run'))
        result = SelectionEvidenceRecognizer(reader, marker_reader=marker)(sample)
        feature.assert_not_called()
        marker.assert_not_called()
        self.assertTrue(result.healthy)
        self.assertEqual(result.reason, 'mining_feature_disabled')
        feed = GameObservationFeed(clock=lambda: sample.acquired_at+.5)
        publish(feed, sample, result)
        self.assertIs(feed.latest(), feed.store.read().snapshot)
        view = feed.game_state_snapshot()
        self.assertEqual(view['ship']['hull_current']['value'], 4684)
        self.assertEqual(view['ship']['speed']['value'], 25.7)
        self.assertFalse(view['input_authorized'])
        feed.clock = lambda: sample.acquired_at+2.1
        self.assertFalse(feed.game_state_snapshot()['ship']['speed']['usable'])
        feed.disconnect('test ended')
        self.assertIsNone(feed.latest())

    def test_mining_exception_cannot_fail_core_capture_or_ship_readings(self):
        sample = frame()
        reader = StartupHudRecognizer(sample.frame_size)
        reader.reader = Mock(side_effect=RuntimeError('feature failure'))
        result = reader(sample)
        self.assertTrue(result.healthy)
        self.assertEqual(result.fields['ship_telemetry'].value['hull_current'], 4684)
        self.assertFalse(result.fields['distance'].valid)
        self.assertEqual(result.reason, 'mining_recognition_failed: RuntimeError')
        reader.reader = Mock(side_effect=RuntimeError('still unavailable'))
        later = reader(replace(sample, acquisition_sequence=2, acquired_at=sample.acquired_at+.5))
        self.assertTrue(later.healthy)

    def test_core_http_never_reads_action_status(self):
        with scratch_directory() as directory:
            sample = frame()
            feed = GameObservationFeed(clock=lambda: sample.acquired_at+.5)
            publish(feed, sample, StartupHudRecognizer(sample.frame_size,
                mining_enabled_provider=lambda: False)(sample))
            actions = Mock()
            actions.actions_snapshot.side_effect = RuntimeError('action module unavailable')
            server = make_server(directory, 0, action_adapter=actions, observation_adapter=feed)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/game-state', timeout=3) as response:
                    state = json.load(response)
                self.assertEqual(state['ship']['hull_current']['value'], 4684)
                actions.actions_snapshot.assert_not_called()
            finally:
                server.shutdown(); server.server_close(); thread.join(2)

    def test_feature_switch_preserves_feed_and_never_enables_actions(self):
        with scratch_directory() as directory:
            sample = frame()
            feed = GameObservationFeed(clock=lambda: sample.acquired_at+.5)
            publish(feed, sample, StartupHudRecognizer(sample.frame_size,
                mining_enabled_provider=lambda: False)(sample))
            launcher = EasyLauncher(directory, directory/'runs', pause_file=directory/'pause')
            runner = Mock(side_effect=RuntimeError('no input requested'))
            controller = ManualAssistController(directory/'actions', snapshot_provider=feed.latest,
                runner=runner, paused=True)
            launcher._controller, launcher._observation_feed = controller, feed
            for enabled in (False, True, False):
                outcome = launcher.set_feature('mining', enabled)
                self.assertTrue(outcome['accepted'])
                self.assertEqual(launcher.features_snapshot()['mining']['enabled'], enabled)
                self.assertEqual(launcher.game_state_snapshot()['ship']['speed']['value'], 25.7)
                self.assertTrue(controller.action_control_snapshot()['paused'])
            self.assertEqual(launcher.start_action('scan_selected')['reason'], 'mining_feature_disabled')
            self.assertEqual(launcher.resume_actions()['reason'], 'mining_feature_disabled')
            runner.assert_not_called()
            self.assertTrue(launcher.close())
