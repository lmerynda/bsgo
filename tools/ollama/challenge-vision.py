"""Three existing malformed fixtures; no live game actions."""
import importlib.util,json,base64,time
from pathlib import Path
from PIL import Image,ImageDraw
spec=importlib.util.spec_from_file_location('common',Path(__file__).with_name('compare-vision.py'))
common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
spec=importlib.util.spec_from_file_location('baseline',common.RUN/'baseline/hud_reader.py')
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
reader=baseline.HudReader()
cases=[('synthetic-erased-final-digit','distance'),('synthetic-missing-decimal','speed'),('synthetic-absent-speed','speed')]
prepared=[]
for name,field in cases:
    path=common.ROOT/'tools/hud-reader/fixtures/synthetic'/f'{name}.png'
    with Image.open(path) as im:
        observed=reader.read(im.convert('RGB'))
        canvas=Image.new('RGB',(810,556),(25,25,25));draw=ImageDraw.Draw(canvas)
        draw.text((8,3),'Selected target panel',(255,255,255))
        canvas.paste(im.crop((700,45,970,165)).resize((810,360),Image.Resampling.NEAREST),(0,20))
        draw.text((8,383),'Actual ship speed',(255,255,255))
        canvas.paste(im.crop((1340,1098,1460,1150)).resize((360,156),Image.Resampling.NEAREST),(0,400))
        crop=common.RUN/'images'/f'{name}-hud.png';canvas.save(crop)
    if observed[field] is not None: raise ValueError('Unexpected baseline acceptance')
    prepared.append((name,field,crop,observed))
results=[]
for model in ['qwen3.5:0.8b','qwen3-vl:4b-instruct']:
    for name,field,path,observed in prepared:
        start=time.perf_counter()
        response=common.api('http://127.0.0.1:11434','/api/chat',{'model':model,'messages':[{'role':'user','content':common.PROMPT,
             'images':[base64.b64encode(path.read_bytes()).decode()]}],'think':False,'stream':False,'keep_alive':'5m',
             'options':{'temperature':0,'num_ctx':8192,'num_predict':96,'repeat_penalty':1.1}})
        parsed=common.parse(response['message']['content']) if response.get('done_reason')!='length' else None
        results.append({'model':model,'case':name,'field_expected_unknown':field,'parsed':parsed,
             'returned_unknown':parsed is not None and parsed[field] is None,'invalid_response':parsed is None,
             'wall_seconds':time.perf_counter()-start,'server_load_seconds':response.get('load_duration',0)/1e9,
             'baseline':observed,'response':response})
        print(json.dumps({k:results[-1][k] for k in ['model','case','parsed','returned_unknown','invalid_response']}),flush=True)
    common.api('http://127.0.0.1:11434','/api/generate',{'model':model,'keep_alive':0})
common.save(common.RUN/'malformed-challenge.json',{'expected_source':'existing validate.py synthetic field rejection cases',
     'scope':'3 deliberately damaged or missing fields; same prompt and crop transform; timed responses include model load',
     'results':results})
