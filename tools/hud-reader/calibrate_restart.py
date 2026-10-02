"""Explicit manual labels for saved post-relaunch visible HUD pixels only."""
import json
from PIL import Image
from hud_reader import ROOT, TITLE, glyphs, white, trim, normalize

sources=[
    # Date slash and narrow day digit touch in the threshold mask; exclude that
    # merged region instead of assigning it a digit label.
    ('runs/restart-calibration-20261001/capture-1.jpg',(460,16,481,33),'10'),
    ('runs/restart-calibration-20261001/capture-1.jpg',(501,16,554,33),'2026'),
    ('runs/restart-calibration-20261001/capture-1.jpg',(560,16,639,33),'8:07:32'),
    ('runs/restart-calibration-20261001/capture-1.jpg',(710,137,804,153),'2555'),
    ('runs/mining-20261001-2013/frame-1790885606183-002.png',(710,137,804,153),'2514'),
    ('runs/mining-20261001-2019/frame-1790885963460-001.png',(710,137,804,153),'2439'),
    # Thin separators are absent at the generic white threshold, confirmed
    # visually. These labels name the eight/six complete digit components.
    ('runs/mining-20261001-2019/frame-1790885963460-001.png',(110,76,240,90),'26692669'),
    ('runs/mining-20261001-2019/frame-1790885963460-001.png',(120,97,233,112),'328328'),
]
templates={str(i):[] for i in range(10)}
for path,box,label in sources:
    pieces=glyphs(white(Image.open(ROOT/path),box))
    if len(pieces)!=len(label):raise ValueError(f'{path}: expected {len(label)} components, got {len(pieces)}')
    for digit,(_,_,mask) in zip(label,pieces):
        if digit.isdigit():templates[digit].append(normalize(mask).astype(int).tolist())
if any(not value for value in templates.values()):raise ValueError('Missing manually labeled calibration digit')
path=ROOT/'restart-calibration.json'
data=json.loads(path.read_text())
data['distance_digits']=templates
data['digit_sources']=[{'path':p,'box':b,'label':label} for p,b,label in sources]
hp_sources=[
    ('runs/mining-20261001-2136/frame-1790890600348-000.png',(797,76,863,90),'3762'),
    ('runs/mining-20261001-2136/frame-1790890600348-000.png',(877,76,938,90),'3762'),
]
# Only the newly observed complete 2 needs another raster variant. Retain all
# existing HP fonts and thresholds, and keep this addition profile-specific.
hp_templates=json.loads((ROOT/'templates.json').read_text())['hp_digits']
for source,box,label in hp_sources:
    pieces=glyphs(white(Image.open(ROOT/source),box))
    if len(pieces)!=len(label):raise ValueError(f'{source}: wrong HP glyph count')
    for digit,(_,_,mask) in zip(label,pieces):
        if digit=='2':hp_templates[digit].append(normalize(mask).astype(int).tolist())
data['hp_digits']=hp_templates
data['hp_digit_sources']=[{'path':p,'box':b,'label':label,'included_digits':'2'} for p,b,label in hp_sources]
data['limitations']='Explicit parent visual labels. Development calibration, not held-out production accuracy. Original profile remains separate.'
path.write_text(json.dumps(data,indent=2))
print('Calibrated distance font and profile-specific complete HP 2 raster from manually labeled saved regions.')
