"""Add semantic numeric scores without erasing strict completion failures."""
import importlib.util, json, re, shutil, time, statistics, hashlib
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
RUN=ROOT/'logs/vision-model-comparison-20261001'
fields=['distance','speed']
for path in RUN.glob('*-results.json'):
    data=json.loads(path.read_text())
    for row in data['results']:
        response=row['response']
        text=response.get('text',response.get('message',{}).get('content',''))
        if 'choices' in response: text=response['choices'][0]['message']['content']
        # Recover a leading object for diagnostic scoring only. Never a control acceptance.
        clean=re.sub(r'^```(?:json)?\s*','',text.strip())
        try: obj,_=json.JSONDecoder().raw_decode(clean)
        except ValueError: obj={}
        values={}
        for field in fields:
            value=obj.get(field) if isinstance(obj,dict) else None
            if isinstance(value,str) and re.fullmatch(r'\d+(?:\.\d+)?',value): value=float(value)
            values[field]=value if type(value) in (int,float) else None
        row['diagnostic_numeric_values']=values
        row['diagnostic_numeric_score']={f:('reject' if values[f] is None else
                 ('correct' if abs(values[f]-row['expected'][f])<.001 else 'wrong')) for f in fields}
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')

# Freeze the reader and its templates because other development may be ongoing.
baseline=RUN/'baseline';baseline.mkdir(exist_ok=True)
for name in ['hud_reader.py','templates.json']:
    shutil.copy2(ROOT/'tools/hud-reader'/name,baseline/name)
spec=importlib.util.spec_from_file_location('frozen_reader',baseline/'hud_reader.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
reader=module.HudReader()
rows=json.loads((RUN/'dataset.json').read_text())['rows']
results=[]
for row in rows:
    path=ROOT/'tools/hud-reader'/row['file']
    start=time.perf_counter()
    with Image.open(path) as image: output=reader.read(image.convert('RGB'))
    wall=time.perf_counter()-start
    score={f:('reject' if output[f] is None else ('correct' if abs(output[f]-row['expected'][f])<.001 else 'wrong')) for f in fields}
    results.append({'id':row['id'],'wall_seconds':wall,'score':score,'output':output})
(RUN/'reader-baseline.json').write_text(json.dumps({'source_sha256':hashlib.sha256((baseline/'hud_reader.py').read_bytes()).hexdigest(),
    'timing':'image decode plus reader; reader construction excluded; no network',
    'freshness':'offline images with no claimed live acquisition time',
    'results':results},indent=2),encoding='utf-8')

summary=[]
for path in RUN.glob('*-results.json'):
    data=json.loads(path.read_text())
    for variant in ['full','crop']:
        rows=[r for r in data['results'] if r['variant']==variant]
        if not rows:continue
        times=[r['wall_seconds'] for r in rows]
        summary.append({'model':data['model'],'backend':data['backend'],'variant':variant,'samples':len(rows),
             'median_seconds':statistics.median(times),'min_seconds':min(times),'max_seconds':max(times),
             'strict_both_correct':sum(all(s=='correct' for s in r['score'].values()) for r in rows),
             'strict_complete':sum(r['parsed'] is not None for r in rows),
             'diagnostic_both_correct':sum(all(s=='correct' for s in r['diagnostic_numeric_score'].values()) for r in rows),
             'strict_wrong_fields':sum(s=='wrong' for r in rows for s in r['score'].values()),
             'strict_rejected_fields':sum(s=='reject' for r in rows for s in r['score'].values())})
(RUN/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
print('BASELINE',statistics.median(r['wall_seconds'] for r in results),sum(all(s=='correct' for s in r['score'].values()) for r in results))
