"""Startup geometry adaptation using saved HUD crops and mocked Windows APIs."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from bsgo_offline.manual_assist_session import native_binding
from bsgo_offline.native_capture import NativeFrame, WindowGeometry
from bsgo_offline.startup_layout import StartupLayout, StartupHudRecognizer, PROFILE
from test_large_hud import fixture_image


def sample(image, *, layout=PROFILE):
    return NativeFrame(7, 3, layout, 12., *image.size, image.tobytes(),
                       WindowGeometry(17,42,"Battlestar Galactica Online",image.size,(20,30)))


class StartupLayoutTests(unittest.TestCase):
    def test_dynamic_sizes_require_anchor_calibration_without_inferring_hud_scale(self):
        for size in ((1280,720),(1920,1080),(3200,1800),(3840,2160)):
            layout=StartupLayout(size)
            self.assertEqual(layout.mode,"auto_calibration")
            self.assertFalse(layout.control_supported)
            self.assertEqual(layout.metadata()["client_size"],size)
        self.assertTrue(StartupLayout((2560,1440)).control_supported)
        self.assertEqual(StartupLayout((1920,1200)).mode,"auto_calibration")
        for size in ((0,1080),(-1,1440),(True,1440),(2560,), (2560.,1440)):
            with self.assertRaises(ValueError):
                StartupLayout(size)
        with self.assertRaises(ValueError):
            StartupHudRecognizer((2560,1440),profile="original")

    def test_native_size_preserves_current_large_hud_readings(self):
        frame=sample(fixture_image())
        reader=StartupHudRecognizer(frame.frame_size)
        expected=reader.reader(frame)
        actual=reader(frame)
        self.assertEqual(actual.frame_size,frame.frame_size)
        for name in ("distance","speed","target_max_hp","target_current_hp","hull_current","hull_max"):
            self.assertEqual(actual.fields[name],expected.fields[name])
        self.assertEqual(actual.fields["ocr_evidence"].value["resampling"],"none")

    def test_scaled_observation_preserves_native_identity_and_marks_experimental(self):
        # A generated derivative exercises coordinate transport only; it is not
        # native accuracy evidence for a differently configured game window.
        image=fixture_image().resize((5120,2880),Image.Resampling.NEAREST)
        frame=sample(image)
        original=frame.rgb
        reader=StartupHudRecognizer(image.size)
        for index in range(3):
            fresh=replace(frame,acquisition_sequence=7+index,acquired_at=12.+index*.8)
            result=reader(fresh)
            self.assertTrue(result.healthy)
            self.assertEqual(result.fields['distance'].valid,index==2)
        self.assertTrue(result.healthy)
        self.assertEqual(result.frame_size,(5120,2880))
        self.assertEqual(frame.rgb,original)
        self.assertEqual((frame.acquisition_sequence,frame.session_epoch),(7,3))
        evidence=result.fields["ocr_evidence"].value
        self.assertEqual(evidence["source_frame_size"],(5120,2880))
        self.assertEqual(evidence["recognition_frame_size"],(2560,1440))
        self.assertEqual(evidence["resampling_validation"],"experimental_unvalidated")
        self.assertFalse(evidence["startup_layout"]["control_supported"])
        for field in result.fields.values():
            self.assertEqual(field.observed_at,13.6)
        self.assertEqual(evidence["startup_calibration"]["status"],"registered")
        self.assertFalse(evidence["startup_calibration"]["input_authorized"])
        self.assertFalse(result.fields["inventory"].valid)

    def test_resize_wrong_profile_and_missing_anchors_publish_unknowns(self):
        cases=((StartupHudRecognizer((1920,1080)),sample(Image.new("RGB",(1600,900))),"startup_client_dimensions_changed"),
               (StartupHudRecognizer((2560,1440)),sample(fixture_image(),layout="original"),"layout_profile_mismatch"),
               (StartupHudRecognizer((1920,1200)),sample(Image.new("RGB",(1920,1200))),"selected_panel_anchor_missing_or_ambiguous"))
        for reader,frame,reason in cases:
            with self.subTest(reason=reason):
                reader.reader=Mock(side_effect=AssertionError("must not read mismatched pixels"))
                result=reader(frame)
                self.assertEqual(result.frame_size,frame.frame_size)
                self.assertEqual(result.healthy,reason=='selected_panel_anchor_missing_or_ambiguous')
                self.assertEqual(result.reason,reason)
                self.assertTrue(all(not field.valid and field.value is None
                    for name,field in result.fields.items() if name not in ("ocr_evidence", "ship_telemetry")))

    def test_ship_observation_does_not_depend_on_an_asteroid_or_target_registration(self):
        from test_ship_telemetry import fixture
        image = fixture(0)  # Manually labelled combat instruments, no asteroid panel.
        reader = StartupHudRecognizer(image.size)
        result = reader(sample(image))
        self.assertTrue(result.healthy)
        self.assertFalse(result.fields['hull_current'].valid)
        self.assertFalse(result.fields['speed'].valid)
        ship = result.fields['ship_telemetry'].value
        self.assertEqual((ship['hull_current'],ship['hull_max'],ship['speed']), (4684,5005,25.7))
        self.assertEqual((ship['energy_current'],ship['energy_max']), (671,800))
        self.assertTrue(ship['display_only'])

    def test_independent_ship_readings_survive_missing_target_at_a_relocated_size(self):
        from test_ship_telemetry import fixture
        native_image = fixture(0)
        reader = StartupHudRecognizer((1920,1200))
        data = reader.reader.hud._reader._large.data
        image = Image.new('RGB',(1920,1200))
        hull = data['hull_box']
        for name,box,delta in (
                ('hull',hull,(30,10)),
                ('energy',tuple(v+(24 if i%2 else 0) for i,v in enumerate(hull)),(30,10)),
                ('speed',data['speed_text_box'],(-400,-100))):
            image.paste(native_image.crop(box),(box[0]+delta[0],box[1]+delta[1]))
        frame = sample(image)
        for index in range(3):
            result = reader(replace(frame,acquisition_sequence=7+index,acquired_at=12.+index*.8))
        self.assertTrue(result.healthy)
        self.assertEqual(result.reason,'selected_panel_anchor_missing_or_ambiguous')
        self.assertFalse(result.fields['distance'].valid)
        self.assertFalse(result.fields['speed'].valid)
        ship = result.fields['ship_telemetry'].value
        self.assertEqual((ship['hull_current'],ship['hull_max'],ship['speed']), (4684,5005,25.7))
        self.assertEqual((ship['energy_current'],ship['energy_max']), (671,800))
        # Losing a target can never authorize a mining routine via display values.
        from bsgo_offline.manual_assist import target_reading
        from bsgo_offline.state import Snapshot
        snapshot = Snapshot(3,3,9,13.6,13.7,frame.layout,image.size,result.fields,result.healthy,result.reason)
        self.assertIsNone(target_reading(snapshot,13.7)[0])

    def test_nonnative_binding_captures_actual_size_but_cannot_focus_or_dispatch(self):
        for size in ((1920,1080),(3840,2160),(1920,1200)):
            with self.subTest(size=size):
                geometry=WindowGeometry(17,42,"Battlestar Galactica Online",size,(20,30))
                capture=Mock(geometry=geometry)
                api=Mock()
                with patch("bsgo_offline.native_capture.inspect_window",return_value=geometry) as inspect,\
                        patch("bsgo_offline.native_capture._get_api",return_value=api),\
                        patch("bsgo_offline.native_capture.NativeCapture",return_value=capture) as capture_class,\
                        patch("bsgo_offline.native_capture.NativeSource"),\
                        patch("bsgo_offline.native_input.NativeInputDispatcher") as dispatcher_class:
                    bound,source,reader,dispatcher,ready,handover=native_binding(17,42,PROFILE,allow_focus_handover=True)
                    self.assertEqual(bound.client_size,size)
                    self.assertIsNone(handover)
                    self.assertEqual(ready(),"game_client_dimensions_not_control_validated")
                    source(.5,action=False)
                    self.assertFalse(capture_class.call_args.kwargs["require_foreground"])
                    self.assertEqual(reader().layout.client_size,size)
                    with self.assertRaisesRegex(ValueError,"not_control_validated"):
                        source(.5)
                    with self.assertRaisesRegex(ValueError,"not_control_validated"):
                        dispatcher(Mock(),Mock())
                    dispatcher_class.assert_not_called()
                    api.user32.SetForegroundWindow.assert_not_called()
                    inspect.return_value=replace(geometry,client_size=(1600,900))
                    with self.assertRaisesRegex(ValueError,"pinned_game_window_changed"):
                        source(.5,action=False)


if __name__=="__main__":
    unittest.main()
