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
                        ('title','title_box'), ('speed-0p0','speed_box'),
                        ('own-hp','hull_box')]:
        with Image.open(FIXTURES/(name+'.png')) as crop:
            im.paste(crop, tuple(data[field][:2]))
    return im


def native(im, layout=PROFILE):
    return NativeFrame(1, 1, layout, time.monotonic(), *im.size, im.tobytes(),
                       WindowGeometry(1, 2, 'fixture', im.size, (0,0)))


class LargeHudTests(unittest.TestCase):
    def test_measured_three_digit_hp_ratio_has_complete_anchored_glyphs(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for damage in (None,'clip_three','remove_three','clip_one','remove_separator',
                       'clip_maximum','shift_current'):
            im = fixture_image()
            with Image.open(FIXTURES/'hp-351-4015.png') as crop:
                im.paste(crop,tuple(data['hp_box'][:2]))
            boxes = {'clip_three':(949,92,959,92),'remove_three':(949,92,959,102),
                     'clip_one':(982,92,985,92),'remove_separator':(993,91,999,102),
                     'clip_maximum':(1060,92,1060,102)}
            if damage in boxes:
                ImageDraw.Draw(im).rectangle(boxes[damage],fill='black')
            elif damage == 'shift_current':
                current = im.crop((949,92,986,103))
                ImageDraw.Draw(im).rectangle((949,92,985,102),fill='black')
                im.paste(current,(950,92))
            fields = HudRecognizer(PROFILE)(native(im)).fields
            if damage is None:
                self.assertEqual(fields['target_current_hp'].value,351)
                self.assertEqual(fields['target_max_hp'].value,4015)
                self.assertTrue(fields['target_identity'].valid)
            else:
                self.assertFalse(fields['target_current_hp'].valid,damage)
                self.assertFalse(fields['target_max_hp'].valid,damage)

    def test_complete_measured_hull_rasters_and_fresh_mining_adapter_fields(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for value in (5005,3295,1638,1742,2127):
            im = fixture_image()
            with Image.open(FIXTURES/f'hull-{value}-5005.png') as crop:
                im.paste(crop,tuple(data['hull_box'][:2]))
            sample = native(im)
            fields = MiningHudRecognizer(PROFILE)(sample).fields
            for name, expected in [('hull_current',value),('hull_max',5005)]:
                self.assertTrue(fields[name].valid)
                self.assertEqual(fields[name].value,expected)
                self.assertEqual(fields[name].observed_at,sample.acquired_at)

    def test_own_hull_remains_readable_without_an_asteroid_selected(self):
        im = fixture_image()
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        ImageDraw.Draw(im).rectangle(tuple(data['title_box']),fill='black')
        fields = MiningHudRecognizer(PROFILE)(native(im)).fields
        self.assertIs(fields['asteroid_selected'].value,False)
        self.assertEqual(fields['hull_current'].value,5005)
        self.assertEqual(fields['hull_max'].value,5005)

    def test_hull_partial_missing_shifted_and_wrong_profile_remain_unknown(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        # Complete1638/5005 includes a measured tall6 and narrow1.
        for box in [(134,92,137,92),(134,92,137,102),
                    (143,91,154,91),(272,92,272,102),(196,91,202,102)]:
            im = fixture_image()
            with Image.open(FIXTURES/'hull-1638-5005.png') as crop:
                im.paste(crop,tuple(data['hull_box'][:2]))
            ImageDraw.Draw(im).rectangle(box,fill='black')
            fields = MiningHudRecognizer(PROFILE)(native(im)).fields
            self.assertFalse(fields['hull_current'].valid,box)
            self.assertFalse(fields['hull_max'].valid,box)
            self.assertIsNone(fields['hull_current'].value)
        im = fixture_image()
        old = MiningHudRecognizer('restart-20261001')(native(im,'restart-20261001'))
        for name in ('hull_current','hull_max'):
            self.assertFalse(old.fields[name].valid)
            self.assertEqual(old.fields[name].reason,'mining_recognizer_not_validated:'+name)
        bad_size = MiningHudRecognizer(PROFILE)(native(Image.new('RGB',(1920,1080))))
        self.assertFalse(bad_size.fields['hull_current'].valid)
        wrong = MiningHudRecognizer(PROFILE)(native(im,'restart-20261001'))
        self.assertFalse(wrong.fields['hull_current'].valid)

    def test_measured_star_beyond_complete_speed_font_extent_is_excluded(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'speed-0p0-star-right.png') as crop:
            im.paste(crop,tuple(data['speed_box'][:2]))
        self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['speed'].value,0.)
        # Light crossing the retained right margin still blocks recognition.
        ImageDraw.Draw(im).rectangle((1460,1063,1461,1064),fill='white')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_measured_internal_narrow_one_recenters_complete_hp_label(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for damage in (None, 'clip_one', 'remove_one', 'remove_current_digit'):
            im = fixture_image()
            with Image.open(FIXTURES/'hp-2446-4102.png') as crop:
                im.paste(crop, tuple(data['hp_box'][:2]))
            if damage == 'clip_one':
                ImageDraw.Draw(im).rectangle((1036,92,1039,92),fill='black')
            elif damage == 'remove_one':
                ImageDraw.Draw(im).rectangle((1036,92,1039,102),fill='black')
            elif damage == 'remove_current_digit':
                ImageDraw.Draw(im).rectangle((936,92,947,102),fill='black')
            fields = HudRecognizer(PROFILE)(native(im)).fields
            if damage is None:
                self.assertEqual(fields['target_current_hp'].value,2446)
                self.assertEqual(fields['target_max_hp'].value,4102)
                self.assertTrue(fields['target_identity'].valid)
            else:
                self.assertFalse(fields['target_current_hp'].valid,damage)
                self.assertFalse(fields['target_max_hp'].valid,damage)

    def test_measured_star_wholly_in_margin_preserves_complete_speed(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'speed-33p0-star-margin.png') as crop:
            im.paste(crop, tuple(data['speed_box'][:2]))
        self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['speed'].value,33.)
        # A leading glyph extending into the margin must not be cleared.
        ImageDraw.Draw(im).rectangle(tuple(data['speed_box']),fill='black')
        with Image.open(FIXTURES/'speed-33p0-star-margin.png') as crop:
            im.paste(crop,(data['speed_box'][0]-4,data['speed_box'][1]))
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_measured_stars_outside_speed_text_do_not_hide_complete_digits(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        for fixture in ('speed-0p0-star-near.png', 'speed-0p0-star-far.png'):
            im = fixture_image()
            with Image.open(FIXTURES/fixture) as crop:
                im.paste(crop, tuple(data['speed_box'][:2]))
            self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['speed'].value, 0.)
            # Light intruding into the text region still invalidates its raster.
            ImageDraw.Draw(im).rectangle((1406,1063,1408,1064),fill='white')
            self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_speed_text_position_is_anchored(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        ImageDraw.Draw(im).rectangle(tuple(data['speed_box']),fill='black')
        with Image.open(FIXTURES/'speed-0p0.png') as crop:
            im.paste(crop, (data['speed_box'][0]+8,data['speed_box'][1]))
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_measured_complete_trailing_four_leaves_six_pixel_slash_gap(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'hp-4384-4384.png') as crop:
            im.paste(crop, tuple(data['hp_box'][:2]))
        fields = HudRecognizer(PROFILE)(native(im)).fields
        self.assertEqual(fields['target_current_hp'].value, 4384)
        self.assertEqual(fields['target_max_hp'].value, 4384)
        # Remove the final 4's leading column; its remainder must stay unknown.
        x, y = data['hp_box'][:2]
        ImageDraw.Draw(im).rectangle((x+52,y+4,x+52,y+14), fill='black')
        damaged = HudRecognizer(PROFILE)(native(im)).fields
        self.assertEqual(damaged['target_max_hp'].value, 4384)
        self.assertFalse(damaged['target_current_hp'].valid)

    def test_measured_tall_hp_six_and_removed_endpoint(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'hp-4166-4166.png') as crop:
            im.paste(crop, tuple(data['hp_box'][:2]))
        full = HudRecognizer(PROFILE)(native(im))
        self.assertEqual(full.fields['target_current_hp'].value, 4166)
        self.assertEqual(full.fields['target_max_hp'].value, 4166)
        ImageDraw.Draw(im).rectangle((1057,91,1068,91),fill='black')
        clipped = HudRecognizer(PROFILE)(native(im))
        self.assertFalse(clipped.fields['target_max_hp'].valid)
        self.assertFalse(clipped.fields['target_current_hp'].valid)

    def test_complete_two_digit_speed_and_clipped_fraction(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'speed-10p7.png') as crop:
            im.paste(crop, tuple(data['speed_box'][:2]))
        self.assertEqual(HudRecognizer(PROFILE)(native(im)).fields['speed'].value, 10.7)
        ImageDraw.Draw(im).rectangle((1446,1062,1467,1091), fill='black')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['speed'].valid)

    def test_measured_hp_label_recentered_by_narrow_final_one(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'hp-3743-3781.png') as crop:
            im.paste(crop, tuple(data['hp_box'][:2]))
        result = HudRecognizer(PROFILE)(native(im))
        self.assertEqual(result.fields['target_current_hp'].value, 3743)
        self.assertEqual(result.fields['target_max_hp'].value, 3781)
        self.assertTrue(result.fields['target_identity'].valid)
        ImageDraw.Draw(im).rectangle((1006,91,1012,102), fill='black')
        self.assertFalse(HudRecognizer(PROFILE)(native(im)).fields['target_identity'].valid)

    def test_missing_current_hp_glyph_rejects_current_without_changing_maximum(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'hp-4166-4166.png') as crop:
            im.paste(crop, tuple(data['hp_box'][:2]))
        x, y = data['hp_box'][:2]
        ImageDraw.Draw(im).rectangle((x+10,y+4,x+23,y+14), fill='black')
        result = HudRecognizer(PROFILE)(native(im))
        self.assertEqual(result.fields['target_max_hp'].value, 4166)
        self.assertFalse(result.fields['target_current_hp'].valid)
        self.assertIn('unvalidated_current_hp_digit_count',
                      result.fields['target_current_hp'].reason)

    def test_measured_one_pixel_leading_four_is_complete_current_hp(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        im = fixture_image()
        with Image.open(FIXTURES/'hp-4500-4500.png') as crop:
            im.paste(crop, tuple(data['hp_box'][:2]))
        fields = HudRecognizer(PROFILE)(native(im)).fields
        self.assertEqual(fields['target_current_hp'].value, 4500)
        self.assertEqual(fields['target_max_hp'].value, 4500)
        # Removing its top row is a partial raster, even though max HP stays valid.
        x, y = data['hp_box'][:2]
        ImageDraw.Draw(im).rectangle((x+1,y+4,x+14,y+4), fill='black')
        damaged = HudRecognizer(PROFILE)(native(im)).fields
        self.assertEqual(damaged['target_max_hp'].value, 4500)
        self.assertFalse(damaged['target_current_hp'].valid)

    def test_current_hp_clipping_and_exceeding_maximum_reject(self):
        data = json.loads((ROOT/'largehud-calibration.json').read_text())
        x, y = data['hp_box'][:2]
        im = fixture_image()
        with Image.open(FIXTURES/'hp-4166-4166.png') as crop:
            im.paste(crop, (x, y))
        ImageDraw.Draw(im).rectangle((x+10,y+4,x+23,y+4), fill='black')
        clipped = HudRecognizer(PROFILE)(native(im))
        self.assertEqual(clipped.fields['target_max_hp'].value, 4166)
        self.assertFalse(clipped.fields['target_current_hp'].valid)

        im = fixture_image()  # 3335/3335
        with Image.open(FIXTURES/'hp-4166-4166.png') as crop:
            ImageDraw.Draw(im).rectangle((x,y,x+71,y+17), fill='black')
            im.paste(crop.crop((0,0,72,18)), (x,y))
        impossible = HudRecognizer(PROFILE)(native(im))
        self.assertEqual(impossible.fields['target_max_hp'].value, 3335)
        self.assertFalse(impossible.fields['target_current_hp'].valid)
        self.assertEqual(impossible.fields['target_current_hp'].reason,
                         'current_hp_exceeds_maximum')

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
        self.assertEqual(old.fields['target_current_hp'].reason,
                         'mining_recognizer_not_validated:target_current_hp')

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
