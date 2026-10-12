"""Independent ship registration using saved manually labeled native crops.

Relocated fixtures verify geometry and rejection; they do not establish live
numeric accuracy for another resolution or font raster.
"""
import fixture_data  # noqa: F401  restores packed test images first
import unittest

from PIL import Image, ImageDraw

from bsgo_offline.game_state import GameStateReader
from bsgo_offline.recognition import HudRecognizer
from test_ship_telemetry import fixture


class GameStateTests(unittest.TestCase):
    def setUp(self):
        self.large = HudRecognizer('largehud-20261003')._reader._large
        self.reader = GameStateReader(self.large)
        self.boxes = {'hull': (148, 100, 299, 118),
                      'energy': (148, 124, 299, 142),
                      'speed': (1650, 1310, 1708, 1339)}

    def relocated(self, index=0, *, squadron=False, noise=False):
        original = fixture(index)
        image = Image.new('RGB', (3096, 1704))
        source_hull = self.large.data['hull_box']
        source_energy = tuple(value+(24 if i % 2 else 0)
                              for i, value in enumerate(source_hull))
        for name, source in (('hull', source_hull), ('energy', source_energy),
                             ('speed', self.large.data['speed_text_box'])):
            image.paste(original.crop(source), self.boxes[name][:2])
        if squadron:
            image.paste(original.crop(source_hull), (440, 100))
            image.paste(original.crop(source_energy), (440, 124))
        if noise:
            draw = ImageDraw.Draw(image)
            for x, y in ((30, 36), (80, 156), (300, 30), (1100, 950), (1900, 1400)):
                draw.rectangle((x, y, x+2, y+2), fill='white')
        return image

    def read(self, sequence, *, image=None, at=None, context='fixture'):
        return self.reader.read(image if image is not None else self.relocated(),
            context=context, sequence=sequence, observed_at=sequence*.5 if at is None else at)

    def registered(self, **kwargs):
        for sequence in range(1, 4):
            result = self.read(sequence, **kwargs)
        return result

    def test_native_delegates_without_target_and_never_authorizes_input(self):
        result = self.read(1, image=fixture(0))
        self.assertEqual(result['speed'], 25.7)
        self.assertEqual((result['hull_current'], result['hull_max']), (4684, 5005))
        self.assertEqual((result['energy_current'], result['energy_max']), (671, 800))
        self.assertEqual(result['calibration']['status'], 'existing_profile')
        self.assertTrue(result['display_only'])
        self.assertFalse(result['calibration']['input_authorized'])

    def test_target_absent_registers_independent_bars_and_speed_in_newer_captures(self):
        image = self.relocated(squadron=True, noise=True)
        for sequence in (1, 2):
            result = self.read(sequence, image=image)
            self.assertIsNone(result['speed'])
            self.assertIsNone(result['hull_current'])
            self.assertEqual(result['calibration']['status'], 'pending')
        result = self.read(3, image=image)
        self.assertEqual((result['hull_current'], result['hull_max'], result['energy_current'],
                          result['energy_max'], result['speed']), (4684, 5005, 671, 800, 25.7))
        self.assertEqual(result['reasons'], {})
        self.assertEqual(result['calibration']['status'], 'registered')
        self.assertFalse(result['calibration']['input_authorized'])

    def test_speed_loss_preserves_current_ship_bars_and_requires_reconfirmation(self):
        self.registered()
        image = self.relocated()
        image.paste('black', self.boxes['speed'])
        result = self.read(4, image=image)
        self.assertIsNone(result['speed'])
        self.assertEqual(result['hull_current'], 4684)
        self.assertEqual(result['energy_current'], 671)
        self.assertIn('speed', result['reasons'])
        self.assertNotIn('hull', result['reasons'])
        self.assertEqual(result['calibration']['status'], 'partial')
        self.assertIsNone(self.read(5)['speed'])
        self.assertIsNone(self.read(6)['speed'])
        self.assertEqual(self.read(7)['speed'], 25.7)

    def test_bar_damage_has_its_own_reason_and_never_reuses_a_previous_value(self):
        self.registered()
        image = self.relocated(index=6)
        image.paste('black', self.boxes['energy'])
        result = self.read(4, image=image)
        self.assertEqual(result['hull_current'], 1580)
        self.assertIsNone(result['energy_current'])
        self.assertEqual(result['reasons']['energy'], 'complete_ratio_not_verified')
        # Missing both bars clears their geometry rather than displaying old HP.
        image.paste('black', self.boxes['hull'])
        result = self.read(5, image=image)
        self.assertIsNone(result['hull_current'])
        self.assertIsNone(result['energy_current'])
        self.assertIsNotNone(result['speed'])

    def test_squadron_cards_cannot_replace_missing_own_ship(self):
        image = self.relocated(squadron=True)
        image.paste('black', self.boxes['hull'])
        image.paste('black', self.boxes['energy'])
        result = self.registered(image=image)
        self.assertIsNone(result['hull_current'])
        self.assertIsNone(result['energy_current'])
        self.assertEqual(result['speed'], 25.7)

    def test_ambiguous_ship_pairs_do_not_hide_independent_speed(self):
        image = self.relocated()
        image.paste(image.crop(self.boxes['hull']), (12, 28))
        image.paste(image.crop(self.boxes['energy']), (12, 52))
        result = self.registered(image=image)
        self.assertIsNone(result['hull_current'])
        self.assertIsNone(result['energy_current'])
        self.assertIn('multiple_geometry_models', result['reasons']['hull'])
        self.assertEqual(result['speed'], 25.7)

    def test_reordered_capture_gap_and_context_change_reset_confirmation(self):
        self.registered()
        result = self.read(3)
        self.assertIsNone(result['hull_current'])
        self.assertIsNone(result['speed'])
        self.assertEqual(result['reasons']['speed'], 'waiting_for_distinct_newer_capture')
        self.assertIsNone(self.read(4)['speed'])
        self.assertIsNone(self.read(5)['speed'])
        self.assertEqual(self.read(6)['speed'], 25.7)
        self.assertIsNone(self.read(7, at=8.)['speed'])
        self.assertEqual(self.read(7, at=8.)['calibration']['components']['speed']['consistent_frames'], 0)
        self.assertIsNone(self.read(1, context='new')['speed'])
        self.assertIsNone(self.read(2, context='new')['speed'])
        self.assertEqual(self.read(3, context='new')['speed'], 25.7)

    def test_erased_decimal_is_unknown_and_explicit_reset_discards_geometry(self):
        image = self.relocated()
        # Manually measured decimal in native 25.7 crop: local (36,22)..(38,24).
        x, y = self.boxes['speed'][:2]
        ImageDraw.Draw(image).rectangle((x+36, y+22, x+38, y+24), fill='black')
        result = self.registered(image=image)
        self.assertIsNone(result['speed'])
        self.assertEqual(result['hull_current'], 4684)
        self.reader.reset('profile_changed')
        self.assertIsNone(self.read(4)['hull_current'])


if __name__ == '__main__':
    unittest.main()
