"""Read-only observer HTTP tests with local fixture packages only."""
import json
from pathlib import Path
import re
import shutil
import subprocess
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bsgo_offline.observer_ui import HTML, ObserverIndex, make_server
from test_trace import scratch_directory


def package(root, name, *, finished=False, worker=True, phase="capturing"):
    run = root / name
    run.mkdir()
    (run / "frames").mkdir()
    (run / "failed-recognition").mkdir()
    (run / "manifest.json").write_text(json.dumps({"mode": "fixture_probe",
        "code_revision": "abc123", "run_id": name}), encoding="utf-8")
    status = {"run_id": name, "mode": "fixture_probe", "worker": {"running": worker},
        "execution": {"phase": phase, "processing": "fresh image",
            "waiting_for": "range 800", "expected_next_step": "PageDown",
            "first_blocker": None, "last_input_intent": {"kind": "wheel"},
            "last_input_delivery": {"outcome": "injected"},
            "last_confirmed_effect": {"speed": 5.0}},
        "observation": {"sequence": 2, "fields": {"speed": {"value": 5.0,
            "valid": True, "fresh": True, "observed_in_current_image": True}}},
        "last_retained_frame": {"path": "frames/observation-00000002.png",
                                "state": "written"}}
    (run / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (run / "frames" / "observation-00000001.png").write_bytes(b"\x89PNG\r\n\x1a\nolder")
    (run / "frames" / "observation-00000002.png").write_bytes(b"\x89PNG\r\n\x1a\nlatest")
    (run / "failed-recognition" / "observation-00000001.png").write_bytes(b"\x89PNG\r\n\x1a\nfailed")
    (run / "failed-recognition" / "observation-00000001.json").write_text(
        json.dumps({"recognition_failures": {"speed": "bad_glyph"},
                    "snapshot": {"fields": {"speed": {"valid": False,
                                                       "reason": "bad_glyph"}}}}), encoding="utf-8")
    if finished:
        (run / "report.json").write_text(json.dumps({"lifecycle": "finalized",
            "complete_diagnostics": True, "result": {"state": "proved",
                "reason": "done", "motion": [{"portrait": list(range(144))}]},
            "shutdown": {"worker": True}}), encoding="utf-8")
    return run


class ObserverUiTests(unittest.TestCase):
    def test_missing_runs_root_is_not_created_by_read_only_observer(self):
        with scratch_directory() as directory:
            missing = directory / "not-created"
            index = ObserverIndex(missing)
            self.assertEqual(index.runs(), [])
            self.assertIsNone(index.detail(None))
            self.assertFalse(missing.exists())

    def test_index_uses_status_mtime_and_never_labels_completed_run_live(self):
        with scratch_directory() as directory:
            older = package(directory, "older", finished=True, worker=True)
            time.sleep(.02)
            newer = package(directory, "newer", phase="braking")
            index = ObserverIndex(directory)
            self.assertEqual([row["name"] for row in index.runs()[:2]], ["newer", "older"])
            live = index.detail(None)
            self.assertEqual(live["run"], "newer")
            self.assertEqual(live["lifecycle"], "live")
            self.assertEqual(live["status"]["execution"]["phase"], "braking")
            self.assertEqual(live["latest_image"], "frames/observation-00000002.png")
            self.assertEqual(live["first_failure"]["recognition_failures"],
                             {"speed": "bad_glyph"})
            final = index.detail("older")
            self.assertEqual(final["lifecycle"], "finalized")
            self.assertTrue(final["report"]["present"])
            self.assertEqual(final["report"]["result"]["state"], "proved")
            self.assertNotIn("motion", final["report"]["result"])

    def test_monotonic_age_and_stale_unfinalized_state(self):
        with scratch_directory() as directory:
            run = package(directory, "stale", worker=True)
            status = run / "status.json"
            old = time.time() - 20
            import os
            os.utime(status, (old, old))
            index = ObserverIndex(directory)
            first = index.detail("stale")
            second = index.detail("stale")
            self.assertEqual(first["lifecycle"], "stale")
            self.assertGreaterEqual(second["status_age_seconds"], first["status_age_seconds"])

    def test_server_exposes_page_status_and_only_run_local_images(self):
        with scratch_directory() as directory:
            package(directory, "alpha")
            (directory / "secret.png").write_bytes(b"secret")
            server = make_server(directory, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def get(path):
                with urlopen(base + path, timeout=3) as response:
                    return response.status, response.headers, response.read()
            try:
                code, headers, html = get("/")
                self.assertEqual(code, 200)
                self.assertIn(b"Show images", html)
                self.assertIn(b"Latest automatically", html)
                self.assertIn("no-store", headers["Cache-Control"])
                _, _, runs = get("/api/runs")
                self.assertEqual(json.loads(runs)["runs"][0]["name"], "alpha")
                _, _, detail = get("/api/status")
                payload = json.loads(detail)
                self.assertEqual(payload["status"]["execution"]["processing"], "fresh image")
                self.assertEqual(payload["run"], "alpha")
                path = "/api/image?run=alpha&path=frames%2Fobservation-00000002.png"
                _, headers, image = get(path)
                self.assertTrue(image.endswith(b"latest"))
                self.assertEqual(headers["Content-Type"], "image/png")
                _, _, sidecar = get("/api/sidecar?run=alpha&path=failed-recognition%2Fobservation-00000001.json")
                self.assertEqual(json.loads(sidecar)["recognition_failures"]["speed"], "bad_glyph")
                for forbidden in ("/api/image?run=alpha&path=..%2Fsecret.png",
                    "/api/image?run=alpha&path=frames%2F..%2F..%2Fsecret.png",
                    "/api/image?run=alpha&path=report.json",
                    "/api/status?run=..%2Fsecret"):
                    with self.subTest(forbidden=forbidden), self.assertRaises(HTTPError) as raised:
                        get(forbidden)
                    self.assertEqual(raised.exception.code, 404)
                with self.assertRaises(HTTPError) as cross_site:
                    urlopen(Request(base + path, headers={"Sec-Fetch-Site": "cross-site"}), timeout=3)
                self.assertEqual(cross_site.exception.code, 403)
                with self.assertRaises(HTTPError) as rebound_host:
                    urlopen(Request(base + path, headers={"Host": "untrusted.example"}), timeout=3)
                self.assertEqual(rebound_host.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_symlink_to_outside_run_is_not_served(self):
        with scratch_directory() as directory:
            run = package(directory, "alpha")
            outside = directory / "secret.png"
            outside.write_bytes(b"secret")
            link = run / "frames" / "observation-00000003.png"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            index = ObserverIndex(directory)
            self.assertIsNone(index.image_path(run, "frames/observation-00000003.png"))
            self.assertEqual(index.detail("alpha")["latest_image"],
                             "frames/observation-00000002.png")

    def test_symlink_directory_escape_rejects_image_sidecar_and_detail(self):
        with scratch_directory() as directory:
            run = package(directory, "alpha")
            outside = directory / "outside"
            outside.mkdir()
            (outside / "observation-00000001.png").write_bytes(b"private")
            (outside / "observation-00000001.json").write_text('{"private":true}')
            for folder in ("frames", "failed-recognition"):
                (run / folder).rename(run / (folder + "-old"))
                try:
                    (run / folder).symlink_to(outside, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self.skipTest("directory symlink creation unavailable")
            index = ObserverIndex(directory)
            self.assertIsNone(index.image_path(run, "frames/observation-00000001.png"))
            self.assertIsNone(index.sidecar_path(run, "failed-recognition/observation-00000001.json"))
            self.assertIsNone(index.detail("alpha")["first_failure"])

    def test_synthetic_never_becomes_automatic_latest(self):
        with scratch_directory() as directory:
            package(directory, "real")
            time.sleep(.02)
            demo = package(directory, "synthetic-demo")
            manifest = demo / "manifest.json"
            data = json.loads(manifest.read_text())
            data["synthetic"] = True
            manifest.write_text(json.dumps(data), encoding="utf-8")
            index = ObserverIndex(directory)
            self.assertEqual(index.runs()[0]["name"], "synthetic-demo")
            self.assertTrue(index.runs()[0]["synthetic"])
            self.assertEqual(index.detail(None)["run"], "real")
            self.assertEqual(index.detail("synthetic-demo")["run"], "synthetic-demo")

    def test_first_rejection_reference_precedes_earlier_benign_failure(self):
        with scratch_directory() as directory:
            run = package(directory, "alpha", finished=True)
            later = run / "failed-recognition" / "observation-00000002.png"
            later.write_bytes(b"rejection")
            (run / "failed-recognition" / "observation-00000002.json").write_text('{}')
            report = json.loads((run / "report.json").read_text())
            report["result"].update(state="blocked", first_blocker="invalid_target_identity",
                first_rejection={"read_error":"RuntimeError: invalid_target_identity",
                    "image_reference":"failed-recognition/observation-00000002.png",
                    "sidecar_reference":"failed-recognition/observation-00000002.json",
                    "fields":{"target_current_hp":{"reason":"invalid_target_hp_separator"}}})
            (run / "report.json").write_text(json.dumps(report))
            detail = ObserverIndex(directory).detail("alpha")
            self.assertEqual(detail["first_failure"]["image"],
                             "failed-recognition/observation-00000002.png")
            self.assertEqual(detail["first_failure"]["field_reasons"],
                             {"target_current_hp":"invalid_target_hp_separator"})
            self.assertEqual(detail["report"]["result"]["first_blocker"],
                             "invalid_target_identity")

    def test_mode_request_without_controller_never_claims_autopilot(self):
        with scratch_directory() as directory:
            server = make_server(directory, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(mode, **headers):
                request = Request(base + "/api/control-mode", method="POST",
                    data=json.dumps({"mode": mode}).encode(), headers={
                        "Content-Type": "application/json", "Origin": base, **headers})
                try:
                    with urlopen(request, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base + "/api/control-mode") as response:
                    initial = json.load(response)
                self.assertEqual(initial["actual_mode"], "manual")
                self.assertFalse(initial["app_control_enabled"])
                code, rejected = post("autopilot")
                self.assertEqual(code, 409)
                self.assertFalse(rejected["accepted"])
                self.assertEqual(rejected["state"]["actual_mode"], "manual")
                self.assertEqual(rejected["state"]["requested_mode"], "autopilot")
                self.assertFalse(rejected["state"]["pending"])
                self.assertEqual(rejected["state"]["last_ack"]["reason"],
                                 "autopilot_controller_not_attached")
                code, cross = post("autopilot", **{"Sec-Fetch-Site":"cross-site"})
                self.assertEqual(code, 403)
                code, invalid_origin = post("autopilot", **{"Origin":"http://bad.example"})
                self.assertEqual(code, 403)
                code, manual = post("manual")
                self.assertEqual(code, 200)
                self.assertEqual(manual["state"]["actual_mode"], "manual")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_attached_controller_ack_is_sole_source_of_actual_mode(self):
        class FakeController:
            actual = "manual"
            pending = False
            ack = None

            def snapshot(self):
                return {"actual_mode": self.actual, "requested_mode": "autopilot",
                    "pending": self.pending, "app_control_enabled": self.actual == "autopilot",
                    "decisions_enabled": self.actual == "autopilot",
                    "attached_controller": "fake", "autopilot_available": True,
                    "reason": self.ack["reason"] if self.ack else None,
                    "handover_blocker": self.ack["reason"] if self.ack else None,
                    "last_ack": self.ack}

            def request(self, mode):
                self.pending = True
                return {"accepted": False, "state": self.snapshot(), "reason": None}

        with scratch_directory() as directory:
            controller = FakeController()
            server = make_server(directory, 0, mode_controller=controller)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                request = Request(base + "/api/control-mode", method="POST",
                    data=b'{"mode":"autopilot"}', headers={"Content-Type":"application/json",
                                                        "Origin":base})
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 202)
                    pending = json.load(response)
                self.assertEqual(pending["state"]["actual_mode"], "manual")
                self.assertTrue(pending["state"]["pending"])
                controller.pending = False
                controller.ack = {"accepted": False, "reason": "readiness_rejected"}
                with urlopen(base + "/api/control-mode", timeout=3) as response:
                    rejected_ack = json.load(response)
                self.assertEqual(rejected_ack["actual_mode"], "manual")
                self.assertEqual(rejected_ack["last_ack"]["reason"], "readiness_rejected")
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 202)
                controller.pending = False
                controller.actual = "autopilot"  # explicit fake controller acknowledgment
                controller.ack = {"accepted": True, "reason": None}
                with urlopen(base + "/api/control-mode", timeout=3) as response:
                    acknowledged = json.load(response)
                self.assertEqual(acknowledged["actual_mode"], "autopilot")
                self.assertTrue(acknowledged["app_control_enabled"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_canceled_passive_package_keeps_partial_coverage_separate(self):
        with scratch_directory() as directory:
            run = package(directory, "passive", finished=True)
            manifest = json.loads((run / "manifest.json").read_text())
            manifest["mode"] = "synthetic_passive_observer"
            manifest["synthetic"] = True
            (run / "manifest.json").write_text(json.dumps(manifest))
            status = json.loads((run / "status.json").read_text())
            status["execution"].update(phase="canceled", reason="cancel_requested",
                                        first_blocker="cancel_requested")
            status["frames"] = {"accepted":64, "written":64, "budget_skipped":7,
                "dropped":0, "unwritten":0, "error":None}
            status["trace"] = {"dropped":0, "unwritten":0, "error":None}
            status["status_write_errors"] = 0
            status["result"] = {"state":"canceled", "reason":"cancel_requested"}
            (run / "status.json").write_text(json.dumps(status))
            report = json.loads((run / "report.json").read_text())
            report["result"] = {"state":"canceled", "reason":"cancel_requested"}
            report["complete_diagnostics"] = False
            (run / "report.json").write_text(json.dumps(report))
            detail = ObserverIndex(directory).detail("passive")
            self.assertEqual(detail["lifecycle"], "finalized")
            self.assertEqual(detail["status"]["execution"]["phase"], "canceled")
            self.assertEqual(detail["report"]["result"]["state"], "canceled")
            self.assertFalse(detail["report"]["complete_diagnostics"])
            self.assertEqual(detail["status"]["frames"]["budget_skipped"], 7)
            self.assertEqual(detail["status"]["frames"]["dropped"], 0)

    def test_manual_actions_reject_invalid_unattached_and_cross_origin_posts(self):
        with scratch_directory() as directory:
            server = make_server(directory, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(payload, origin=None):
                request = Request(base + "/api/manual-actions", method="POST",
                    data=json.dumps(payload).encode(), headers={"Content-Type":"application/json",
                        "Origin":origin or base})
                try:
                    with urlopen(request, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base + "/api/manual-actions") as response:
                    state = json.load(response)
                self.assertEqual(state["actual_mode"], "manual")
                self.assertFalse(state["routines"]["scan_nearby_unscanned"]["ready"])
                self.assertFalse(state["routines"]["scan_selected"]["ready"])
                self.assertFalse(state["routines"]["approach_current_target"]["ready"])
                self.assertEqual(post({"action":"mine","routine":"scan_nearby_unscanned"})[0], 400)
                self.assertEqual(post({"action":"start","routine":"unlisted"})[0], 400)
                self.assertEqual(post({"action":"start","routine":"scan_nearby_unscanned",
                                       "request":[]})[0], 400)
                self.assertEqual(post({"action":"start","routine":"scan_nearby_unscanned"},
                                      origin="http://bad.example")[0], 403)
                self.assertEqual(post({"action":"start","routine":"scan_selected"})[0], 409)
                code, blocked = post({"action":"start","routine":"scan_nearby_unscanned"})
                self.assertEqual(code, 409)
                self.assertEqual(blocked["reason"], "controller_not_attached")
                self.assertIsNone(blocked["state"]["ownership"]["owner"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_manual_action_ownership_serializes_conflicts_and_stop(self):
        class FakeActions:
            owner = None
            state = "idle"
            starts = 0

            def actions_snapshot(self):
                return {"actual_mode":"manual", "paused":False,
                    "app_control_enabled":self.owner is not None,
                    "ownership":{"owner":self.owner,"state":self.state,
                                 "input_scope":"navigation" if self.owner else None,"run_id":"fake"},
                    "routines":{name:{"available":True,"ready":True,"reason":None}
                        for name in ("scan_nearby_unscanned","scan_selected","approach_current_target")}}

            def start_action(self, name, request=None):
                self.starts += 1
                time.sleep(.04)
                self.owner = name
                self.state = "active"
                return {"accepted":True,"reason":None,"state":self.actions_snapshot()}

            def stop_action(self, name):
                self.state = "stopping"
                return {"accepted":True,"reason":None,"state":self.actions_snapshot()}

        with scratch_directory() as directory:
            actions = FakeActions()
            server = make_server(directory, 0, action_adapter=actions)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(action, routine, request=None):
                payload = {"action":action,"routine":routine,"request":request or {}}
                req = Request(base + "/api/manual-actions", method="POST",
                    data=json.dumps(payload).encode(), headers={"Content-Type":"application/json",
                        "Origin":base})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                requests = []
                from threading import Barrier
                barrier = Barrier(3)
                def simultaneous(name):
                    barrier.wait()
                    requests.append(post("start", name))
                first = Thread(target=simultaneous, args=("scan_nearby_unscanned",))
                second = Thread(target=simultaneous, args=("approach_current_target",))
                first.start(); second.start(); barrier.wait()
                first.join(3); second.join(3)
                self.assertEqual(sorted(code for code, _ in requests), [200, 409])
                self.assertEqual(actions.starts, 1)
                owner = actions.owner
                other = "approach_current_target" if owner == "scan_nearby_unscanned" else "scan_nearby_unscanned"
                self.assertEqual(post("stop", other)[0], 409)
                self.assertEqual(post("stop", owner)[0], 200)
                self.assertEqual(actions.state, "stopping")
                self.assertEqual(post("start", other)[0], 409)
                with urlopen(base + "/api/control-mode") as response:
                    mode = json.load(response)
                self.assertEqual(mode["actual_mode"], "manual")
                self.assertTrue(mode["app_control_enabled"])
                self.assertEqual(mode["mission_decisions_owner"], "human")
                self.assertEqual(mode["routine_ownership"]["owner"], owner)
                manual_request = Request(base + "/api/control-mode", method="POST",
                    data=b'{"mode":"manual"}', headers={"Content-Type":"application/json",
                                                    "Origin":base})
                with urlopen(manual_request, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response)["state"]["actual_mode"], "manual")
                self.assertEqual(actions.owner, owner)
                self.assertEqual(actions.state, "stopping")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_waiting_scan_without_input_lease_can_stop_but_blocks_new_start(self):
        class WaitingScan:
            stopped = False

            def actions_snapshot(self):
                return {"actual_mode":"manual","paused":False,
                    "ownership":{"owner":None,"state":"idle","input_scope":None},
                    "routines":{"scan_nearby_unscanned":{"active":not self.stopped,
                        "state":"waiting_for_ship_stop"},
                        "approach_current_target":{"active":False,"ready":True}}}

            def start_action(self, name, request=None):
                raise AssertionError("competing start reached native adapter")

            def stop_action(self, name):
                self.stopped = True
                return {"accepted":True,"reason":None,"state":self.actions_snapshot()}

        with scratch_directory() as directory:
            adapter = WaitingScan()
            server = make_server(directory, 0, action_adapter=adapter)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(action, routine):
                req = Request(base + "/api/manual-actions", method="POST",
                    data=json.dumps({"action":action,"routine":routine}).encode(),
                    headers={"Content-Type":"application/json","Origin":base})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                self.assertEqual(post("start", "approach_current_target")[0], 409)
                code, stopped = post("stop", "scan_nearby_unscanned")
                self.assertEqual(code, 200)
                self.assertTrue(adapter.stopped)
                self.assertFalse(stopped["state"]["routines"]["scan_nearby_unscanned"]["active"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_manual_start_rejects_autopilot_or_pending_mode_but_stop_still_works(self):
        class Mode:
            actual = "autopilot"
            pending = False

            def snapshot(self):
                return {"actual_mode": self.actual, "pending": self.pending,
                        "reason": None}

            def request(self, mode):
                raise AssertionError("mode request not part of this test")

        class Actions:
            owner = None
            starts = 0
            stopped = False

            def actions_snapshot(self):
                return {"actual_mode":"manual","paused":False,
                    "ownership":{"owner":self.owner,"state":"active" if self.owner else "idle"},
                    "routines":{"scan_selected":{"active":self.owner == "scan_selected",
                        "ready":True,"available":True}}}

            def start_action(self, name, request=None):
                self.starts += 1
                raise AssertionError("start must not reach adapter")

            def stop_action(self, name):
                self.stopped = True
                self.owner = None
                return {"accepted":True,"reason":"stopping","state":self.actions_snapshot()}

        with scratch_directory() as directory:
            mode, actions = Mode(), Actions()
            server = make_server(directory, 0, mode_controller=mode, action_adapter=actions)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(action):
                req = Request(base + "/api/manual-actions", method="POST",
                    data=json.dumps({"action":action,"routine":"scan_selected"}).encode(),
                    headers={"Content-Type":"application/json","Origin":base})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base + "/api/manual-actions") as response:
                    self.assertEqual(json.load(response)["actual_mode"], "autopilot")
                self.assertEqual(post("start")[1]["reason"], "manual_mode_required")
                mode.actual, mode.pending = "manual", True
                self.assertEqual(post("start")[1]["reason"], "manual_mode_required")
                self.assertEqual(actions.starts, 0)
                actions.owner = "scan_selected"
                code, stopped = post("stop")
                self.assertEqual(code, 200)
                self.assertTrue(actions.stopped)
                self.assertEqual(stopped["state"]["actual_mode"], "manual")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for inline UI render smoke")
    def test_canceled_budget_skip_renders_canceled_but_true_blocker_renders_blocked(self):
        script = re.search(r"(?s)<script>(.*?)</script>", HTML.read_text()).group(1)
        script = script.replace("  refresh();\n  refreshMode();\n  refreshActions();",
                                "  globalThis.testRender = render; globalThis.testActions = renderActions;")
        harness = f"""
const elements = {{}};
globalThis.document = {{getElementById(id) {{ return elements[id] ||= {{
  textContent:'', hidden:false, className:'', setAttribute(){{}}, removeAttribute(){{}},
  addEventListener(){{}}
}}; }} }};
eval({json.dumps(script)});
const common = {{run:'passive',run_path:'fixture',lifecycle:'finalized',
  status_age_seconds:1,manifest:{{mode:'synthetic_passive_observer',synthetic:true}},
  status:{{execution:{{phase:'canceled',first_blocker:'cancel_requested'}},
    result:{{state:'canceled'}},frames:{{accepted:64,written:64,budget_skipped:7,
    dropped:0,unwritten:0}},trace:{{dropped:0,unwritten:0}},status_write_errors:0}},
  report:{{result:{{state:'canceled'}},complete_diagnostics:false}}}};
testRender(common);
const canceled = {{badge:elements.lifecycle.textContent, outcome:elements.outcome.textContent,
  coverage:elements.coverage.textContent, writer:elements.writerhealth.textContent}};
common.status.execution.first_blocker='invalid_speed_glyph';
testRender(common);
const recovered = {{badge:elements.lifecycle.textContent,
  firstLabel:elements.firstcauselabel.textContent,
  firstCause:elements.firstblocker.textContent}};
common.status.execution.phase='blocked'; common.status.execution.first_blocker='failed_capture';
common.status.result.state='blocked'; common.report.result.state='blocked';
testRender(common);
const blocked = {{badge:elements.lifecycle.textContent, outcome:elements.outcome.textContent}};
const actions = {{actual_mode:'manual',paused:false,ownership:{{owner:null,state:'idle'}},
  current_observation:{{target_identity:'asteroid:4500',distance:700,speed:0,
    fields:{{distance:{{valid:true,fresh:true}},speed:{{valid:true,fresh:true}}}}}},
  assessment_context:{{token:'t1',context_key:'target-A',target_identity:'asteroid:4500',wheel_point:{{x:1280,y:400}}}},
  routines:{{scan_nearby_unscanned:{{available:false,ready:false,reason:'discovery_provider_missing'}},
    scan_selected:{{available:true,ready:true}},
    approach_current_target:{{available:true,ready:true}}}}}};
testActions(actions);
const independent = {{nearDisabled:elements.scanbutton.disabled,
  selectedDisabled:elements.selectedscanbutton.disabled,
  approachDisabled:elements.approachbutton.disabled,
  target:elements.currenttarget.textContent}};
elements.scanselectedsafe.checked=true;
testActions(actions);
const selectedAssessedEnabled = !elements.selectedscanbutton.disabled;
for (const id of ['clearcorridor','alignedheading','safestopped','flightpoint']) elements[id].checked=true;
testActions(actions);
const assessedEnabled = !elements.approachbutton.disabled;
actions.assessment_context={{token:'t2',context_key:'target-B',target_identity:'asteroid:5000',wheel_point:{{x:1280,y:400}}}};
testActions(actions);
const changedDisabled = elements.approachbutton.disabled && !elements.clearcorridor.checked;
actions.routines.scan_nearby_unscanned.active=true;
testActions(actions);
const waitingScan = {{button:elements.scanbutton.textContent,
  canStop:!elements.scanbutton.disabled, approachDisabled:elements.approachbutton.disabled}};
console.log(JSON.stringify({{canceled,recovered,blocked,independent,selectedAssessedEnabled,assessedEnabled,changedDisabled,waitingScan}}));
"""
        result = subprocess.run(["node", "-e", harness], capture_output=True,
                                text=True, check=True)
        rendered = json.loads(result.stdout)
        self.assertIn("CANCELED", rendered["canceled"]["badge"])
        self.assertEqual(rendered["canceled"]["outcome"], "CANCELED (explicit stop)")
        self.assertIn("64/64", rendered["canceled"]["coverage"])
        self.assertIn("7 routine budget-skipped", rendered["canceled"]["coverage"])
        self.assertIn("dropped 0", rendered["canceled"]["writer"])
        self.assertIn("CANCELED", rendered["recovered"]["badge"])
        self.assertEqual(rendered["recovered"]["firstLabel"], "First recognition issue")
        self.assertEqual(rendered["recovered"]["firstCause"], "invalid_speed_glyph")
        self.assertIn("BLOCKED", rendered["blocked"]["badge"])
        self.assertEqual(rendered["blocked"]["outcome"], "BLOCKED")
        self.assertTrue(rendered["independent"]["nearDisabled"])
        self.assertTrue(rendered["independent"]["selectedDisabled"])
        self.assertTrue(rendered["independent"]["approachDisabled"])
        self.assertEqual(rendered["independent"]["target"], "asteroid:4500")
        self.assertTrue(rendered["selectedAssessedEnabled"])
        self.assertTrue(rendered["assessedEnabled"])
        self.assertTrue(rendered["changedDisabled"])
        self.assertEqual(rendered["waitingScan"]["button"], "Stop scanning")
        self.assertTrue(rendered["waitingScan"]["canStop"])
        self.assertTrue(rendered["waitingScan"]["approachDisabled"])


if __name__ == "__main__":
    unittest.main()
