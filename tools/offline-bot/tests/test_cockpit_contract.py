"""Cockpit HTTP contracts through the real Manual controller; no native APIs."""
from contextlib import contextmanager
from dataclasses import replace
import json
from threading import Event, Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bsgo_offline.manual_assist import ManualAssistController
from bsgo_offline.observer_ui import make_server
from bsgo_offline.state import FieldValue
from test_manual_assist import drained, snapshot
from test_observer_ui import package
from test_trace import scratch_directory


class CockpitFixture:
    def __init__(self, directory):
        self.now = time.monotonic()
        self.row = snapshot(at=self.now)
        self.calls = []
        self.entered = Event()
        self.release = Event()
        self.controller = ManualAssistController(directory / "session",
            snapshot_provider=lambda: self.row, clock=lambda: self.now,
            runner=self.run, paused=True, wheel_point=(2, 1),
            focus_handover_capability=lambda cause: self.calls.append("focus"))
        self.server = make_server(directory, 0, action_adapter=self.controller)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def run(self, name, **args):
        self.calls.append(name)
        args["on_delivery"]({"synthetic": True}, "injected")
        self.entered.set()
        self.release.wait(3)
        return {**drained(), "cancel_requested": True}

    def http(self, path, data=None, **headers):
        if data is not None:
            headers = {"Content-Type": "application/json", "Origin": self.base, **headers}
        request = Request(self.base + path,
            data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def state(self):
        code, state = self.http("/api/manual-actions")
        assert code == 200
        return state

    def enable(self):
        _, startup = self.http("/api/startup")
        return self.http("/api/startup", {"action": "enable_manual_actions",
            "acknowledgment_token": startup["action_control"]["acknowledgment_token"]})

    def request(self):
        return {"assessment_token": self.state()["assessment_context"]["token"],
            "supervisor_assessment": {key: True for key in (
                "clear_corridor", "aligned_heading", "safe_stopped_scene", "flight_point")}}

    def start(self, request):
        return self.http("/api/manual-actions", {"action": "start",
            "routine": "approach_current_target", "request": request})

    def fresh(self, **changes):
        self.now += 1
        self.row = snapshot(sequence=self.row.sequence + 1, at=self.now, **changes)

    def close(self):
        self.release.set()
        self.controller.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


@contextmanager
def cockpit():
    with scratch_directory() as directory:
        fixture = CockpitFixture(directory)
        try:
            yield fixture, directory
        finally:
            fixture.close()


class CockpitContractTests(unittest.TestCase):
    def test_enable_and_reading_or_history_selection_never_dispatch(self):
        with cockpit() as (fixture, directory):
            package(directory, "historical", finished=True)
            self.assertEqual(fixture.start(fixture.request())[0], 409)
            self.assertEqual(fixture.enable()[0], 200)
            fixture.http("/api/status?run=historical")
            state = fixture.state()
            self.assertEqual(state["current_observation"]["distance"], 1000)
            self.assertFalse(state["app_control_enabled"])
            self.assertEqual(state["actual_mode"], "manual")
            self.assertIsNone(state["ownership"]["owner"])
            self.assertEqual(fixture.calls, [])

    def test_current_hp_unknown_does_not_discard_known_maximum(self):
        with cockpit() as (fixture, _):
            at = fixture.row.acquired_at
            fixture.row = replace(fixture.row, fields={**fixture.row.fields,
                "target_current_hp": FieldValue(None, at, False, "synthetic_incomplete_glyph")})
            fields = fixture.state()["current_observation"]["fields"]
            self.assertIsNone(fields["target_current_hp"]["value"])
            self.assertFalse(fields["target_current_hp"]["valid"])
            self.assertEqual(fields["target_current_hp"]["reason"], "synthetic_incomplete_glyph")
            self.assertTrue(fields["target_max_hp"]["valid"])
            self.assertEqual(fields["target_max_hp"]["value"], 3900)
            self.assertIsNotNone(fixture.state()["assessment_context"])

    def test_new_invalid_fields_replace_prior_values_and_stale_context_cannot_arm(self):
        with cockpit() as (fixture, _):
            self.assertEqual(fixture.state()["current_observation"]["speed"], 0)
            fixture.fresh()
            fixture.row = replace(fixture.row, fields={**fixture.row.fields,
                "speed": FieldValue(None, fixture.now, False, "synthetic_occlusion")})
            state = fixture.state()
            self.assertIsNone(state["current_observation"]["speed"])
            self.assertIsNone(state["assessment_context"])
            fixture.fresh()
            fixture.now += 3
            state = fixture.state()
            self.assertFalse(state["current_observation"]["fields"]["speed"]["fresh"])
            self.assertIsNone(state["assessment_context"])
            self.assertFalse(state["routines"]["approach_current_target"]["ready"])

    def test_distance_is_actual_unclamped_measurement_and_negative_cannot_arm(self):
        with cockpit() as (fixture, _):
            fixture.fresh(distance=4500)
            state = fixture.state()
            self.assertEqual(state["current_observation"]["distance"], 4500)
            self.assertFalse(state["routines"]["scan_selected"]["ready"])
            fixture.fresh(distance=-1)
            state = fixture.state()
            self.assertEqual(state["current_observation"]["distance"], -1)
            self.assertIsNone(state["assessment_context"])

    def test_original_checked_token_expires_even_after_fresh_polling(self):
        with cockpit() as (fixture, _):
            fixture.enable()
            request = fixture.request()
            original = fixture.state()["assessment_context"]
            for _ in range(16):
                fixture.fresh()
                fixture.state()
            latest = fixture.state()["assessment_context"]
            self.assertEqual(original["context_key"], latest["context_key"])
            self.assertGreater(latest["expires_at"], original["expires_at"])
            code, result = fixture.start(request)
            self.assertEqual(code, 409)
            self.assertEqual(result["reason"], "fresh_target_assessment_token_required")
            self.assertEqual(fixture.calls, [])

    def test_four_assessments_required_and_cancel_remains_reachable_while_paused(self):
        with cockpit() as (fixture, _):
            fixture.enable()
            request = fixture.request()
            for key in tuple(request["supervisor_assessment"]):
                rejected = {**request, "supervisor_assessment": {
                    **request["supervisor_assessment"], key: False}}
                self.assertEqual(fixture.start(rejected)[0], 409)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(fixture.start(request)[0], 200)
            self.assertTrue(fixture.entered.wait(1))
            state = fixture.state()
            self.assertEqual(state["ownership"]["owner"], "approach_current_target")
            self.assertTrue(state["app_control_enabled"])
            self.assertFalse(state["decisions_enabled"])
            self.assertEqual(fixture.http("/api/startup", {"action": "pause_manual_actions"})[0], 200)
            self.assertEqual(fixture.http("/api/manual-actions", {"action": "stop",
                "routine": "approach_current_target"})[0], 200)
            self.assertNotIn("focus", fixture.calls)

    def test_disabled_capabilities_and_mutation_origin_guards(self):
        with cockpit() as (fixture, _):
            fixture.enable()
            state = fixture.state()
            self.assertFalse(state["routines"]["scan_nearby_unscanned"]["available"])
            code, result = fixture.http("/api/control-mode", {"mode": "autopilot"})
            self.assertEqual(code, 409)
            self.assertEqual(result["state"]["actual_mode"], "manual")
            self.assertEqual(result["state"]["requested_mode"], "autopilot")
            for headers in ({"Origin": "http://other.invalid"}, {"Host": "other.invalid"},
                            {"Sec-Fetch-Site": "cross-site"}):
                self.assertEqual(fixture.http("/api/startup",
                    {"action": "pause_manual_actions"}, **headers)[0], 403)
            self.assertEqual(fixture.calls, [])

    def test_mode_mutation_serializes_with_pause(self):
        with cockpit() as (fixture, directory):
            fixture.server.shutdown()
            fixture.server.server_close()
            fixture.thread.join(2)
            entered, release, paused, pause_sent = Event(), Event(), Event(), Event()
            class Mode:
                def snapshot(self):
                    return {"actual_mode": "manual", "pending": False}
                def request(self, mode):
                    entered.set()
                    release.wait(2)
                    return {"accepted": True}
            original_pause = fixture.controller.pause_actions
            def pause():
                paused.set()
                return original_pause()
            fixture.controller.pause_actions = pause
            fixture.server = make_server(directory, 0, action_adapter=fixture.controller,
                mode_controller=Mode())
            fixture.thread = Thread(target=fixture.server.serve_forever, daemon=True)
            fixture.thread.start()
            fixture.base = f"http://127.0.0.1:{fixture.server.server_port}"
            replies = []
            first = Thread(target=lambda: replies.append(fixture.http(
                "/api/control-mode", {"mode": "manual"})))
            def send_pause():
                pause_sent.set()
                replies.append(fixture.http("/api/startup", {"action": "pause_manual_actions"}))
            second = Thread(target=send_pause)
            try:
                first.start()
                self.assertTrue(entered.wait(1))
                second.start()
                self.assertTrue(pause_sent.wait(1))
                self.assertFalse(paused.wait(.15), "Pause raced an unacknowledged mode mutation")
            finally:
                release.set()
                first.join(2)
                if second.ident is not None:
                    second.join(2)
            self.assertTrue(paused.is_set())
            self.assertEqual([code for code, _ in replies], [200, 200])


if __name__ == "__main__":
    unittest.main()
