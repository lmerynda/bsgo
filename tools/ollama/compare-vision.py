"""Offline numeric-HUD comparison. All model outputs remain observations for review."""
import argparse, base64, hashlib, io, json, re, statistics, subprocess, time, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / 'logs/vision-model-comparison-20261001'
PROMPT = ('Read the selected target distance: the number below the target portrait, not its health. '
          'Read actual ship speed: the number above the orange throttle bar, not throttle percentage. '
          'Return only JSON with keys "distance" and "speed". Use numbers, or null if unreadable. No explanation.')
def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
def api(endpoint, path, body=None):
    req = urllib.request.Request(endpoint + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=180) as response:
        return json.load(response)
def gpu():
    return subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,utilization.gpu','--format=csv,noheader'], text=True).strip()
def prepare():
    manifest = json.loads((ROOT/'tools/hud-reader/fixtures/accuracy-manifest.json').read_text())
    chosen = []
    for source in ['miner-dry-relaunch-2141','sol-four-live-2309','sol-separator-live-2252',
                   'sol-speed-live-2303','sol-static-0121','sol-tylium-live-2321','sol-water-0125']:
        rows = [r for r in manifest['rows'] if r['source_run'] == source]
        chosen.append(max(rows, key=lambda r:r['expected']['speed']))
    for file in ['fixtures/live/live-01.png','fixtures/live/live-03.png']:
        chosen.append(next(r for r in manifest['rows'] if r['file'].replace('\\','/') == file))
    for i,row in enumerate(chosen):
        source = ROOT/'tools/hud-reader'/row['file']
        row['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
        row['id'] = f'frame-{i:02d}'
        with Image.open(source) as im:
            if im.size != (2560,1440): raise ValueError('Unexpected source layout')
            canvas = Image.new('RGB',(810,556),(25,25,25))
            draw = ImageDraw.Draw(canvas)
            draw.text((8,3),'Selected target panel',(255,255,255))
            canvas.paste(im.crop((700,45,970,165)).resize((810,360),Image.Resampling.NEAREST),(0,20))
            draw.text((8,383),'Actual ship speed',(255,255,255))
            canvas.paste(im.crop((1340,1098,1460,1150)).resize((360,156),Image.Resampling.NEAREST),(0,400))
            crop = RUN/'images'/f'{row["id"]}-hud.png'
            crop.parent.mkdir(parents=True,exist_ok=True)
            canvas.save(crop)
            row['crop_file'] = str(crop.relative_to(ROOT)).replace('\\','/')
    save(RUN/'dataset.json',{'label_source':'existing accuracy-manifest; labels never sent to models',
         'scope':'9 selected frames from 8 source groups, original HUD layout; mixed existing development and held-out splits',
         'transforms':{'target_box':[700,45,970,165],'speed_box':[1340,1098,1460,1150],'scale':3,'resampling':'nearest','canvas':[810,556]},'rows':chosen})
def parse(text):
    try:
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
        result = json.loads(cleaned)
        if not isinstance(result,dict) or set(result) != {'distance','speed'}: return None
        if any(v is not None and (type(v) not in (int,float)) for v in result.values()): return None
        return result
    except (ValueError, TypeError): return None
def run(model, backend, endpoint, variants):
    dataset=json.loads((RUN/'dataset.json').read_text())
    results=[]
    if backend=='ollama':
        identity=api(endpoint,'/api/show',{'model':model})
        save(RUN/f'{model.replace(":","-")}-identity.json',identity)
        start=time.perf_counter()
        warm=api(endpoint,'/api/chat',{'model':model,'messages':[{'role':'user','content':'Reply OK.'}],
                 'think':False,'stream':False,'keep_alive':'10m','options':{'num_ctx':8192,'num_predict':8}})
        save(RUN/f'{model.replace(":","-")}-warmup.json',{'wall_seconds':time.perf_counter()-start,'response':warm})
    for variant in variants:
        for row in dataset['rows']:
            path=ROOT/row['crop_file'] if variant=='crop' else ROOT/'tools/hud-reader'/row['file']
            start=time.perf_counter()
            encoded=base64.b64encode(path.read_bytes()).decode()
            if backend=='ollama':
                body={'model':model,'messages':[{'role':'user','content':PROMPT,'images':[encoded]}],
                      'stream':False,'think':False,'keep_alive':'10m',
                      'options':{'temperature':0,'num_ctx':8192,'num_predict':96,'repeat_penalty':1.1}}
                response=api(endpoint,'/api/chat',body)
                text=response.get('message',{}).get('content','')
                reason=response.get('done_reason')
                server_seconds=response.get('total_duration',0)/1e9
                cached=response.get('prompt_eval_cached_count')
                load=response.get('load_duration',0)/1e9
            else:
                body={'model':model,'messages':[{'role':'user','content':[
                      {'type':'text','text':PROMPT},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+encoded}}]}],
                      'stream':False,'temperature':0,'max_tokens':96,'cache_prompt':False}
                response=api(endpoint,'/v1/chat/completions',body)
                text=response['choices'][0]['message']['content']
                reason=response['choices'][0]['finish_reason']
                server_seconds=None;cached=None;load=None
            wall=time.perf_counter()-start
            parsed=parse(text) if reason!='length' else None
            score={}
            for field in ['distance','speed']:
                value=parsed.get(field) if parsed else None
                score[field]='reject' if value is None else ('correct' if abs(value-row['expected'][field])<0.001 else 'wrong')
            entry={'id':row['id'],'source_run':row['source_run'],'variant':variant,'source':str(path.relative_to(ROOT)),
                   'image_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'wall_seconds':wall,
                   'server_seconds':server_seconds,'load_seconds':load,'cached_tokens':cached,
                   'expected':row['expected'],'parsed':parsed,'score':score,'done_reason':reason,
                   'gpu_after':gpu(),'response':response}
            results.append(entry)
            save(RUN/f'{model.replace(":","-")}-results.json',{'model':model,'backend':backend,'prompt':PROMPT,'results':results})
            print(json.dumps({k:entry[k] for k in ['id','variant','wall_seconds','parsed','score','done_reason']}),flush=True)
    if backend=='ollama': api(endpoint,'/api/generate',{'model':model,'keep_alive':0})
def summarize():
    summary=[]
    for path in RUN.glob('*-results.json'):
        data=json.loads(path.read_text())
        for variant in ['full','crop']:
            rows=[r for r in data['results'] if r['variant']==variant]
            if not rows:continue
            times=[r['wall_seconds'] for r in rows]
            summary.append({'model':data['model'],'backend':data['backend'],'variant':variant,'samples':len(rows),
                'median_seconds':statistics.median(times),'min_seconds':min(times),'max_seconds':max(times),
                'complete_json':sum(r['parsed'] is not None for r in rows),
                'both_correct':sum(all(s=='correct' for s in r['score'].values()) for r in rows),
                'distance_correct':sum(r['score']['distance']=='correct' for r in rows),
                'speed_correct':sum(r['score']['speed']=='correct' for r in rows),
                'wrong_fields':sum(s=='wrong' for r in rows for s in r['score'].values()),
                'rejected_fields':sum(s=='reject' for r in rows for s in r['score'].values())})
    save(RUN/'summary.json',summary)
    print(json.dumps(summary,indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--summarize',action='store_true')
    p.add_argument('--model');p.add_argument('--backend',default='ollama');p.add_argument('--endpoint',default='http://127.0.0.1:11434')
    p.add_argument('--variants',nargs='+',default=['full','crop']);args=p.parse_args()
    if args.prepare:prepare()
    elif args.summarize:summarize()
    else:run(args.model,args.backend,args.endpoint,args.variants)
