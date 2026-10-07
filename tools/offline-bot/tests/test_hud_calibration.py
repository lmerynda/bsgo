"""Synthetic geometry transport using saved, manually measured native HUD crops.

These tests do not establish native OCR accuracy at a new game resolution.
"""
from dataclasses import replace
import unittest

from PIL import Image

from bsgo_offline.hud_calibration import HudAnchorCalibrator
from bsgo_offline.startup_layout import StartupHudRecognizer
from test_large_hud import fixture_image
from test_startup_layout import sample


def relocated(size=(1920,1200), *, panel_delta=(-200,30), speed_delta=(-400,-100)):
    reader = StartupHudRecognizer(size)
    data = reader.calibrator.data
    original = fixture_image()
    image = Image.new('RGB',size)
    boxes = {}
    for name in ('title_box','distance_box','hp_box','portrait_box','hull_box','speed_text_box'):
        box = data[name]
        dx,dy = ((50,20) if name == 'hull_box' else speed_delta if name == 'speed_text_box' else panel_delta)
        boxes[name] = tuple(value+(dx if index%2==0 else dy) for index,value in enumerate(box))
        image.paste(original.crop(box),boxes[name][:2])
    return image,boxes


class HudCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.image,self.boxes = relocated()
        self.reader = StartupHudRecognizer(self.image.size)

    def read(self,sequence,at=None, *, image=None,epoch=3):
        frame = replace(sample(image or self.image),acquisition_sequence=sequence,
                        acquired_at=sequence*.8 if at is None else at,session_epoch=epoch)
        return self.reader(frame)

    def ready(self):
        for index in range(1,4):
            result = self.read(index)
        self.assertTrue(result.healthy)
        return result

    def assert_unknown(self,result):
        self.assertFalse(result.healthy)
        self.assertTrue(all(not field.valid and field.value is None
            for name,field in result.fields.items() if name != 'ocr_evidence'))
        self.assertEqual(result.fields['ocr_evidence'].value['startup_calibration']['status'],'pending')

    def test_independent_relocation_works_across_aspect_ratios_without_frame_stretching(self):
        for size,delta in (((1920,1200),(-400,-100)),((1600,900),(-550,-320))):
            with self.subTest(size=size):
                image,boxes = relocated(size,speed_delta=delta)
                self.reader = StartupHudRecognizer(size)
                for index in range(1,4):
                    result = self.read(index,image=image)
                    if index < 3:
                        self.assert_unknown(result)
                self.assertTrue(result.healthy)
                self.assertEqual(result.frame_size,size)
                self.assertEqual(result.fields['distance'].value,1517)
                self.assertEqual(result.fields['hull_current'].value,5005)
                evidence = result.fields['ocr_evidence'].value
                self.assertEqual(evidence['startup_calibration']['hud_scale'],1.)
                self.assertEqual(evidence['startup_calibration']['region_boxes_in_normalized_pixels']['title_box'],boxes['title_box'])
                self.assertFalse(evidence['startup_calibration']['numeric_accuracy_independently_validated'])
                self.assertFalse(evidence['startup_layout']['control_supported'])

    def test_repeated_or_reordered_captures_cannot_confirm_a_layout(self):
        self.read(1)
        self.read(2)
        self.assert_unknown(self.read(2))
        self.assertEqual(self.reader.calibrator.count,0)
        self.assert_unknown(self.read(3))
        self.assert_unknown(self.read(4,at=1.))
        self.assertEqual(self.reader.calibrator.count,0)
        self.assert_unknown(self.read(4))
        self.assert_unknown(self.read(5))
        self.assertTrue(self.read(6).healthy)

    def test_freshness_gap_and_session_change_require_new_agreement(self):
        self.ready()
        self.assert_unknown(self.read(4,at=6.))
        self.assertEqual(self.reader.calibrator.count,1)
        self.assert_unknown(self.read(1,epoch=4))
        self.assertEqual(self.reader.calibrator.count,1)
        self.assert_unknown(self.read(2,epoch=4))
        self.assertTrue(self.read(3,epoch=4).healthy)

    def test_moving_anchor_restarts_registration_and_missing_anchor_clears_it(self):
        self.ready()
        moved,_ = relocated(panel_delta=(-180,40))
        self.assert_unknown(self.read(4,image=moved))
        self.assertEqual(self.reader.calibrator.count,1)
        self.assert_unknown(self.read(5,image=moved))
        self.assertTrue(self.read(6,image=moved).healthy)
        self.assert_unknown(self.read(7,image=Image.new('RGB',self.image.size)))
        self.assertIsNone(self.reader.calibrator.registration)

    def test_duplicate_readable_anchors_are_ambiguous(self):
        for name,point in (('title_box',(950,96)),('hull_box',(80,170)),('speed_text_box',(800,850))):
            with self.subTest(anchor=name):
                image = self.image.copy()
                image.paste(self.image.crop(self.boxes[name]),point)
                calibrator = HudAnchorCalibrator(self.reader.reader.hud._reader._large)
                self.assertIsNone(calibrator.update(image,context=1,sequence=1,observed_at=1.))
                self.assertIn('ambiguous',calibrator.reason)
                self.assertEqual(calibrator.count,0)

    def test_numeric_corruption_cannot_confirm_panel_offsets(self):
        image = self.image.copy()
        image.paste((0,0,0),self.boxes['distance_box'])
        self.assert_unknown(self.read(1,image=image))
        self.assertEqual(self.reader.calibrator.reason,'registered_regions_failed_existing_font_guards')
        self.assertEqual(self.reader.calibrator.count,0)

    def test_lossy_scale_does_not_relax_existing_font_guards(self):
        image = fixture_image().resize((3840,2160),Image.Resampling.NEAREST)
        self.reader = StartupHudRecognizer(image.size)
        for index in range(1,4):
            self.assert_unknown(self.read(index,image=image))
        self.assertEqual(self.reader.calibrator.count,0)

    def test_window_origin_change_and_wrong_profile_invalidate_prior_registration(self):
        self.ready()
        frame = replace(sample(self.image),acquisition_sequence=4,acquired_at=3.2)
        moved = replace(frame,geometry=replace(frame.geometry,screen_origin=(50,60)))
        self.assert_unknown(self.reader(moved))
        self.assertEqual(self.reader.calibrator.count,1)
        wrong = replace(moved,layout='original',acquisition_sequence=5,acquired_at=4.)
        self.assert_unknown(self.reader(wrong))
        self.assertEqual(self.reader.calibrator.count,0)


if __name__ == '__main__':
    unittest.main()
