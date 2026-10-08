"""Cockpit display consumes only fresh, matching ship observations."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from test_trace import scratch_directory


HTML = Path(__file__).resolve().parents[1] / 'bsgo_offline' / 'observer.html'


class ShipTelemetryUiTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable')
    def test_native_display_fallback_freshness_and_numeric_rejection(self):
        script = re.search(r'(?s)<script>(.*?)</script>',HTML.read_text(encoding='utf-8')).group(1)
        script = script.replace('  refreshLive();\n  setInterval(renderLive,250);\n})();',
            '  globalThis.show=(s,m,a)=>{startup=s;mode=m;actions=a;renderLive();};\n})();')
        harness = '''
const elements={};
globalThis.document={getElementById(id){return elements[id] ||= {
 textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
 addEventListener(){},setAttribute(){},removeAttribute(){},scrollIntoView(){}
};}};
eval(SCRIPT);
const telemetry={profile:'native-ship-telemetry',display_only:true,speed:25.7,
 hull_current:4684,hull_max:5005,energy_current:671,energy_max:800};
const field={value:telemetry,valid:true,fresh:true,observed_at:1};
const unreadable={valid:false,fresh:true,observed_at:1,reason:'calibrated_font_rejected'};
const absent={...unreadable,reason:'asteroid_title_not_verified'};
const current={healthy:true,age_seconds:.2,acquired_at:1,frame_size:[2560,1440],fields:{ship_telemetry:field,
 speed:unreadable,hull_current:unreadable,hull_max:unreadable,
 asteroid_selected:{value:false,valid:true,fresh:true,observed_at:1},
 target_identity:absent,target_portrait:absent,target_max_hp:absent,target_current_hp:absent}};
const startup={phase:'connected',game:{},action_control:{enabled:false}};
function render(row){show(startup,{actual_mode:'manual'},{current_observation:row,
 ownership:{owner:null,state:'idle'},routines:{}});return {
 speed:elements.speed.textContent,hull:elements.hullvalue.textContent,
 energy:elements.energyvalue.textContent,hp:elements.hpvalue.textContent,
 issues:elements.issuebutton.textContent,startDisabled:elements.startbutton.disabled,
 target:elements.targetname.textContent};}
const live=render(current);
const stale=render({...current,age_seconds:3});
const wrongFrame=render({...current,fields:{ship_telemetry:{...field,observed_at:2}}});
const invalid=render({...current,fields:{ship_telemetry:{...field,valid:false}}});
const malformed=render({...current,fields:{ship_telemetry:{...field,value:{...telemetry,speed:-1,energy_current:900}}}});
const targetEvidence={profile:'native-stationary-scanner-v1',input_scope:'scanner_only',
 frame_size:[2560,1440],target_ratio_verified:true,target_current_hp:3000,
 target_max_hp:3180,target_identity:'asteroid:hp:3180'};
const selected={...current,fields:{...current.fields,
 asteroid_selected:{value:true,valid:true,fresh:true,observed_at:1},
 stationary_scan_evidence:{...field,value:targetEvidence}}};
const targetLive=render(selected);
const targetStale=render({...selected,age_seconds:3});
const targetGeometry=render({...selected,frame_size:[3840,2160]});
const targetWrongFrame=render({...selected,fields:{...selected.fields,
 stationary_scan_evidence:{...field,observed_at:2,value:targetEvidence}}});
const targetConflict=render({...selected,fields:{...selected.fields,
 target_identity:{...field,value:'asteroid:hp:4000'}}});
console.log(JSON.stringify({live,stale,wrongFrame,invalid,malformed,
 targetLive,targetStale,targetGeometry,targetWrongFrame,targetConflict}));
'''.replace('SCRIPT',json.dumps(script))
        with scratch_directory() as directory:
            path=directory/'ship-ui.js'
            path.write_text(harness,encoding='utf-8')
            result=subprocess.run(['node',str(path)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        values=json.loads(result.stdout)
        self.assertEqual(values['live']['speed'],'25.7 m/s')
        self.assertEqual(values['live']['hull'],'4,684 / 5,005')
        self.assertEqual(values['live']['energy'],'671 / 800')
        self.assertEqual(values['live']['hp'],'UNKNOWN')
        self.assertTrue(values['live']['startDisabled'])
        self.assertEqual(values['live']['target'],'No asteroid selected')
        self.assertEqual(values['targetLive']['hp'],'3,000 / 3,180')
        self.assertTrue(values['targetLive']['startDisabled'])
        for name in ('targetStale','targetGeometry','targetWrongFrame','targetConflict'):
            self.assertEqual(values[name]['hp'],'UNKNOWN',name)
        # Display-only ship instruments do not hide rejected calibrated fields
        # or unavailable asteroid identity/health from the field-health count.
        self.assertIn(f'{getattr(self,"expected_issue_count",7)} field issues',values['live']['issues'])
        for name in ('stale','wrongFrame','invalid'):
            for field in ('speed','hull','energy'):
                self.assertEqual(values[name][field],'UNKNOWN')
        self.assertEqual(values['malformed']['speed'],'UNKNOWN')
        self.assertEqual(values['malformed']['energy'],'UNKNOWN')
