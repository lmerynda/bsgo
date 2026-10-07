"""Native combat HUD crops and damaged/absent readout rejection."""
import hashlib
import json
from pathlib import Path
import time
import unittest

from PIL import Image, ImageDraw
from bsgo_offline.recognition import HudRecognizer
from bsgo_offline.ship_telemetry import ShipTelemetryReader
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.manual_assist import target_reading
from bsgo_offline.state import Snapshot


ROOT = Path(__file__).resolve().parents[2] / 'hud-reader' / 'fixtures' / 'ship-telemetry-20261007'


def fixture(index):
    image = Image.new('RGB', (2560, 1440))
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    for row in manifest['rows']:
        if row['sample'] == index:
            with Image.open(ROOT / row['file']) as crop:
                image.paste(crop, tuple(row['box'][:2]))
    return image


class ShipTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.hud = HudRecognizer('largehud-20261003')
        self.reader = ShipTelemetryReader(self.hud._reader._large)

    def test_native_crops_and_manual_labels(self):
        for row in json.loads((ROOT/'manifest.json').read_text())['rows']:
            self.assertEqual(hashlib.sha256((ROOT/row['file']).read_bytes()).hexdigest(), row['sha256'])
        for index, expected in ((0, (4684, 5005, 671, 800, 25.7)),
                                (1, (710, 5005, 728, 800, 33.0))):
            result = self.reader.read(fixture(index))
            self.assertEqual(tuple(result[name] for name in
                ('hull_current', 'hull_max', 'energy_current', 'energy_max', 'speed')), expected)
            self.assertEqual(result['reasons'], {})
            self.assertTrue(result['display_only'])

    def test_station_without_flight_instruments_is_unknown(self):
        result = self.reader.read(fixture(2))
        for name in ('hull_current', 'hull_max', 'energy_current', 'energy_max', 'speed'):
            self.assertIsNone(result[name])

    def test_erased_separator_and_complete_digit_do_not_become_smaller_values(self):
        for name, box, separator in [('hull',(128,88,279,106),(200,91,206,102)),
                                    ('energy',(128,112,279,130),(198,115,204,126))]:
            image = fixture(0)
            # Replace the translucent slash with neighboring same-row bar pixels.
            for y in range(separator[1], separator[3]+1):
                for x in range(separator[0], separator[2]+1):
                    image.putpixel((x,y), image.getpixel((separator[0]-3,y)))
            result = self.reader.read(image)
            self.assertIsNone(result[name+'_current'])
            self.assertIsNone(result[name+'_max'])
        image = fixture(0)
        ImageDraw.Draw(image).rectangle((129,91,142,103), fill='black')
        self.assertIsNone(self.reader.read(image)['hull_current'])

    def test_erased_speed_decimal_or_clipped_glyph_is_unknown(self):
        for box in ((1440,1084,1442,1086), (1406,1066,1418,1066),
                    (1448,1066,1459,1066)):
            image = fixture(0)
            ImageDraw.Draw(image).rectangle(box, fill='black')
            self.assertIsNone(self.reader.read(image)['speed'], box)

    def test_display_reader_never_promotes_calibrated_action_fields(self):
        image = fixture(0)
        sample = NativeFrame(1,1,'largehud-20261003', time.monotonic(), *image.size,
                             image.tobytes(), WindowGeometry(1,2,'fixture',image.size,(0,0)))
        result = self.hud(sample)
        self.assertEqual(result.fields['ship_telemetry'].value['hull_current'], 4684)
        self.assertFalse(result.fields['hull_current'].valid)
        self.assertFalse(result.fields['speed'].valid)
        snapshot = Snapshot(1,1,1,sample.acquired_at,time.monotonic(),sample.layout,
                            image.size,result.fields)
        self.assertIsNone(target_reading(snapshot,time.monotonic())[0])

    def test_non_native_layout_is_not_assumed(self):
        result = self.reader.read(Image.new('RGB',(1920,1080)))
        self.assertIsNone(result['speed'])
        self.assertEqual(result['reasons']['layout'], 'native_layout_required')
