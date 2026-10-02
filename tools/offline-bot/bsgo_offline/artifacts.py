"""Inspectable request/effect traces, with a local HTML view of click coordinates."""
from dataclasses import fields, is_dataclass
from collections.abc import Mapping
import html, json
from pathlib import Path

def plain(value):
    if is_dataclass(value): return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping): return {str(k):plain(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)): return [plain(v) for v in value]
    return value

def save_report(directory, report):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = plain(report)
    (directory/'report.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
    actions = payload.get('input_requests', [])
    effects = payload.get('receiver_effects', [])
    discarded=set(payload.get('discarded_sequences',[]))
    received={event['action_sequence']:event['effect'] for event in effects}
    circles = ''.join(f'<circle cx="{a["x"]}" cy="{a["y"]}" r="18" fill="#ef5d73"/><text x="{a["x"]+24}" y="{a["y"]}">{a["sequence"]}</text>'
                      for a in actions if a['kind']=='mouse_click')
    rows = ''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [a['sequence'],round(a['at'],3),a['kind'],
        a.get('key') or a.get('button') or '',f'{a.get("x", "")}, {a.get("y", "")}' if a.get('x') is not None else '',
        'Discarded before receipt' if a['sequence'] in discarded else received.get(a['sequence'],'Pending receipt'),a.get('reason','')])+'</tr>' for a in actions)
    effect_text = html.escape(json.dumps(effects,indent=2))
    result=payload.get('result',{})
    status=('Synthetic approach completed with a fresh observed stop.' if result.get('completed') else
            'Synthetic run ended: '+str(result.get('reason','recognition-only replay'))+'. Stop observed: '+str(result.get('stop_observed',False)))
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Offline bot input receiver</title><style>body{{font:16px system-ui;background:#111824;color:#e7edf7;max-width:1100px;margin:32px auto;padding:0 24px}}svg{{width:100%;background:#172334;border:1px solid #41526b}}svg text{{fill:#e7edf7;font-size:34px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #41526b}}pre{{white-space:pre-wrap}}.status{{color:#96e6b2}}</style>
<h1>Offline input receiver</h1><p>Recorded requests and synthetic receiver effects from the offline harness.</p>
<p class="status">{html.escape(status)}</p>
<svg id="surface" tabindex="0" viewBox="0 0 2560 1440" role="img" aria-label="Recorded clicks in a simulated 2560 by 1440 frame"><rect x="1160" y="560" width="80" height="80" fill="#728db8"/><text x="1260" y="620">Synthetic target</text>{circles}</svg>
<h2>Recorded requests</h2><table><thead><tr><th>Sequence</th><th>Time</th><th>Action</th><th>Key or button</th><th>Frame coordinates</th><th>Receiver result</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Receiver effects</h2><pre>{effect_text}</pre></html>'''
    local_receiver = r'''
<h2>Browser event receiver</h2>
<p>Click the frame above to focus it, then press keys or click. This records events delivered to this tab only, separately from the saved Python run. The frame coordinate space is 2560 by 1440.</p>
<button id="export">Download received events</button><button id="clear">Clear received events</button>
<p id="live-status">No new events received.</p><pre id="live-events"></pre>
<script>
(() => {
 const surface=document.getElementById('surface'), out=document.getElementById('live-events');
 const status=document.getElementById('live-status'); let events=[], held=new Set();
 const append=(kind,data={})=>{
  if(events.length>=256){status.textContent='Event limit reached. Export or clear the trace.';return false;}
  events.push({sequence:events.length+1,at_seconds:performance.now()/1000,source:'browser_tab_event',kind,...data});
  out.textContent=JSON.stringify(events,null,2);status.textContent=events.length+' received events; '+held.size+' keys held.';return true;
 };
 const point=e=>{const r=surface.getBoundingClientRect();return {x:Math.min(2559,Math.max(0,Math.round((e.clientX-r.left)*2560/r.width))),y:Math.min(1439,Math.max(0,Math.round((e.clientY-r.top)*1440/r.height)))};};
 surface.addEventListener('click',e=>{surface.focus();const p=point(e);if(!append('mouse_click',{...p,button:e.button,trusted:e.isTrusted}))return;
  const mark=document.createElementNS('http://www.w3.org/2000/svg','circle');mark.setAttribute('class','live-click');mark.setAttribute('cx',p.x);mark.setAttribute('cy',p.y);mark.setAttribute('r',14);mark.setAttribute('fill','#96e6b2');surface.appendChild(mark);
 });
 surface.addEventListener('contextmenu',e=>{e.preventDefault();surface.focus();append('mouse_click',{...point(e),button:2,trusted:e.isTrusted});});
 surface.addEventListener('keydown',e=>{e.preventDefault();held.add(e.code);append('key_down',{key:e.key,code:e.code,repeat:e.repeat,trusted:e.isTrusted});});
 surface.addEventListener('keyup',e=>{e.preventDefault();held.delete(e.code);append('key_up',{key:e.key,code:e.code,trusted:e.isTrusted});});
 surface.addEventListener('blur',()=>{const lost=[...held];held.clear();append('focus_lost',{previously_held:lost,release_observed:false});});
 document.getElementById('clear').onclick=()=>{events=[];held.clear();surface.querySelectorAll('.live-click').forEach(mark=>mark.remove());out.textContent='';status.textContent='Trace cleared.';};
 document.getElementById('export').onclick=()=>{const blob=new Blob([JSON.stringify({coordinate_space:'frame',frame_size:[2560,1440],time_domain:'browser_performance_seconds',events},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='receiver-events.json';a.click();URL.revokeObjectURL(url);};
})();
</script>
'''
    document=document.replace('</html>',local_receiver+'</html>')
    (directory/'receiver.html').write_text(document,encoding='utf-8')
