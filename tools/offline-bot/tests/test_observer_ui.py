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
                self.assertIn(b"Show retained images", html)
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
    def test_cockpit_live_authority_fresh_fields_bound_assessment_and_recorded_history(self):
        script = re.search(r"(?s)<script>(.*?)</script>", HTML.read_text(encoding="utf-8")).group(1)
        script = script.replace("  refreshLive();\n  setInterval(renderLive,250);\n})();", "  globalThis.setCockpit=(s,m,a)=>{startup=s;mode=m;actions=a;}; globalThis.cockpit={renderLive,captureAssessment,actionRequest,renderHistory};\n})();")
        harness = """
const elements = {};
globalThis.document = {getElementById(id) { return elements[id] ||= {
  textContent:'', hidden:false, checked:false, disabled:false, className:'', dataset:{}, style:{},
  attributes:{}, addEventListener(){}, scrollIntoView(){}, removeAttribute(name){delete this.attributes[name]},
  setAttribute(name,value){this.attributes[name]=String(value)}
}; }};
eval(SCRIPT);
const field=(value,t=1)=>({value,valid:true,fresh:true,observed_at:t});
const current={healthy:true,age_seconds:.4,acquired_at:1,sequence:1,fields:{
  target_identity:field('asteroid:4500'),distance:field(800),speed:field(0),
  hull_current:field(5005),hull_max:field(5005),target_current_hp:field(4500),target_max_hp:field(4500)}};
const startup={phase:'connected',instance_id:'launcher-A',game:{connection_id:'game-A',client_size:[2560,1440],observation_healthy:true},
  action_control:{enabled:true},focus_handover:{available:true,enabled:false}};
const mode={actual_mode:'manual',pending:false};
const actions={actual_mode:'manual',paused:false,server_monotonic:100,current_observation:current,
  ownership:{owner:null,state:'idle'},assessment_context:{token:'first',context_key:'same-target',expires_at:115,
    observation_sequence:1,wheel_point:[1280,400],flight_point_description:'upper-center flight view'},
  routines:{approach_current_target:{available:true,ready:true},scan_selected:{available:true,ready:true},
    auto_scan_selected:{available:true,ready:true,active:false}}};
setCockpit(startup,mode,actions); cockpit.renderLive();
const normal={range:elements.range.textContent,speed:elements.speed.textContent,mode:elements.actualmode.textContent,
  owner:elements.inputowner.textContent,marker:elements.targetmarker.style.left,band:elements.workband.style.width,
  startDisabled:elements.startbutton.disabled,pointHidden:elements.flightdiagram.hidden};
for(const id of ['clearcorridor','alignedheading','safestopped','flightpoint']) {elements[id].checked=true; cockpit.captureAssessment('approach_current_target')}
const assessed=!elements.startbutton.disabled;
actions.assessment_context={...actions.assessment_context,token:'renewed',expires_at:130,observation_sequence:2};
actions.server_monotonic=105; cockpit.renderLive();
const original=cockpit.actionRequest('approach_current_target','start').assessment_token;
actions.server_monotonic=116; cockpit.renderLive();
const expired={disabled:elements.startbutton.disabled,checked:elements.clearcorridor.checked};
actions.current_observation={...current,age_seconds:3,fields:{...current.fields,speed:field(5)}};
cockpit.renderLive();
const stale={speed:elements.speed.textContent,range:elements.range.textContent};
actions.current_observation={...current,fields:{...current.fields,distance:field(3400),target_current_hp:{valid:false,fresh:false,reason:'unreadable'}}};
cockpit.renderLive();
const offscale={marker:elements.targetmarker.textContent,flag:elements.offscale.hidden,hp:elements.hpvalue.textContent};
actions.current_observation={...current,fields:{...current.fields,distance:field(-2)}};cockpit.renderLive();
const invalidRange={range:elements.range.textContent,markerHidden:elements.targetmarker.hidden,
  issues:elements.issuebutton.textContent,reason:elements.diagfields.textContent};
actions.ownership={owner:'approach_current_target',state:'active',input_scope:'navigation'};
startup.action_control.enabled=false;cockpit.renderLive();
const pausedOwner={owner:elements.inputowner.textContent,cancelDisabled:elements.cancelbutton.disabled,startDisabled:elements.startbutton.disabled};
const recorded={run:'sample',manifest:{synthetic:true},lifecycle:'FINALIZED',status:{execution:{phase:'canceled'},
  result:{state:'canceled'},frames:{accepted:64,written:64,budget_skipped:7,dropped:0,unwritten:0,error:null},
  trace:{dropped:0,unwritten:0,error:null},status_write_errors:0},report:{complete_diagnostics:false}};
cockpit.renderHistory(recorded);
const history={state:elements.historicstate.textContent,coverage:elements.historiccoverage.textContent,
  synthetic:elements.historicsynthetic.hidden,liveRange:elements.range.textContent};
console.log(JSON.stringify({normal,assessed,original,expired,stale,offscale,invalidRange,pausedOwner,history}));
""".replace("SCRIPT", json.dumps(script))
        with scratch_directory() as directory:
            path = Path(directory) / "cockpit-render-smoke.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True,
                                    text=True, encoding="utf-8", check=True)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["normal"]["range"], "800 m")
        self.assertEqual(rendered["normal"]["speed"], "0 m/s")
        self.assertEqual(rendered["normal"]["mode"], "Manual")
        self.assertEqual(rendered["normal"]["owner"], "Human")
        self.assertAlmostEqual(float(rendered["normal"]["marker"].removesuffix("%")), 800/3000*100)
        self.assertAlmostEqual(float(rendered["normal"]["band"].removesuffix("%")), 800/3000*100)
        self.assertTrue(rendered["normal"]["startDisabled"])
        self.assertFalse(rendered["normal"]["pointHidden"])
        self.assertTrue(rendered["assessed"])
        self.assertEqual(rendered["original"], "first")
        self.assertEqual(rendered["expired"], {"disabled": True, "checked": False})
        self.assertEqual(rendered["stale"], {"speed": "UNKNOWN", "range": "UNKNOWN"})
        self.assertEqual(ord(rendered["offscale"]["marker"]), 0x203A)
        self.assertFalse(rendered["offscale"]["flag"])
        self.assertTrue(rendered["offscale"]["hp"].endswith("/ 4,500"))
        self.assertEqual(rendered["invalidRange"]["range"], "UNKNOWN")
        self.assertTrue(rendered["invalidRange"]["markerHidden"])
        self.assertIn("1 field issue", rendered["invalidRange"]["issues"])
        self.assertIn("invalid numeric value", rendered["invalidRange"]["reason"])
        self.assertFalse(rendered["pausedOwner"]["cancelDisabled"])
        self.assertTrue(rendered["pausedOwner"]["startDisabled"])
        self.assertIn("Approach", rendered["pausedOwner"]["owner"])
        self.assertIn("canceled", rendered["history"]["state"])
        self.assertIn("budget-skipped 7", rendered["history"]["coverage"])
        self.assertIn("trace dropped 0", rendered["history"]["coverage"])
        self.assertFalse(rendered["history"]["synthetic"])
        self.assertEqual(rendered["history"]["liveRange"], "UNKNOWN")

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for inline UI render smoke")
    def test_active_owner_can_request_generic_pause_when_live_gets_fail(self):
        script = re.search(r"(?s)<script>(.*?)</script>", HTML.read_text(encoding="utf-8")).group(1)
        script = script.replace("  refreshLive();\n  setInterval(renderLive,250);\n})();", "  globalThis.cockpit={refreshLive,requestAction,requestStartup};\n})();")
        harness = """
const elements={};let failGets=false;const posts=[];
globalThis.document={getElementById(id){return elements[id] ||= {
  textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
  addEventListener(){},scrollIntoView(){},removeAttribute(){},setAttribute(){}};}};
globalThis.setTimeout=()=>{};
const field=value=>({value,valid:true,fresh:true,observed_at:1});
const startup={phase:'connected',instance_id:'live-one',game:{connection_id:'game-one'},action_control:{enabled:true}};
const actions={actual_mode:'manual',paused:false,server_monotonic:100,
  ownership:{owner:'approach_current_target',state:'active',input_scope:'navigation'},
  current_observation:{healthy:true,age_seconds:.3,acquired_at:1,sequence:1,fields:{distance:field(900),speed:field(5)}},
  routines:{approach_current_target:{active:true,phase:'cruising'}}};
globalThis.fetch=async (url,options={})=>{
  if(options.method==='POST'){posts.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({accepted:true,reason:'cancel_requested'})};}
  if(failGets)throw Error('synthetic GET failure');
  return {ok:true,json:async()=>url==='/api/startup'?startup:url==='/api/manual-actions'?actions:{actual_mode:'manual'}};
};
eval(SCRIPT);
(async()=>{
  await cockpit.refreshLive();failGets=true;await cockpit.refreshLive();
  const unavailable={range:elements.range.textContent,owner:elements.inputowner.textContent,
    cancelDisabled:elements.cancelbutton.disabled,pauseDisabled:elements.powerbutton.disabled,
    pauseLabel:elements.powerbutton.textContent,startDisabled:elements.startbutton.disabled,
    instruction:elements.instruction.textContent};
  await cockpit.requestStartup('pause_manual_actions');
  console.log(JSON.stringify({unavailable,posts}));
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("SCRIPT", json.dumps(script))
        with scratch_directory() as directory:
            path = Path(directory) / "cockpit-get-failure.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True,
                                    text=True, encoding="utf-8", check=True)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["unavailable"]["range"], "UNKNOWN")
        self.assertEqual(rendered["unavailable"]["owner"], "UNKNOWN")
        self.assertTrue(rendered["unavailable"]["cancelDisabled"])
        self.assertFalse(rendered["unavailable"]["pauseDisabled"])
        self.assertTrue(rendered["unavailable"]["startDisabled"])
        self.assertIn("status unknown", rendered["unavailable"]["pauseLabel"])
        self.assertIn("Routine state unknown", rendered["unavailable"]["instruction"])
        self.assertEqual([(row["url"], row["body"]["action"]) for row in rendered["posts"]],
                         [("/api/startup", "pause_manual_actions")])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable for inline UI render smoke")
    def test_stalled_poll_ages_image_and_original_assessment_without_renewal(self):
        script = re.search(r"(?s)<script>(.*?)</script>", HTML.read_text(encoding="utf-8")).group(1)
        script = script.replace("  refreshLive();\n  setInterval(renderLive,250);\n})();",
                                "  globalThis.cockpit={refreshLive,captureAssessment}; setInterval(renderLive,250);\n})();")
        harness = """
const elements={};let clock=0,stuck=false,watchdog=null;const timers=[];
globalThis.performance={now:()=>clock};
globalThis.setInterval=callback=>{watchdog=callback};
globalThis.setTimeout=(callback,ms)=>{const timer={callback,ms,cleared:false};timers.push(timer);return timer};
globalThis.clearTimeout=timer=>{if(timer)timer.cleared=true};
globalThis.document={getElementById(id){return elements[id] ||= {
  textContent:'',hidden:false,checked:false,disabled:false,className:'',dataset:{},style:{},
  addEventListener(){},scrollIntoView(){},removeAttribute(){},setAttribute(){}};}};
const field=value=>({value,valid:true,fresh:true,observed_at:1});
const startup={phase:'connected',instance_id:'i',game:{connection_id:'g'},action_control:{enabled:true}};
const actions={actual_mode:'manual',paused:false,server_monotonic:100,ownership:{owner:null,state:'idle'},
  current_observation:{healthy:true,age_seconds:.1,acquired_at:1,sequence:1,fields:{distance:field(700),speed:field(0)}},
  assessment_context:{token:'original',context_key:'target',expires_at:115,observation_sequence:1},
  routines:{scan_selected:{available:true,ready:true},approach_current_target:{available:true,ready:true}}};
globalThis.fetch=async url=>{
  if(stuck)return new Promise(()=>{});
  return {ok:true,json:async()=>url==='/api/startup'?startup:url==='/api/manual-actions'?actions:{actual_mode:'manual'}};
};
eval(SCRIPT);
(async()=>{
  await cockpit.refreshLive();
  elements.scanselectedsafe.checked=true;cockpit.captureAssessment('scan_selected');
  const first=elements.range.textContent;
  clock=2100;watchdog();const stale={range:elements.range.textContent,age:elements.imageage.textContent};
  clock=16000;watchdog();const expired=elements.scanselectedsafe.checked;
  stuck=true;const pending=cockpit.refreshLive();
  clock=19000;for(const timer of timers.filter(row=>!row.cleared&&row.ms===3000))timer.callback();
  await pending;
  console.log(JSON.stringify({first,stale,expired,afterTimeout:elements.range.textContent,
    pauseDisabled:elements.powerbutton.disabled}));
})().catch(error=>{console.error(error);process.exitCode=1});
""".replace("SCRIPT", json.dumps(script))
        with scratch_directory() as directory:
            path = Path(directory) / "cockpit-stalled-poll.js"
            path.write_text(harness, encoding="utf-8")
            result = subprocess.run(["node", str(path)], capture_output=True,
                                    text=True, encoding="utf-8", check=True)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["first"], "700 m")
        self.assertEqual(rendered["stale"]["range"], "UNKNOWN")
        self.assertIn("STALE", rendered["stale"]["age"])
        self.assertFalse(rendered["expired"])
        self.assertEqual(rendered["afterTimeout"], "UNKNOWN")
        self.assertFalse(rendered["pauseDisabled"])


if __name__ == "__main__":
    unittest.main()
