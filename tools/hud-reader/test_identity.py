"""Actual failed-preflight frame pair plus independent target-change/fault cases."""
import fixture_data  # noqa: F401  restores packed test images first
import json, unittest
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np
from hud_reader import HudReader, ROOT, TARGET_HP, white, glyphs, normalize

DRY=ROOT/'runs/sol-water-dry-2226/frame-1790807205672-000.png'
LIVE=ROOT/'runs/sol-water-live-2227/frame-1790807274365-000.png'
APPROVED=ROOT/'runs/sol-human-approval-2252/frame-1790808363195-000.png'

class TargetIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.reader=HudReader()

    def test_actual_two_pixel_raster_change_keeps_recognized_3770_identity(self):
        a=Image.open(DRY).convert('RGB');b=Image.open(LIVE).convert('RGB')
        ga=glyphs(white(a,TARGET_HP))[-4:];gb=glyphs(white(b,TARGET_HP))[-4:]
        self.assertEqual(sum(np.count_nonzero(normalize(x[2])!=normalize(y[2])) for x,y in zip(ga,gb)),2)
        rows=[self.reader.read(im) for im in (a,b)]
        for row in rows:
            self.assertTrue(row['valid']);self.assertEqual(row['target_max_hp'],3770)
            self.assertEqual(row['target_identity'],'asteroid:hp:3770')
            self.assertEqual(row['distance'],2211);self.assertEqual(row['speed'],0)
        (ROOT/'fixtures/identity-pair-observations.json').write_text(json.dumps({'source_frames':[str(DRY.relative_to(ROOT)),str(LIVE.relative_to(ROOT))],
            'manual_labels':{'max_hp':3770,'distance':2211,'speed':0},'observations':rows},indent=2))

    def test_different_real_maximum_hp_does_not_match(self):
        baseline=self.reader.read(Image.open(DRY))
        for file,hp in [('live-01.png',4168),('live-02.png',4292),('live-09.png',3622)]:
            row=self.reader.read(Image.open(ROOT/'fixtures/live'/file))
            self.assertTrue(row['valid']);self.assertEqual(row['target_max_hp'],hp)
            self.assertNotEqual(row['target_identity'],baseline['target_identity'])

    def test_actual_approved_frame_with_shorter_raster_slash(self):
        im=Image.open(APPROVED).convert('RGB')
        self.assertEqual(glyphs(white(im,TARGET_HP))[-5][2].shape,(9,5))
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['target_max_hp'],row['distance'],row['speed']),(3770,2211,0))
        self.assertEqual(row['target_identity'],self.reader.read(Image.open(LIVE))['target_identity'])

    def test_missing_malformed_or_clipped_separator_rejects(self):
        base=Image.open(APPROVED).convert('RGB')
        # Independently constructed fault strokes, including one with the same
        # dimensions/pixel count as the valid slash but reversed orientation.
        strokes={
            'missing':[],
            'vertical_one':[(2,y) for y in range(10)],
            'backslash':[(y//2,y) for y in range(10)],
            'dense_block':[(x,y) for x in range(6) for y in range(10)],
            'clipped_half':[(4-y//2,y) for y in range(5)],
            'wrong_position':[(8+4-y//2,y) for y in range(9)],
        }
        for name,stroke in strokes.items():
            with self.subTest(name=name):
                im=base.copy();ImageDraw.Draw(im).rectangle((863,77,876,90),fill=(125,10,15))
                for x,y in stroke:im.putpixel((865+x,79+y),(255,255,255))
                row=self.reader.read(im)
                self.assertFalse(row['valid']);self.assertIsNone(row['target_identity'])

    def test_missing_or_malformed_maximum_digit_rejects(self):
        for name in ('missing','noise'):
            with self.subTest(name=name):
                im=Image.open(APPROVED).convert('RGB')
                ImageDraw.Draw(im).rectangle((877,77,889,90),fill=(125,10,15))
                if name=='noise':
                    for y in range(10):
                        for x in range(10):
                            if (x+y)%3==0:im.putpixel((877+x,79+y),(255,255,255))
                row=self.reader.read(im)
                self.assertFalse(row['valid']);self.assertIsNone(row['target_identity'])

    def test_real_planetoid_is_not_an_asteroid_identity(self):
        row=self.reader.read(Image.open(ROOT/'fixtures/live/live-03.png'))
        self.assertFalse(row['valid']);self.assertIsNone(row['target_identity']);self.assertIsNone(row['target_max_hp'])

    def test_occluded_maximum_hp_rejects_instead_of_guessing(self):
        im=Image.open(LIVE).convert('RGB')
        ImageDraw.Draw(im).rectangle((877,84,889,89),fill=(75,8,10))
        row=self.reader.read(im);self.assertFalse(row['valid']);self.assertIsNone(row['target_identity'])
        self.assertTrue(any('hp' in reason for reason in row['reasons']))

    def test_current_hp_can_drop_without_changing_maximum_identity(self):
        im=Image.open(LIVE).convert('RGB');draw=ImageDraw.Draw(im)
        zero=glyphs(white(Image.open(ROOT/'fixtures/calibration/hp-3770-current.png'),(0,0,64,14)))[-1][2]
        draw.rectangle((800,77,859,90),fill=(125,10,15))
        for y,x in zip(*np.where(zero)):im.putpixel((842+int(x),79+int(y)),(255,255,255))
        row=self.reader.read(im);self.assertTrue(row['valid']);self.assertEqual(row['target_identity'],'asteroid:hp:3770')

if __name__=='__main__':unittest.main()
