// Runs the side panel's inline script against a fake DOM and fake fetch; never opens a browser or sends input.
// Usage: node dock_ui_checks.js <dock.html> <scenario>
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8'),scenario=process.argv[3];
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

class Element {
  constructor(tag,id){this.tag=tag;this.id=id;this.children=[];this.own='';this.hidden=false;this.disabled=false;this.checked=false;
    this.className='';this.dataset={};this.style={};this.attrs={};this.listeners={};this.classes=new Set();
    this.classList={add:n=>this.classes.add(n),remove:n=>this.classes.delete(n),contains:n=>this.classes.has(n),
      toggle:(n,on)=>(on?this.classes.add(n):this.classes.delete(n),on)};}
  get textContent(){return this.own+this.children.map(c=>c.textContent).join('');}
  set textContent(v){this.own=String(v);this.children=[];}
  set innerHTML(v){this.own='';this.children=[];}
  append(...nodes){for(const n of nodes)this.children.push(typeof n==='string'?text(n):n);}
  querySelector(sel){const match=c=>sel.startsWith('.')?c.className.split(' ').includes(sel.slice(1)):c.tag===sel;
    for(const c of this.children){if(match(c))return c;const d=c.querySelector?.(sel);if(d)return d;}return null;}
  setAttribute(n,v){this.attrs[n]=String(v);}
  addEventListener(type,fn){(this.listeners[type]||=[]).push(fn);}
  fire(type){for(const fn of this.listeners[type]||[])fn({target:this});}
}
const text=v=>({textContent:String(v)});
const elements={};
function el(id){return elements[id]||=new Element('div',id);}
// Build the static children the script queries inside each key button.
for(const [action,key] of [['scan_view','F9'],['approach','F10'],['auto_scan','F11'],['npc_fight','F9']]){
  const b=el('k-'+action),label=new Element('span'),kbd=new Element('kbd'),small=new Element('small');
  label.className='label';kbd.own=key;b.children=[label,kbd,small];}
{const b=el('k-pause'),label=new Element('span'),kbd=new Element('kbd');label.className='label';kbd.own='F8';b.children=[label,kbd];}
for(const id of ['c-safe','c-corridor','c-heading','c-point'])el(id).tag='input';
for(const step of ['select','check','fire','pause']){const li=new Element('li');li.dataset.step=step;el('npc-loop').children.push(li);}
const loopNow=()=>el('npc-loop').children.filter(li=>li.classes.has('now')).map(li=>li.dataset.step);

// Server state the fake fetch serves; scenarios mutate these between polls.
const S={startup:null,actions:null,dock:{hotkeys:{sequence:0,last:null,registered:{F8:true,F9:true,F10:true,F11:true}},window:{mode:'beside_right'}},
  game:{ship:{},target:{present:false}},npc:{environment:'simulation',input_authorized:false,phase:'idle',pass_number:0},down:false,replies:{}};
const posts=[];
async function fakeFetch(url,options={}){
  if(S.down)throw new Error('connection refused');
  if(options.method==='POST'){const body=JSON.parse(options.body);posts.push({url,body});
    const reply=S.replies[body.action]||{accepted:true};return {json:async()=>reply};}
  const row={'/api/startup':S.startup,'/api/manual-actions':S.actions,'/api/dock':S.dock,'/api/game-state':S.game,'/api/npc-farm':S.npc}[url];
  if(row===undefined)throw new Error('unexpected '+url);
  return {json:async()=>JSON.parse(JSON.stringify(row))};
}
const ctx=vm.createContext({document:{getElementById:el,createElement:tag=>new Element(tag),createTextNode:text},
  fetch:fakeFetch,AbortController,setTimeout:()=>0,clearTimeout:()=>{},setInterval:()=>0,window:{open(){}},console,Date});
const flush=async()=>{for(let i=0;i<30;i++)await new Promise(r=>setImmediate(r));};
const poll=async()=>{await ctx.refresh();await flush();};
const read=expr=>{const v=vm.runInContext(expr,ctx);return v===undefined?v:JSON.parse(JSON.stringify(v));}; // copy out of the vm realm
const click=async id=>{el(id).fire('click');await flush();};
const tick=id=>{el(id).checked=true;el(id).fire('change');};
const press=async action=>{S.dock.hotkeys.sequence+=1;S.dock.hotkeys.last={sequence:S.dock.hotkeys.sequence,action};await poll();};
const starts=()=>posts.filter(p=>p.body.action==='start');
const status=()=>({title:el('title').textContent,line:el('line').textContent,badge:el('badge').textContent,
  act:el('stateaction').hidden?null:el('stateaction').textContent,actDisabled:el('stateaction').disabled,notice:el('notice').textContent});
const small=action=>el('k-'+action).querySelector('small').textContent;
const label=action=>el('k-'+action).querySelector('.label').textContent;

// Fixtures
const connected=(control={enabled:true,paused:false})=>({phase:'connected',retry_available:false,
  game:{startup_layout:{control_supported:true},client_size:[2560,1440]},features:{mining:{enabled:true}},action_control:control});
const idleRoutine={available:true,ready:true,active:false};
const readyActions=(extra={})=>({actual_mode:'manual',mode_pending:false,paused:false,server_monotonic:100,
  ownership:{owner:null,state:'idle'},
  visible_assessment_context:{token:'vis-1',context_key:'scene-A',expires_at:115},
  assessment_context:{token:'tgt-1',context_key:'portrait-A',expires_at:115,wheel_point:[1200,700]},
  routines:{scan_nearby_unscanned:{...idleRoutine},approach_current_target:{...idleRoutine},auto_scan_selected:{...idleRoutine},npc_fight:{...idleRoutine}},...extra});
async function boot(startup,actions){S.startup=startup;S.actions=actions;vm.runInContext(script,ctx);await flush();}

const scenarios={
  async status_not_connected(){
    await boot({phase:'not_connected',retry_available:true,reason:'game_connection_not_attached',action_control:{enabled:false}},{routines:{}});
    let s=status();assert.equal(s.title,'Waiting for game');assert.equal(s.act,'Find game window');assert.equal(s.line,'The game is not connected.');
    assert.equal(small('scan_view'),'Waiting for the game.');assert.equal(el('checksec').hidden,true);
    await click('stateaction');
    assert.deepEqual(posts,[{url:'/api/startup',body:{action:'retry_connection'}}]);
    S.startup={...S.startup,retry_available:false};await poll();assert.equal(status().act,null);
  },
  async status_connecting(){
    await boot({phase:'connecting',retry_available:true,action_control:{enabled:false}},{routines:{}});
    const s=status();assert.equal(s.title,'Finding the game…');assert.equal(s.act,null);assert.equal(posts.length,0);
  },
  async status_unsupported_layout(){
    const startup=connected({enabled:false,paused:true,acknowledgment_token:'ack-1'});
    startup.game={startup_layout:{control_supported:false},client_size:[1920,1080]};
    await boot(startup,readyActions({paused:true}));
    const s=status();assert.equal(s.title,'Watching only');assert.equal(s.act,'Fit game to 2560 × 1440');assert.match(s.line,/1920 × 1080/);
    assert.equal(small('scan_view'),'Actions need the game at 2560 × 1440.');assert.equal(el('checksec').hidden,true);
    assert.equal(el('rows').textContent,'Asteroids you scan will be listed here.');
    assert.equal(posts.length,0,'polling never resizes the game');
    await click('stateaction');
    assert.deepEqual(posts,[{url:'/api/startup',body:{action:'fit_game_window'}}],'fit never enables actions');
  },
  async status_paused_and_enable(){
    await boot(connected({enabled:false,paused:true,acknowledgment_token:'ack-7'}),readyActions({paused:true}));
    for(let i=0;i<3;i++)await poll();
    assert.equal(posts.length,0,'polling must never enable actions');
    let s=status();assert.equal(s.title,'Actions paused');assert.equal(s.act,'Enable actions');assert.equal(s.actDisabled,false);
    assert.equal(small('scan_view'),'Enable actions first.');assert.equal(el('checksec').hidden,true);
    await click('stateaction');
    assert.deepEqual(posts,[{url:'/api/startup',body:{action:'enable_manual_actions',acknowledgment_token:'ack-7'}}]);
    S.startup=connected({enabled:false,paused:true,acknowledgment_token:null});await poll();
    s=status();assert.equal(s.act,'Enable actions');assert.equal(s.actDisabled,true);
  },
  async status_ready_and_active(){
    await boot(connected(),readyActions());
    let s=status();assert.equal(s.title,'Ready');assert.equal(s.act,null);assert.equal(el('checksec').hidden,false);
    assert.equal(small('scan_view'),'Tick “Ship stopped, scene clear”.');
    S.actions=readyActions({ownership:{owner:'scan_nearby_unscanned',state:'active'},
      routines:{...readyActions().routines,scan_nearby_unscanned:{active:true,ready:false,phase:'selecting'}}});
    await poll();
    s=status();assert.equal(s.title,'Scanning this view');assert.equal(s.badge,'▶');
    assert.equal(label('scan_view'),'Stop scanning');assert.equal(small('scan_view'),'Running · press again to stop');
    assert.ok(el('k-scan_view').classes.has('running'));assert.equal(el('checksec').hidden,true);
    assert.equal(small('approach'),'Scanning this view is running.');
    await click('k-scan_view');
    assert.deepEqual(posts,[{url:'/api/manual-actions',body:{action:'stop',routine:'scan_nearby_unscanned'}}]);
    S.actions=readyActions({routines:{...readyActions().routines,auto_scan_selected:{active:true,waiting_for:'x'}},
      selected_scan_toggle:{attempts:3}});
    await poll();
    s=status();assert.equal(s.title,'Auto-scan on');assert.match(s.line,/3 scanned so far/);
    assert.equal(label('auto_scan'),'Turn auto-scan off');assert.equal(small('auto_scan'),'Running · press again to stop');
  },
  async routine_failure_is_reported_not_silent(){
    await boot(connected(),readyActions());tick('c-safe');
    // The routine fails before the next poll sees it running.
    await click('k-scan_view');assert.equal(starts().length,1);
    S.actions=readyActions({routines:{...readyActions().routines,scan_nearby_unscanned:{...idleRoutine,state:'blocked',
      error:'RuntimeError: arming_expired_before_input'}}});
    await poll();
    assert.equal(status().title,'Ready');
    assert.equal(status().notice,'Scanning this view stopped: The game window was not in front in time. Click into the game, then try again.');
    // While arming, the panel says what it is waiting for.
    S.actions=readyActions({ownership:{owner:'approach_current_target',state:'arming'},routines:{...readyActions().routines,
      approach_current_target:{...idleRoutine,active:true,state:'arming',waiting_for:'waiting_for_game_focus'}}});
    await poll();assert.equal(status().line,'Click into the game window to let it start.');
    // A routine that completes normally adds no failure notice.
    S.actions=readyActions({routines:{...readyActions().routines,approach_current_target:{...idleRoutine,state:'completed'}}});
    el('notice').textContent='';await poll();assert.equal(status().notice,'');
  },
  async npc_mode_toggle_hides_mining_and_ignores_mining_keys(){
    await boot(connected(),readyActions());tick('c-safe');
    await click('m-npc');
    assert.equal(el('m-npc').attrs['aria-pressed'],'true');assert.equal(el('m-mining').attrs['aria-pressed'],'false');
    assert.equal(el('c-safe').checked,false,'switching clears scene checks');
    for(const a of ['scan_view','approach','auto_scan']) assert.equal(el('k-'+a).hidden,true);
    assert.equal(el('k-pause').hidden,false,'F8 pause stays');
    assert.equal(el('checksec').hidden,true);assert.equal(el('findsec').hidden,true);assert.equal(el('npcsec').hidden,false);
    assert.equal(status().title,'NPC farm ready');assert.equal(small('npc_fight'),'Ready');
    assert.deepEqual(loopNow(),[],'idle loop has no active step');assert.equal(el('loop-stats').textContent,'Not running.');
    // Mining hotkeys are neither claimed nor started in NPC mode; F8 still works.
    S.dock.hotkeys.sequence+=1;S.dock.hotkeys.last={sequence:S.dock.hotkeys.sequence,action:'approach',key:'F10'};await poll();
    assert.equal(posts.length,0,'F10 is neither claimed nor started');assert.match(status().notice,/^F10 is an asteroid-mining key/);
    await press('pause');assert.equal(status().notice,'Paused with F8.');
    // F9 in NPC mode is Auto-fight: claimed, then started with no scene token.
    await press('scan_view');
    assert.deepEqual(posts.slice(-2),[{url:'/api/dock',body:{action:'claim_hotkey',sequence:S.dock.hotkeys.sequence}},
      {url:'/api/manual-actions',body:{action:'start',routine:'npc_fight',request:{}}}]);
    assert.equal(status().notice,'Auto-fight on.');
    // While running, Asteroids is locked and F9 stops it.
    S.actions=readyActions({ownership:{owner:'npc_fight',state:'active'},routines:{...readyActions().routines,
      npc_fight:{...idleRoutine,active:true,state:'active'}}});await poll();
    assert.equal(status().title,'Auto-fight on');assert.equal(el('m-mining').disabled,true);assert.equal(label('npc_fight'),'Stop Auto-fight');
    // The loop diagram follows the live step and shows the last check.
    const running=step=>readyActions({ownership:{owner:'npc_fight',state:'active'},routines:{...readyActions().routines,
      npc_fight:{...idleRoutine,active:true,state:'active',loop:step}}});
    S.actions=running({step:'fire',fired:3,skipped:1,checked:true,max_range:2500,fire_ms:[100,300],pause_s:[1,2],
      last:{fire:true,distance:886,reason:'target_in_range'}});await poll();
    assert.deepEqual(loopNow(),['fire']);assert.equal(el('loop-check').textContent,'886 m · in range');
    assert.equal(el('loop-stats').textContent,'3 fired · 1 skipped');assert.equal(el('loop-skip').classes.has('hit'),false);
    S.actions=running({step:'pause',fired:3,skipped:2,checked:true,max_range:2500,last:{fire:false,distance:null,reason:'no_target'}});await poll();
    assert.deepEqual(loopNow(),['pause']);assert.equal(el('loop-check').textContent,'No target');assert.equal(el('loop-skip').classes.has('hit'),true);
    await press('scan_view');assert.deepEqual(posts.at(-1),{url:'/api/manual-actions',body:{action:'stop',routine:'npc_fight'}});
    // Paused actions refuse Auto-fight without posting a start.
    S.actions=readyActions({paused:true});S.startup=connected({enabled:false,paused:true,acknowledgment_token:'a'});await poll();
    const before=posts.length;await click('k-npc_fight');assert.equal(posts.length,before);assert.equal(status().notice,'Enable actions first.');
    S.actions=readyActions();S.startup=connected();await poll();
    // Back to mining restores the keys.
    await click('m-mining');assert.equal(el('k-approach').hidden,false);assert.equal(el('npcsec').hidden,true);
    // A running mining routine blocks switching to NPC.
    S.actions=readyActions({ownership:{owner:'approach_current_target',state:'active'},routines:{...readyActions().routines,
      approach_current_target:{...idleRoutine,active:true,state:'active'}}});await poll();
    assert.equal(el('m-npc').disabled,true);await click('m-npc');assert.equal(el('m-npc').attrs['aria-pressed'],'false');
  },
  async status_app_down(){
    await boot(connected(),readyActions());S.down=true;await poll();
    assert.equal(status().title,'App not responding');
  },
  async start_refused_until_checked(){
    await boot(connected({enabled:false,paused:true,acknowledgment_token:'ack'}),readyActions({paused:true}));
    tick('c-safe');await click('k-scan_view');
    assert.equal(starts().length,0,'paused actions never start');assert.equal(status().notice,'Enable actions first.');
    S.startup=connected();S.actions=readyActions();el('c-safe').checked=false;el('c-safe').fire('change');await poll();
    await click('k-scan_view');assert.equal(starts().length,0,'unticked check never starts');
    assert.match(status().notice,/Tick “Ship stopped, scene clear”/);
    // A tick without a fresh token does not bind and is undone.
    S.actions=readyActions({server_monotonic:120});await poll();tick('c-safe');
    assert.equal(el('c-safe').checked,false);assert.match(status().notice,/No fresh still scene/);
    await click('k-scan_view');assert.equal(starts().length,0);
    // Manual mode is required too.
    S.actions=readyActions({actual_mode:'automatic'});await poll();tick('c-safe');await click('k-scan_view');
    assert.equal(starts().length,0);assert.equal(status().notice,'Switch to Assist first.');
  },
  async start_scan_posts_exact_body(){
    await boot(connected(),readyActions());tick('c-safe');
    assert.equal(small('scan_view'),'Ready');assert.ok(el('k-scan_view').classes.has('ready'));
    await click('k-scan_view');
    assert.deepEqual(starts(),[{url:'/api/manual-actions',body:{action:'start',routine:'scan_nearby_unscanned',
      request:{assessment_token:'vis-1',supervisor_assessment:{safe_stopped_scene:true}}}}]);
    assert.equal(el('c-safe').checked,false,'check clears after a start');assert.deepEqual(read('checks'),{visible:null,target:null});
    await click('k-scan_view');assert.equal(starts().length,1,'a second start needs a new tick');
    // Auto-scan binds the target assessment token.
    tick('c-safe');await click('k-auto_scan');
    assert.deepEqual(starts()[1].body,{action:'start',routine:'auto_scan_selected',
      request:{assessment_token:'tgt-1',supervisor_assessment:{safe_stopped_scene:true}}});
    // A refused start still clears the check.
    S.replies.start={accepted:false,reason:'image_stale'};tick('c-safe');await click('k-scan_view');
    assert.equal(starts().length,3);assert.equal(el('c-safe').checked,false);assert.equal(status().notice,'Game image is old.');
  },
  async start_approach_requires_all_checks_and_point(){
    await boot(connected(),readyActions({assessment_context:{token:'tgt-1',context_key:'portrait-A',expires_at:115}}));
    tick('c-safe');await click('k-approach');
    assert.equal(starts().length,0);assert.equal(status().notice,'Tick the three approach checks.');
    tick('c-corridor');tick('c-heading');await click('k-approach');assert.equal(starts().length,0);
    tick('c-point');await click('k-approach');
    assert.equal(starts().length,0);assert.equal(status().notice,'No flight point for this target.');
    S.actions=readyActions({assessment_context:{token:'tgt-1',context_key:'portrait-A',expires_at:115,wheel_point:[1200]}});await poll();
    await click('k-approach');assert.equal(starts().length,0,'malformed wheel point is refused');
    S.actions=readyActions();await poll();
    assert.equal(small('approach'),'Ready');
    await click('k-approach');
    assert.deepEqual(starts(),[{url:'/api/manual-actions',body:{action:'start',routine:'approach_current_target',
      request:{assessment_token:'tgt-1',supervisor_assessment:{clear_corridor:true,aligned_heading:true,safe_stopped_scene:true,flight_point:true}}}}]);
    for(const id of ['c-safe','c-corridor','c-heading','c-point'])assert.equal(el(id).checked,false,id);
  },
  async check_clears_on_scene_change_and_expiry(){
    await boot(connected(),readyActions());
    // Both scene keys change: the tick clears.
    tick('c-safe');S.actions=readyActions({visible_assessment_context:{token:'vis-2',context_key:'scene-B',expires_at:130},
      assessment_context:{token:'tgt-2',context_key:'portrait-B',expires_at:130,wheel_point:[1,2]}});
    await poll();
    assert.equal(el('c-safe').checked,false);assert.match(status().notice,/scene changed/);
    // Only the visible scene changes: scan is refused, the target binding survives.
    S.actions=readyActions();await poll();tick('c-safe');
    S.actions=readyActions({visible_assessment_context:{token:'vis-2',context_key:'scene-B',expires_at:130}});await poll();
    assert.equal(read('checks.visible'),null);assert.equal(read('checks.target.token'),'tgt-1');
    await click('k-scan_view');assert.equal(starts().length,0);assert.match(status().notice,/again/);
    // Server time passes expires_at: everything clears.
    S.actions=readyActions();await poll();el('c-safe').checked=false;el('c-safe').fire('change');tick('c-safe');
    assert.equal(read('checks.visible.token'),'vis-1');
    S.actions=readyActions({server_monotonic:115});await poll();
    assert.equal(el('c-safe').checked,false);assert.deepEqual(read('checks'),{visible:null,target:null});
    await click('k-scan_view');assert.equal(starts().length,0);
    // Missing server time also drops the binding.
    S.actions=readyActions();await poll();tick('c-safe');S.actions=readyActions({server_monotonic:null});await poll();
    assert.equal(el('c-safe').checked,false);
  },
  async hotkeys_never_replay_and_route(){
    S.dock.hotkeys.sequence=5;S.dock.hotkeys.last={sequence:5,action:'scan_view'};
    await boot(connected(),readyActions({ownership:{owner:'scan_nearby_unscanned',state:'active'},
      routines:{...readyActions().routines,scan_nearby_unscanned:{active:true}}}));
    await poll();assert.equal(posts.length,0,'presses before load are never replayed');assert.equal(read('seenHotkey'),5);
    await press('scan_view');
    assert.deepEqual(posts,[{url:'/api/dock',body:{action:'claim_hotkey',sequence:6}},
      {url:'/api/manual-actions',body:{action:'stop',routine:'scan_nearby_unscanned'}}]);
    S.actions=readyActions();await poll();
    await press('scan_view');assert.equal(starts().length,0);assert.match(status().notice,/^F9: Tick/);
    tick('c-safe');await press('scan_view');
    assert.deepEqual(starts().map(p=>p.body.request.assessment_token),['vis-1']);
    await poll();assert.equal(starts().length,1,'one press starts once');
    // A press another panel already claimed is never acted on here.
    S.replies.claim_hotkey={accepted:false,reason:'hotkey_press_already_handled'};
    tick('c-safe');await press('scan_view');assert.equal(starts().length,1,'unclaimed press never starts');
    assert.equal(el('c-safe').checked,true,'an unclaimed press leaves this panel untouched');
    delete S.replies.claim_hotkey;
    // F8 shows a notice and clears checks, never POSTing a start.
    const before=posts.length;tick('c-safe');await press('pause');
    assert.equal(posts.length,before);assert.equal(status().notice,'Paused with F8.');assert.equal(el('c-safe').checked,false);
    // A press arriving in the same poll as a scene change must not use the old token.
    tick('c-safe');S.actions=readyActions({visible_assessment_context:{token:'vis-2',context_key:'scene-B',expires_at:130},
      assessment_context:{token:'tgt-2',context_key:'portrait-B',expires_at:130,wheel_point:[1,2]}});
    await press('scan_view');assert.equal(starts().length,1,'stale token never sent');
    // A restarted server resets the sequence; later presses still work and old ones are not replayed.
    S.dock.hotkeys={...S.dock.hotkeys,sequence:1,last:{sequence:1,action:'scan_view'}};await poll();
    assert.equal(starts().length,1);tick('c-safe');await press('scan_view');
    assert.deepEqual(starts().map(p=>p.body.request.assessment_token),['vis-1','vis-2']);
  },
  async pause_button(){
    await boot(connected(),readyActions());tick('c-safe');await click('k-pause');
    assert.deepEqual(posts,[{url:'/api/startup',body:{action:'pause_manual_actions'}}]);
    assert.equal(el('c-safe').checked,false);assert.match(status().notice,/^Paused/);
  },
  async readings_unknown_show_dash(){
    const no={value:null,usable:false,reason:'image_stale'},zero={value:0,usable:false,reason:'recognition_rejected'};
    S.game={ship:{hull_current:zero,hull_max:{value:5210,usable:true},energy_current:zero,energy_max:no,speed:zero},
      target:{present:true,distance:zero,current_hp:zero,max_hp:no}};
    await boot(connected(),readyActions({routines:{...readyActions().routines,scan_nearby_unscanned:{...idleRoutine,
      visible_scan:{rows:[{status:'scanning',distance:null},{status:'selection_unverified',distance:0},{status:'mystery_state'},
        {resource:'Water',distance:812.4}]}}}}));
    await ctx.refreshGame();await poll();
    assert.equal(el('v-hull').textContent,'—');assert.equal(el('v-energy').textContent,'—');assert.equal(el('v-speed').textContent,'—');
    assert.equal(el('target').textContent,'Target · range — · HP — / —');
    const rows=el('rows').children.map(r=>r.children.map(c=>c.textContent));
    assert.deepEqual(rows,[['Scanning…',''],['Could not select',''],['Mystery state.',''],['Water','812 m']]);
    assert.equal(el('findtitle').textContent,'This view · 4 checked');
    // Usable-but-malformed values are unknown too.
    S.game={ship:{hull_current:{value:undefined,usable:true},hull_max:{value:5210,usable:true},
      energy_current:{value:'7',usable:true},speed:{value:undefined,usable:true}},target:{present:false}};
    await ctx.refreshGame();await poll();
    assert.equal(el('v-hull').textContent,'—');assert.equal(el('v-energy').textContent,'—');assert.equal(el('v-speed').textContent,'—');
    // Real zeros and readings are shown.
    S.game={ship:{hull_current:{value:2605,usable:true},hull_max:{value:5210,usable:true},energy_current:{value:1234,usable:true},
      energy_max:{value:2000,usable:true},speed:{value:0,usable:true}},
      target:{present:true,distance:{value:800,usable:true},current_hp:{value:1000,usable:true},max_hp:{value:2000,usable:true}}};
    await ctx.refreshGame();await poll();
    assert.equal(el('v-hull').textContent,'50%');assert.equal(el('v-energy').textContent,'1,234');assert.equal(el('v-speed').textContent,'0 m/s');
    assert.equal(el('target').textContent,'Target · 800 m · HP 1,000 / 2,000');
    // No game state at all.
    S.game=null;await ctx.refreshGame();await poll();
    assert.equal(el('v-hull').textContent,'—');assert.equal(el('target').textContent,'No target selected.');
  },
};

if(!scenarios[scenario]){console.error('unknown scenario '+scenario+'; known: '+Object.keys(scenarios).join(', '));process.exit(2);}
scenarios[scenario]().then(()=>console.log('ok '+scenario)).catch(error=>{console.error(error);process.exitCode=1;});
