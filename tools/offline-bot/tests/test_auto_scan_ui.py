"""Exercise auto-scan controls against target-free, active and failed states."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from test_trace import scratch_directory


class AutoScanUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_visible_scene_assessment_survives_portrait_changes_and_stop_shows_results(self):
        html = Path(__file__).resolve().parents[1] / 'bsgo_offline' / 'observer.html'
        script = re.search(r'(?s)<script>(.*?)</script>', html.read_text(encoding='utf-8')).group(1)
        script = script.replace('  refreshLive();\n  refreshGameState();\n  setInterval(renderLive,250);\n})();',
            '  globalThis.show=(s,m,a)=>{startup=s;mode=m;actions=a;renderLive();};'
            '\n globalThis.assess=()=>captureAssessment("scan_nearby_unscanned");\n})();')
        harness = '''
const elements={};
globalThis.document={getElementById(id){return elements[id] ||= {
 textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
 addEventListener(){},setAttribute(){},removeAttribute(){},scrollIntoView(){}
};}};
eval(SCRIPT);
const field=value=>({value,valid:true,fresh:true,observed_at:100});
const current={healthy:true,age_seconds:.1,acquired_at:100,frame_size:[2560,1440],fields:{
 asteroid_selected:field(false),speed:field(0),hull_current:field(5210),hull_max:field(5210)}};
const startup={phase:'connected',instance_id:'fixture',game:{connection_id:'fixture'},action_control:{enabled:true}};
const state={actual_mode:'manual',paused:false,server_monotonic:100,current_observation:current,
 ownership:{owner:null,state:'idle'},assessment_context:{context_key:'portrait1'},
 visible_assessment_context:{token:'scene',context_key:'scene',expires_at:115},
 routines:{scan_nearby_unscanned:{available:true,ready:true,active:false}}};
function render(row){show(startup,{actual_mode:'manual'},row);return {
 start:!elements.startbutton.disabled,stop:!elements.cancelbutton.disabled,
 checked:elements.visiblesafe.checked,results:elements.visibleresults.textContent,
 phase:elements.phasetext.textContent};}
render(state);elements.visiblesafe.checked=true;assess();
const assessed=render({...state,assessment_context:{context_key:'portrait2'}});
const active=render({...state,ownership:{owner:'scan_nearby_unscanned',state:'active'},
 routines:{scan_nearby_unscanned:{active:true,ready:false,state:'active',phase:'selecting',
 visible_scan:{clicks_sent:2,scan_inputs_sent:1,rows:[{status:'scan_color_observed',resource:'Water',distance:800},{status:'selection_unverified'}]}}}});
const stopped=render({...state,routines:{scan_nearby_unscanned:{available:true,ready:true,state:'canceled',result:{reason:'explicit_stop'}}}});
console.log(JSON.stringify({assessed,active,stopped}));
'''.replace('SCRIPT', json.dumps(script))
        with scratch_directory() as directory:
            path = directory / 'visible-ui.js'
            path.write_text(harness, encoding='utf-8')
            result = subprocess.run(['node',str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=json.loads(result.stdout)
        self.assertTrue(rows['assessed']['checked'])
        self.assertTrue(rows['assessed']['start'])
        self.assertTrue(rows['active']['stop'])
        self.assertFalse(rows['active']['start'])
        self.assertIn('Water',rows['active']['results'])
        self.assertIn('Selection could not be verified',rows['active']['results'])
        self.assertEqual(rows['stopped']['phase'],'Pass stopped')

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_watch_can_be_armed_without_target_and_stop_survives_blocked_readings(self):
        html = Path(__file__).resolve().parents[1] / "bsgo_offline" / "observer.html"
        script = re.search(r"(?s)<script>(.*?)</script>", html.read_text(encoding="utf-8")).group(1)
        script = script.replace('  refreshLive();\n  refreshGameState();\n  setInterval(renderLive,250);\n})();',
            '  globalThis.show=(s,m,a)=>{selectedAction="auto_scan_selected";startup=s;mode=m;actions=a;renderLive();};'
            '\n  globalThis.assess=()=>captureAssessment("auto_scan_selected");\n})();')
        harness = '''
const elements={};
globalThis.document={getElementById(id){return elements[id] ||= {
 textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
 addEventListener(){},setAttribute(){},removeAttribute(){},scrollIntoView(){}
};}};
eval(SCRIPT);
const field=value=>({value,valid:true,fresh:true,observed_at:100});
const current={healthy:true,age_seconds:.1,acquired_at:100,frame_size:[2560,1440],fields:{
 asteroid_selected:field(false),speed:field(0),hull_current:field(5210),hull_max:field(5210)}};
const startup={phase:'connected',instance_id:'fixture',game:{connection_id:'fixture'},action_control:{enabled:true}};
const state={actual_mode:'manual',paused:false,server_monotonic:100,current_observation:current,
 ownership:{owner:null,state:'idle'},assessment_context:{token:'scene',context_key:'scene',scan_scope:'stopped_scene',expires_at:115},
 selected_scan_toggle:{attempts:0,delivered:0},routines:{auto_scan_selected:{available:true,ready:true,active:false}}};
function render(row,boot=startup){show(boot,{actual_mode:'manual'},row);return {
 phase:elements.phasetext.textContent,hint:elements.instruction.textContent,
 start:!elements.startbutton.disabled,stop:!elements.cancelbutton.disabled,
 progress:elements.autoscanprogress.textContent};}
const initial=render(state);
elements.autoscansafe.checked=true;assess();
const assessed=render(state);
function watching(reason,extra={}) {return {...state,selected_scan_toggle:{attempts:2,delivered:1,enabled:true},
 routines:{auto_scan_selected:{active:true,state:'waiting',waiting_for:reason}},...extra};}
const waiting=render(watching('observed_selected_target_loss'));
const scanning=render(watching(null,{ownership:{owner:'auto_scan_selected',state:'active'},
 routines:{auto_scan_selected:{active:true,state:'active',phase:'after'}}}));
const moved=render(watching('stationary_ship_required'));
const gap=render(watching('continuity_gap_restart_or_observed_target_change_required'));
const selected={...current,fields:{...current.fields,asteroid_selected:field(true),selected_marker_scan_state:field('scanned')}};
const scanned=render(watching('selected_marker_image_known_scanned',{current_observation:selected}));
const stopping=render(watching(null,{routines:{auto_scan_selected:{active:true,state:'stopping'}}}));
const unknown=render(null);
const paused=render(state,{...startup,action_control:{enabled:false}});
console.log(JSON.stringify({initial,assessed,waiting,scanning,moved,gap,scanned,stopping,unknown,paused}));
'''.replace("SCRIPT", json.dumps(script))
        with scratch_directory() as directory:
            path = directory / "auto-ui.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertFalse(rows["initial"]["start"])
        self.assertTrue(rows["assessed"]["start"])
        self.assertIn("Ready to watch", rows["initial"]["phase"])
        self.assertIn("Select an asteroid", rows["waiting"]["hint"])
        self.assertIn("Scanning", rows["scanning"]["phase"])
        self.assertIn("2 scan attempts", rows["scanning"]["progress"])
        self.assertIn("1 scan inputs sent", rows["scanning"]["progress"])
        self.assertIn("stop", rows["moved"]["phase"])
        self.assertIn("restart", rows["gap"]["phase"])
        self.assertIn("already scanned", rows["scanned"]["phase"])
        for name in ("waiting", "scanning", "moved", "gap", "scanned"):
            self.assertTrue(rows[name]["stop"], name)
            self.assertFalse(rows[name]["start"], name)
        for name in ("stopping", "unknown", "paused"):
            self.assertFalse(rows[name]["start"], name)
        self.assertFalse(rows["stopping"]["stop"])


if __name__ == "__main__":
    unittest.main()
