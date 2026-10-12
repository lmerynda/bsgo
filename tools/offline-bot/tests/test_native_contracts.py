"""Cross-boundary checks using saved pixels and journals, never desktop input."""
import fixture_data  # noqa: F401  restores packed test images first
from dataclasses import replace
from pathlib import Path
from threading import Event
import unittest

from PIL import Image

from bsgo_offline.input import RecordingInputBackend
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.native_input import DeliveryRecord, input_tag
from bsgo_offline.native_receiver import FIDUCIALS, STATE_MARKERS
from bsgo_offline.native_test import ReceiverPixelRecognizer, verify_received
from bsgo_offline.recognition import HudRecognizer
from bsgo_offline.replay import ReplaySource


class NativeContractsTests(unittest.TestCase):
    def test_native_rgb_and_saved_path_feed_same_ocr_without_profile_guessing(self):
        corpus=Path(__file__).resolve().parents[3]/'test-data/vision/v1'
        sample=ReplaySource(corpus/'manifest.json',source_run='sol-tylium-live-2321',
                            interval=0,limit=1,labeled_only=True).next_sample(Event())
        with Image.open(sample.path) as opened:
            image=opened.convert('RGB')
        geometry=WindowGeometry(1,2,'saved pixels',image.size,(0,0))
        frame=NativeFrame(1,1,'original',sample.acquired_at,*image.size,image.tobytes(),geometry)
        reader=HudRecognizer()
        saved,native=reader(sample),reader(frame)
        self.assertEqual(native.fields,saved.fields)
        self.assertTrue(native.fields['distance'].valid)
        wrong=reader(replace(frame,layout='native-client-rgb'))
        self.assertFalse(wrong.healthy)
        self.assertEqual(wrong.reason,'layout_profile_mismatch')

    def test_small_native_frame_cannot_authorize_numeric_hud_values(self):
        geometry=WindowGeometry(1,2,'small',(800,500),(0,0))
        frame=NativeFrame(1,1,'original',1.,800,500,bytes(800*500*3),geometry)
        result=HudRecognizer()(frame)
        self.assertFalse(result.healthy)
        self.assertTrue(all(not result.fields[name].valid for name in ('speed','distance')))

    def test_receiver_classifier_requires_fixture_identity_and_preserves_unknown_state(self):
        ready={'client_size':[800,500],'fiducials':FIDUCIALS,'state_markers':STATE_MARKERS}
        image=Image.new('RGB',(800,500))
        for marker in FIDUCIALS:
            image.putpixel(tuple(marker['client']),tuple(marker['rgb']))
        image.putpixel((340,190),tuple(STATE_MARKERS['selected']['rgb']['true']))
        image.putpixel((420,190),tuple(STATE_MARKERS['throttle']['rgb']['idle']))
        geometry=WindowGeometry(1,2,'fixture',image.size,(0,0))
        def frame():
            return NativeFrame(1,1,'native-client-rgb',1.,*image.size,image.tobytes(),geometry)
        recognizer=ReceiverPixelRecognizer(ready)
        result=recognizer(frame())
        self.assertTrue(result.healthy)
        self.assertIs(result.fields['selected'].value,True)
        image.putpixel((420,190),(0,0,0))
        unknown=recognizer(frame())
        self.assertFalse(unknown.fields['throttle'].valid)
        self.assertIsNone(unknown.fields['throttle'].value)
        image.putpixel((15,15),(0,0,0))
        wrong_fixture=recognizer(frame())
        self.assertFalse(wrong_fixture.healthy)
        self.assertFalse(wrong_fixture.fields['selected'].valid)

    def test_native_insertion_requires_matching_key_and_mouse_receipts(self):
        backend=RecordingInputBackend(width=800,height=500)
        backend.click_mouse(240,190)
        backend.press_key('A')
        requests=backend.records()
        deliveries=[DeliveryRecord(a.sequence,1.,a.kind,'injected',1,1,input_tag(a.sequence)) for a in requests]
        events=[{'kind':kind,'button':'left','x':240,'y':190,'extra_info':input_tag(1)}
                for kind in ('mouse_down','mouse_up')]
        events += [{'kind':a.kind,'key':a.key,'extra_info':input_tag(a.sequence)} for a in requests[1:]]
        self.assertEqual(verify_received(requests,deliveries,events)['matched_count'],3)
        with self.assertRaises(AssertionError):
            verify_received(requests,deliveries,events[:-1])
        with self.assertRaises(AssertionError):
            verify_received(requests,deliveries[:-1],events)
        with self.assertRaises(AssertionError):
            verify_received(requests,[replace(deliveries[0],outcome='partial'),*deliveries[1:]],events)
        events[0]['x']=250
        with self.assertRaises(AssertionError):
            verify_received(requests,deliveries,events)


if __name__=='__main__':
    unittest.main()
