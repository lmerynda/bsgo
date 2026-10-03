"""Cropped HUD regression for explicitly resampled 4K restart speed readings."""
import unittest

from PIL import Image, ImageDraw

from hud_reader import HudReader, ROOT


FIXTURES = ROOT / 'fixtures' / 'calibration'


class Box4KSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = HudReader('restart-20261001', speed_raster='box_3840_to_2560')
        cls.reader.speed_box = (0, 0, 80, 26)
        cls.native = HudReader('restart-20261001')
        cls.native.speed_box = (0, 0, 80, 26)

    def crop(self, label):
        return Image.open(FIXTURES / f'box4k-speed-{label}.png').convert('RGB')

    def test_only_explicit_box_raster_accepts_complete_moving_digits(self):
        for label, value in (('1p6', 1.6), ('1p4', 1.4)):
            with self.subTest(speed=label):
                image = self.crop(label)
                self.assertEqual(image.size, (80, 26))
                self.assertEqual(self.reader.speed(image)[0], value)
                self.assertIsNone(self.native.speed(image)[0])

    def test_box_narrow_one_clipping_gap_and_interior_erasure_reject(self):
        for name, rectangle, color in (
                ('upper endpoint', (30, 5, 32, 5), (0, 0, 0)),
                ('lower endpoint', (30, 20, 32, 20), (0, 0, 0)),
                ('middle gap filled', (30, 12, 32, 13), (255, 255, 255)),
                ('upper interior', (31, 7, 32, 10), (0, 0, 0))):
            with self.subTest(fault=name):
                image = self.crop('1p6')
                ImageDraw.Draw(image).rectangle(rectangle, fill=color)
                self.assertIsNone(self.reader.speed(image)[0])

    def test_box_four_endpoint_damage_or_noise_reject(self):
        for name, rectangle, color in (
                ('upper endpoint erased', (41, 4, 41, 4), (0, 0, 0)),
                ('lower endpoint erased', (51, 20, 52, 20), (0, 0, 0)),
                ('extra upper pixel', (42, 4, 42, 4), (255, 255, 255)),
                ('lower right segment erased', (51, 15, 52, 18), (0, 0, 0))):
            with self.subTest(fault=name):
                image = self.crop('1p4')
                ImageDraw.Draw(image).rectangle(rectangle, fill=color)
                self.assertIsNone(self.reader.speed(image)[0])

    def test_decimal_damage_still_rejects(self):
        image = self.crop('1p6')
        ImageDraw.Draw(image).rectangle((36, 19, 38, 21), fill=(0, 0, 0))
        self.assertIsNone(self.reader.speed(image)[0])

    def test_mode_validation(self):
        with self.assertRaises(ValueError):
            HudReader('original', speed_raster='box_3840_to_2560')
        with self.assertRaises(ValueError):
            HudReader('restart-20261001', speed_raster='any')


if __name__ == '__main__':
    unittest.main()
