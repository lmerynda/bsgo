"""A click delivery alone must never certify target selection."""
import unittest
from unittest.mock import patch

from bsgo_offline.selection_probe import run_selection_probe
from bsgo_offline.recognition import RecognitionResult
from bsgo_offline.state import FieldValue
from test_scan_probe import PixelSource, FakeDispatcher, recognize, PROFILE
from test_trace import scratch_directory


class SelectionProbeTests(unittest.TestCase):
    def run_probe(self, directory, source, ignore=False, baseline_seconds=.25,
                  recognizer=recognize):
        return run_selection_probe(directory/'run',source_factory=lambda:source,
            recognizer_factory=lambda:recognizer,
            dispatcher_factory=lambda backend,journal:FakeDispatcher(backend,ignore=ignore),
            configuration={'fixture':True},profile=PROFILE,window_id='42:17',frame_size=(4,3),
            x=1,y=1,safe_scene_assessed=True,seconds=.25,
            baseline_seconds=baseline_seconds,scope='synthetic')

    def changed_source(self, **options):
        return PixelSource(change_after=4,changed={'target_max_hp':3500,
            'target_identity':'asteroid:hp:3500','distance':1300},**options)

    def test_requires_two_changed_post_delivery_images(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,self.changed_source())
            self.assertEqual(result['state'],'proved',result)
            self.assertEqual(len(result['matched_sequences']),2)
            self.assertTrue(result['selection_effect_verified'])
            self.assertFalse(result['mining_ready'])
            self.assertTrue(result['complete_diagnostics'])
            self.assertTrue(all(result['shutdown'].values()))

    def test_unchanged_selection_is_not_proved_by_click(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,PixelSource())
            self.assertEqual(result['reason'],'selection_change_not_observed')
            self.assertFalse(result['selection_effect_verified'])

    def test_ignored_click_cannot_claim_effect_even_when_pixels_change(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,self.changed_source(),ignore=True)
            self.assertFalse(result['selection_effect_verified'])

    def test_motion_or_context_change_blocks_probe(self):
        for source in (PixelSource(change_after=4,changed={'speed':1}),
                       self.changed_source(epoch_after=4)):
            with scratch_directory() as directory:
                result = self.run_probe(directory,source)
                self.assertEqual(result['state'],'blocked')
                self.assertFalse(result['selection_effect_verified'])
                self.assertTrue(all(result['shutdown'].values()))

    def test_no_prior_selection_can_become_verified_selection(self):
        with scratch_directory() as directory:
            source = self.changed_source()
            source.values.update(asteroid_selected=False,target_identity=None,target_max_hp=None)
            source.changed['asteroid_selected'] = True
            result = self.run_probe(directory,source)
            self.assertEqual(result['state'],'proved',result)

    def test_unreadable_baseline_speed_retries_then_selects(self):
        class FirstSpeedUnreadable(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample and sample.acquisition_sequence == 1:
                    sample.values['speed'] = None
                return sample
        with scratch_directory() as directory:
            result = self.run_probe(directory,FirstSpeedUnreadable(change_after=4,
                changed={'target_identity':'asteroid:hp:3500',
                         'target_max_hp':3500,'distance':1300}))
            self.assertEqual(result['state'],'proved',result)
            self.assertEqual([r['sequence'] for r in result['baseline']],[2,3])
            self.assertEqual(result['baseline_rejections'][0]['sequence'],1)
            self.assertEqual(len(list((directory/'run'/'failed-recognition').glob('*.json'))),1)

    def test_unreadable_speed_between_baselines_resets_consecutive_count(self):
        class InterruptedBaseline(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample and sample.acquisition_sequence == 2:
                    sample.values['speed'] = None
                return sample
        with scratch_directory() as directory:
            source = InterruptedBaseline(change_after=5,
                changed={'target_identity':'asteroid:hp:3500',
                         'target_max_hp':3500,'distance':1300})
            result = self.run_probe(directory,source)
            self.assertEqual(result['state'],'proved',result)
            self.assertEqual([r['sequence'] for r in result['baseline']],[3,4])
            self.assertEqual(result['baseline_rejections'][0]['sequence'],2)

    def test_baseline_timeout_never_clicks_on_unreadable_speed(self):
        class UnreadableSource(PixelSource):
            def next_sample(self, stop):
                sample = super().next_sample(stop)
                if sample:
                    sample.values['speed'] = None
                return sample
        with scratch_directory() as directory:
            result = self.run_probe(directory,UnreadableSource(),baseline_seconds=.15)
            self.assertEqual(result['state'],'blocked')
            self.assertEqual(result['reason'],'baseline_timeout')
            self.assertIsNone(result['request'])
            self.assertTrue(result['baseline_rejections'])
            self.assertFalse(result['selection_effect_verified'])

    def test_observed_motion_before_click_is_not_retried(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,PixelSource(change_after=1,
                changed={'speed':1}))
            self.assertEqual(result['state'],'blocked')
            self.assertIn('fresh_stopped_speed_required',result['reason'])
            self.assertIsNone(result['request'])

    def test_reused_speed_field_is_not_two_fresh_baseline_images(self):
        def reused_speed(sample):
            base = recognize(sample)
            fields = dict(base.fields)
            fields['speed'] = FieldValue(0., sample.acquired_at-.1)
            return RecognitionResult(base.frame_size, fields, base.healthy, base.reason)
        with scratch_directory() as directory:
            result = self.run_probe(directory,PixelSource(),baseline_seconds=.15,
                                    recognizer=reused_speed)
            self.assertEqual(result['reason'],'baseline_timeout')
            self.assertIsNone(result['request'])

    def test_context_change_during_baseline_is_fatal_without_click(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,self.changed_source(
                invalid_first=True,epoch_after=2))
            self.assertEqual(result['state'],'blocked')
            self.assertIn('invalid_capture_context_or_age',result['reason'])
            self.assertIsNone(result['request'])

    def test_invalid_speed_after_click_blocks_probe(self):
        with scratch_directory() as directory:
            result = self.run_probe(directory,PixelSource(change_after=4,
                changed={'speed':None,'target_identity':'asteroid:hp:3500',
                         'target_max_hp':3500,'distance':1300}))
            self.assertEqual(result['state'],'blocked')
            self.assertIn('fresh_stopped_speed_required',result['reason'])
            self.assertFalse(result['selection_effect_verified'])

    def test_dispatcher_shutdown_failure_does_not_skip_worker_cleanup(self):
        def fail_stop(dispatcher, timeout):
            raise OSError('fixture stop failure')
        with scratch_directory() as directory:
            source = self.changed_source()
            with patch.object(FakeDispatcher,'stop',fail_stop):
                result = self.run_probe(directory,source)
            self.assertEqual(result['state'],'blocked')
            self.assertFalse(result['shutdown']['dispatcher'])
            self.assertTrue(result['shutdown']['recognition'])
            self.assertTrue(source.closed)


if __name__ == '__main__':
    unittest.main()
