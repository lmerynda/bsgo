import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { runApproach, ApproachPolicy } from './approach-controller.mjs';
import { runCaptureLoop } from './capture-loop.mjs';

const root=path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));
const base=Date.parse('2026-09-30T22:00:00Z');
function frame(distance,speed,index,extra={}) {
  return {valid:true,reasons:[],asteroid_selected:true,distance,speed,frame_age_ms:10,
    captured_at_utc:new Date(base+index*2000).toISOString(),target_identity:'asteroid:fixture-max-hp',
    target_portrait:Array(144).fill(2),...extra};
}
async function harness(name,frames,options={}) {
  const directory=path.join(root,'runs','mock-tests',name+'-'+Date.now());
  const actions=[];const keys=[];let position=0;
  const sky={
    drag:async()=>{throw Error('Throttle commands must use keyboard shortcuts');},
    press_key:async input=>{
      assert.equal(input.window.id,1);keys.push(input.key);
      actions.push(input.key==='Page_Up'?'full':input.key==='Page_Down'?'zero':input.key);
      if(options.delayFull&&input.key==='Page_Up')await new Promise(r=>setTimeout(r,130));
    },
    get_window_state:async()=>{actions.push('cleanup-snapshot');return {window:{id:1},screenshots:[{id:'cleanup'}]};}
  };
  const observer=async()=>{
    if(options.readFailure&&position===1)throw Error('Simulated OCR transport failure');
    const index=position++;return {row:frames[index]??frames.at(-1),state:{window:{id:1},screenshots:[{id:'frame-'+index}]}};
  };
  const run=()=>runApproach({sky,window:{id:1},outputDirectory:directory,mode:'live',corridorVerified:true,
    headingVerified:true,brakeAt:700,count:frames.length,intervalMs:100,maxDurationMs:4000,observer,
    routeAssessment:{captured_at_utc:new Date().toISOString(),screenshot_ids:['preflight'],notes:'Mock clear aligned route',
      target_identity:frames[0].target_identity,candidate_distance:frames[0].distance,intervening_objects:[]},...options});
  return {directory,actions,keys,run};
}

test('maximum throttle approach sends PageUp, then PageDown and G, and verifies stopped speed',async()=>{
  const frames=[frame(1100,0,0),frame(1018,44,1),frame(930,44,2),frame(842,44,3),
    frame(754,44,4),frame(666,44,5),frame(545,0,7)];
  const h=await harness('normal',frames,{wantedResource:true,fireWhenBraking:true});
  const result=await h.run();assert.deepEqual(h.actions,['full','zero','g']);
  assert.deepEqual(h.keys,['Page_Up','Page_Down','g']);
  assert.equal(result.final_distance,545);assert.equal(result.brake.observed_distance,666);
  const events=JSON.parse(await fs.readFile(result.event_log));assert(events.completed);
  assert.equal(events.events.find(e=>e.type==='zero_input_started').actual_input_distance,null);
});

test('default dry-run performs no input',async()=>{
  const h=await harness('dry',[frame(1300,0,0)],{mode:'dry-run'});
  const result=await h.run();assert.equal(result.mode,'dry-run');assert.deepEqual(h.actions,[]);
});

for(const [name,second,message] of [
  ['invalid-ocr',frame(1020,44,1,{valid:false,reasons:['ambiguous_speed']}),'Invalid HUD'],
  ['target-change',frame(1020,44,1,{target_identity:'asteroid:different-hp'}),'Selected target'],
  ['stale',frame(1020,44,1,{frame_age_ms:3000}),'Stale HUD'],
  ['distance-jump',frame(500,44,1),'Impossible distance'],
  ['non-closing',frame(1200,44,1),'Target is not closing'],
  ['gap',frame(1000,44,3),'Observation gap']]) {
  test(name+' stops instead of coasting, persists failing observation/events',async()=>{
    const h=await harness(name,[frame(1100,0,0),second]);
    await assert.rejects(h.run,message?new RegExp(message):undefined);
    assert.deepEqual(h.actions,['full','zero','cleanup-snapshot']);
    const files=await fs.readdir(h.directory);
    const eventLog=JSON.parse(await fs.readFile(path.join(h.directory,files.find(f=>f.startsWith('approach-events-')))));
    assert(eventLog.failure);assert(eventLog.events.some(e=>e.type==='cleanup_snapshot'));
    const captureLog=await fs.readFile(path.join(h.directory,files.find(f=>f.startsWith('capture-'))),'utf8');
    assert(captureLog.includes('failed_observation'));
  });
}

test('OCR transport failure stops and captures visible cleanup state',async()=>{
  const h=await harness('read-failure',[frame(1100,0,0),frame(1020,44,1)],{readFailure:true});
  await assert.rejects(h.run,/transport failure/);assert.deepEqual(h.actions,['full','zero','cleanup-snapshot']);
});

test('control latency exceeding cadence triggers immediate zero',async()=>{
  const h=await harness('control-overrun',[frame(1100,0,0),frame(1020,44,1)],{delayFull:true});
  await assert.rejects(h.run,/exceeded observation deadline/);assert.deepEqual(h.actions,['full','zero','cleanup-snapshot']);
});

test('bounded approach without final zero stops in cleanup',async()=>{
  const h=await harness('incomplete',[frame(1100,0,0),frame(1020,44,1)]);
  await assert.rejects(h.run,/before a verified stop/);assert.deepEqual(h.actions,['full','zero','cleanup-snapshot']);
});

test('cancel during flight stops and does not take another sample',async()=>{
  const abort=new AbortController();const h=await harness('cancel',[frame(1100,0,0),frame(1020,44,1)],{signal:abort.signal});
  const promise=h.run();setTimeout(()=>abort.abort(),25);await assert.rejects(promise,/cancelled/);
  assert.deepEqual(h.actions,['full','zero','cleanup-snapshot']);
});

test('live mode needs visually verified corridor and heading; G needs wanted resource',async()=>{
  await assert.rejects(runApproach({mode:'live'}),/visually verify/);
  await assert.rejects(runApproach({fireWhenBraking:true}),/wanted resource/);
});

test('awaits observation hook before another frame',async()=>{
  const calls=[];const directory=path.join(root,'runs','mock-tests','await-hook-'+Date.now());
  await runCaptureLoop({outputDirectory:directory,count:2,intervalMs:100,observer:async({id})=>{
    calls.push('frame'+id);return{state:{screenshots:[{id:String(id)}]},row:frame(1100,0,id)};
  },onObservation:async(row)=>{calls.push('hook-start');await new Promise(r=>setTimeout(r,20));calls.push('hook-end');}});
  assert.deepEqual(calls,['frame0','hook-start','hook-end','frame1','hook-start','hook-end']);
});

test('wrong OCR response request ID is rejected before callback',async()=>{
  const directory=path.join(root,'runs','mock-tests','correlation-'+Date.now());await fs.mkdir(directory,{recursive:true});
  const png=await fs.readFile(path.join(root,'sample.png'));let hookCalled=false;
  const worker=(async()=>{
    for(let i=0;i<100;i++){
      const files=await fs.readdir(directory);const file=files.find(f=>/^request-.*\.json$/.test(f));
      if(file){const request=JSON.parse(await fs.readFile(path.join(directory,file),'utf8'));
        await fs.writeFile(path.join(directory,file.replace('request-','response-')),JSON.stringify({...frame(1100,0,0),id:request.id+1,run_id:request.run_id}));return;}
      await new Promise(r=>setTimeout(r,10));
    }throw Error('Mock request absent');
  })();
  await assert.rejects(runCaptureLoop({outputDirectory:directory,count:1,sky:{get_window_state:async()=>({screenshots:[{id:'mock',url:'data:image/png;base64,'+png.toString('base64')} ]})},
    onObservation:()=>{hookCalled=true;}}),/correlation mismatch/);
  await worker;assert.equal(hookCalled,false);
});

const approachFrames=()=>[frame(1100,0,0),frame(1018,44,1),frame(930,44,2),frame(842,44,3),frame(754,44,4),frame(666,44,5)];
test('line approach brakes at long range and follows sustained slow deceleration to zero',async()=>{
  const rows=[frame(1700,0,0),frame(1640,30,1),frame(1580,30,2),frame(1520,30,3),frame(1460,30,4),
    frame(1400,30,5),frame(1340,30,6),frame(1280,30,7),frame(1220,30,8),frame(1160,30,9)];
  for(let i=10;i<=24;i++)rows.push(frame(1160-(i-9)*25,30-(i-9)*2,i));
  const h=await harness('line-deceleration',rows,{brakeAt:1200});
  const result=await h.run();assert.equal(result.brake.observed_distance,1160);
  assert.equal(result.final_speed,0);assert.deepEqual(h.keys,['Page_Up','Page_Down']);
});
test('line policy rejects invalid threshold and prolonged uncompleted braking',()=>{
  assert.throws(()=>new ApproachPolicy({brakeAt:NaN}),/Brake threshold/);
  assert.throws(()=>new ApproachPolicy({brakeAt:1500}),/Brake threshold/);
  const policy=new ApproachPolicy({brakeAt:1200});policy.decide(frame(1700,0,0));policy.decide(frame(1630,40,1));
  for(let i=2;i<=7;i++)policy.decide(frame(1630-(i-1)*80,40,i));
  for(let i=8;i<=21;i++)policy.decide(frame(1150-(i-7)*30,40-(i-7),i));
  assert.throws(()=>policy.decide(frame(700,25,22)),/time limit/);
});
test('unacknowledged zero command reissues stop after two fresh samples, not ninety seconds',async()=>{
  const h=await harness('brake-no-ack',[...approachFrames(),frame(578,44,6),frame(490,44,7)]);
  await assert.rejects(h.run,/Brake not acknowledged/);assert.deepEqual(h.actions,['full','zero','zero','cleanup-snapshot']);
});
test('legitimate delayed deceleration tolerates first residual full speed then confirms zero',async()=>{
  const h=await harness('delayed-brake',[...approachFrames(),frame(578,44,6),frame(498,40,7),frame(438,20,8),frame(410,0,9)]);
  const result=await h.run();assert.equal(result.final_speed,0);assert.equal(result.final_distance,410);assert.deepEqual(h.actions,['full','zero']);
});
test('renewed speed increase after braking triggers stop immediately',async()=>{
  const h=await harness('brake-increase',[...approachFrames(),frame(590,40,6),frame(514,42,7)]);
  await assert.rejects(h.run,/Braking speed increased/);assert.deepEqual(h.actions,['full','zero','zero','cleanup-snapshot']);
});
test('partially acknowledged but stalled deceleration reissues stop',async()=>{
  const h=await harness('brake-stalled',[...approachFrames(),frame(590,40,6),frame(510,40,7),frame(430,40,8)]);
  await assert.rejects(h.run,/Braking stalled/);assert.deepEqual(h.actions,['full','zero','zero','cleanup-snapshot']);
});
test('fresh consecutive captures may legitimately reuse screenshot-0',async()=>{
  const rows=[];const directory=path.join(root,'runs','mock-tests','same-id-'+Date.now());
  await runCaptureLoop({outputDirectory:directory,count:3,intervalMs:100,observer:async({id})=>({
    state:{screenshots:[{id:'screenshot-0'}]},row:frame(1100-id*50,id?44:0,id)
  }),onObservation:row=>rows.push(row)});
  assert.equal(rows.length,3);assert(rows.every(row=>row.valid));
});
test('live preflight rejects missing, invalid timestamp and uncertain obstacle evidence',async()=>{
  const common={mode:'live',corridorVerified:true,headingVerified:true};
  await assert.rejects(runApproach(common),/route assessment/);
  const route={captured_at_utc:'invalid',screenshot_ids:['screenshot-0'],notes:'Clear route',
    target_identity:'same',candidate_distance:1500,intervening_objects:[]};
  await assert.rejects(runApproach({...common,routeAssessment:route}),/valid past capture timestamp/);
  route.captured_at_utc=new Date().toISOString();route.intervening_objects=[{in_corridor:true,range:null,notes:'Unknown depth'}];
  await assert.rejects(runApproach({...common,routeAssessment:route}),/unresolved depth/);
});

test('static route remains usable after elapsed time when stopped candidate is unchanged',async()=>{
  const frames=[frame(1100,0,0),frame(1018,44,1),frame(930,44,2),frame(842,44,3),
    frame(754,44,4),frame(666,44,5),frame(545,0,7)];
  const route={captured_at_utc:new Date(Date.now()-3600000).toISOString(),screenshot_ids:['route'],
    notes:'Static corridor; ship has not moved and candidate is unchanged',target_identity:frames[0].target_identity,
    candidate_distance:1100,intervening_objects:[]};
  const h=await harness('static-route',frames,{routeAssessment:route});
  const result=await h.run();assert(result.completed);assert.deepEqual(h.keys,['Page_Up','Page_Down']);
});

test('reused route cannot authorize thrust when stopped candidate no longer matches',async()=>{
  for(const changed of [{target_identity:'another-rock'},{candidate_distance:1500}]){
    const route={captured_at_utc:new Date(Date.now()-3600000).toISOString(),screenshot_ids:['route'],
      notes:'Previously clear',target_identity:'asteroid:fixture-max-hp',candidate_distance:1100,
      intervening_objects:[],...changed};
    const h=await harness('changed-route',[frame(1100,0,0)],{routeAssessment:route});
    await assert.rejects(h.run(),/Fresh target differs/);assert.deepEqual(h.keys,[]);
  }
});

test('actual same-target OCR pair is accepted while different HP and portrait stay rejected',async()=>{
  const pair=JSON.parse(await fs.readFile(path.join(root,'fixtures/identity-pair-observations.json'),'utf8'));
  const actual=pair.observations.map((row,index)=>({...row,frame_age_ms:10,captured_at_utc:new Date(base+index*2000).toISOString()}));
  const policy=new ApproachPolicy();assert.equal(policy.decide(actual[0]),'accelerate');
  assert.equal(policy.decide(actual[1]),'observe');
  assert.equal(actual[0].target_identity,'asteroid:hp:3770');assert.equal(actual[0].target_identity,actual[1].target_identity);
  const hpChanged=new ApproachPolicy();hpChanged.decide(actual[0]);
  assert.throws(()=>hpChanged.decide({...actual[1],target_identity:'asteroid:hp:3771'}),/Selected target changed/);
  const portraitChanged=new ApproachPolicy();portraitChanged.decide(actual[0]);
  assert.throws(()=>portraitChanged.decide({...actual[1],target_portrait:Array(144).fill(7)}),/portrait changed/);
  const typeChanged=new ApproachPolicy();typeChanged.decide(actual[0]);
  assert.throws(()=>typeChanged.decide({...actual[1],valid:false,asteroid_selected:false,target_identity:null}),/Invalid HUD/);
});
