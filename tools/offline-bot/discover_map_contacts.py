"""Propose system-map marker locations from one saved image; never send input."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw
from bsgo_offline.map_discovery import discover_map_contacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True,help='New local artifact directory')
    args = parser.parse_args()
    source_bytes = args.image.read_bytes()
    with Image.open(args.image) as opened:
        image = opened.convert('RGB')
    result = discover_map_contacts(image)
    args.output.mkdir(parents=True,exist_ok=False)
    rows = [{**asdict(contact),'center':contact.center} for contact in result.contacts]
    report = {**asdict(result),'contacts':rows,'source_sha256':hashlib.sha256(source_bytes).hexdigest(),
        'source_image':str(args.image.resolve()),'frame_size':image.size,'mode':'saved_image_read_only',
        'validation':'experimental_geometry_only','selection_effect_verified':False}
    (args.output/'contacts.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    for index,contact in enumerate(result.contacts):
        x,y = contact.center
        draw.rectangle((x-7,y-7,x+7,y+7),outline='yellow',width=2)
        draw.text((x+9,y-8),str(index+1),fill='yellow')
    draw.rectangle((10,10,920,40),fill='black')
    draw.text((20,18),'READ ONLY: unclassified map contacts; no input or resource evidence',fill='yellow')
    overlay.save(args.output/'contacts-overlay.png')
    print(f'{result.reason}; contacts={len(rows)}; input_authorized=False; {args.output}')


if __name__ == '__main__':
    main()
