"""Native map-glyph evaluation and synthetic transport, with no game input."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

import numpy as np
from PIL import Image

from bsgo_offline.map_discovery import (FOOTER_BOX, NATIVE_SIZE, component_boxes,
    discover_map_contacts, locate_hollow_contacts, map_view_verified)
from evaluate_map_contacts import evaluate
from test_trace import scratch_directory


FIXTURES = Path(__file__).parent/'fixtures'/'system_map_contacts'
MODULE_ROOT = Path(__file__).parents[1]


def map_frame():
    image = Image.new('RGB',NATIVE_SIZE)
    with Image.open(MODULE_ROOT/'bsgo_offline'/'data'/'system-map-controls.png') as crop:
        image.paste(crop,FOOTER_BOX[:2])
    with Image.open(FIXTURES/'map-close-corner.png') as crop:
        image.paste(crop,(2505,0))
    return image


def ring_patch():
    with Image.open(FIXTURES/'overview-visible_contact_glyph-00.png') as image:
        return image.convert('RGB')


class MapDiscoveryTests(unittest.TestCase):
    def test_native_corpus_has_zero_wrong_proposals_and_preserves_rejections(self):
        result = evaluate()
        for group in result['totals'].values():
            self.assertEqual(group['accepted_wrong_contacts'],0)
            self.assertEqual(group['rejected_negative_cases'],group['negative_cases'])
        held_out = result['totals']['evaluation']
        self.assertEqual(held_out['positive_cases'],12)
        self.assertEqual(held_out['accepted_correct_contacts'],10)
        self.assertEqual(held_out['rejected_positive_cases'],2)
        self.assertFalse(result['asteroid_classification_verified'])
        self.assertFalse(result['native_autoscan_acceptance'])

    def test_native_crop_hashes_and_labels_do_not_invent_game_semantics(self):
        manifest = json.loads((FIXTURES/'manifest.json').read_text())
        for row in manifest['rows']:
            self.assertEqual(hashlib.sha256((FIXTURES/row['file']).read_bytes()).hexdigest(),row['crop_sha256'])
            self.assertEqual(len(row['source_sha256']),64)
            self.assertEqual(row['object_type'],'unknown')
            self.assertEqual(row['scan_state'],'unknown')
            self.assertIsNone(row['resource'])

    def test_map_controls_and_close_corner_are_separate_requirements(self):
        image = map_frame()
        self.assertTrue(map_view_verified(image))
        for box in (FOOTER_BOX,(2505,0,2560,55)):
            broken = image.copy()
            broken.paste((0,0,0),box)
            result = discover_map_contacts(broken)
            self.assertFalse(result.map_verified)
            self.assertEqual(result.contacts,())
        resized = image.resize((1920,1080))
        self.assertEqual(discover_map_contacts(resized).reason,'unsupported_system_map_geometry')

    def test_flight_hud_cannot_be_mistaken_for_system_map(self):
        from test_large_hud import fixture_image
        image = fixture_image()
        image.paste(ring_patch(),(900,600))
        self.assertFalse(map_view_verified(image))
        self.assertEqual(discover_map_contacts(image).contacts,())

    def test_native_ring_coordinates_transport_without_mutating_source(self):
        image = map_frame()
        patch = ring_patch()
        local = locate_hollow_contacts(patch)[0]
        for x,y in ((500,300),(1500,700)):
            image.paste(patch,(x,y))
        before = image.tobytes()
        result = discover_map_contacts(image)
        self.assertTrue(result.map_verified)
        self.assertEqual(len(result.contacts),2)
        self.assertEqual([c.center for c in result.contacts],
            [(500+local.center[0],300+local.center[1]),(1500+local.center[0],700+local.center[1])])
        self.assertEqual(image.tobytes(),before)
        self.assertFalse(result.input_authorized)
        for contact in result.contacts:
            self.assertEqual(contact.object_type,'unknown')
            self.assertEqual(contact.scan_state,'unknown')
            self.assertIsNone(contact.distance)
            self.assertIsNone(contact.resource)

    def test_edge_clipping_and_neighboring_glyphs_do_not_supply_isolated_centers(self):
        patch = ring_patch()
        local = locate_hollow_contacts(patch)[0]
        clipped = patch.crop((local.box[0]+1,0,24,24))
        self.assertEqual(locate_hollow_contacts(clipped),())
        crowded = patch.copy()
        # A separate neutral neighbor within the two-pixel exclusion margin
        # creates ambiguity rather than a permissive best-match click.
        crowded.putpixel((local.box[0]-2,local.center[1]),(255,255,255))
        self.assertEqual(locate_hollow_contacts(crowded),())

    def test_capacity_and_component_budget_fail_closed_without_truncation(self):
        patch = ring_patch()
        image = Image.new('RGB',(80,40))
        image.paste(patch,(4,4))
        image.paste(patch,(44,4))
        self.assertIsNone(locate_hollow_contacts(image,capacity=1))
        for invalid in (0,257,True):
            with self.assertRaises(ValueError):
                locate_hollow_contacts(image,capacity=invalid)
        clutter = np.tile(np.array([True,False]),(20,20))
        self.assertIsNone(component_boxes(clutter,max_runs=10))

    def test_absent_contacts_do_not_prove_a_clear_route_or_completed_sweep(self):
        result = discover_map_contacts(map_frame())
        self.assertTrue(result.map_verified)
        self.assertEqual(result.contacts,())
        self.assertEqual(result.reason,'no_isolated_supported_map_contacts')
        self.assertFalse(result.input_authorized)
        self.assertFalse(result.asteroid_classification_verified)

    def test_cli_emits_read_only_source_linked_artifacts_and_refuses_overwrite(self):
        with scratch_directory() as directory:
            source = directory/'saved map image.png'
            image = map_frame()
            image.paste(ring_patch(),(900,600))
            image.save(source)
            output = directory/'read only replay'
            command = [sys.executable,str(MODULE_ROOT/'discover_map_contacts.py'),
                '--image',str(source),'--output',str(output)]
            completed = subprocess.run(command,capture_output=True,text=True,check=True)
            self.assertIn('input_authorized=False',completed.stdout)
            report = json.loads((output/'contacts.json').read_text())
            self.assertEqual(report['source_sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(len(report['contacts']),1)
            self.assertFalse(report['input_authorized'])
            self.assertTrue((output/'contacts-overlay.png').is_file())
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

    def test_discovery_import_does_not_load_any_input_or_controller_module(self):
        script = ('import json,sys;import bsgo_offline.map_discovery;'
                  'print(json.dumps([name for name in sys.modules if name.startswith("bsgo_offline.")]))')
        result = subprocess.run([sys.executable,'-c',script],cwd=MODULE_ROOT,capture_output=True,text=True,check=True)
        modules = json.loads(result.stdout)
        for name in ('input','native_input','manual_assist','manual_routines'):
            self.assertNotIn('bsgo_offline.'+name,modules)


if __name__ == '__main__':
    unittest.main()
