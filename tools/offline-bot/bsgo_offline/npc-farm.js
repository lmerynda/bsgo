/* Offline rehearsal UI: never calls live-input or mining action endpoints. */
(() => {
  const $ = id => document.getElementById(id);
  let state=null, busy=false, available=false;
  const phases={idle:'Ready for rehearsal',discovering:'Discovering enemy cycle',returning:'Returning to intended target',waiting:'Holding selection',complete:'Pass complete',paused:'Rehearsal paused',blocked:'Stopped for a check'};
  function render() {
    const active=state?.active===true;
    $('miningmode').disabled=busy || active;
    $('miningmode').title=active ? 'Pause the NPC rehearsal before switching farming features' : '';
    for(const id of ['npcrun','npconce','npcscenario','npcmin','npcmax']) $(id).disabled=busy || !available || active;
    $('npcpause').disabled=busy || !active;
    $('npcreset').disabled=busy || !available;
    $('npcphase').textContent=available ? phases[state.phase] || state.phase : 'Rehearsal status unavailable';
    $('npcselected').textContent=state?.simulated_selected || '—';
    const target=state?.targets?.find(row=>row.identity===state.intended);
    $('npcintended').textContent=target?.name || '—';
    $('npccountdown').textContent=!available ? 'Status unavailable' : state.next_pass_seconds==null ? 'Not scheduled' : `${state.next_pass_seconds.toFixed(1)} s`;
    $('npcpass').textContent=state?.pass_number ? `Pass ${state.pass_number} · ${state.steps} / ${state.max_steps} simulated X presses` : 'No pass yet';
    document.querySelectorAll('[data-npc-phase]').forEach(el=>{
      el.classList.toggle('active',available && (el.dataset.npcPhase===state.phase || state.phase==='complete'));
      if(el.dataset.npcPhase==='waiting' && state?.interval) el.textContent=`04 · Wait ${state.interval[0]}–${state.interval[1]}s`;
    });
    const body=$('npctargets'); body.replaceChildren();
    for(const row of state?.targets || []) {
      const tr=document.createElement('tr');
      if(row.identity===state.intended) tr.className='intended';
      const decision=row.identity===state.intended ? 'Intended' : !row.npc ? 'Excluded · player' : !row.hostile || row.hp<=0 ? 'Excluded' : 'Candidate';
      for(const value of [row.name,row.distance==null?'Unknown':`${row.distance.toLocaleString()} m`,row.hp.toLocaleString(),`${row.identity===state.selected?'Selected · ':''}${decision}`]) {
        const td=document.createElement('td');td.textContent=value;tr.append(td);
      }
      if(row.identity===state.selected) tr.classList.add('selected');
      body.append(tr);
    }
    if(!body.children.length) {const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.textContent=active?'Waiting for target evidence.':'No targets recorded. Start a rehearsal to discover the cycle.';tr.append(td);body.append(tr);}
    const events=$('npcevents');events.replaceChildren();
    for(const event of state?.events || ['Ready for an offline rehearsal.']) {const li=document.createElement('li');li.textContent=event;events.append(li);}
  }
  async function command(action,repeat) {
    if(busy) return;
    const payload={action};
    if(action==='start') {
      const min=Number($('npcmin').value),max=Number($('npcmax').value);
      if(!Number.isFinite(min)||!Number.isFinite(max)||min<1||max>60||min>max) {$('npcmessage').textContent='Choose an ordered wait interval between 1 and 60 seconds.';return;}
      Object.assign(payload,{scenario:$('npcscenario').value,interval:[min,max],repeat});
    }
    busy=true;render();
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),3000);
    try {
      const response=await fetch('/api/npc-farm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:abort.signal});
      const result=await response.json();
      if(!response.ok || !result.accepted) throw new Error(result.reason || result.error || 'Request rejected');
      state=result.state;available=true;$('npcmessage').textContent=state.reason;
    } catch(error) {available=false;$('npcmessage').textContent=`Request unconfirmed: ${error.message}. Reconnecting to status.`;}
    finally {clearTimeout(timer);busy=false;render();}
  }
  async function refresh() {
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),3000);
    try {
      const response=await fetch('/api/npc-farm',{cache:'no-store',signal:abort.signal});
      if(!response.ok) throw new Error('Rehearsal service unavailable');
      const result=await response.json();
      if(result.environment!=='simulation' || result.input_authorized!==false) throw new Error('Unexpected rehearsal response');
      if(!busy) {state=result;available=true;$('npcmessage').textContent=state.reason;render();}
    } catch(error) {if(!busy) {available=false;$('npcmessage').textContent=error.message;render();}}
    finally {clearTimeout(timer);setTimeout(refresh,500);}
  }
  $('npcrun').addEventListener('click',()=>command('start',true));
  $('npconce').addEventListener('click',()=>command('start',false));
  $('npcpause').addEventListener('click',()=>command('pause'));
  $('npcreset').addEventListener('click',()=>command('reset'));
  refresh();
})();
