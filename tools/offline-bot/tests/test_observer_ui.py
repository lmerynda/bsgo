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
                with urlopen(base + "/api/startup") as response:
                    startup = json.load(response)
                self.assertEqual(startup["phase"], "unattached")
                self.assertFalse(startup["action_control"]["enabled"])
                self.assertEqual(state["actual_mode"], "manual")
                self.assertIsInstance(state["server_monotonic"], float)
                self.assertFalse(state["routines"]["scan_nearby_unscanned"]["ready"])
                self.assertFalse(state["routines"]["auto_scan_selected"]["ready"])
                self.assertEqual(state["routines"]["auto_scan_selected"]["kind"], "toggle")
                self.assertFalse(state["routines"]["scan_selected"]["ready"])
                self.assertFalse(state["routines"]["approach_current_target"]["ready"])
                self.assertEqual(post({"action":"mine","routine":"scan_nearby_unscanned"})[0], 400)
                self.assertEqual(post({"action":"start","routine":"unlisted"})[0], 400)
                self.assertEqual(post({"action":"start","routine":"scan_nearby_unscanned",
                                       "request":[]})[0], 400)
                self.assertEqual(post({"action":"start","routine":"scan_nearby_unscanned"},
                                      origin="http://bad.example")[0], 403)
                self.assertEqual(post({"action":"start","routine":"scan_selected"})[0], 409)
                self.assertEqual(post({"action":"start","routine":"auto_scan_selected"})[0], 409)
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

    def test_auto_scan_waits_without_lease_and_saved_run_link_is_confined(self):
        class Adapter:
            active = True
            directory = None

            def actions_snapshot(self):
                return {"actual_mode":"manual", "paused":False,
                    "ownership":{"owner":None,"state":"idle","input_scope":None},
                    "routines":{"auto_scan_selected":{"active":self.active,
                        "kind":"toggle","state":"waiting","waiting_for":"stationary_ship_required"},
                        "approach_current_target":{"active":False,"available":True,"ready":True}},
                    "last_input_intent":{"kind":"key","key":"Z"},
                    "last_input_delivery":{"outcome":"injected"},
                    "last_confirmed_effect":{"effect":"scan_result_unverified"},
                    "action_artifacts":{"directory":str(self.directory),
                                        "status":str(self.directory / "status.json")}}

            def start_action(self, name, request=None):
                raise AssertionError("competing start reached adapter")

            def stop_action(self, name):
                self.active = False
                return {"accepted":True,"reason":"toggle_disabled",
                        "state":self.actions_snapshot()}

        with scratch_directory() as directory:
            run = package(directory, "auto-scan-run", finished=True)
            adapter = Adapter()
            adapter.directory = run
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
                with urlopen(base + "/api/manual-actions") as response:
                    state = json.load(response)
                self.assertEqual(state["action_artifacts"]["run"], "auto-scan-run")
                self.assertEqual(state["routines"]["auto_scan_selected"]["waiting_for"],
                                 "stationary_ship_required")
                self.assertEqual(state["last_input_delivery"]["outcome"], "injected")
                self.assertEqual(post("start", "approach_current_target")[0], 409)
                self.assertEqual(post("stop", "auto_scan_selected")[0], 200)
                self.assertFalse(adapter.active)
                outside = directory.parent / "outside-action"
                adapter.directory = outside
                with urlopen(base + "/api/manual-actions") as response:
                    self.assertNotIn("run", json.load(response)["action_artifacts"])
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

    def test_startup_connection_and_exact_pause_acknowledgment_guard_actions(self):
        class Startup:
            phase = "waiting_for_game"
            enabled = False
            token = "pause-fingerprint-one"
            retry_count = 0
            owner = None
            focus_enabled = False

            def startup_snapshot(self):
                return {"protocol":"bsgo-manual-assist-launcher-v1", "instance_id":"fixture",
                    "phase":self.phase, "reason":None,
                    "game":{"connection_id":"game-one"} if self.phase == "connected" else None,
                    "focus_handover":{"enabled":self.focus_enabled,"available":self.phase == "connected",
                        "reason":None if self.phase == "connected" else "game_not_connected"},
                    "action_control":{"paused":not self.enabled,"enabled":self.enabled,
                        "global_pause_present":True,"pause_reason":"persistent_mining_pause",
                        "acknowledgment_required":True,"acknowledgment_token":self.token}}

            def retry_connection(self):
                self.retry_count += 1
                self.phase = "connected"
                return {"accepted":True,"reason":"read_only_connection_checked"}

            def resume_actions(self, *, acknowledgment_token):
                if acknowledgment_token != self.token:
                    return {"accepted":False,"reason":"pause_acknowledgment_stale"}
                self.enabled = True
                return {"accepted":True,"reason":"manual_actions_enabled"}

            def pause_actions(self):
                self.enabled = False
                return {"accepted":True,"reason":"manual_actions_paused"}

            def set_focus_handover(self, enabled):
                if self.phase != "connected":
                    return {"accepted":False,"reason":"game_not_connected"}
                self.focus_enabled = enabled
                return {"accepted":True,"reason":"focus_preference_updated"}

            def actions_snapshot(self):
                return {"actual_mode":"manual","paused":False,
                    "ownership":{"owner":self.owner,"state":"active" if self.owner else "idle"},
                    "routines":{"scan_selected":{"available":True,"ready":True,
                        "active":self.owner == "scan_selected"}}}

            def start_action(self, name, request=None):
                self.owner = name
                return {"accepted":True,"reason":"started"}

            def stop_action(self, name):
                self.owner = None
                return {"accepted":True,"reason":"stopping"}

        with scratch_directory() as directory:
            adapter = Startup()
            server = make_server(directory, 0, action_adapter=adapter,
                                 startup_adapter=adapter)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(path, payload, origin=None):
                req = Request(base + path, method="POST", data=json.dumps(payload).encode(),
                    headers={"Content-Type":"application/json","Origin":origin or base})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base + "/api/startup") as response:
                    initial = json.load(response)
                self.assertEqual(initial["phase"], "waiting_for_game")
                self.assertEqual(adapter.retry_count, 0)
                self.assertFalse(adapter.enabled)
                self.assertEqual(post("/api/startup", {"action":"retry_connection"},
                                      origin="http://outside.example")[0], 403)
                self.assertEqual(post("/api/startup", {"action":"enable_manual_actions"})[0], 400)
                self.assertEqual(post("/api/startup", {"action":"invalid"})[0], 400)
                self.assertEqual(post("/api/startup", {"action":"set_focus_handover",
                    "enabled":"true"})[0], 400)
                code, retry = post("/api/startup", {"action":"retry_connection"})
                self.assertEqual(code, 200)
                self.assertEqual(retry["state"]["phase"], "connected")
                self.assertFalse(adapter.enabled)
                self.assertFalse(adapter.focus_enabled)
                self.assertEqual(post("/api/startup", {"action":"set_focus_handover",
                    "enabled":True})[0], 200)
                self.assertTrue(adapter.focus_enabled)
                self.assertFalse(adapter.enabled)
                self.assertEqual(post("/api/manual-actions", {"action":"start",
                    "routine":"scan_selected"})[1]["reason"], "manual_actions_paused")
                self.assertEqual(post("/api/startup", {"action":"enable_manual_actions",
                    "acknowledgment_token":"stale"})[1]["reason"], "pause_acknowledgment_stale")
                self.assertFalse(adapter.enabled)
                code, enabled = post("/api/startup", {"action":"enable_manual_actions",
                    "acknowledgment_token":adapter.token})
                self.assertEqual(code, 200)
                self.assertTrue(enabled["state"]["action_control"]["enabled"])
                self.assertEqual(post("/api/manual-actions", {"action":"start",
                    "routine":"scan_selected"})[0], 200)
                self.assertEqual(post("/api/startup", {"action":"pause_manual_actions"})[0], 200)
                self.assertFalse(adapter.enabled)
                self.assertEqual(adapter.owner, "scan_selected")
                self.assertEqual(post("/api/manual-actions", {"action":"stop",
                    "routine":"scan_selected"})[0], 200)
                adapter.token = "pause-fingerprint-two"
                self.assertEqual(post("/api/startup", {"action":"enable_manual_actions",
                    "acknowledgment_token":"pause-fingerprint-one"})[0], 409)
                self.assertFalse(adapter.enabled)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_advanced_attached_session_can_enable_without_launcher_adapter(self):
        class PinnedActions:
            enabled = False
            starts = 0

            def action_control_snapshot(self):
                return {"enabled":self.enabled,"paused":not self.enabled,
                    "pause_reason":None if self.enabled else "session_paused",
                    "acknowledgment_token":"pinned-token"}

            def resume_actions(self, *, acknowledgment_token):
                if acknowledgment_token != "pinned-token":
                    return {"accepted":False,"reason":"stale_token"}
                self.enabled = True
                return {"accepted":True,"reason":"enabled"}

            def pause_actions(self):
                self.enabled = False
                return {"accepted":True,"reason":"paused"}

            def actions_snapshot(self):
                return {"actual_mode":"manual","paused":not self.enabled,
                    "ownership":{"owner":None,"state":"idle"},
                    "routines":{"scan_selected":{"available":True,"ready":True,"active":False}}}

            def start_action(self, name, request=None):
                self.starts += 1
                return {"accepted":True,"reason":"started"}

            def stop_action(self, name):
                return {"accepted":False,"reason":"no_owner"}

        with scratch_directory() as directory:
            actions = PinnedActions()
            server = make_server(directory, 0, action_adapter=actions)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def post(path, payload):
                req = Request(base + path, method="POST", data=json.dumps(payload).encode(),
                    headers={"Content-Type":"application/json","Origin":base})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                with urlopen(base + "/api/startup") as response:
                    state = json.load(response)
                self.assertEqual(state["phase"], "connected")
                self.assertFalse(state["action_control"]["enabled"])
                self.assertEqual(post("/api/manual-actions", {"action":"start",
                    "routine":"scan_selected"})[1]["reason"], "manual_actions_paused")
                self.assertEqual(post("/api/startup", {"action":"retry_connection"})[1]["reason"],
                                 "pinned_session_retry_unavailable")
                self.assertEqual(post("/api/startup", {"action":"enable_manual_actions",
                    "acknowledgment_token":"pinned-token"})[0], 200)
                self.assertEqual(post("/api/manual-actions", {"action":"start",
                    "routine":"scan_selected"})[0], 200)
                self.assertEqual(actions.starts, 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for inline UI render smoke")
    def test_canceled_budget_skip_renders_canceled_but_true_blocker_renders_blocked(self):
        script = re.search(r"(?s)<script>(.*?)</script>", HTML.read_text()).group(1)
        script = script.replace("  refresh();\n  refreshMode();\n  refreshActions();\n  refreshStartup();",
                                "  globalThis.testRender = render; globalThis.testActions = renderActions; globalThis.testStartup = renderStartup; globalThis.testCapture = captureAssessment; globalThis.testActionRequest = actionRequest;")
        harness = f"""
const elements = {{}};
globalThis.document = {{getElementById(id) {{ return elements[id] ||= {{
  textContent:'', hidden:false, className:'', dataset:{{}}, setAttribute(){{}}, removeAttribute(){{}},
  addEventListener(){{}}
}}; }} }};
eval({json.dumps(script)});
const startup = {{instance_id:'launcher-one',phase:'waiting_for_game',reason:'game_not_found',game:null,
  focus_handover:{{enabled:false,available:false,reason:'game_not_connected'}},
  action_control:{{paused:true,enabled:false,pause_reason:'startup_paused',
    global_pause_present:true,acknowledgment_token:'pause-one'}}}};
testStartup(startup);
const waitingStartup = {{state:elements.startupstate.textContent,
  enableDisabled:elements.startupenable.disabled,
  notice:elements.startupacknotice.textContent}};
startup.phase='blocked'; startup.reason='No visible game window found. Open or restore the game.';
testStartup(startup);
const missingGameStartup = elements.startupstate.textContent;
startup.phase='blocked'; startup.reason='multiple_game_windows';
testStartup(startup);
const ambiguousStartup = elements.startupstate.textContent;
startup.reason='unsupported_layout';
testStartup(startup);
const layoutStartup = elements.startupstate.textContent;
startup.phase='connected'; startup.reason=null; startup.game={{connection_id:'game-one',window_attached:true,observation_healthy:false,observation_reason:'HUD waiting'}};
startup.focus_handover={{enabled:false,available:true,reason:null}};
testStartup(startup);
const connectedStartup = {{state:elements.startupstate.textContent,
  enableDisabled:elements.startupenable.disabled,
  pauseDisabled:elements.startuppause.disabled,
  focusDisabled:elements.startupfocus.disabled,
  image:elements.startupimage.textContent}};
startup.action_control.enabled=true; startup.action_control.paused=false;
testStartup(startup);
const enabledStartup = {{actions:elements.startupactions.textContent,
  detail:elements.startupdetail.textContent,
  pauseDisabled:elements.startuppause.disabled}};
startup.action_control.enabled=false; startup.action_control.paused=true;
testStartup(startup);
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
const actions = {{actual_mode:'manual',paused:false,server_monotonic:100,
  focus_handover_enabled:true,focus_handover:{{accepted:false,reason:'game_focus_wait'}},
  ownership:{{owner:null,state:'idle'}},
  current_observation:{{target_identity:'asteroid:4500',distance:700,speed:0,
    fields:{{distance:{{valid:true,fresh:true}},speed:{{valid:true,fresh:true}}}}}},
  assessment_context:{{token:'t1',context_key:'target-A',target_identity:'asteroid:4500',expires_at:115,wheel_point:{{x:1280,y:400}},flight_point_description:'upper-center flight view'}},
  action_artifacts:{{directory:'fixture/last-run',run:'last-run'}},
  selected_scan_toggle:{{enabled:false,scope:'human_selected_target_only',waiting_for:'stationary_ship_required'}},
  routines:{{scan_nearby_unscanned:{{available:false,ready:false,reason:'discovery_provider_missing'}},
    auto_scan_selected:{{available:true,ready:true}},
    scan_selected:{{available:true,ready:true}},
    approach_current_target:{{available:true,ready:true}}}}}};
elements.runs = {{options:[{{value:'last-run'}}]}};
testActions(actions);
const independent = {{nearDisabled:elements.scanbutton.disabled,
  autoDisabled:elements.autoscanbutton.disabled,
  selectedDisabled:elements.selectedscanbutton.disabled,
  approachDisabled:elements.approachbutton.disabled,
  target:elements.currenttarget.textContent,
  assessment:elements.assessmentcontext.textContent,
  selectedReadiness:elements.selectedscanreadiness.textContent,
  flightCue:elements.flightpointvalue.textContent,
  focus:elements.focushandover.textContent,
  artifact:elements.actionartifactlink.dataset.run,
  artifactLinkVisible:!elements.actionartifactlink.hidden,
  toggle:elements.autoscantoggle.textContent}};
elements.runs.options=[];
testActions(actions);
const missingArtifactOptionHidden = elements.actionartifactlink.hidden;
elements.runs.options=[{{value:'last-run'}}];
testActions(actions);
elements.autoscansafe.checked=true;
testCapture('auto_scan_selected');
const autoAssessedEnabled = !elements.autoscanbutton.disabled;
elements.scanselectedsafe.checked=true;
testCapture('scan_selected');
const selectedAssessedEnabled = !elements.selectedscanbutton.disabled;
for (const id of ['clearcorridor','alignedheading','safestopped','flightpoint']) elements[id].checked=true;
testCapture('approach_current_target');
const assessedEnabled = !elements.approachbutton.disabled;
actions.assessment_context={{token:'t2',context_key:'target-A',target_identity:'asteroid:4500',expires_at:130,wheel_point:{{x:1280,y:400}}}};
actions.server_monotonic=105;
testActions(actions);
const originalTokenHeld = testActionRequest('approach_current_target','start').assessment_token === 't1'
  && testActionRequest('scan_selected','start').assessment_token === 't1'
  && testActionRequest('auto_scan_selected','start').assessment_token === 't1'
  && !elements.approachbutton.disabled;
actions.server_monotonic=116;
testActions(actions);
const originalExpired = elements.approachbutton.disabled && elements.selectedscanbutton.disabled
  && elements.autoscanbutton.disabled && !elements.clearcorridor.checked
  && !elements.scanselectedsafe.checked && !elements.autoscansafe.checked;
actions.assessment_context={{token:'t3',context_key:'target-B',target_identity:'asteroid:5000',expires_at:130,wheel_point:{{x:1280,y:400}}}};
testActions(actions);
const changedDisabled = elements.approachbutton.disabled && !elements.clearcorridor.checked;
actions.routines.auto_scan_selected.active=true;
testActions(actions);
const waitingScan = {{button:elements.autoscanbutton.textContent,
  canStop:!elements.autoscanbutton.disabled, approachDisabled:elements.approachbutton.disabled}};
actions.routines.auto_scan_selected.active=false;
actions.assessment_context={{token:'t4',context_key:'target-B',target_identity:'asteroid:5000',expires_at:145,wheel_point:{{x:1280,y:400}}}};
actions.server_monotonic=120;
testActions(actions);
elements.scanselectedsafe.checked=true;
testCapture('scan_selected');
const beforeReconnect = !elements.selectedscanbutton.disabled;
startup.game={{connection_id:'game-two'}};
testStartup(startup);
testActions(actions);
const afterReconnect = elements.selectedscanbutton.disabled && !elements.scanselectedsafe.checked;
console.log(JSON.stringify({{waitingStartup,missingGameStartup,ambiguousStartup,layoutStartup,connectedStartup,enabledStartup,canceled,recovered,blocked,independent,missingArtifactOptionHidden,autoAssessedEnabled,selectedAssessedEnabled,assessedEnabled,originalTokenHeld,originalExpired,changedDisabled,waitingScan,beforeReconnect,afterReconnect}}));
"""
        with scratch_directory() as directory:
            path = Path(directory) / "observer-render-smoke.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True,
                                    text=True, check=True)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["waitingStartup"]["state"], "Waiting for the game")
        self.assertTrue(rendered["waitingStartup"]["enableDisabled"])
        self.assertIn("exact pause", rendered["waitingStartup"]["notice"])
        self.assertEqual(rendered["missingGameStartup"], "Waiting for the game")
        self.assertEqual(rendered["ambiguousStartup"], "More than one game window")
        self.assertEqual(rendered["layoutStartup"], "Game layout needs attention")
        self.assertEqual(rendered["connectedStartup"]["state"], "Game window attached")
        self.assertFalse(rendered["connectedStartup"]["enableDisabled"])
        self.assertFalse(rendered["connectedStartup"]["pauseDisabled"])
        self.assertFalse(rendered["connectedStartup"]["focusDisabled"])
        self.assertIn("HUD waiting", rendered["connectedStartup"]["image"])
        self.assertEqual(rendered["enabledStartup"]["actions"], "ENABLED")
        self.assertNotIn("remain paused", rendered["enabledStartup"]["detail"])
        self.assertFalse(rendered["enabledStartup"]["pauseDisabled"])
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
        self.assertTrue(rendered["independent"]["autoDisabled"])
        self.assertTrue(rendered["independent"]["selectedDisabled"])
        self.assertTrue(rendered["independent"]["approachDisabled"])
        self.assertEqual(rendered["independent"]["target"], "asteroid:4500")
        self.assertIn("expires in 15.0s", rendered["independent"]["assessment"])
        self.assertIn("no blocker", rendered["independent"]["selectedReadiness"])
        self.assertIn("game_focus_wait", rendered["independent"]["focus"])
        self.assertEqual(rendered["independent"]["artifact"], "last-run")
        self.assertTrue(rendered["independent"]["artifactLinkVisible"])
        self.assertTrue(rendered["missingArtifactOptionHidden"])
        self.assertIn("human_selected_target_only", rendered["independent"]["toggle"])
        self.assertIn("upper-center flight view", rendered["independent"]["flightCue"])
        self.assertNotIn("1280", rendered["independent"]["flightCue"])
        self.assertTrue(rendered["autoAssessedEnabled"])
        self.assertTrue(rendered["selectedAssessedEnabled"])
        self.assertTrue(rendered["assessedEnabled"])
        self.assertTrue(rendered["originalTokenHeld"])
        self.assertTrue(rendered["originalExpired"])
        self.assertTrue(rendered["changedDisabled"])
        self.assertEqual(rendered["waitingScan"]["button"], "Stop scanning")
        self.assertTrue(rendered["waitingScan"]["canStop"])
        self.assertTrue(rendered["waitingScan"]["approachDisabled"])
        self.assertTrue(rendered["beforeReconnect"])
        self.assertTrue(rendered["afterReconnect"])


if __name__ == "__main__":
    unittest.main()
