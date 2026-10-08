"""Bounded visible scan decisions, cancellation and view guards with fake input."""
from dataclasses import replace
from pathlib import Path
import time
import unittest

import numpy as np
from PIL import Image

from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from bsgo_offline.visible_scan_probe import run_visible_scan_pass
from bsgo_offline.visible_scan_vision import visible_rock_candidates, view_fingerprint, same_view
from test_manual_assist import snapshot, request, drained
from test_scan_probe import FakeDispatcher, PixelSample, PROFILE
from test_trace import scratch_directory


class SceneSource:
    def __init__(self):
        self.sequence=0
        self.clicked=False
        self.scanned=False
        self.closed=False
        self.nonasteroid=False
        self.distance=800
        self.view_changed=False

    def next_sample(self, stop):
        if stop.wait(.04): return None
        self.sequence+=1
        values={'speed':0,'hull_current':5000,'hull_max':5000,
                'asteroid_selected':self.clicked and not self.nonasteroid,
                'target_identity':'asteroid:hp:3900','target_max_hp':3900,
                'target_portrait':tuple([2]*144),'distance':self.distance,
                'ship_telemetry':{'energy_current':getattr(self,'energy',827)}}
        sample=PixelSample(self.sequence,time.monotonic(),values)
        sample.geometry=None
        sample.window_id='42:17'
        sample.view_changed=self.view_changed
        sample.clicked=self.clicked
        sample.scanned=self.scanned
        if self.scanned:
            self.scan_frames=getattr(self,'scan_frames',0)+1
        sample.marker_hidden=self.scanned and getattr(self,'hide_scan_marker',False) and self.scan_frames<=2
        return sample

    def close(self): self.closed=True


def recognize(sample):
    return RecognitionResult((2560,1440), {name:FieldValue(value,sample.acquired_at)
        for name,value in sample.values.items()})


class SceneReader:
    def __init__(self, recognizer):
        self.recognizer=recognizer
        self.scenes={}

    def __call__(self,sample):
        self.scenes[sample.acquisition_sequence]={
            'candidates':[{'x':500,'y':500,'area':100}],
            'fingerprint':bytes([255 if sample.view_changed else 0])*14400,
            'marker':(475,475,525,525) if sample.clicked and not sample.marker_hidden else None,
            'scan_state':'scanned' if sample.scanned else 'unknown',
            'resource':'Water' if sample.scanned else None}
        return self.recognizer(sample)

    def scene_for(self,snapshot): return self.scenes.get(snapshot.acquisition_sequence)


class VisibleScanTests(unittest.TestCase):
    def run_pass(self,directory,source,*,cancel_click=False):
        cancel=directory/'cancel'
        dispatchers=[]
        def dispatcher(backend,journal):
            prior=backend.report_delivery
            def reported(record,outcome):
                prior(record,outcome)
                if outcome=='injected':
                    if record.kind=='mouse_click':
                        source.clicked=True
                        if cancel_click: cancel.touch()
                    if record.kind=='key_up': source.scanned=True
            backend.report_delivery=reported
            instance=FakeDispatcher(backend)
            dispatchers.append(instance)
            return instance
        result=run_visible_scan_pass(directory/'run',source_factory=lambda:source,
            recognizer_factory=lambda:recognize,dispatcher_factory=dispatcher,
            profile=PROFILE,expected_window_id='42:17',frame_size=(2560,1440),
            expected_scene={'context':{'layout':PROFILE},'hull_max':5000},
            safe_scene_assessed=True,scope='synthetic',cancel_file=cancel,
            scene_recognizer_factory=SceneReader)
        return result,dispatchers

    def test_select_verify_scan_once_and_record_color(self):
        with scratch_directory() as directory:
            source=SceneSource()
            result,dispatchers=self.run_pass(directory,source)
            self.assertEqual(result['state'],'recorded',result)
            self.assertEqual(result['visible_scan']['clicks_sent'],1)
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],1)
            self.assertEqual(result['visible_scan']['rows'][0]['resource'],'Water')
            self.assertTrue(all(result['shutdown'].values()))
            self.assertTrue(source.closed)

    def test_nonasteroid_and_out_of_range_never_scan(self):
        for change in ('nonasteroid','distance'):
            with self.subTest(change=change),scratch_directory() as directory:
                source=SceneSource()
                setattr(source,change,True if change=='nonasteroid' else 4500)
                result,_=self.run_pass(directory,source)
                self.assertEqual(result['state'],'recorded',result)
                self.assertEqual(result['visible_scan']['scan_inputs_sent'],0)
                expected='selection_unverified' if change=='nonasteroid' else 'outside_scanner_range'
                self.assertEqual(result['visible_scan']['rows'][0]['status'],expected)

    def test_stop_after_click_suppresses_z_and_drains(self):
        with scratch_directory() as directory:
            result,_=self.run_pass(directory,SceneSource(),cancel_click=True)
            self.assertTrue(result['cancel_requested'])
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],0)
            self.assertTrue(all(result['shutdown'].values()))

    def test_view_change_stops_before_scan(self):
        class ChangingSource(SceneSource):
            def next_sample(self,stop):
                if self.clicked: self.view_changed=True
                return super().next_sample(stop)
        with scratch_directory() as directory:
            result,_=self.run_pass(directory,ChangingSource())
            self.assertEqual(result['reason'],'view_changed_restart_scan',result)
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],0)

    def test_low_energy_stops_without_z(self):
        with scratch_directory() as directory:
            source=SceneSource()
            source.energy=70
            result,_=self.run_pass(directory,source)
            self.assertEqual(result['reason'],'energy_reserve_unverified_or_low')
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],0)

    def test_scan_animation_can_hide_marker_without_aborting_the_pass(self):
        with scratch_directory() as directory:
            source=SceneSource()
            source.hide_scan_marker=True
            result,_=self.run_pass(directory,source)
            self.assertEqual(result['state'],'recorded',result)
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],1)
            self.assertEqual(result['visible_scan']['rows'][0]['resource'],'Water')

    def test_known_scanned_selection_is_recorded_without_input(self):
        with scratch_directory() as directory:
            source=SceneSource()
            source.clicked=source.scanned=True
            result,_=self.run_pass(directory,source)
            self.assertEqual(result['state'],'recorded',result)
            self.assertEqual(result['visible_scan']['clicks_sent'],0)
            self.assertEqual(result['visible_scan']['scan_inputs_sent'],0)
            self.assertEqual(result['visible_scan']['rows'][0]['status'],'already_scanned')

    def test_controller_requires_token_assessment_and_drains_visible_job(self):
        with scratch_directory() as directory:
            row=snapshot()
            def runner(name,**kwargs): return drained()
            runner.supports_visible_scan=True
            controller=ManualAssistController(directory,snapshot_provider=lambda:row,
                runner=runner,clock=lambda:row.acquired_at+.1)
            self.assertTrue(controller.actions_snapshot()['routines']['scan_nearby_unscanned']['available'])
            self.assertFalse(controller.start_action('scan_nearby_unscanned',{})['accepted'])
            body=request(controller)
            body['assessment_token']=controller.actions_snapshot()['visible_assessment_context']['token']
            self.assertTrue(controller.start_action('scan_nearby_unscanned',body)['accepted'])
            self.assertTrue(controller.close())


class VisibleVisionTests(unittest.TestCase):
    def image(self):
        image=Image.new('RGB',(2560,1440))
        rgb=np.zeros((40,40,3),np.uint8)
        rgb[:]=(110,90,70)
        rgb[::2]=(70,56,44)
        patch=Image.fromarray(rgb)
        for point in ((500,500),(1100,700),(2200,500),(500,1100),(500,50)):
            image.paste(patch,point)
        return image

    def test_texture_proposals_exclude_hud_own_ship_and_other_geometry(self):
        image=self.image()
        rows=visible_rock_candidates(image)
        self.assertEqual(len(rows),1,rows)
        self.assertTrue(500<=rows[0]['x']<=540)
        self.assertEqual(visible_rock_candidates(image.resize((1280,720))),[])

    def test_view_guard_ignores_hud_and_rejects_scene_change(self):
        image=self.image()
        before=view_fingerprint(image)
        changed=image.copy()
        changed.paste('white',(2010,0,2560,1440))
        self.assertTrue(same_view(before,view_fingerprint(changed)))
        changed.paste('white',(0,200,2000,900))
        self.assertFalse(same_view(before,view_fingerprint(changed)))
