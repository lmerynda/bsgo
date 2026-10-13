"""Measured native target pixels plus rejection and shared-state freshness."""
import fixture_data  # noqa: F401  restores packed test images first
from dataclasses import replace
from pathlib import Path
import unittest

from PIL import Image

from bsgo_offline.game_observation import game_state_view
from bsgo_offline.startup_layout import StartupHudRecognizer, PROFILE
from bsgo_offline.state import Snapshot
from test_game_observation import frame


class SelectedTargetTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new('RGB', (3096, 1704))
        with Image.open(Path(__file__).parent/'fixtures/selected-target/asteroid-4674-range-1421.png') as crop:
            self.image.paste(crop, (988, 74))
        self.reader = StartupHudRecognizer(self.image.size, mining_enabled_provider=lambda: False)

    def test_native_card_shared_with_mining_off_and_stale_values_expire(self):
        original = frame()
        sample = replace(original, width=self.image.width, height=self.image.height,
                         rgb=self.image.tobytes(),
                         geometry=replace(original.geometry, client_size=self.image.size))
        result = self.reader(sample)
        snapshot = Snapshot(1, 1, 1, sample.acquired_at, sample.acquired_at, PROFILE,
                            sample.frame_size, result.fields, result.healthy, result.reason)
        target = game_state_view(snapshot, sample.acquired_at+.5)['target']
        self.assertTrue(target['present'])
        self.assertEqual(target['kind']['value'], 'asteroid')
        self.assertEqual(target['distance']['value'], 1421)
        self.assertEqual(target['current_hp']['value'], 4674)
        self.assertFalse(result.fields['asteroid_selected'].valid)
        self.assertFalse(target['input_authorized'])
        stale = game_state_view(snapshot, sample.acquired_at+2.1)['target']
        self.assertIsNone(stale['present'])
        self.assertIsNone(stale['current_hp']['value'])

    def test_card_loss_never_reuses_values_or_claims_no_selection(self):
        self.assertTrue(self.reader.selected_target.read(self.image)['present'])
        self.image.paste((0, 0, 0), (1093, 100, 1300, 129))
        missing = self.reader.selected_target.read(self.image)
        self.assertIsNone(missing['present'])
        self.assertIsNone(missing['distance'])
        self.assertIsNone(missing['current_hp'])

    def test_unreadable_title_does_not_discard_target_ratio(self):
        self.image.paste((0, 0, 0), (999, 74, 1135, 94))
        target = self.reader.selected_target.read(self.image)
        self.assertTrue(target['present'])
        self.assertIsNone(target['kind'])
        self.assertEqual(target['current_hp'], 4674)

    def test_partial_range_and_unknown_layout_are_rejected(self):
        self.image.paste((0, 0, 0), (1020, 187, 1028, 213))
        self.assertIsNone(self.reader.selected_target.read(self.image)['distance'])
        self.assertIsNone(self.reader.selected_target.read(self.image.resize((2560, 1440)))['present'])


class NativeTargetCardTests(unittest.TestCase):
    """Live 2560 cards (October 12): any HP/shield ratio, range and hidden values."""
    CARDS = Path(__file__).parent / 'fixtures' / 'target_cards'

    @classmethod
    def setUpClass(cls):
        from bsgo_offline.recognition import load_hud_reader
        from bsgo_offline.selected_target import SelectedTargetReader
        from bsgo_offline.ship_telemetry import ShipTelemetryReader
        cls.reader = SelectedTargetReader(ShipTelemetryReader(load_hud_reader(PROFILE)._large))

    def read(self, name):
        with Image.open(self.CARDS / (name + '.png')) as image:
            return self.reader.read(image.convert('RGB'))

    def test_partial_bars_read_hp_shield_and_range(self):
        for name, expected in (
                ('npc-1091-of-2364', (1091, 2364, 273, 300, 1296)),
                ('npc-611-of-2364-slash-visible', (611, 2364, 275, 300, 1205)),
                ('player-4719-full', (4719, 4719, 748, 750, 1791)),
                ('player-9031-of-9129', (9031, 9129, 695, 750, 1741))):
            with self.subTest(name):
                card = self.read(name)
                self.assertIs(card['present'], True)
                self.assertIsNone(card['kind'])
                self.assertEqual((card['current_hp'], card['max_hp'], card['shield_current'],
                                  card['shield_max'], card['distance']), expected)

    def test_hidden_or_obstructed_hp_stays_unknown_but_the_target_is_present(self):
        for name in ('hp-hidden-dashes', 'hp-overlay-line'):
            with self.subTest(name):
                card = self.read(name)
                self.assertIs(card['present'], True)
                self.assertIsNone(card['current_hp'])
                self.assertIsNone(card['max_hp'])
                self.assertIsNotNone(card['distance'])

    def test_empty_card_area_is_not_a_target(self):
        card = self.reader.read(Image.new('RGB', (2560, 1440)))
        self.assertIsNone(card['present'])
        self.assertIsNone(card['current_hp'])
