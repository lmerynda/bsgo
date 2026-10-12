// Capture itself is read-only. Awaitable hooks may supply explicit controls.
import fs from 'node:fs/promises';
import { performance } from 'node:perf_hooks';
import path from 'node:path';
const root=path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));

export async function runCaptureLoop({sky,window,count=10,intervalMs=2000,outputDirectory=path.join(root,'..','..','logs','hud-reader'),
  onObservation=()=>{},onError=()=>{},observer,signal,maxDurationMs=120000,now=()=>performance.now()}={}) {
  if (!Number.isInteger(count)||count<1||count>1000)throw Error('count must be 1..1000');
  if(intervalMs<100)throw Error('intervalMs must be >=100');
  await fs.mkdir(outputDirectory,{recursive:true});
  const log=path.join(outputDirectory,`capture-${Date.now()}.jsonl`);
  const runId=Date.now();
  const request=async payload=>{
    const name=`request-${runId}-${payload.id}.json`;
    const response=path.join(outputDirectory,name.replace('request-','response-'));
    const temporary=path.join(outputDirectory,name+'.tmp');
    await fs.writeFile(temporary,JSON.stringify(payload));await fs.rename(temporary,path.join(outputDirectory,name));
    const deadline=performance.now()+1500;
    while(performance.now()<deadline){
      if(signal?.aborted)throw Error('Capture loop cancelled');
      try{
        const value=JSON.parse(await fs.readFile(response,'utf8'));
        if(value.id!==payload.id||value.run_id!==payload.run_id)throw Error('OCR response correlation mismatch');
        return value;
      }
      catch(error){if(error.code!=='ENOENT')throw error;}
      await new Promise(resolve=>setTimeout(resolve,10));
    }
    throw Error('OCR worker response exceeded 1500 ms; start --watch-directory first');
  };
  const rows=[];let previousStart=null;let lastRow=null;let lastCapture=null;const loopStarted=now();
  const checkBound=()=>{if(signal?.aborted)throw Error('Capture loop cancelled');if(now()-loopStarted>=maxDurationMs)throw Error('Capture loop duration exceeded');};
  try{
    for(let id=0;id<count;id++){
      checkBound();const started=now();const capturedAt=Date.now()/1000;
      lastRow=null;
      lastCapture={id,run_id:runId,captured_at_utc:new Date(capturedAt*1000).toISOString(),
        source:observer?'injected_observation':'live_sky_screenshot',frame_path:null};
      let state,value,file,captureMs;
      if(observer){({state,row:value}=await observer({id,run_id:runId}));captureMs=value.capture_ms??0;}
      else{
        state=await sky.get_window_state({window,include_screenshot:true,include_text:false});
        captureMs=now()-started;const shot=state.screenshots?.[0];
        if(!shot?.url?.startsWith('data:image/'))throw Error('No supported screenshot data URL');
        file=path.join(outputDirectory,`frame-${runId}-${id.toString().padStart(3,'0')}.png`);
        await fs.writeFile(file,Buffer.from(shot.url.slice(shot.url.indexOf(',')+1),'base64'));
        lastCapture.frame_path=file;
        value=await request({id,run_id:runId,path:file,captured_at:capturedAt});
      }
      checkBound();const elapsed=now()-started;const shot=state.screenshots?.[0];
      const row={...value,captured_at_utc:value.captured_at_utc??new Date(capturedAt*1000).toISOString(),
        screenshot_id:shot?.id,capture_ms:+captureMs.toFixed(3),cycle_ms:+elapsed.toFixed(3),
        start_interval_ms:previousStart===null?null:+(started-previousStart).toFixed(3),
        deadline_missed:elapsed>intervalMs,source:lastCapture.source,frame_path:file};
      if(!observer)row.frame_age_ms=Date.now()-Date.parse(row.captured_at_utc);
      if(row.deadline_missed){row.valid=false;row.reasons.push('cycle_deadline_missed');}
      // Sky screenshot IDs are cache tokens (often "screenshot-0" on every
      // fresh capture), not cross-observation freshness identifiers.
      lastRow=row;
      // Safety-critical hook runs before logging and is awaited: no overlapped
      // controls, and no file logging delay between observation and braking.
      const hookStarted=now();const decision=await onObservation(row,state);
      row.control_hook_ms=+(now()-hookStarted).toFixed(3);row.total_cycle_ms=+(now()-started).toFixed(3);
      if(row.total_cycle_ms>intervalMs&&!row.deadline_missed){row.valid=false;row.reasons.push('control_cycle_deadline_missed');throw Error('Control cycle exceeded observation deadline');}
      checkBound();
      rows.push(row);await fs.appendFile(log,JSON.stringify(row)+'\n');
      await fs.writeFile(path.join(outputDirectory,'latest.tmp'),JSON.stringify(row));
      await fs.rename(path.join(outputDirectory,'latest.tmp'),path.join(outputDirectory,'latest.json'));
      previousStart=started;if(decision?.stop)break;
      if(id<count-1)await new Promise((resolve,reject)=>{
        const abort=()=>{clearTimeout(timer);reject(Error('Capture loop cancelled'));};
        const timer=setTimeout(()=>{signal?.removeEventListener('abort',abort);resolve();},Math.max(0,intervalMs-(now()-started)));
        signal?.addEventListener('abort',abort,{once:true});
      });
    }
  }catch(error){
    try{await onError(error);}catch(cleanupError){error.cleanup_error=cleanupError.message;}
    await fs.appendFile(log,JSON.stringify({utc:new Date().toISOString(),failure:error.message,cleanup_error:error.cleanup_error,
      failed_capture:lastCapture,failed_observation:lastRow})+'\n');
    throw error;
  }
  finally{await fs.writeFile(path.join(outputDirectory,'stop'),'');}
  return {log,rows};
}
