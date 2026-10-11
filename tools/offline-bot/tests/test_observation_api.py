"""LLM observation contracts, with real loopback HTTP and no native game APIs."""
from dataclasses import replace
import json
from threading import Event, Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bsgo_offline.observation_api import OBSERVATION_SCHEMA, observation_snapshot
from bsgo_offline.observer_ui import make_server
from bsgo_offline.state import FieldValue
from test_cockpit_contract import cockpit
from test_observer_ui import package
from test_trace import scratch_directory


def providers():
    field = lambda value: {"value": value, "valid": True, "fresh": True, "observed_at": 99.8}
    startup = {"phase": "connected", "instance_id": "fixture", "action_control": {
        "enabled": True, "paused": False}}
    mode = {"actual_mode": "manual", "requested_mode": "manual", "pending": False}
    actions = {"attached_controller": "fixture", "paused": False,
        "ownership": {"owner": None, "state": "idle"},
        "current_observation": {"sequence": 7, "acquired_at": 99.8, "age_seconds": .2,
            "healthy": True, "fields": {name: field(value) for name, value in {
                "speed": 0, "distance": 4500, "asteroid_selected": True,
                "target_identity": "asteroid:fixture", "hull_current": 5005,
                "hull_max": 5005, "target_current_hp": 4618, "target_max_hp": 4618}.items()}},
        "assessment_context": {"token": "not-an-observation-permission", "context_key": "same-target",
            "expires_at": 114.8, "observation_sequence": 7, "wheel_point": [1280, 400]},
        "routines": {"approach_current_target": {"available": True, "ready": True,
            "active": False, "state": "canceled", "phase": "canceled", "reason": None}},
        "last_input_delivery": {"outcome": "injected"}, "last_confirmed_effect": None}
    return startup, mode, actions


def project(startup, mode, actions, **extra):
    return observation_snapshot(startup, mode, actions, collected_at=100, started_at=99.9, **extra)


def with_ship_telemetry(actions, **changes):
    value = {"display_only": True, "speed": 25.7, "hull_current": 4684, "hull_max": 5005,
             "energy_current": 671, "energy_max": 800, "reasons": {}, **changes}
    actions["current_observation"]["fields"]["ship_telemetry"] = {
        "value": value, "valid": True, "fresh": True, "observed_at": 99.8}
    return value


class ObservationProjectionTests(unittest.TestCase):
    def test_general_ship_readings_survive_missing_asteroid_and_paused_controls(self):
        startup, mode, actions = providers()
        with_ship_telemetry(actions)
        for name, field in actions["current_observation"]["fields"].items():
            if name != "ship_telemetry":
                field.update(value=None, valid=False, reason="selected_panel_anchor_missing_or_ambiguous")
        startup["action_control"].update(enabled=False, paused=True)
        actions["paused"] = True
        mode["actual_mode"] = "autopilot"
        response = project(startup, mode, actions)
        ship = response["game_state"]["ship"]
        self.assertEqual({name: row["value"] for name, row in ship.items()}, {
            "speed": 25.7, "hull_current": 4684, "hull_max": 5005,
            "energy_current": 671, "energy_max": 800})
        self.assertTrue(all(row["usable"] and row["display_only"] for row in ship.values()))
        self.assertIsNone(response["observation"]["fields"]["speed"]["value"])
        self.assertIsNone(response["observation"]["fields"]["hull_current"]["value"])
        self.assertIsNone(response["assessment_context"])
        self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])
        self.assertEqual(response["status"], "degraded")

    def test_general_ship_readings_do_not_fall_back_to_action_fields(self):
        response = project(*providers())
        self.assertEqual(response["observation"]["fields"]["speed"]["value"], 0)
        self.assertTrue(all(row["value"] is None and row["reason"] == "field_missing"
                            for row in response["game_state"]["ship"].values()))

    def test_bad_ship_instrument_preserves_others_and_requires_complete_ratios(self):
        for changes, names, reason in (
                ({"speed": float("nan")}, ("speed",), "invalid_numeric_value"),
                ({"speed": True}, ("speed",), "invalid_numeric_value"),
                ({"hull_current": None}, ("hull_current", "hull_max"), "complete_ratio_not_verified"),
                ({"energy_max": 0}, ("energy_current", "energy_max"), "nonpositive_maximum"),
                ({"energy_current": 900}, ("energy_current", "energy_max"), "current_exceeds_maximum"),
                ({"hull_max": float("inf")}, ("hull_current", "hull_max"), "invalid_numeric_value"),
                ({"reasons": {"hull": "complete_ratio_not_verified"}},
                 ("hull_current", "hull_max"), "complete_ratio_not_verified")):
            with self.subTest(changes=changes):
                startup, mode, actions = providers()
                expected = with_ship_telemetry(actions, **changes)
                response = project(startup, mode, actions)
                for name, row in response["game_state"]["ship"].items():
                    self.assertEqual(row["usable"], name not in names)
                    self.assertEqual(row["reason"], reason if name in names else None)
                    self.assertEqual(row["value"], None if name in names else expected[name])
                json.dumps(response, allow_nan=False)

    def test_general_ship_requires_current_healthy_image_and_reader_acceptance(self):
        for raw_changes, field_changes, telemetry_changes, extra, reason in (
                ({"age_seconds": 2}, {}, {}, {}, "image_stale"),
                ({"age_seconds": -1}, {}, {}, {}, "image_stale"),
                ({"age_seconds": None}, {}, {}, {}, "image_age_unknown"),
                ({"healthy": False}, {}, {}, {}, "image_unhealthy"),
                ({}, {"valid": False}, {}, {}, "recognition_rejected"),
                ({}, {"fresh": False}, {}, {}, "field_stale"),
                ({}, {"observed_at": 99.7}, {}, {}, "not_from_current_image"),
                ({"acquired_at": None}, {"observed_at": None}, {}, {}, "not_from_current_image"),
                ({}, {}, {"display_only": False}, {}, "display_telemetry_required"),
                ({}, {}, {}, {"actions_collected_at": 98}, "image_stale")):
            with self.subTest(reason=reason, raw_changes=raw_changes, field_changes=field_changes):
                startup, mode, actions = providers()
                with_ship_telemetry(actions, **telemetry_changes)
                # Display readings must never repair a rejected action speed.
                actions["current_observation"]["fields"]["speed"].update(value=None, valid=False)
                actions["current_observation"]["fields"]["ship_telemetry"].update(field_changes)
                actions["current_observation"].update(raw_changes)
                response = project(startup, mode, actions, **extra)
                self.assertTrue(all(row["value"] is None and row["reason"] == reason
                                    for row in response["game_state"]["ship"].values()))
                self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])

    def test_disconnection_and_source_errors_do_not_reuse_general_ship_values(self):
        for source in ("startup", "actions", "mode", None):
            with self.subTest(source=source):
                startup, mode, actions = providers()
                with_ship_telemetry(actions)
                errors = {source: "fixture failure"} if source else {}
                if source is None:
                    startup["phase"] = "blocked"
                response = project(startup, mode, actions, source_errors=errors)
                ship = response["game_state"]["ship"]
                self.assertEqual(all(row["usable"] for row in ship.values()), source == "mode")
                if source != "mode":
                    self.assertTrue(all(row["value"] is None for row in ship.values()))
                self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])

    def test_fresh_zero_speed_unclamped_distance_and_assessment_are_observation_only(self):
        response = project(*providers())
        self.assertEqual(response["schema_version"], "bsgo-observation-v1")
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["observation"]["fields"]["speed"]["value"], 0)
        self.assertEqual(response["observation"]["fields"]["distance"]["value"], 4500)
        self.assertEqual(response["control"]["input_owner"], "human")
        self.assertEqual(response["state_machine"]["phase"], "idle")
        self.assertTrue(response["routines"]["approach_current_target"]["machine_ready"])
        self.assertFalse(response["routines"]["approach_current_target"]["assessments_verified_by_api"])
        self.assertNotIn("token", response["assessment_context"])
        self.assertFalse(response["assessment_context"]["human_assessments_verified"])
        self.assertFalse(response["evidence"]["input_delivery_proves_game_effect"])
        self.assertFalse(response["collection"]["atomic"])
        self.assertFalse(response["collection"]["history_used"])

    def test_rejected_partial_semantic_and_wrong_image_fields_never_keep_values(self):
        startup, mode, actions = providers()
        fields = actions["current_observation"]["fields"]
        fields["target_current_hp"].update(value=12, valid=False, reason="incomplete_glyph")
        fields["distance"]["value"] = -2
        fields["speed"]["observed_at"] = 99.7
        response = project(startup, mode, actions)
        current = response["observation"]["fields"]
        self.assertEqual(response["status"], "degraded")
        self.assertEqual(response["observation"]["issue_count"], 3)
        for name in ("target_current_hp", "distance", "speed"):
            self.assertIsNone(current[name]["value"])
            self.assertFalse(current[name]["usable"])
        self.assertEqual(current["target_current_hp"]["reader_reason"], "incomplete_glyph")
        self.assertEqual(current["target_max_hp"]["value"], 4618)
        self.assertEqual(current["distance"]["reason"], "invalid_numeric_value")
        self.assertEqual(current["speed"]["reason"], "not_from_current_image")
        self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])

    def test_stale_unhealthy_missing_and_future_images_clear_readings(self):
        for changes in ({"age_seconds": 2}, {"age_seconds": -1}, {"healthy": False},
                        {"age_seconds": None}):
            with self.subTest(changes=changes):
                startup, mode, actions = providers()
                actions["current_observation"].update(changes)
                response = project(startup, mode, actions)
                self.assertFalse(response["observation"]["fresh"])
                self.assertEqual(response["observation"]["valid_for_seconds"], 0)
                self.assertIsNone(response["assessment_context"])
                self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])
                self.assertTrue(all(row["value"] is None for row in response["observation"]["fields"].values()))
        startup, mode, actions = providers()
        del actions["current_observation"]["fields"]["speed"]
        response = project(startup, mode, actions)
        self.assertEqual(response["observation"]["fields"]["speed"]["reason"], "field_missing")
        self.assertEqual(response["observation"]["issue_count"], 1)

    def test_pending_mode_waiting_toggle_and_disconnected_retained_owner(self):
        startup, mode, actions = providers()
        mode.update(pending=True, requested_mode="autopilot")
        actions["routines"]["auto_scan_selected"] = {"active": True, "state": "waiting",
            "phase": "waiting", "processing": "observing selected target", "waiting_for": "stationary ship",
            "expected_next_step": "scan selected target once", "available": True, "ready": False}
        response = project(startup, mode, actions)
        self.assertEqual(response["control"]["actual_mode"], "manual")
        self.assertEqual(response["control"]["input_owner"], "human")
        self.assertEqual(response["state_machine"]["routine"], "auto_scan_selected")
        self.assertEqual(response["state_machine"]["phase"], "waiting")
        self.assertEqual(response["state_machine"]["waiting_for"], "stationary ship")
        self.assertIn("mode_handover_pending", response["state_machine"]["blockers"])
        self.assertFalse(response["routines"]["approach_current_target"]["machine_ready"])
        startup.update(phase="blocked", reason="source_occluded")
        actions["ownership"].update(owner="approach_current_target", state="faulted")
        actions["routines"]["approach_current_target"].update(active=True, phase="faulted",
            waiting_for="proved stop", expected_next_step="human recovery")
        response = project(startup, mode, actions)
        self.assertEqual(response["control"]["input_owner"], "approach_current_target")
        self.assertEqual(response["state_machine"]["phase"], "faulted")
        self.assertFalse(response["observation"]["available"])
        self.assertIsNone(response["observation"]["fields"]["speed"]["value"])

    def test_collection_delay_expiry_and_nonfinite_values_are_honest_json(self):
        startup, mode, actions = providers()
        actions["assessment_context"]["expires_at"] = 99.99
        actions["current_observation"]["fields"]["distance"]["value"] = float("inf")
        actions["last_input_delivery"]["extra"] = float("nan")
        response = project(startup, mode, actions)
        self.assertIsNone(response["assessment_context"])
        self.assertIsNone(response["observation"]["fields"]["distance"]["value"])
        self.assertIsNone(response["evidence"]["last_input_delivery"]["extra"])
        json.dumps(response, allow_nan=False)
        response = project(*providers(), actions_collected_at=98)
        self.assertFalse(response["observation"]["fresh"])


class ObservationHttpTests(unittest.TestCase):
    def test_general_ship_http_read_preserves_human_control_while_actions_paused(self):
        with cockpit() as (fixture, _):
            before = fixture.controller.action_control_snapshot()
            fixture.row = replace(fixture.row, fields={**fixture.row.fields,
                "ship_telemetry": FieldValue({"display_only": True, "speed": 25.7,
                    "hull_current": 4684, "hull_max": 5005,
                    "energy_current": 671, "energy_max": 800, "reasons": {}},
                    fixture.row.acquired_at),
                "asteroid_selected": FieldValue(None, fixture.row.acquired_at, False, "no_asteroid")})
            code, state = fixture.http("/api/observation")
            self.assertEqual(code, 200)
            self.assertEqual(state["game_state"]["ship"]["speed"]["value"], 25.7)
            self.assertTrue(state["game_state"]["ship"]["hull_current"]["display_only"])
            self.assertFalse(state["routines"]["approach_current_target"]["machine_ready"])
            self.assertIsNone(state["assessment_context"])
            self.assertEqual(fixture.calls, [])
            self.assertEqual(fixture.controller.action_control_snapshot(), before)

    def test_get_schema_history_isolation_no_control_and_rejected_mutation(self):
        with cockpit() as (fixture, directory):
            package(directory, "recorded", finished=True)
            before = fixture.controller.action_control_snapshot()
            for _ in range(3):
                code, state = fixture.http("/api/observation")
                self.assertEqual(code, 200)
                self.assertEqual(state["observation"]["fields"]["distance"]["value"], 1000)
                self.assertEqual(state["state_machine"]["phase"], "paused")
                self.assertTrue(state["capabilities"]["read_only"])
            fixture.http("/api/status?run=recorded")
            self.assertEqual(fixture.http("/api/observation")[1]["observation"]["fields"]["distance"]["value"], 1000)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(fixture.controller.action_control_snapshot(), before)
            self.assertEqual(fixture.http("/api/observation", {"action": "start"})[0], 404)
            self.assertEqual(fixture.http("/api/observation?run=recorded")[0], 400)
            code, schema = fixture.http("/api/observation/schema")
            self.assertEqual(code, 200)
            self.assertEqual(schema, OBSERVATION_SCHEMA)
            self.assertTrue(set(schema["required"]).issubset(state))
            self.assertEqual(fixture.http("/api/observation", Host="untrusted.invalid")[0], 403)
            self.assertEqual(fixture.http("/api/observation", **{"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_unattached_never_uses_historical_observations(self):
        with scratch_directory() as directory:
            package(directory, "recorded")
            server = make_server(directory, 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/api/observation", timeout=3) as response:
                    state = json.load(response)
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(state["status"], "unavailable")
                self.assertEqual(state["control"]["input_owner"], "unknown")
                self.assertFalse(state["observation"]["available"])
                self.assertIsNone(state["observation"]["issue_count"])
                self.assertTrue(all(row["value"] is None for row in state["observation"]["fields"].values()))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_provider_failure_returns_partial_503_and_read_does_not_lock_pause(self):
        with cockpit() as (fixture, _):
            original = fixture.controller.actions_snapshot
            fixture.controller.actions_snapshot = lambda: (_ for _ in ()).throw(ValueError("fixture failure"))
            code, state = fixture.http("/api/observation")
            self.assertEqual(code, 503)
            self.assertIn("actions", state["source_errors"])
            self.assertIsNone(state["observation"]["fields"]["speed"]["value"])
            self.assertEqual(state["control"]["input_owner"], "unknown")
            fixture.controller.actions_snapshot = original
        with scratch_directory() as directory:
            entered, release, done = Event(), Event(), Event()
            class Mode:
                def snapshot(self):
                    entered.set()
                    release.wait(2)
                    return {"actual_mode": "manual"}
            class Startup:
                def startup_snapshot(self):
                    return {"phase": "connected", "action_control": {"enabled": True}}
                def pause_actions(self):
                    done.set()
                    return {"accepted": True}
            server = make_server(directory, 0, startup_adapter=Startup(), mode_controller=Mode())
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            def get():
                with urlopen(base + "/api/observation", timeout=3) as response:
                    response.read()
            reader = Thread(target=get, daemon=True)
            reader.start()
            try:
                self.assertTrue(entered.wait(1))
                request = Request(base + "/api/startup", data=b'{"action":"pause_manual_actions"}',
                    headers={"Origin": base, "Content-Type": "application/json"})
                with urlopen(request, timeout=1) as response:
                    self.assertTrue(json.load(response)["accepted"])
                self.assertTrue(done.is_set())
            finally:
                release.set()
                reader.join(3)
                server.shutdown()
                server.server_close()
                thread.join(2)


if __name__ == "__main__":
    unittest.main()
