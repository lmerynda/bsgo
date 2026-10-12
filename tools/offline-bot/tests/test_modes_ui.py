"""Behavioral checks for workspace selection, acknowledgment, and urgent pause."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from test_trace import scratch_directory

HTML = Path(__file__).resolve().parents[1] / 'bsgo_offline' / 'observer.html'


@unittest.skipUnless(shutil.which('node'), 'Node unavailable')
class ModesUiTests(unittest.TestCase):
    def test_selection_never_activates_and_pause_remains_available_during_start(self):
        script = re.search(r'(?s)<script>(.*?)</script>', HTML.read_text(encoding='utf-8')).group(1)
        script = script.replace('  refreshLive();\n  refreshGameState();\n  setInterval(renderLive,250);\n})();',
            '  globalThis.ui={set:(s,m,a)=>{startup=s;mode=m;actions=a;renderLive();},selectMode,selectAction,requestMode,requestAction,requestStartup,captureAssessment,renderLive};\n})();')
        harness = r'''
const assert=require('node:assert/strict'),elements={},requests=[];
let resolveStart,clock=0;
globalThis.performance={now:()=>clock};
globalThis.document={getElementById(id){return elements[id] ||= {
 textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},attributes:{},
 addEventListener(){},setAttribute(k,v){this.attributes[k]=String(v)},removeAttribute(k){delete this.attributes[k]},scrollIntoView(){}
};}};
const field=value=>({value,valid:true,fresh:true,observed_at:1});
const startup={phase:'connected',instance_id:'launcher',game:{connection_id:'game',client_size:[2560,1440]},action_control:{enabled:true,acknowledgment_token:'ack'}};
const mode={actual_mode:'manual',requested_mode:'manual',pending:false,autopilot_available:false};
const actions={actual_mode:'manual',paused:false,server_monotonic:100,ownership:{owner:null,state:'idle'},
 current_observation:{healthy:true,age_seconds:.1,acquired_at:1,fields:{distance:field(800),speed:field(0),asteroid_selected:field(true),target_identity:field('asteroid'),hull_current:field(5005),hull_max:field(5005)}},
 assessment_context:{token:'token',context_key:'context',expires_at:115,wheel_point:[1280,400]},
 routines:{scan_selected:{available:true,ready:true},auto_scan_selected:{available:true,ready:true},approach_current_target:{available:true,ready:true}}};
globalThis.fetch=async(url,options)=>{
 const body=JSON.parse(options.body);requests.push({url,body});
 if(url==='/api/control-mode')return{json:async()=>({accepted:true,state:{...mode,requested_mode:'autopilot',pending:true}})};
 if(url==='/api/manual-actions'&&body.action==='start')return await new Promise(resolve=>{resolveStart=resolve});
 return{json:async()=>({accepted:true,state:{...startup,action_control:{enabled:false}}})};
};
eval(SCRIPT);
(async()=>{
 ui.set(startup,mode,actions);ui.selectMode('autopilot');
 assert.equal(requests.length,0);assert.equal(elements.actualmode.textContent,'Assist');
 assert.equal(elements.assistworkspace.hidden,true);assert.equal(elements.autopilotworkspace.hidden,false);
 assert.equal(elements.activateautopilot.disabled,true);assert.equal(elements.startbutton.disabled,true);
 await ui.requestMode('autopilot');assert.equal(requests.length,0);
 ui.set(startup,{...mode,autopilot_available:true},actions);
 assert.equal(elements.activateautopilot.disabled,false);
 await ui.requestMode('autopilot');assert.equal(requests.length,1);
 assert.equal(elements.actualmode.textContent,'Assist');assert.equal(elements.modepending.hidden,false);
 assert.equal(elements.activateautopilot.disabled,true);
 await ui.requestMode('autopilot');assert.equal(requests.length,1);
 ui.set(startup,mode,actions);ui.selectMode('assist');ui.selectAction('scan_selected');
 elements.scanselectedsafe.checked=true;ui.captureAssessment('scan_selected');
 assert.equal(elements.startbutton.disabled,false);
 const pending=ui.requestAction('start');
 assert.equal(elements.startbutton.disabled,true);assert.equal(elements.globalpause.disabled,false);
 await ui.requestStartup('pause_manual_actions');
 assert.equal(requests.at(-1).body.action,'pause_manual_actions');
 resolveStart({json:async()=>({accepted:false,reason:'session_paused',state:actions})});await pending;
 assert.equal(elements.scanselectedsafe.checked,false);assert.equal(elements.startbutton.disabled,true);
 ui.set(startup,mode,{...actions,ownership:{owner:'approach_current_target',state:'active'},routines:{approach_current_target:{active:true}}});
 ui.selectMode('autopilot');assert.equal(elements.globalcancel.hidden,false);assert.equal(elements.globalcancel.disabled,false);
 console.log('Modes checks passed: local selection, unavailable activation, acknowledged mode, pending handover, urgent pause, rejected assessment, cross-workspace cancellation.');
})().catch(error=>{console.error(error);process.exitCode=1});
'''.replace('SCRIPT', json.dumps(script))
        with scratch_directory() as directory:
            path = directory / 'modes-ui.js'
            path.write_text(harness, encoding='utf-8')
            result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Modes checks passed', result.stdout)

    def test_live_viewer_retains_only_fenced_pixels_and_tracks_source_frame(self):
        check = Path(__file__).with_name('cockpit_viewer_checks.js')
        result = subprocess.run(['node', str(check), str(HTML), '--retain'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Viewer checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
