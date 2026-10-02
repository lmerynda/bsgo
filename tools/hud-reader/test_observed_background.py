"""Saved real faults and bounded contamination/occlusion regressions."""
import unittest
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT

CASES=[
 ('sol-page-live-0012','frame-1790813557046-000.png',1862,0.0,4366),
 ('sol-page-water-0018','frame-1790813998678-000.png',1863,0.0,3730),
 ('sol-static-0114','frame-1790817218587-000.png',2067,0.0,3338),
 ('sol-static-0121','frame-1790817668601-012.png',645,44.0,3131),
 ('sol-water-0125','frame-1790817952724-005.png',1163,44.0,3844),
]

class ObservedBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.reader=HudReader()

    def test_five_saved_false_rejections_read_all_fields(self):
        for run,file,distance,speed,hp in CASES:
            with self.subTest(run=run):
                row=self.reader.read(Image.open(ROOT/'runs'/run/file))
                self.assertTrue(row['valid'],row)
                self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),
                                 (distance,speed,hp))

    def test_speed_exterior_filter_does_not_hide_decimal_or_interior_faults(self):
        base=Image.open(ROOT/'runs'/CASES[1][0]/CASES[1][1]).convert('RGB')
        for box in [(1360,1132,1362,1133),(1388,1112,1394,1115)]:
            with self.subTest(box=box):
                im=base.copy();ImageDraw.Draw(im).rectangle(box,fill='white')
                self.assertIsNone(self.reader.read(im)['speed'])

    def test_distance_border_recovery_requires_strong_complete_core(self):
        base=Image.open(ROOT/'runs'/CASES[-1][0]/CASES[-1][1]).convert('RGB')
        for box in [(756,139,758,139),(754,141,761,147),(710,138,804,139)]:
            with self.subTest(box=box):
                im=base.copy();ImageDraw.Draw(im).rectangle(box,fill='white')
                self.assertIsNone(self.reader.read(im)['distance'])

    def test_hp_exterior_filter_preserves_four_digit_count_and_separator(self):
        base=Image.open(ROOT/'runs'/CASES[3][0]/CASES[3][1]).convert('RGB')
        for box in [(914,76,921,90),(865,76,872,90),(914,83,921,90)]:
            with self.subTest(box=box):
                im=base.copy();ImageDraw.Draw(im).rectangle(box,fill=(70,8,10))
                row=self.reader.read(im)
                self.assertFalse(row['valid']);self.assertIsNone(row['target_identity'])

if __name__=='__main__':unittest.main()
