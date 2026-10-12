"""Reproduce manually reviewed, privacy-bounded system-map contact fixtures."""
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent/'tests'/'fixtures'/'system_map_contacts'
CASES = (
    ('overview','tools/hud-reader/runs/belt-reorient-20261001-2103/03-map-overview.jpg',
     'development',(800,300),
     ((85,126),(110,151),(139,167),(166,147),(183,132),(323,130),
      (408,59),(394,82),(403,91),(405,168),(350,188),(421,198)),
     ((261,50),(408,9),(349,296),(240,199),(25,390),(478,265))),
    # This second run was inspected while tuning the neutral threshold. It is
    # development evidence, despite coming from a separate source run.
    ('recovery','tools/hud-reader/runs/map-recovery-20261001-2333/04-second-marker-map.jpg',
     'development',(1300,350),
     ((132,72),(171,81),(211,58),(45,112),(74,144),(94,154),
      (165,175),(187,169),(9,219),(38,244),(18,262),(97,342),
      (137,333),(133,349),(185,350),(67,400),(137,405),
      (41,469),(85,447),(110,443),(145,478),(228,466),(13,471)),
     ((104,215),(227,140),(134,106),(316,119),(341,287),(358,366))),
    # Frozen-parameter evaluation on an older, different source run. Its
    # smaller contact rasters can legitimately be rejected as unsupported.
    ('older-map','test-data/vision/bootstrap-20261001/2026-09-30-3d-map.png',
     'evaluation',(800,300),
     ((458,9),(249,58),(192,142),(177,157),(118,177),(329,140),
      (451,151),(407,177),(281,232),(219,261),(271,295),(207,329)),
     ((407,10),(264,49),(399,119),(350,250),(163,105),(264,207),(359,300),(33,415))),
)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    rows = []
    previews = []
    for group,source,split,origin,positive,negative in CASES:
        source_path = ROOT/source
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        with Image.open(source_path) as opened:
            image = opened.convert('RGB')
        for label,points in (('visible_contact_glyph',positive),('background_or_noncontact',negative)):
            for index,(x,y) in enumerate(points):
                x,y = x+origin[0],y+origin[1]
                box = (x-12,y-12,x+12,y+12)
                crop = image.crop(box)
                name = f'{group}-{label}-{index:02d}.png'
                crop.save(OUT/name)
                rows.append({'file':name,'source_image':source,'source_sha256':source_hash,
                    'native_size':image.size,'crop_box':box,'split':split,
                    'label':label,'reviewed_center_in_crop':[12,12] if label=='visible_contact_glyph' else None,
                    'label_source':'Manual visual review of native map pixels, before evaluation; no detector prediction label.',
                    'object_type':'unknown','scan_state':'unknown','resource':None,
                    'crop_sha256':hashlib.sha256((OUT/name).read_bytes()).hexdigest()})
                previews.append((name,crop))
    source_path = ROOT/CASES[0][1]
    with Image.open(source_path) as image:
        image.crop((2505,0,2560,55)).save(OUT/'map-close-corner.png')
        image.crop((1400,1350,1620,1435)).save(Path(__file__).parent/'bsgo_offline'/'data'/'system-map-controls.png')
    manifest = {'scope':'Map contact glyph geometry only; no asteroid, unscanned, resource or click-authority labels.',
        'privacy':'Small map-only crops; no account names, chat, full screenshots or cargo.',
        'independence':'Overview and recovery are development. Older-map is frozen-parameter evaluation from another run; no prospective native acceptance.',
        'footer_template':{'file':'bsgo_offline/data/system-map-controls.png','source_image':CASES[0][1],
            'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),'crop_box':[1400,1350,1620,1435]},
        'close_corner':{'file':'map-close-corner.png','source_image':CASES[0][1],
            'source_sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),'crop_box':[2505,0,2560,55]},
        'rows':rows}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    # Local visual review sheet is deliberately outside the committed corpus.
    board = Image.new('RGB',(800,((len(previews)+7)//8)*110),(20,20,20))
    draw = ImageDraw.Draw(board)
    for index,(name,crop) in enumerate(previews):
        x,y = index%8*100,index//8*110
        board.paste(crop.resize((72,72),Image.Resampling.NEAREST),(x+14,y))
        draw.text((x+2,y+74),name.split('-')[0],fill='white')
        draw.text((x+2,y+86),('contact' if 'visible' in name else 'negative')+name[-6:-4],fill='white')
    preview = ROOT/'logs'/'offline-bot'/'map-discovery-20261007'/'fixture-review.png'
    preview.parent.mkdir(parents=True,exist_ok=True)
    board.save(preview)
    print(f'{len(rows)} native map-only patches; {preview}')


if __name__ == '__main__':
    main()
