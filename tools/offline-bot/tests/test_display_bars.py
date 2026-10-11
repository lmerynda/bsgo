"""Later live numeric crops, excluded from font calibration and labeled visually."""
from pathlib import Path
import unittest

from PIL import Image, ImageDraw
from bsgo_offline.startup_layout import StartupHudRecognizer


ROOT = Path(__file__).resolve().parents[2] / 'hud-reader/fixtures/ship-telemetry-20261011'


def fixture(index):
    image = Image.new('RGB', (3096, 1704))
    with Image.open(ROOT / f'own-bars-{index}.png') as crop:
        image.paste(crop, (147, 104))
    return image


class DisplayBarTests(unittest.TestCase):
    def setUp(self):
        self.reader = StartupHudRecognizer((3096, 1704)).game_state

    def test_independent_live_crops_match_visual_labels(self):
        for index, energy in ((7, 772), (8, 797), (9, 819)):
            result = self.reader.reader.read_display_bars(fixture(index))
            self.assertEqual((result['hull_current'], result['hull_max']), (5210, 5210))
            self.assertEqual((result['energy_current'], result['energy_max']), (energy, 827))

    def test_three_fresh_frames_then_independent_missing_instrument(self):
        for sequence, index in enumerate((7, 8, 9), 1):
            result = self.reader.read(fixture(index), context='live crop',
                                      sequence=sequence, observed_at=sequence*.5)
            self.assertEqual(result['hull_current'], 5210 if sequence == 3 else None)
        self.assertEqual(result['energy_current'], 819)
        self.assertFalse(result['calibration']['input_authorized'])
        image = fixture(9)
        ImageDraw.Draw(image).rectangle((147, 132, 338, 153), fill='black')
        result = self.reader.read(image, context='live crop', sequence=4, observed_at=2.)
        self.assertEqual(result['hull_current'], 5210)
        self.assertIsNone(result['energy_current'])

    def test_missing_slash_or_leading_digit_is_not_a_smaller_value(self):
        for box in ((237, 108, 245, 121), (166, 108, 181, 122)):
            image = fixture(7)
            ImageDraw.Draw(image).rectangle(box, fill='black')
            result = self.reader.reader.read_display_bars(image)
            self.assertIsNone(result['hull_current'])
            self.assertEqual(result['energy_current'], 772)
