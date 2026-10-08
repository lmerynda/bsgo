"""Current selected scan evidence reaches HTTP and the paused cockpit."""
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

from bsgo_offline.state import FieldValue
from test_cockpit_contract import cockpit
from test_observation_api import providers, project
from test_trace import scratch_directory


class SelectedScanDisplayTests(unittest.TestCase):
    def test_paused_http_publishes_marker_and_local_track_without_inputs(self):
        with cockpit() as (fixture, _):
            at = fixture.row.acquired_at
            fixture.row = replace(fixture.row, frame_size=(2560, 1440), fields={**fixture.row.fields,
                "selected_marker_scan_state": FieldValue("scanned", at, True,
                    "measured_bright_scan_texture"),
                "selection_track_id": FieldValue(3, at)})
            raw = fixture.state()["current_observation"]["fields"]
            self.assertEqual(raw["selection_track_id"]["value"], 3)
            for path in ("/api/manual-actions", "/api/observation"):
                code, state = fixture.http(path)
                self.assertEqual(code, 200)
                fields = (state["current_observation"] if path.endswith("actions")
                          else state["observation"])["fields"]
                self.assertEqual(fields["selected_marker_scan_state"]["value"], "scanned")
            fixture.now += 3
            stale = fixture.http("/api/observation")[1]["observation"]["fields"]
            self.assertIsNone(stale["selected_marker_scan_state"]["value"])
            self.assertEqual(fixture.calls, [])
            self.assertTrue(fixture.controller.action_control_snapshot()["paused"])

    def test_projection_rejects_wrong_frame_target_and_unsupported_state(self):
        for change in ("wrong_frame", "non_asteroid", "invalid_target", "stale_target", "unsupported"):
            startup, mode, actions = providers()
            actions["current_observation"]["frame_size"] = [2560, 1440]
            fields = actions["current_observation"]["fields"]
            fields["selected_marker_scan_state"] = {**fields["speed"], "value": "scanned"}
            if change == "wrong_frame":
                fields["selected_marker_scan_state"]["observed_at"] = 99.7
            elif change == "unsupported":
                fields["selected_marker_scan_state"]["value"] = "unscanned"
            elif change == "non_asteroid":
                fields["asteroid_selected"]["value"] = False
            else:
                fields["asteroid_selected"]["valid" if change == "invalid_target" else "fresh"] = False
            row = project(startup, mode, actions)["observation"]["fields"]["selected_marker_scan_state"]
            self.assertFalse(row["usable"], change)
            self.assertIsNone(row["value"], change)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_cockpit_current_evidence_clears_on_target_loss_and_stale_images(self):
        html = Path(__file__).resolve().parents[1] / "bsgo_offline" / "observer.html"
        script = re.search(r"(?s)<script>(.*?)</script>", html.read_text(encoding="utf-8")).group(1)
        script = script.replace('  refreshLive();\n  setInterval(renderLive,250);\n})();',
            '  globalThis.show=(s,m,a)=>{startup=s;mode=m;actions=a;renderLive();};\n})();')
        harness = '''
const elements={};
globalThis.document={getElementById(id){return elements[id] ||= {
 textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
 addEventListener(){},setAttribute(){},removeAttribute(){},scrollIntoView(){}
};}};
eval(SCRIPT);
const field=value=>({value,valid:true,fresh:true,observed_at:1});
const current={healthy:true,age_seconds:.2,acquired_at:1,frame_size:[2560,1440],fields:{
 asteroid_selected:field(true),selected_marker_scan_state:field('scanned')}};
function render(row,extra={}){
 show({phase:'connected',action_control:{enabled:false}},{actual_mode:'manual'},
 {current_observation:row,ownership:{owner:null,state:'idle'},routines:{},...extra});
 return {finding:elements.scanfindings.textContent,disabled:elements.startbutton.disabled};
}
const live=render(current);
const stale=render({...current,age_seconds:3});
const unhealthy=render({...current,healthy:false});
const lost=render({...current,fields:{...current.fields,asteroid_selected:field(false)}});
const wrong=render({...current,fields:{...current.fields,
 selected_marker_scan_state:{...field('scanned'),observed_at:2}}});
const unknown=render({...current,fields:{asteroid_selected:field(true)}});
const historical=render({...current,age_seconds:3},{selected_scan_toggle:{last_result:{state:'scanned'}}});
const withResource={...current,fields:{...current.fields,selected_scan_resource:field('Water')}};
const water=render(withResource);
const empty=render({...withResource,fields:{...withResource.fields,selected_scan_resource:field('empty')}});
const resourceStale=render({...withResource,age_seconds:3});
const geometry=render({...withResource,frame_size:[1920,1080]});
const resourceWrong=render({...withResource,fields:{...withResource.fields,
 selected_scan_resource:{...field('Water'),observed_at:2}}});
const resourceLost=render({...withResource,fields:{...withResource.fields,asteroid_selected:field(false)}});
const unsupported=render({...withResource,fields:{...withResource.fields,selected_scan_resource:field('Copper')}});
console.log(JSON.stringify({live,stale,unhealthy,lost,wrong,unknown,historical,
 water,empty,resourceStale,resourceWrong,resourceLost,unsupported,geometry}));
'''.replace("SCRIPT", json.dumps(script))
        with scratch_directory() as directory:
            path = directory / "scan-ui.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertIn("Scanned appearance observed", values["live"]["finding"])
        self.assertIn("resource unknown", values["live"]["finding"])
        self.assertIn("No asteroid selected", values["lost"]["finding"])
        for name in ("stale", "unhealthy", "lost", "wrong", "unknown", "historical"):
            self.assertNotIn("Scanned appearance observed", values[name]["finding"], name)
        self.assertIn("(recorded)", values["historical"]["finding"])
        self.assertIn("Water color observed", values["water"]["finding"])
        self.assertIn("Empty rock color observed", values["empty"]["finding"])
        for name in ("resourceStale", "resourceWrong", "resourceLost", "unsupported", "geometry"):
            self.assertNotIn("Water color observed", values[name]["finding"])
            self.assertNotIn("Copper", values[name]["finding"])
        self.assertTrue(all(row["disabled"] for row in values.values()))
