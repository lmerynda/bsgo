"""Read-only observer HTTP tests with local fixture packages only."""
import json
from pathlib import Path
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
                                 "controller_not_attached")
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


if __name__ == "__main__":
    unittest.main()
