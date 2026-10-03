"""Development-crop regressions for explicit enlarged native HUD geometry."""
import json
from pathlib import Path
import time
import unittest

from PIL import Image, ImageDraw
from bsgo_offline.recognition import HudRecognizer
from bsgo_offline.mining_recognition import MiningHudRecognizer
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.diagnostics import source_manifest

ROOT = Path(__file__).resolve().parents[2] / 'hud-reader'
FIXTURES = ROOT / 'fixtures' / 'largehud-20261003'
PROFILE = 'largehud-20261003'


def fixture_image():
    data = json.loads((ROOT/'largehud-calibration.json').read_text())
    im = Image.new('RGB', (2560,1440))
    for name, field in [('distance','distance_box'), ('hp','hp_box'),
                        ('title','title_box'), ('speed-0p0','speed_box')]:
        with Image.open(FIXTURES/(name+'.png')) as crop:
            im.paste(crop, tuple(data[field][:2]))
    return im


def native(im, layout=PROFILE):
    return NativeFrame(1, 1, layout, time.monotonic(), *im.size, im.tobytes(),
                       WindowGeometry(1, 2, 'fixture', im.size, (0,0)))


class LargeHudTests(unittest.TestCase):
    def test_measured_motion_crops_and_clipped_one(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for label in ('1p0', '1p9', '1p1', '0p1', '1p3', '0p7'):
            im = fixture_image()
            with Image.open(FIXTURES/('speed-'+label+'.png')) as crop:
                im.paste(crop, tuple(data['speed_box'][:2]))
            self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['speed'].value,
                             float(label.replace('p','.')))
            # Remove the top endpoint of a measured one, in either position.
            x = 1408 if label.startswith('1') else 1431
            ImageDraw.Draw(im).rectangle((x,1066,x+2,1068), fill='black')
            self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_damaged_twelve_column_three_is_unknown(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for box in [(1423,1066,1432,1066), (1423,1075,1432,1077)]:
            im = fixture_image()
            with Image.open(FIXTURES/'speed-1p3.png') as crop:
                im.paste(crop, tuple(data['speed_box'][:2]))
            ImageDraw.Draw(im).rectangle(box, fill='black')
            self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_missing_top_bar_cannot_turn_seven_into_one(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'speed-0p7.png') as crop:
            im.paste(crop, tuple(data['speed_box'][:2]))
        ImageDraw.Draw(im).rectangle((1431,1066,1442,1068), fill='black')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_measured_tall_distance_six_and_clipped_variant(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'distance-1677.png') as crop:
            im.paste(crop, tuple(data['distance_box'][:2]))
        self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['distance'].value, 1677)
        ImageDraw.Draw(im).rectangle((857,161,868,161), fill='black')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['distance'].valid)

    def test_labeled_stationary_crops_read_without_resampling(self):
        result = HudRecognizer(PROFILE)(native(fixture_image()))
        self.assertTrue(result.healthy)
        expected = {'distance':1517, 'speed':0., 'asteroid_selected':True, 'target_max_hp':3335}
        for name, value in expected.items():
            self.assertTrue(result.fields[name].valid, (name, result.fields[name]))
            self.assertEqual(result.fields[name].value, value)
        self.assertEqual(result.fields['ocr_evidence'].value['resampling'], 'none')

    def test_missing_decimal_and_partial_segment_are_unknown(self):
        for box in [(1423,1084,1426,1087), (1406,1070,1408,1074),
                    (1406,1066,1416,1087)]:
            im = fixture_image()
            ImageDraw.Draw(im).rectangle((box[0],box[1],box[2]-1,box[3]-1), fill='black')
            field = HudRecognizer(PROFILE)(native(im)).fields['speed']
            self.assertFalse(field.valid, box)
            self.assertIsNone(field.value)

    def test_clipped_distance_glyph_is_unknown(self):
        im = fixture_image()
        ImageDraw.Draw(im).rectangle((886,162,897,164), fill='black')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['distance'].valid)

    def test_missing_hp_separator_never_yields_target_identity(self):
        im = fixture_image()
        ImageDraw.Draw(im).rectangle((1001,91,1007,102), fill='black')
        result = HudRecognizer(PROFILE)(native(im))
        self.assertFalse(result.fields['target_identity'].valid)
        self.assertFalse(result.fields['target_max_hp'].valid)

    def test_profile_is_explicit_and_rejects_old_geometry(self):
        reader = HudRecognizer(PROFILE)
        mismatch = reader(native(fixture_image(), 'restart-20261001'))
        self.assertFalse(mismatch.healthy)
        self.assertEqual(mismatch.reason, 'layout_profile_mismatch')
        old = HudRecognizer('restart-20261001')(native(fixture_image(), 'restart-20261001'))
        self.assertFalse(old.fields['speed'].valid)
        self.assertFalse(old.fields['distance'].valid)

    def test_wrong_size_and_converted_source_reject(self):
        result = HudRecognizer(PROFILE)(native(Image.new('RGB',(1920,1080))))
        self.assertFalse(result.healthy)
        with self.assertRaisesRegex(ValueError,'requires_native_pixels'):
            HudRecognizer(PROFILE, source_frame_size=(3840,2160))

    def test_development_profile_does_not_claim_mining_capabilities(self):
        recognizer = MiningHudRecognizer(PROFILE)
        self.assertFalse(any(recognizer.capabilities.values()))
        result = recognizer(native(fixture_image()))
        self.assertFalse(result.fields['scan_result'].valid)

    def test_manifest_hashes_new_reader_and_calibration(self):
        hashes = source_manifest()['source_sha256']
        self.assertIn('hud-reader/large_hud_reader.py',hashes)
        self.assertIn('hud-reader/largehud-calibration.json',hashes)


if __name__ == '__main__':
    unittest.main()
