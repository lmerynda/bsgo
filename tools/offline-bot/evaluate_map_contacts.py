"""Evaluate map-contact geometry on reviewed native patches; never send input."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image
from bsgo_offline.map_discovery import locate_hollow_contacts


FIXTURES = Path(__file__).parent/'tests'/'fixtures'/'system_map_contacts'


def evaluate(fixtures=FIXTURES):
    manifest = json.loads((fixtures/'manifest.json').read_text(encoding='utf-8'))
    totals = {}
    cases = []
    for row in manifest['rows']:
        path = fixtures/row['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['crop_sha256']:
            raise ValueError('map_fixture_hash_mismatch:'+row['file'])
        with Image.open(path) as opened:
            found = locate_hollow_contacts(opened.convert('RGB'))
        found = found or ()
        center = row['reviewed_center_in_crop']
        matched = [contact for contact in found if center is not None
                   and max(abs(a-b) for a,b in zip(contact.center,center)) <= 3]
        wrong = len(found)-len(matched)
        counts = totals.setdefault(row['split'],{'positive_cases':0,'negative_cases':0,
            'accepted_correct_contacts':0,'accepted_wrong_contacts':0,'rejected_positive_cases':0,
            'rejected_negative_cases':0})
        positive = row['label']=='visible_contact_glyph'
        counts['positive_cases' if positive else 'negative_cases'] += 1
        counts['accepted_correct_contacts'] += len(matched)
        counts['accepted_wrong_contacts'] += wrong
        if not matched and positive:
            counts['rejected_positive_cases'] += 1
        if not found and not positive:
            counts['rejected_negative_cases'] += 1
        cases.append({'file':row['file'],'split':row['split'],'matched':len(matched),
            'wrong':wrong,'proposals':[contact.center for contact in found]})
    return {'scope':manifest['scope'],'independence':manifest['independence'],
        'totals':totals,'cases':cases,'input_authorized':False,
        'asteroid_classification_verified':False,'native_autoscan_acceptance':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = evaluate()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result['totals']))
    return int(any(group['accepted_wrong_contacts'] for group in result['totals'].values()))


if __name__ == '__main__':
    raise SystemExit(main())
