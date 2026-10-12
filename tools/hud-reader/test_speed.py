"""Manually read moving frames and independently drawn speed/fault coverage."""
import fixture_data  # noqa: F401  restores packed test images first
import json, unittest
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT, SPEED
from validate import speed_fixture

MOVING=ROOT/'runs/sol-separator-live-2252'
FOUR_FRAME=ROOT/'runs/sol-speed-live-2303/frame-1790809416784-001.png'
DECIMAL_RUN=ROOT/'runs/sol-four-live-2309'

class MovingSpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.reader=HudReader();cls.base=Image.open(ROOT/'sample.png').convert('RGB')

    def test_actual_manually_read_moving_frames(self):
        manifest=[]
        for index,distance,speed in [(1,2205,8.9),(2,2173,21.8)]:
            file=MOVING/f'frame-1790808721870-00{index}.png'
            row=self.reader.read(Image.open(file))
            self.assertTrue(row['valid'],row['reasons'])
            self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(distance,speed,3770))
            manifest.append({'file':str(file.relative_to(ROOT)),'source':'live_sky_capture',
                'label_source':'independent manual visual inspection of saved pixels',
                'distance':distance,'speed':speed,'max_hp':3770,'observation':row})
        manifest.append({'file':str(FOUR_FRAME.relative_to(ROOT)),'source':'live_sky_capture',
            'label_source':'independent manual visual inspection of saved pixels',
            'distance':2075,'speed':15.4,'max_hp':3770,'observation':self.reader.read(Image.open(FOUR_FRAME))})
        for index,distance,speed in [(1,2011,13.3),(2,1993,20.0)]:
            file=DECIMAL_RUN/f'frame-1790809793199-00{index}.png'
            manifest.append({'file':str(file.relative_to(ROOT)),'source':'live_sky_capture',
                'label_source':'independent manual visual inspection of saved pixels',
                'distance':distance,'speed':speed,'max_hp':3770,'observation':self.reader.read(Image.open(file))})
        (ROOT/'fixtures/moving-speed-manifest.json').write_text(json.dumps(manifest,indent=2))

    def test_actual_15_4_with_full_width_sixteen_pixel_four(self):
        row=self.reader.read(Image.open(FOUR_FRAME))
        self.assertTrue(row['valid'],row['reasons'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(2075,15.4,3770))

    def test_actual_13_3_and_20_0_with_three_pixel_decimal(self):
        for index,distance,speed in [(1,2011,13.3),(2,1993,20.0)]:
            row=self.reader.read(Image.open(DECIMAL_RUN/f'frame-1790809793199-00{index}.png'))
            self.assertTrue(row['valid'],row['reasons'])
            self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(distance,speed,3770))

    def test_three_pixel_decimal_faults_reject(self):
        for name in ['missing','corner_missing','misplaced','extra','overheight','overwidth']:
            with self.subTest(fault=name):
                im=Image.open(DECIMAL_RUN/'frame-1790809793199-002.png').convert('RGB')
                draw=ImageDraw.Draw(im)
                if name=='missing':draw.rectangle((1418,1132,1420,1133),fill=(35,45,49))
                elif name=='corner_missing':im.putpixel((1418,1132),(35,45,49))
                elif name=='misplaced':
                    draw.rectangle((1418,1132,1420,1133),fill=(35,45,49))
                    draw.rectangle((1418,1129,1420,1130),fill='white')
                elif name=='extra':draw.rectangle((1375,1132,1377,1133),fill='white')
                elif name=='overheight':draw.rectangle((1418,1131,1420,1131),fill='white')
                elif name=='overwidth':draw.rectangle((1421,1132,1421,1133),fill='white')
                row=self.reader.read(im);self.assertFalse(row['valid'],row);self.assertIsNone(row['speed'])

    def test_real_four_raster_in_each_legal_speed_position(self):
        # Copy actual saved-pixel 4 only, not a classifier-generated template,
        # into independently drawn integer and fractional slots.
        raster=Image.open(FOUR_FRAME).crop((1417,1116,1428,1134))
        for label,slots in [('4.0',[0]),('24.0',[1]),('40.0',[0]),('20.4',[2]),('44.0',[0,1])]:
            im=speed_fixture(self.base,label)
            width=14*sum(ch!='.' for ch in label)+6;start=1405-width//2
            origins=[start+14*i for i in range(label.index('.'))]
            origins.append(start+14*label.index('.')+6)
            for slot in slots:
                ImageDraw.Draw(im).rectangle((origins[slot],1116,origins[slot]+11,1133),fill=(35,45,49))
                im.paste(raster,(origins[slot],1116))
            with self.subTest(speed=label):
                row=self.reader.read(im);self.assertTrue(row['valid'],row['reasons']);self.assertEqual(row['speed'],float(label))

    def test_real_four_clipping_or_partial_segment_rejects(self):
        for name,box,color in [
            ('upper endpoint clipped',(1417,1117,1427,1118),(35,45,49)),
            ('lower endpoint clipped',(1417,1132,1427,1133),(35,45,49)),
            ('upper left partial',(1417,1119,1418,1122),(35,45,49)),
            ('lower right erased',(1426,1126,1427,1131),(35,45,49)),
            ('spurious top',(1420,1116,1423,1117),(255,255,255))]:
            with self.subTest(fault=name):
                im=Image.open(FOUR_FRAME).convert('RGB');ImageDraw.Draw(im).rectangle(box,fill=color)
                row=self.reader.read(im);self.assertFalse(row['valid'],row);self.assertIsNone(row['speed'])

    def test_independent_drawings_cover_every_fraction_digit_and_narrow_one(self):
        for value in [f'20.{i}' for i in range(10)]+['1.0','11.1','21.8','0.0','44.0']:
            with self.subTest(speed=value):
                row=self.reader.read(speed_fixture(self.base,value))
                self.assertTrue(row['valid'],row['reasons']);self.assertEqual(row['speed'],float(value))

    def test_moderate_brightness_variation_and_background_noise_preserve_read(self):
        im=speed_fixture(self.base,'21.8');draw=ImageDraw.Draw(im)
        # Recognition remains binary at the existing 160 threshold. Small
        # non-white background noise cannot connect unrelated glyphs.
        for x in range(SPEED[0]+2,SPEED[2]-2,3):
            im.putpixel((x,SPEED[1]+2),(135,140,145))
        for y in range(SPEED[1]+4,SPEED[1]+22):
            for x in range(SPEED[0]+2,SPEED[2]-2):
                if im.getpixel((x,y))==(255,255,255):im.putpixel((x,y),(200,203,206))
        row=self.reader.read(im);self.assertTrue(row['valid']);self.assertEqual(row['speed'],21.8)

    def test_corrupt_actual_narrow_one_never_defaults_to_one(self):
        for name,box,color in [
            ('upper erased',(1405,1117,1407,1123),(35,45,49)),
            ('lower erased',(1405,1126,1407,1132),(35,45,49)),
            ('entire one erased',(1405,1117,1407,1132),(35,45,49)),
            ('terminal row clipped',(1405,1117,1407,1117),(35,45,49)),
            ('gap filled',(1405,1124,1407,1125),(255,255,255)),
            ('partial interior',(1405,1119,1406,1122),(35,45,49))]:
            with self.subTest(fault=name):
                im=Image.open(MOVING/'frame-1790808721870-002.png').convert('RGB')
                ImageDraw.Draw(im).rectangle(box,fill=color)
                row=self.reader.read(im);self.assertFalse(row['valid']);self.assertIsNone(row['speed'])

    def test_decimal_clipping_layout_segments_and_range_faults_reject(self):
        faults=[]
        no_decimal=speed_fixture(self.base,'21.8');ImageDraw.Draw(no_decimal).rectangle((1410,1132,1411,1133),fill=(35,45,49))
        faults.append(('missing decimal',no_decimal))
        misplaced=speed_fixture(self.base,'21.8');draw=ImageDraw.Draw(misplaced)
        draw.rectangle((1410,1132,1411,1133),fill=(35,45,49));draw.rectangle((1410,1129,1411,1130),fill='white')
        faults.append(('misplaced decimal',misplaced))
        extra=speed_fixture(self.base,'21.8');ImageDraw.Draw(extra).rectangle((1375,1132,1376,1133),fill='white');faults.append(('extra decimal',extra))
        clipped=speed_fixture(self.base,'20.0');ImageDraw.Draw(clipped).rectangle((1360,1116,1440,1117),fill=(35,45,49));faults.append(('top clipped',clipped))
        shift=speed_fixture(self.base,'20.0');crop=shift.crop(SPEED);ImageDraw.Draw(shift).rectangle(SPEED,fill=(35,45,49));shift.paste(crop,(SPEED[0],SPEED[1]-2));faults.append(('shifted layout',shift))
        ambiguous=speed_fixture(self.base,'0.0');ImageDraw.Draw(ambiguous).rectangle((1391,1124,1393,1125),fill='white');faults.append(('partial middle segment',ambiguous))
        faults.extend((f'out of range {value}',speed_fixture(self.base,value)) for value in ['44.1','99.9'])
        for name,im in faults:
            with self.subTest(fault=name):
                row=self.reader.read(im);self.assertFalse(row['valid'],row);self.assertIsNone(row['speed'])

if __name__=='__main__':unittest.main()
