"""Frozen manual labels, whole-run development holdout, paired warm benchmark.

No captures, game inputs, template training, or inferred OCR ground truth.
The baseline snapshot reconstructs the immediately preceding reader, excluding
the eight newly added current-side HP glyph templates and the three pixel fixes.
Both versions run in the same process on preloaded images; startup and PNG decode
are excluded from recognition timings. Original capture logs are separate.
"""
import hashlib, importlib.util, json, statistics, time
from datetime import datetime, timezone
from PIL import Image
from hud_reader import HudReader, ROOT, DISTANCE, SPEED, TARGET_HP, TITLE

FIELDS=('distance','speed','target_max_hp','target_identity')

def quantiles(values):
    values=sorted(values)
    return {'n':len(values),'median':statistics.median(values),
            'p95':values[min(len(values)-1,int(.95*len(values)))],
            'p99':values[min(len(values)-1,int(.99*len(values)))],
            'mean':statistics.mean(values)} if values else None

def metrics(rows):
    output={'frames':len(rows),'fields':{}}
    for field in FIELDS:
        correct=sum(r['actual'][field]==r['expected'][field] for r in rows)
        rejected=sum(r['actual'][field] is None for r in rows)
        output['fields'][field]={'correct':correct,'rejected':rejected,
            'wrong_returned':len(rows)-correct-rejected,'accuracy':correct/len(rows)}
    correct=sum(r['actual']['valid'] and all(r['actual'][f]==r['expected'][f] for f in FIELDS) for r in rows)
    accepted=sum(r['actual']['valid'] for r in rows)
    output.update(correct_accepted=correct,wrong_accepted=accepted-correct,
                  false_rejections=len(rows)-accepted,coverage=accepted/len(rows),
                  useful_accuracy=correct/len(rows))
    return output

def main():
    manifest=json.loads((ROOT/'fixtures/accuracy-manifest.json').read_text())
    spec=importlib.util.spec_from_file_location('accuracy_baseline',ROOT/'fixtures/accuracy-baseline-reader.py')
    baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
    readers={'before':baseline.HudReader(),'after':HudReader()}
    rows=[];images=[];hashes=set();hud_hashes=set();decode=[]
    for label in manifest['rows']:
        start=time.perf_counter();im=Image.open(ROOT/label['file']).convert('RGB');im.load()
        decode.append((time.perf_counter()-start)*1000)
        digest=hashlib.sha256(im.tobytes()).hexdigest()
        if digest in hashes:raise ValueError('Duplicate real frame '+label['file'])
        hashes.add(digest)
        hud_hashes.add(hashlib.sha256(b''.join(im.crop(b).tobytes() for b in (DISTANCE,SPEED,TARGET_HP,TITLE))).hexdigest())
        rows.append({**label,'pixel_sha256':digest,
                     'observations':{name:r.read(im) for name,r in readers.items()}})
        images.append(im)
    report={'utc':datetime.now(timezone.utc).isoformat(),'distinct_real_frames':len(hashes),
            'distinct_hud_pixel_frames':len(hud_hashes),'source_runs':len(set(r['source_run'] for r in rows)),
            'synthetic_and_mock_frames_counted':0,'split_method':manifest['split_method'],
            'limitations':manifest['limitations'],'results':{},'rows':rows}
    report['label_revisions']=manifest.get('label_revisions',[])
    report['artifact_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT/'hud_reader.py',ROOT/'templates.json',ROOT/'fixtures/accuracy-manifest.json',
                  ROOT/'fixtures/accuracy-baseline-reader.py',ROOT/'fixtures/accuracy-baseline-templates.json')}
    for name in readers:
        groups={}
        for split in ('held_out','development','calibration','all_real_asteroid'):
            selected=[{**row,'actual':row['observations'][name]} for row in rows
                      if row['expected']['valid'] and (split=='all_real_asteroid' or row['split']==split)]
            groups[split]=metrics(selected)
        negative=[r for r in rows if not r['expected']['valid']]
        groups['real_planetoid_negatives']={'frames':len(negative),
            'correct_rejections':sum(not r['observations'][name]['valid'] and r['observations'][name]['target_identity'] is None for r in negative)}
        report['results'][name]=groups
    held=[r for r in rows if r['split']=='held_out']
    positive=[r for r in rows if r['expected']['valid']]
    report['all_real_asteroid_diversity']={'hp_values':sorted(set(r['expected']['target_max_hp'] for r in positive)),
       'distance_min_max':[min(r['expected']['distance'] for r in positive),max(r['expected']['distance'] for r in positive)],
       'speeds':sorted(set(r['expected']['speed'] for r in positive)),
       'moving_frames':sum(r['expected']['speed']>0 for r in positive),
       'stopped_frames':sum(r['expected']['speed']==0 for r in positive),
       'resolutions':[[2560,1440]],'current_hp_damage_coverage':'Current-HP decrease is synthetic regression coverage; this real corpus mainly contains pre-mining full-HP targets.'}
    report['held_out_diversity']={'source_runs':sorted(set(r['source_run'] for r in held)),
       'hp_values':sorted(set(r['expected']['target_max_hp'] for r in held)),
       'distances':sorted(set(r['expected']['distance'] for r in held)),
       'speeds':sorted(set(r['expected']['speed'] for r in held)),
       'moving_frames':sum(r['expected']['speed']>0 for r in held),
       'stopped_frames':sum(r['expected']['speed']==0 for r in held)}
    n=len(held)
    report['uncertainty']={'one_sided_95_percent_lower_bound_if_independent_all_success':.05**(1/n),
       'independence_valid':False,'reason':'25 of26 frames are one correlated approach; only two HP identities and one client layout. Bound is illustrative and optimistic, not a valid production confidence claim.',
       'target_99_percent_demonstrated':False,
       'needed':'At least300 genuinely held-out representative frames from new natural production runs, varied HP digits, range, moving/stopped speeds and HUD backgrounds; freeze reader before scoring. Count false rejections and wrong accepted values as failures; retain run grouping and all inputs.'}
    # Alternate paired order across rounds to avoid a systematic warm-cache bias.
    times={name:[] for name in readers}
    indices=[i for i,r in enumerate(rows) if r['split']=='held_out']
    for round_no in range(30):
        for index in indices:
            for name in (('before','after') if round_no%2==0 else ('after','before')):
                start=time.perf_counter();readers[name].read(images[index])
                times[name].append((time.perf_counter()-start)*1000)
    report['warm_recognition_ms']={name:quantiles(v) for name,v in times.items()}
    report['decode_ms_separate_single_pass']=quantiles(decode)
    capture=[]
    for p in (ROOT/'runs').rglob('capture-*.jsonl'):
        if 'mock-tests' in p.parts:continue
        for line in p.read_text().splitlines():
            row=json.loads(line)
            if row.get('source')=='live_sky_screenshot':
                capture.append({'file':str(p.relative_to(ROOT)),**{key:row.get(key) for key in
                    ('frame_path','capture_ms','recognition_ms','cycle_ms','total_cycle_ms','control_hook_ms','frame_age_ms','valid','reasons')}})
    report['historical_live_capture_timings_ms']={key:quantiles([r[key] for r in capture if isinstance(r.get(key),(int,float))]) for key in
        ('capture_ms','recognition_ms','cycle_ms','total_cycle_ms','control_hook_ms','frame_age_ms')}
    report['historical_capture_rows']=capture
    (ROOT/'accuracy-results.json').write_text(json.dumps(report,indent=2))
    with (ROOT/'accuracy-benchmark-history.jsonl').open('a') as history:
        history.write(json.dumps({key:report[key] for key in ('utc','artifact_sha256','warm_recognition_ms','distinct_real_frames','results')})+'\n')
    print(json.dumps({key:value for key,value in report.items() if key not in ('rows','historical_capture_rows')},indent=2))
    after=report['results']['after']
    if after['all_real_asteroid']['useful_accuracy']!=1 or after['real_planetoid_negatives']['correct_rejections']!=6:
        raise SystemExit(1)

if __name__=='__main__':main()
