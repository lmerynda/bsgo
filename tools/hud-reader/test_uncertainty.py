"""Ambiguous glyph context must preserve fail-stop control semantics."""
import fixture_data  # noqa: F401  restores packed test images first
import unittest
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT

class UncertaintyTests(unittest.TestCase):
    def test_real_font_mismatch_exposes_variants_without_accepting(self):
        # Saved 2439/7.2 frame, moved one row to the original distance anchor.
        # This is a derived regression, not a new independently captured frame.
        source=Image.open(ROOT/'runs/mining-20261001-2019/frame-1790885963460-001.png')
        image=Image.new('RGB',source.size);image.paste(source,(0,1))
        row=HudReader().read(image)
        self.assertFalse(row['valid'])
        self.assertIsNone(row['distance'])
        self.assertIn('ambiguous_distance_glyph',row['reasons'])
        info=row['ocr_uncertainty']['distance']
        self.assertTrue(info['geometry_complete'])
        self.assertTrue(info['for_context_only'])
        self.assertFalse(info['scores_are_probabilities'])
        values=[value['value'] for value in info['possible_values']]
        self.assertIn(2439,values)
        self.assertIn(2430,values)
        self.assertLessEqual(len(values),4)
        self.assertTrue(all(len(glyph['choices'])<=2 for glyph in info['glyphs']))

    def test_partial_text_has_no_asserted_candidates(self):
        image=Image.open(ROOT/'sample.png').convert('RGB')
        ImageDraw.Draw(image).rectangle((764,138,790,154),fill=(12,19,22))
        row=HudReader().read(image)
        self.assertFalse(row['valid'])
        self.assertIsNone(row['distance'])
        info=row['ocr_uncertainty']['distance']
        self.assertFalse(info['geometry_complete'])
        self.assertEqual(info['possible_values'],[])

    def test_valid_capture_keeps_exact_reading(self):
        row=HudReader().read(Image.open(ROOT/'sample.png'))
        self.assertTrue(row['valid'])
        self.assertEqual((row['distance'],row['speed'],row['target_max_hp']),(1416,0.0,4168))
        self.assertNotIn('ocr_uncertainty',row)

    def test_synthetic_three_or_eight_keeps_identity_invalid(self):
        image=Image.open(ROOT/'fixtures/synthetic/hp-ambiguity.png')
        row=HudReader('restart-20261001').read(image)
        self.assertFalse(row['valid'])
        self.assertIsNone(row['target_max_hp'])
        self.assertIsNone(row['target_identity'])
        info=row['ocr_uncertainty']['target_max_hp']
        self.assertEqual([choice['digit'] for choice in info['glyphs'][0]['choices']],['3','8'])
        self.assertEqual([candidate['value'] for candidate in info['possible_values']],[3995,8995])
        self.assertFalse(info['scores_are_probabilities'])

if __name__=='__main__':unittest.main()
