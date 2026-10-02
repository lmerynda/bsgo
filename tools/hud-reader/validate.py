"""Independent manually labeled live frames and explicitly synthetic fault cases."""
import argparse, json, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw
from hud_reader import HudReader, ROOT, SPEED

LIVE_LABELS=[(1416,True,'9:03:30 PM'),(205,True,'9:03:49 PM'),
 (3063,False,'9:04:30 PM'),(3063,False,'9:05:07 PM'),
 (10327,False,'9:05:26 PM'),(9091,False,'9:06:01 PM'),
 (3063,False,'9:06:21 PM'),(3063,False,'9:08:18 PM'),
 (2885,True,'9:08:55 PM'),(2885,True,'9:09:50 PM')]

def speed_fixture(base,text):
    im=base.copy();draw=ImageDraw.Draw(im);draw.rectangle(SPEED,fill=(35,45,49))
    # Independently drawn seven-segment display, not derived from classifier templates.
    active={'0':'ab cdef'.replace(' ',''),'1':'bc','2':'abdeg','3':'abcdg','4':'bcfg',
            '5':'acdfg','6':'acdefg','7':'abc','8':'abcdefg','9':'abcdfg'}
    # a top, b upper-right, c lower-right, d bottom, e lower-left, f upper-left, g middle
    boxes={'a':(2,0,9,1),'b':(10,1,11,7),'c':(10,10,11,17),
           'd':(2,16,9,17),'e':(0,10,1,17),'f':(0,1,1,7),'g':(2,8,9,9)}
    width=14*sum(ch!='.' for ch in text)+6
    x=1405-width//2
    for ch in text:
        if ch=='.':draw.rectangle((x+1,1132,x+2,1133),fill='white');x+=6;continue
        for segment in active[ch]:
            a,b,c,d=boxes[segment];draw.rectangle((x+a,1116+b,x+c,1116+d),fill='white')
        x+=14
    return im

def build_cases():
    base=Image.open(ROOT/'sample.png').convert('RGB');cases=[]
    for value in ('0.0','5.0','20.0','44.0','12.3','23.4','34.5','16.7','28.9'):
        cases.append((f'synthetic-speed-{value}',speed_fixture(base,value),{'distance':1416,'speed':float(value),'valid':True}))
    absent=base.copy();ImageDraw.Draw(absent).rectangle((710,50,955,160),fill='black')
    cases.append(('synthetic-absent-target',absent,{'valid':False}))
    erased=base.copy();ImageDraw.Draw(erased).rectangle((764,138,790,154),fill=(12,19,22))
    cases.append(('synthetic-erased-final-digit',erased,{'valid':False,'distance':None}))
    clipped=base.copy();ImageDraw.Draw(clipped).rectangle((769,138,790,154),fill=(12,19,22))
    cases.append(('synthetic-clipped-final-digit',clipped,{'valid':False,'distance':None}))
    shifted=Image.new('RGB',base.size);shifted.paste(base,(10,0))
    cases.append(('synthetic-shifted-layout',shifted,{'valid':False}))
    cases.append(('synthetic-wrong-resolution',base.resize((1920,1080)),{'valid':False}))
    no_decimal=base.copy();ImageDraw.Draw(no_decimal).rectangle((1403,1131,1408,1134),fill=(35,45,49))
    cases.append(('synthetic-missing-decimal',no_decimal,{'valid':False,'speed':None}))
    empty_speed=base.copy();ImageDraw.Draw(empty_speed).rectangle(SPEED,fill=(35,45,49))
    cases.append(('synthetic-absent-speed',empty_speed,{'valid':False,'speed':None}))
    unknown=base.copy();draw=ImageDraw.Draw(unknown);draw.rectangle(SPEED,fill=(35,45,49));draw.rectangle((1389,1116,1400,1133),fill='white')
    cases.append(('synthetic-unknown-speed-glyph',unknown,{'valid':False,'speed':None}))
    return cases

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--interval',type=float,default=0)
    args=parser.parse_args();reader=HudReader();rows=[];failures=[];live_manifest=[]
    cases=[]
    for index,(distance,asteroid,clock) in enumerate(LIVE_LABELS,1):
        file=ROOT/'fixtures/live'/f'live-{index:02}.png'
        label={'distance':distance,'speed':0.0,'valid':asteroid}
        cases.append((file.name,Image.open(file).convert('RGB'),label))
        live_manifest.append({'file':str(file.relative_to(ROOT)),'source':'live_sky_capture','label_source':'manual_visual_inspection',
          'game_clock':clock,'saved_at_utc':datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat(),
          'saved_timestamp_is_not_precise_capture_time':True,**label})
    synthetic_directory=ROOT/'fixtures/synthetic';synthetic_directory.mkdir(parents=True,exist_ok=True)
    for name,im,label in build_cases():
        im.save(synthetic_directory/(name+'.png'));cases.append((name,im,label))
    (ROOT/'fixtures/manifest.json').write_text(json.dumps({'calibration':'sample.png HUD manually annotated; live frames held out',
      'live':live_manifest,'synthetic_count':len(cases)-len(live_manifest)},indent=2))
    for round_no in range(args.rounds):
        for name,im,label in cases:
            start=time.perf_counter();result=reader.read(im)
            mismatches={key:{'expected':value,'actual':result.get(key)} for key,value in label.items() if result.get(key)!=value}
            row={'case':name,'round':round_no,'expected':label,'actual':result,'pass':not mismatches};rows.append(row)
            if mismatches:failures.append({'case':name,**mismatches})
            if args.interval>0:time.sleep(max(0,args.interval-(time.perf_counter()-start)))
    stale=reader.read(cases[0][1],time.time()-10)
    if stale['valid'] or 'stale_or_future_frame' not in stale['reasons']:failures.append({'case':'stale_frame'})
    report={'utc':datetime.now(timezone.utc).isoformat(),'real_frames':10,'real_distances':sorted(set(x[0] for x in LIVE_LABELS)),
      'real_distance_digits':''.join(sorted(set(''.join(str(x[0]) for x in LIVE_LABELS)))),
      'real_speeds':[0.0],'synthetic_cases':len(cases)-10,'rounds':args.rounds,'checks':len(rows)+1,
      'failures':failures,'median_recognition_ms':statistics.median(row['actual'].get('recognition_ms',0) for row in rows),
      'rows':rows}
    (ROOT/'validation-results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
    if failures:raise SystemExit(1)

if __name__=='__main__':main()
