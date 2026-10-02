// Uses only documented Sky methods. Dry-run is the default; no game internals.
import { performance } from 'node:perf_hooks';
import fs from 'node:fs/promises';
import path from 'node:path';
import { runCaptureLoop } from './capture-loop.mjs';

// User-provided controls screenshot: Full speed = PageUp; Idle speed = PageDown.
// Sky accepts X Window System keysym names.
export const THROTTLE_KEYS=Object.freeze({maximum:'Page_Up',zero:'Page_Down'});
export const APPROACH_DEFAULTS=Object.freeze({brakeAt:900,intervalMs:2000});

export class ApproachPolicy {
  // maxSpeed is a telemetry plausibility bound, not a commanded speed target.
  constructor({brakeAt=APPROACH_DEFAULTS.brakeAt,maxSpeed=44,maxFrameAgeMs=2000,brakeAckMs=4000,maxBrakingMs=30000}={}) {
    if(!Number.isFinite(brakeAt)||brakeAt<600||brakeAt>1400)throw Error('Brake threshold must be 600..1400 m');
    Object.assign(this,{brakeAt,maxSpeed,maxFrameAgeMs,brakeAckMs,maxBrakingMs,phase:'initial',identity:null,last:null,brake:null,final:null});
  }
  decide(row) {
    if(!row.valid||!row.asteroid_selected||!Number.isFinite(row.distance)||!Number.isFinite(row.speed))throw Error('Invalid HUD observation');
    if(!Number.isFinite(row.frame_age_ms)||row.frame_age_ms<0||row.frame_age_ms>this.maxFrameAgeMs)throw Error('Stale HUD observation');
    if(row.speed<0||row.speed>this.maxSpeed+.1)throw Error('Invalid speed');
    if(typeof row.target_identity!=='string'||!row.target_identity)throw Error('Missing visible target identity');
    if(!Array.isArray(row.target_portrait)||row.target_portrait.length!==144||row.target_portrait.some(value=>!Number.isInteger(value)||value<0||value>7))throw Error('Missing visible target portrait');
    if(this.identity&&row.target_identity!==this.identity)throw Error('Selected target changed');
    if(this.portrait&&row.target_portrait&&this.portrait.length===row.target_portrait.length){
      const difference=this.portrait.reduce((sum,value,i)=>sum+Math.abs(value-row.target_portrait[i]),0)/this.portrait.length;
      if(difference>.75)throw Error('Selected target portrait changed');
    }
    if(this.phase==='initial') {
      if(row.speed!==0)throw Error('Approach must start stopped');
      if(row.distance<this.brakeAt+250)throw Error('Initial target too close for full-speed approach');
      this.identity=row.target_identity;this.portrait=row.target_portrait;this.last=row;this.phase='approaching';return 'accelerate';
    }
    if(this.last) {
      const dt=(Date.parse(row.captured_at_utc)-Date.parse(this.last.captured_at_utc))/1000;
      if(!Number.isFinite(dt)||dt<=0||dt>4)throw Error('Observation gap exceeded');
      if(Math.abs(row.distance-this.last.distance)>this.maxSpeed*dt+30)throw Error('Impossible distance change');
      if(this.phase==='approaching'&&row.distance>this.last.distance+3)throw Error('Target is not closing');
      if(this.phase==='approaching'&&row.speed>5&&this.last.speed>5&&row.distance>=this.last.distance-2)throw Error('Approach stalled or tangent');
    }
    const previous=this.last;this.last=row;
    if(this.phase==='approaching'&&row.distance<=this.brakeAt) {
      this.phase='braking';this.brake={observed_distance:row.distance,observed_speed:row.speed,captured_at_utc:row.captured_at_utc};
      this.brakeSamples=0;this.brakeAcknowledged=false;this.brakeProgressAt=Date.parse(row.captured_at_utc);this.minimumBrakeSpeed=row.speed;return 'brake';
    }
    if(this.phase==='braking'&&row.speed===0) {this.phase='complete';this.final=row;return 'complete';}
    if(this.phase==='braking') {
      const timestamp=Date.parse(row.captured_at_utc);const elapsed=timestamp-Date.parse(this.brake.captured_at_utc);
      this.brakeSamples++;
      if(row.speed>previous.speed+.3)throw Error('Braking speed increased; stop input not holding');
      if(row.speed<=this.minimumBrakeSpeed-.5){this.minimumBrakeSpeed=row.speed;this.brakeProgressAt=timestamp;this.brakeAcknowledged=true;}
      if(!this.brakeAcknowledged&&(this.brakeSamples>=2||elapsed>=this.brakeAckMs))throw Error('Brake not acknowledged within two samples / four seconds');
      if(this.brakeAcknowledged&&timestamp-this.brakeProgressAt>=this.brakeAckMs)throw Error('Braking stalled without further deceleration');
      if(elapsed>=this.maxBrakingMs)throw Error('Braking exceeded the time limit without verified zero');
    }
    return 'observe';
  }
}

function validateRouteAssessment(assessment) {
  if(!assessment||!Array.isArray(assessment.screenshot_ids)||!assessment.screenshot_ids.length||
    assessment.screenshot_ids.some(id=>typeof id!=='string'||!id)||typeof assessment.notes!=='string'||!assessment.notes.trim()||
    typeof assessment.target_identity!=='string'||!assessment.target_identity||!Number.isFinite(assessment.candidate_distance)||
    !Array.isArray(assessment.intervening_objects))throw Error('Live preflight requires timestamped route assessment, screenshot IDs, candidate identity/range and obstacle observations');
  // Static asteroid clearance does not expire while ship position and target stay
  // unchanged. The miner invalidates it after movement, target loss or UI handoff;
  // fresh HUD state and candidate matching are still required before thrust.
  const capturedAt=Date.parse(assessment.captured_at_utc);
  if(!Number.isFinite(capturedAt)||capturedAt>Date.now())throw Error('Route assessment requires a valid past capture timestamp');
  for(const object of assessment.intervening_objects){
    if(typeof object.in_corridor!=='boolean'||typeof object.notes!=='string')throw Error('Incomplete intervening-object assessment');
    if(object.in_corridor&&(!Number.isFinite(object.range)||object.range<=assessment.candidate_distance+200))throw Error('Intervening corridor object has obstructed or unresolved depth');
  }
}

export async function runApproach({sky,window,outputDirectory,mode='dry-run',corridorVerified=false,
  headingVerified=false,wantedResource=false,fireWhenBraking=false,brakeAt=APPROACH_DEFAULTS.brakeAt,count=40,
  intervalMs=APPROACH_DEFAULTS.intervalMs,maxDurationMs=90000,signal,observer,routeAssessment,now=()=>performance.now()}={}) {
  if(!['dry-run','live'].includes(mode))throw Error('mode must be dry-run or live');
  if(mode==='live'&&(!corridorVerified||!headingVerified))throw Error('Miner must visually verify corridor and aligned heading before live thrust');
  if(mode==='live')validateRouteAssessment(routeAssessment);
  if(fireWhenBraking&&!wantedResource)throw Error('G requires a verified wanted resource');
  const policy=new ApproachPolicy({brakeAt});const events=[];
  const eventLog=outputDirectory?path.join(outputDirectory,`approach-events-${Date.now()}.json`):null;
  let moved=false;let zeroVerified=false;let latestState=null;let stoppedCommand=false;let failure=null;
  const record=(type,data={})=>events.push({utc:new Date().toISOString(),type,...data});
  if(routeAssessment)record('route_assessment',{assessment:routeAssessment});
  const zero=async(state,reason,fire=false)=>{
    if(mode!=='live'){record('would_brake',{reason,fire});return;}
    const start=now();record('zero_input_started',{reason,pre_command_distance:policy.last?.distance,
      pre_command_speed:policy.last?.speed,pre_command_capture_utc:policy.last?.captured_at_utc,
      actual_input_distance:null});
    // Idle throttle does not establish zero speed; fresh HUD readings must confirm it.
    await sky.press_key({window:state?.window??window,key:THROTTLE_KEYS.zero});
    stoppedCommand=true;
    record('zero_input_returned',{duration_ms:now()-start,reason});
    if(fire){const gunStart=now();await sky.press_key({window:state?.window??window,key:'g'});record('g_input_returned',{duration_ms:now()-gunStart});}
  };
  try{
    const loop=await runCaptureLoop({sky,window,outputDirectory,count,intervalMs,maxDurationMs,signal,observer,now,
      onObservation:async(row,state)=>{
        latestState=state;const action=policy.decide(row);record('observation',{distance:row.distance,speed:row.speed,action,
          frame_age_ms:row.frame_age_ms,captured_at_utc:row.captured_at_utc});
        if(action==='accelerate') {
          if(mode==='dry-run'){record('would_accelerate',{throttle:'maximum'});return {stop:true};}
          validateRouteAssessment(routeAssessment);
          if(row.target_identity!==routeAssessment.target_identity||Math.abs(row.distance-routeAssessment.candidate_distance)>30)throw Error('Fresh target differs from the route assessment');
          // Set moved before awaiting: rejection/timeout leaves input outcome uncertain.
          moved=true;const start=now();record('full_input_started',{distance:row.distance});
          await sky.press_key({window:state.window,key:THROTTLE_KEYS.maximum});
          record('full_input_returned',{duration_ms:now()-start});
        }
        if(action==='brake')await zero(state,'threshold',fireWhenBraking&&wantedResource);
        if(action==='complete'){zeroVerified=true;record('stopped',{distance:row.distance,speed:row.speed});return {stop:true};}
      },
      onError:async(error)=>{failure=error;record('failure',{message:error.message});if(moved&&!zeroVerified)await zero(null,'failure');}
    });
    if(mode==='live'&&policy.phase!=='complete')throw Error('Approach ended before a verified stop');
    return {mode,completed:policy.phase==='complete',brake_threshold:brakeAt,brake:policy.brake,
      final_distance:policy.final?.distance??null,final_speed:policy.final?.speed??null,events,event_log:eventLog,log:loop.log};
  }catch(error){failure=error;record('failure',{message:error.message});throw Object.assign(error,{approach_events:events});}
  finally{
    let cleanupFailure=null;
    if(mode==='live'&&moved&&!zeroVerified){
      try{
        if(!stoppedCommand||!failure)await zero(null,'final_cleanup');
        // Fresh visible verification even if OCR is unavailable; never claim 0.0
        // from a command alone. Miner can inspect this snapshot after the run.
        latestState=await sky.get_window_state({window,include_screenshot:true,include_text:false});
        record('cleanup_snapshot',{screenshot_id:latestState.screenshots?.[0]?.id,zero_verified_by_ocr:false});
      }catch(error){cleanupFailure=error;record('cleanup_failed',{message:error.message});if(failure)failure.cleanup_error=error.message;}
    }
    if(eventLog){await fs.mkdir(outputDirectory,{recursive:true});await fs.writeFile(eventLog,JSON.stringify({mode,brake_threshold:brakeAt,
      completed:zeroVerified,failure:failure?.message??null,events},null,2));}
    if(cleanupFailure&&!failure)throw cleanupFailure;
  }
}
