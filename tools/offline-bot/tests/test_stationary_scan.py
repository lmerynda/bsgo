"""Scanner-only evidence does not widen navigation authority."""
from dataclasses import replace
import time
import unittest
from PIL import Image, ImageDraw
from bsgo_offline.stationary_scan import StationaryScanReader, stationary_scan_values
from bsgo_offline.recognition import HudRecognizer
from bsgo_offline.manual_assist import ManualAssistController, target_reading
from bsgo_offline.manual_routines import PinnedRecognizer
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.state import FieldValue
from test_manual_assist import snapshot, drained
from test_trace import scratch_directory
from test_ship_telemetry import fixture
from test_large_hud import fixture_image


def scan_snapshot(now=None):
    row = snapshot(at=time.monotonic() if now is None else now)
    fields = dict(row.fields)
    for name in ('speed','hull_current','hull_max'):
        fields[name] = FieldValue(None,row.acquired_at,False,'translucent_native_text')
    fields['stationary_scan_evidence'] = FieldValue({
        'profile':'native-stationary-scanner-v1','input_scope':'scanner_only',
        'frame_size':(2560,1440),'stationary_verified':True,'full_hull_verified':True,
        'speed':0.,'hull_current':5005,'hull_max':5005},row.acquired_at)
    return replace(row,frame_size=(2560,1440),fields=fields)


class StationaryScanTests(unittest.TestCase):
    def test_native_stationary_and_moving_corpus(self):
        reader = StationaryScanReader(HudRecognizer('largehud-20261003')._reader._large)
        stopped = reader.read(fixture_image())
        self.assertTrue(stopped['stationary_verified'])
        self.assertTrue(stopped['full_hull_verified'])
        for index in (0,1,2,3,4,5,6):
            self.assertFalse(reader.read(fixture(index))['stationary_verified'],index)
        image = fixture_image()
        ImageDraw.Draw(image).rectangle((1422,1084,1424,1086),fill='black')
        self.assertFalse(reader.read(image)['stationary_verified'])
        self.assertFalse(reader.read(Image.new('RGB',(2560,1440)))['stationary_verified'])

    def test_scanner_evidence_cannot_admit_navigation(self):
        row = scan_snapshot()
        self.assertIsNotNone(target_reading(row,row.acquired_at,for_scan=True)[0])
        self.assertIsNone(target_reading(row,row.acquired_at)[0])
        with scratch_directory() as directory:
            controller = ManualAssistController(directory,snapshot_provider=lambda:row,
                runner=lambda *a,**kw:drained(),clock=lambda:row.acquired_at)
            state = controller.actions_snapshot()
            self.assertTrue(state['routines']['scan_selected']['ready'])
            self.assertFalse(state['routines']['approach_current_target']['ready'])
            token = state['assessment_context']['token']
            decision = controller.start_action('approach_current_target',{
                'assessment_token':token,'supervisor_assessment':{
                    k:True for k in ('safe_stopped_scene','clear_corridor','aligned_heading','flight_point')}})
            self.assertFalse(decision['accepted'])
            self.assertTrue(controller.close())

    def test_rejections_and_calibrated_contradictions(self):
        row = scan_snapshot(100.)
        self.assertIsNone(stationary_scan_values(row,102.))
        self.assertIsNone(stationary_scan_values(replace(row,healthy=False),100.))
        self.assertIsNone(stationary_scan_values(replace(row,frame_size=(4,3)),100.))
        for change in ({'input_scope':'navigation'},{'speed':1.},{'hull_current':4000},
                       {'stationary_verified':False},{'full_hull_verified':False},
                       {'hull_max':5005.0},{'speed':False}):
            fields = dict(row.fields)
            fields['stationary_scan_evidence'] = FieldValue({**fields['stationary_scan_evidence'].value,**change},100.)
            self.assertIsNone(stationary_scan_values(replace(row,fields=fields),100.),change)
        fields = dict(row.fields)
        fields['speed'] = FieldValue(4.,100.)
        self.assertIsNone(stationary_scan_values(replace(row,fields=fields),100.))

    def test_scan_executor_rechecks_independent_hull_without_promoting_fields(self):
        row = scan_snapshot()
        pin, _ = target_reading(row, row.acquired_at, for_scan=True)
        pid, hwnd = map(int, row.window_id.split(':'))
        frame = NativeFrame(1, 1, row.layout, row.acquired_at, 2560, 1440,
            bytes(2560*1440*3), WindowGeometry(hwnd,pid,'fixture',(2560,1440),(0,0)))
        def recognize(sample):
            return RecognitionResult(row.frame_size, row.fields)
        observed = PinnedRecognizer(recognize,pin,for_scan=True)(frame)
        self.assertTrue(observed.healthy)
        self.assertIsNone(observed.reason)
        self.assertFalse(observed.fields['hull_current'].valid)
        self.assertTrue(observed.fields['stationary_scan_evidence'].valid)
        strict = PinnedRecognizer(recognize,pin)(frame)
        self.assertEqual(strict.reason,'pinned_target_field_unknown:hull_current')
        fields = dict(row.fields)
        fields['stationary_scan_evidence'] = FieldValue({
            **fields['stationary_scan_evidence'].value,'hull_current':4000},row.acquired_at)
        row = replace(row,fields=fields)
        damaged = PinnedRecognizer(recognize,pin,for_scan=True)(frame)
        self.assertIsNotNone(damaged.reason)
        self.assertFalse(damaged.fields['stationary_scan_evidence'].valid)

if __name__=='__main__':
    unittest.main()
