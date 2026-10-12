"""Copy existing capture files and independent labels into a versioned offline corpus."""
import hashlib, json, shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'test-data/vision/v1'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

def freeze():
    if (DEST / 'manifest.json').exists():
        raise SystemExit('v1 is already frozen; create another version instead of replacing it')
    source_manifest = ROOT / 'tools/hud-reader/fixtures/accuracy-manifest.json'
    label_bytes = source_manifest.read_bytes()
    labels = json.loads(label_bytes)
    by_source = {str((ROOT / 'tools/hud-reader' / row['file']).resolve()): row for row in labels['rows']}
    captures = [p for p in (ROOT / 'tools/hud-reader/runs').rglob('*')
                if p.suffix.lower() in EXTENSIONS and 'mock-tests' not in p.parts]
    captures += list((ROOT / 'tools/hud-reader/fixtures/live').glob('*.png'))
    captures += [ROOT / 'tools/hud-reader/sample.png',
                 ROOT / 'test-data/vision/bootstrap-20261001/2026-09-30-3d-map.png']
    captures += [Path(p) for p in by_source]
    challenges = {'synthetic-erased-final-digit': 'distance', 'synthetic-missing-decimal': 'speed',
                  'synthetic-absent-speed': 'speed'}
    captures += [ROOT / 'tools/hud-reader/fixtures/synthetic' / (name + '.png') for name in challenges]
    captures = sorted(set(p.resolve() for p in captures))
    rows, by_hash = [], {}
    (DEST / 'images').mkdir(parents=True)
    (DEST / 'crops').mkdir()
    (DEST / 'labels').mkdir()
    (DEST / 'labels/accuracy-manifest.json').write_bytes(label_bytes)
    for path in captures:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        relative = path.relative_to(ROOT).as_posix()
        if digest in by_hash:
            existing = by_hash[digest]
            existing['source_aliases'].append(relative)
            label = by_source.get(str(path))
            if label:
                if existing['expected'] and existing['expected'] != label['expected']:
                    raise ValueError('Conflicting labels for identical image: ' + relative)
                existing.update(expected=label['expected'], label_source=label.get('label_source'),
                                source_run=label['source_run'], split=label['split'])
            continue
        label = by_source.get(str(path))
        is_challenge = path.stem in challenges
        row = {'id': 'image-' + digest[:16], 'sha256': digest, 'source': relative,
               'source_aliases': [], 'kind': 'synthetic_challenge' if is_challenge else 'real_capture',
               'source_run': label['source_run'] if label else (
                    path.parent.name if 'runs' in path.parts else path.parent.name + '/' + path.stem),
               'split': label['split'] if label else 'unassigned',
               'expected': label['expected'] if label else {},
               'label_source': label.get('label_source') if label else None,
               'file': 'images/' + digest + path.suffix.lower()}
        if is_challenge:
            row.update(expected={challenges[path.stem]: None}, split='robustness',
                       label_source='existing validate.py explicit synthetic field rejection expectation')
        (DEST / row['file']).write_bytes(raw)
        with Image.open(path) as image:
            row['size'] = list(image.size)
            if image.size == (2560, 1440):
                canvas = Image.new('RGB', (810, 556), (25, 25, 25))
                draw = ImageDraw.Draw(canvas)
                draw.text((8, 3), 'Selected target panel', (255, 255, 255))
                canvas.paste(image.crop((700, 45, 970, 165)).resize((810, 360), Image.Resampling.NEAREST), (0, 20))
                draw.text((8, 383), 'Actual ship speed', (255, 255, 255))
                canvas.paste(image.crop((1340, 1098, 1460, 1150)).resize((360, 156), Image.Resampling.NEAREST), (0, 400))
                row['crop_file'] = 'crops/' + digest + '.png'
                canvas.save(DEST / row['crop_file'])
                row['crop_sha256'] = hashlib.sha256((DEST / row['crop_file']).read_bytes()).hexdigest()
        rows.append(row)
        by_hash[digest] = row
    # Preserve the exact reader used in the first comparison, avoiding concurrent edits.
    shutil.copytree(ROOT / 'test-data/vision/bootstrap-20261001/baseline', DEST / 'baseline')
    manifest = {'version': 1, 'frozen_utc': datetime.now(timezone.utc).isoformat(),
                'label_manifest_sha256': hashlib.sha256(label_bytes).hexdigest(),
                'scope': 'Non-mock run captures, ten live fixtures, calibration sample, original saved map, and three robustness fixtures. Derived contact sheets, debug crops, and generated runtime assets are excluded.',
                'split_policy': labels['split_method'], 'prior_label_limitations': labels['limitations'],
                'unlabeled_policy': 'Run inference, retain raw outputs, exclude from accuracy denominators. Recognition output is not ground truth.',
                'crop_transform': {'source_size': [2560, 1440], 'target_box': [700, 45, 970, 165],
                     'speed_box': [1340, 1098, 1460, 1150], 'scale': 3, 'resampling': 'nearest', 'canvas': [810, 556],
                     'limitation': 'Fixed flight HUD regions; map and other scene crops are observations without labeled numeric accuracy.'},
                'rows': rows}
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'rows': len(rows), 'kinds': dict(Counter(r['kind'] for r in rows)),
                     'labeled_real_pairs': sum(r['kind']=='real_capture' and all(f in r['expected'] for f in ['distance','speed']) for r in rows),
                     'source_groups': len(set(r['source_run'] for r in rows if r['kind']=='real_capture'))}, indent=2))

if __name__ == '__main__':
    freeze()
