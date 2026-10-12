"""Independent visual labels for day-two production speed failures."""
import fixture_data  # noqa: F401  restores packed test images first
import unittest
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT

CASES = [
    ('mining-20261001-1510', 'frame-1790867455787-011.png', 27.1),
    ('mining-20261001-1517', 'frame-1790867886210-003.png', 37.3),
]

class DayTwoSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = HudReader()

    def test_saved_production_failures(self):
        for run, name, expected in CASES:
            with self.subTest(run=run):
                image = Image.open(ROOT / 'runs' / run / name)
                self.assertEqual(self.reader.speed(image)[0], expected)
                self.assertTrue(self.reader.read(image)['valid'])

    def test_two_column_gap_requires_complete_decimal_and_integer(self):
        base = Image.open(ROOT / 'runs' / CASES[1][0] / CASES[1][1]).convert('RGB')
        for name, box, color in [
            ('missing middle digit', (1405, 1116, 1415, 1133), (35, 45, 49)),
            ('decimal corner missing', (1418, 1132, 1418, 1132), (35, 45, 49)),
            ('decimal touches digit', (1416, 1132, 1417, 1133), 'white'),
            ('decimal overheight', (1418, 1131, 1420, 1131), 'white'),
            ('integer clipped', (1405, 1116, 1415, 1117), (35, 45, 49)),
        ]:
            with self.subTest(fault=name):
                image = base.copy()
                ImageDraw.Draw(image).rectangle(box, fill=color)
                self.assertIsNone(self.reader.speed(image)[0])

    def test_exterior_filter_does_not_hide_interior_or_decimal_noise(self):
        base = Image.open(ROOT / 'runs' / CASES[0][0] / CASES[0][1]).convert('RGB')
        for box in [(1370, 1132, 1372, 1133), (1388, 1112, 1394, 1115)]:
            with self.subTest(box=box):
                image = base.copy()
                ImageDraw.Draw(image).rectangle(box, fill='white')
                self.assertIsNone(self.reader.speed(image)[0])

if __name__ == '__main__':
    unittest.main()
