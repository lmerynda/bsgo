"""Post-relaunch pixel calibration; saved labels and independent fault cases."""
import time
import unittest
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT, SPEED
from validate import speed_fixture

class RestartCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader=HudReader('restart-20261001')
        cls.base=Image.open(ROOT/'runs/restart-calibration-20261001/capture-1.jpg').convert('RGB')

    def test_three_later_real_stopped_captures(self):
        # Same stopped target: temporal regression, not independent motion coverage.
        for path in (ROOT/'runs/restart-calibration-20261001').glob('*.jpg'):
            with self.subTest(path=path.name):
                row=self.reader.read(Image.open(path))
                self.assertTrue(row['valid'],row['reasons'])
                self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2555,0.0,4213))

    def test_profile_mismatch_rejects(self):
        self.assertFalse(HudReader().read(self.base)['valid'])
        self.assertFalse(self.reader.read(Image.open(ROOT/'sample.png'))['valid'])

    def test_line_preflight_scene_padding_and_corrupted_speed(self):
        im=Image.open(ROOT/'runs/miner-line02-3970-20261002/frame-1790906633724-000.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(1220,0.0,3970))
        # Missing strokes, a missing decimal, and noise attached to a digit
        # cannot be mistaken for the detached planetoid outside the text area.
        for box,color in [((1388,1117,1399,1118),(20,20,20)),
                          ((1403,1132,1405,1134),(20,20,20)),
                          ((1389,1135,1392,1138),'white')]:
            bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=color)
            self.assertIsNone(self.reader.speed(bad)[0])

    def test_line_braking_one_has_complete_two_row_middle_gap(self):
        im=Image.open(ROOT/'runs/miner-line04-water3813-a/frame-1790907450090-016.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(714,15.6,3813))
        for box,color in [((1390,1117,1392,1117),(20,20,20)),
                          ((1390,1134,1392,1134),(20,20,20)),
                          ((1390,1125,1392,1125),'white'),
                          ((1390,1127,1392,1131),(20,20,20))]:
            bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=color)
            self.assertIsNone(self.reader.speed(bad)[0])

    def test_line_distance_isolated_gap_pixel_does_not_hide_missing_digit(self):
        im=Image.open(ROOT/'runs/miner-line04-water3511-b/frame-1790907845429-000.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(1825,0.0,3511))
        bad=im.copy();draw=ImageDraw.Draw(bad)
        draw.rectangle((752,137,764,152),fill=(20,20,20))
        draw.point((760,143),fill='white')
        self.assertIsNone(self.reader.distance(bad)[0])

    def test_actual_motion_before_fail_stop(self):
        labels=[(2555,0.0),(2548,8.3),(2514,23.2)]
        paths=sorted((ROOT/'runs/mining-20261001-2013').glob('frame*.png'))
        self.assertEqual(len(paths),len(labels))
        for path,(distance,speed) in zip(paths,labels):
            with self.subTest(path=path.name):
                row=self.reader.read(Image.open(path))
                self.assertTrue(row['valid'],row['reasons'])
                self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(distance,speed,4213))

    def test_second_motion_trial_distance_variant(self):
        for index,distance,speed in [(0,2445,0.0),(1,2439,7.2)]:
            path=ROOT/f'runs/mining-20261001-2019/frame-1790885963460-00{index}.png'
            row=self.reader.read(Image.open(path))
            self.assertTrue(row['valid'],row['reasons'])
            self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(distance,speed,4213))

    def test_observed_complete_hp_two_and_identity_faults(self):
        im=Image.open(ROOT/'runs/mining-20261001-2136/frame-1790890600348-000.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2172,0.0,3762))
        self.assertEqual(row['target_identity'],'asteroid:hp:3762')
        # Independently constructed missing digit, lower-half occlusion and
        # separator loss must not become the newly calibrated identity.
        for box in [(923,76,933,90),(923,83,933,90),(865,76,873,90)]:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=(125,10,15))
                result=self.reader.read(bad)
                self.assertFalse(result['valid'])
                self.assertIsNone(result['target_identity'])
        other=self.reader.read(Image.open(ROOT/'runs/mining-20261001-2057-stopped/frame-1790888178781-000.png'))
        self.assertTrue(other['valid'])
        self.assertNotEqual(other['target_identity'],row['target_identity'])

    def test_actual_full_speed_fours_and_clipping(self):
        im=Image.open(ROOT/'runs/mining-20261001-2024/frame-1790886260489-004.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2247,44.0,4213))
        for box in [(1388,1117,1399,1117),(1388,1134,1399,1134),
                    (1388,1121,1390,1123),(1397,1127,1399,1131)]:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=(35,45,49))
                self.assertIsNone(self.reader.speed(bad)[0])

    def test_actual_braking_one_and_corruptions(self):
        im=Image.open(ROOT/'runs/mining-20261001-2028/frame-1790886491767-020.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(525,21.7,4213))
        faults=[((1405,1117,1407,1117),(35,45,49)),
                ((1405,1134,1407,1134),(35,45,49)),
                ((1405,1118,1407,1124),(35,45,49)),
                ((1405,1125,1407,1125),'white')]
        for box,color in faults:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=color)
                self.assertIsNone(self.reader.speed(bad)[0])

    def test_actual_title_background_star_and_wrong_titles(self):
        im=Image.open(ROOT/'runs/mining-20261001-2051/frame-1790887874238-010.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(478,6.4,3995))
        bad=im.copy();ImageDraw.Draw(bad).rectangle((759,52,770,68),fill=(35,45,49))
        self.assertFalse(self.reader.read(bad)['asteroid_selected'])

    def test_actual_title_left_padding_star_and_partial_first_letter(self):
        im=Image.open(ROOT/'runs/mining-20261001-2140/frame-1790890836456-002.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2132,22.7,3762))
        for box in [(729,52,744,68),(729,52,744,59),(729,59,744,68)]:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=(35,45,49))
                self.assertFalse(self.reader.read(bad)['asteroid_selected'])

    def test_actual_distance_exterior_star_and_corruptions(self):
        path=ROOT/'runs/mining-20261001-2057-stopped/frame-1790888178781-000.png'
        im=Image.open(path).convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(475,0.0,3995))
        for box,color in [((710,143,710,145),'white'),
                          ((736,137,739,138),'white'),
                          ((765,137,781,153),(35,45,49)),
                          ((736,139,747,142),(35,45,49))]:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=color)
                self.assertIsNone(self.reader.distance(bad)[0])
        planetoid=Image.open(ROOT/'runs/route-depth-20261001-2045/02-planetoid.jpg')
        self.assertFalse(self.reader.read(planetoid)['asteroid_selected'])
        # Bright interior pixels are still part of the observed glyph mask.
        bad=im.copy();ImageDraw.Draw(bad).rectangle((805,56,815,60),fill='white')
        self.assertFalse(self.reader.read(bad)['asteroid_selected'])

    def test_actual_upper_title_padding_star(self):
        im=Image.open(ROOT/'runs/mining-20261001-2150/frame-1790891459902-004.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(1893,44.0,3762))

    def test_second_client_preflight_exterior_pixels_and_faults(self):
        im=Image.open(ROOT/'runs/mining-20261001-2346/frame-1790898415825-000.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2730,0.0,3034))
        # Exterior filtering must not conceal a missing digit/decimal, a shift
        # clipping the crop, digit-column noise, or a larger padding fragment.
        distance_faults=[(712,150,712,152),(731,139,740,148),
                         (780,151,782,152)]
        for box in distance_faults:
            with self.subTest(distance_fault=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill='white' if box[0]!=731 else (35,45,49))
                self.assertIsNone(self.reader.distance(bad)[0])
        speed_faults=[((1403,1132,1403,1132),(35,45,49)),
                      ((1388,1117,1399,1134),(35,45,49)),
                      ((1388,1135,1390,1136),'white')]
        for box,color in speed_faults:
            with self.subTest(speed_fault=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=color)
                self.assertIsNone(self.reader.speed(bad)[0])
        padding=im.copy();ImageDraw.Draw(padding).rectangle((1362,1135,1367,1138),fill='white')
        self.assertEqual(self.reader.speed(padding)[0],0.0)
        for box in [self.reader.distance_box,self.reader.speed_box]:
            bad=im.copy();region=im.crop(box);ImageDraw.Draw(bad).rectangle(box,fill=(35,45,49))
            bad.paste(region,(box[0],box[1]+3))
            field=self.reader.distance if box==self.reader.distance_box else self.reader.speed
            self.assertIsNone(field(bad)[0])

    def test_title_exterior_noise_and_shifted_word(self):
        im=self.base.copy();draw=ImageDraw.Draw(im)
        for box in [(724,52,833,55),(724,66,833,67),(724,56,728,65),(825,56,833,65)]:
            draw.rectangle(box,fill='white')
        self.assertTrue(self.reader.read(im)['valid'])
        base=self.base;word=base.crop(self.reader.title_box)
        for dx,dy in [(-1,0),(1,0),(0,-1),(0,1)]:
            with self.subTest(shift=(dx,dy)):
                bad=base.copy();ImageDraw.Draw(bad).rectangle((724,52,834,68),fill=(35,45,49))
                bad.paste(word,(729+dx,56+dy))
                self.assertFalse(self.reader.read(bad)['asteroid_selected'])

    def test_new_separator_threshold_keeps_geometry_guard(self):
        base=Image.open(ROOT/'runs/mining-20261001-2013/frame-1790885606183-002.png').convert('RGB')
        for name in ['absent','vertical','reversed','short','filled']:
            with self.subTest(fault=name):
                im=base.copy();draw=ImageDraw.Draw(im)
                draw.rectangle((865,76,877,90),fill=(110,25,30))
                if name=='vertical':draw.line((870,79,870,88),fill=(255,155,165))
                elif name=='reversed':draw.line((867,79,872,88),fill=(255,155,165))
                elif name=='short':draw.line((872,80,869,85),fill=(255,155,165))
                elif name=='filled':draw.rectangle((867,79,872,88),fill=(255,155,165))
                self.assertIsNone(self.reader.maximum_hp(im)[0])

    def test_actual_faint_separator_and_missing_strokes(self):
        im=Image.open(ROOT/'runs/mining-20261001-2200/frame-1790891997879-013.png').convert('RGB')
        row=self.reader.read(im)
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(751,44.0,3762))
        # Restore observed faint pixels, never fill an actually absent stroke.
        for box in [(866,87,873,89),(866,81,873,83),(866,76,875,90)]:
            with self.subTest(box=box):
                bad=im.copy();ImageDraw.Draw(bad).rectangle(box,fill=(125,10,15))
                self.assertIsNone(self.reader.maximum_hp(bad)[0])

    def test_corrupted_actual_hud_rejects(self):
        faults=[
            ('missing decimal',(1403,1132,1405,1134)),
            ('fraction erased',(1408,1117,1419,1134)),
            ('integer upper segment erased',(1388,1117,1399,1118)),
            ('HP separator erased',(867,76,875,90)),
            ('partial final distance digit',(772,137,783,142)),
            ('title erased',(724,52,950,68)),
        ]
        for name,box in faults:
            with self.subTest(fault=name):
                im=self.base.copy();ImageDraw.Draw(im).rectangle(box,fill=(35,45,49))
                self.assertFalse(self.reader.read(im)['valid'])
        for name,box in [('decimal corner',(1403,1132,1403,1132)),
                         ('decimal overheight',(1403,1131,1405,1131))]:
            with self.subTest(fault=name):
                im=self.base.copy()
                ImageDraw.Draw(im).rectangle(box,fill=(35,45,49) if name=='decimal corner' else 'white')
                self.assertIsNone(self.reader.speed(im)[0])

    def test_independent_motion_patterns_in_calibrated_positions(self):
        # Synthetic digits: exercises decoder/guards; does not validate live motion.
        for text in [f'20.{i}' for i in range(10)]+['0.0','1.0','11.1','27.1','37.3','44.0']:
            with self.subTest(speed=text):
                source=speed_fixture(self.base,text)
                im=self.base.copy();im.paste(source.crop(SPEED),(1360,1113))
                width=14*sum(ch!='.' for ch in text)+6
                dot=1405-width//2+14*text.index('.')+1
                draw=ImageDraw.Draw(im)
                draw.rectangle((dot,1132,dot+2,1134),fill='white')
                # Full-speed production supplies a complete observed 4 raster;
                # the old independently drawn 4 has the original shorter phase.
                four=Image.open(ROOT/'runs/mining-20261001-2024/frame-1790886260489-004.png').crop((1388,1117,1400,1135))
                one=Image.open(ROOT/'runs/mining-20261001-2028/frame-1790886491767-020.png').crop((1405,1117,1408,1135))
                start=1405-width//2
                position=start
                for ch in text:
                    if ch=='.':position+=6;continue
                    if ch=='4':im.paste(four,(position,1117))
                    if ch=='1':
                        draw.rectangle((position,1117,position+11,1134),fill=(35,45,49))
                        im.paste(one,(position+9,1117))
                    position+=14
                self.assertEqual(self.reader.speed(im)[0],float(text))

    def test_size_and_freshness_guards(self):
        self.assertFalse(self.reader.read(self.base.resize((1920,1080)))['valid'])
        self.assertIn('stale_or_future_frame',self.reader.read(self.base,time.time()-10)['reasons'])

if __name__=='__main__':unittest.main()
