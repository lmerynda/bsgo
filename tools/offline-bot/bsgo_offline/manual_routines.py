"""Existing guarded probes composed as Manual building blocks, not a mission."""
import time
from pathlib import Path

from .approach_probe import run_approach_probe
from .artifacts import plain
from .diagnostics import DiagnosticRun
from .manual_assist import same_target, target_reading
from .recognition import RecognitionResult, RecognitionWorker
from .scan_probe import run_scan_probe
from .state import FieldValue, SnapshotStore
from .throttle_probe import run_throttle_probe


class DeliveryGatedBackend:
    """Keep the probe's delivery contract; additionally enforce the session stop."""
    def __init__(self, backend, may_deliver, on_delivery):
        self.backend, self.may_deliver, self.on_delivery = backend, may_deliver, on_delivery

    def __getattr__(self, name):
        return getattr(self.backend, name)

    def delivery_allowed(self, record):
        # Reserved stop/release cleanup must remain possible after cancellation.
        cleanup = dict(record.metadata).get("cleanup") is True
        return (cleanup or self.may_deliver()) and self.backend.delivery_allowed(record)

    def report_delivery(self, record, outcome):
        self.backend.report_delivery(record, outcome)
        self.on_delivery(record, outcome)


class PinnedRecognizer:
    """Reject changed target/hull before an existing probe can authorize input.

    A restarted capture has its own epoch. Window/layout/geometry remain pinned
    externally; identity, portrait, HP and optional stopped range are checked on
    every new image, without pretending the HP signature is a world identity.
    """
    def __init__(self, recognizer, target, *, baseline_distance=None, for_scan=False):
        self.recognizer, self.target = recognizer, target
        self.baseline_distance = baseline_distance
        self.images = 0
        self.for_scan = for_scan

    def __call__(self, sample):
        result = self.recognizer(sample)
        fields = dict(result.fields)
        reason = None
        geometry = getattr(sample, "geometry", None)
        window = f"{geometry.pid}:{geometry.hwnd}" if geometry else getattr(sample, "window_id", None)
        expected = self.target["context"]
        if self.for_scan:
            from .state import Snapshot
            from .stationary_scan import stationary_scan_values
            scanner_snapshot = Snapshot(sample.acquisition_sequence, sample.session_epoch,
                sample.acquisition_sequence, sample.acquired_at, time.monotonic(), sample.layout,
                result.frame_size, fields, result.healthy, result.reason, window_id=window)
            scanner = stationary_scan_values(scanner_snapshot, time.monotonic())
            if scanner:
                for name in ('hull_current','hull_max','target_identity','target_max_hp'):
                    if name in scanner and (not fields.get(name) or not fields[name].valid):
                        fields[name] = FieldValue(scanner[name], sample.acquired_at)
        if (window != expected["window_id"] or sample.layout != expected["layout"]
                or tuple(result.frame_size) != tuple(expected["frame_size"])):
            return RecognitionResult(result.frame_size,
                {name: FieldValue(None, sample.acquired_at, False, "pinned_capture_geometry_changed")
                 for name in fields}, False, "pinned_capture_geometry_changed")
        for name in ("target_identity", "target_portrait", "target_max_hp", "hull_current", "hull_max"):
            field = fields.get(name)
            if not field or not field.valid or field.observed_at != sample.acquired_at:
                reason = "pinned_target_field_unknown:" + name
                break
        if not reason:
            if (fields["target_identity"].value != self.target["target_identity"]
                    or fields["target_max_hp"].value != self.target["target_max_hp"]
                    or fields["hull_max"].value != self.target["hull_max"]
                    or fields["hull_current"].value != fields["hull_max"].value
                    or len(fields["target_portrait"].value) != 144
                    or sum(abs(a-b) for a,b in zip(fields["target_portrait"].value,
                                self.target["target_portrait"])) / 144 > .75):
                reason = "pinned_target_or_full_hull_changed"
        if not reason and self.images < 2 and self.baseline_distance is not None:
            distance = fields.get("distance")
            if (not distance or not distance.valid or distance.observed_at != sample.acquired_at
                    or abs(distance.value - self.baseline_distance) > 5):
                reason = "new_leg_stopped_range_not_continuous"
        self.images += 1
        if reason:
            return RecognitionResult(result.frame_size,
                {name: field if name == "speed" else FieldValue(None, sample.acquired_at, False, reason)
                 for name, field in fields.items()}, result.healthy, reason)
        return result


class GuardedManualRoutines:
    @property
    def supports_visible_scan(self):
        return self.frame_size == (2560, 1440)

    @property
    def supports_npc_fight(self):
        return self.frame_size == (2560, 1440)

    def __init__(self, *, source_factory, recognizer_factory, dispatcher_factory,
                 profile, window_id, frame_size, configuration=None, scope="native",
                 wheel_point=None, flight_point_assessed=False, sample_seconds=5.,
                 snapshot_provider=None, clock=time.monotonic):
        if scope not in ("native", "synthetic") or sample_seconds != 5:
            raise ValueError("guarded_manual_approach_requires_five_second_checks")
        self.source_factory, self.recognizer_factory = source_factory, recognizer_factory
        self.dispatcher_factory = dispatcher_factory
        self.profile, self.window_id, self.frame_size = profile, window_id, tuple(frame_size)
        self.configuration, self.scope = configuration or {}, scope
        self.wheel_point, self.flight_point_assessed = wheel_point, flight_point_assessed
        self.sample_seconds, self.snapshot_provider, self.clock = sample_seconds, snapshot_provider, clock

    def __call__(self, name, *, output, cancel_file, expected_target, assessment,
                 on_progress, on_delivery, may_deliver):
        output = Path(output)
        legs = []
        def dispatcher(backend, journal):
            return self.dispatcher_factory(DeliveryGatedBackend(backend, may_deliver, on_delivery), journal)
        if name == "npc_fight":
            from .npc_fight import run_npc_fight
            on_progress(output)
            guns = {"on": False}
            def fight_dispatcher(backend, journal):
                # Stop/pause still let the one G that turns the guns back off through.
                return self.dispatcher_factory(DeliveryGatedBackend(backend,
                    lambda: may_deliver() or guns["on"], on_delivery), journal)
            return run_npc_fight(output, dispatcher_factory=fight_dispatcher, configuration=self.configuration,
                profile=self.profile, frame_size=self.frame_size, cancel_file=cancel_file, scope=self.scope,
                guns_state=guns)
        if name == "scan_nearby_unscanned":
            from .visible_scan_probe import run_visible_scan_pass
            on_progress(output)
            return run_visible_scan_pass(output, source_factory=lambda: self.source_factory(.5),
                recognizer_factory=self.recognizer_factory, dispatcher_factory=dispatcher,
                profile=self.profile, expected_window_id=self.window_id, frame_size=self.frame_size,
                expected_scene=expected_target, configuration=self.configuration, scope=self.scope,
                safe_scene_assessed=assessment.get('safe_stopped_scene') is True, cancel_file=cancel_file)
        def common(directory, interval, baseline_distance):
            on_progress(directory)
            return {"source_factory": lambda: self.source_factory(interval),
                "recognizer_factory": lambda: PinnedRecognizer(self.recognizer_factory(), expected_target,
                                                               baseline_distance=baseline_distance, for_scan=name=='scan_selected'),
                "dispatcher_factory": dispatcher,
                "configuration": {**self.configuration, "manual_assist": True,
                    "selected_target_pin": {key: plain(expected_target[key]) for key in
                        ("context", "sequence", "acquired_at", "target_identity", "target_max_hp")},
                    "supervisor_assessment": assessment},
                "profile": self.profile, "frame_size": self.frame_size,
                "expected_window_id": self.window_id, "scope": self.scope,
                "cancel_file": cancel_file}
        if name == "scan_selected":
            result = run_scan_probe(output.with_name(output.name + "-scan"),
                **common(output.with_name(output.name + "-scan"), .5, expected_target["distance"]),
                safe_scene_assessed=assessment.get("safe_stopped_scene") is True)
            return {**result, "manual_assist": True, "routine": name}
        if name != "approach_current_target":
            return {"state": "blocked", "reason": "nearby_target_discovery_not_available",
                    "shutdown": {key: True for key in ("input_idle", "dispatcher", "recognition", "source")}}
        if expected_target["distance"] <= 800:
            path = output.with_name(output.name + "-stopped-verification")
            on_progress(path)
            return self._verify_already_stopped(path, cancel_file, expected_target, may_deliver)
        if (self.wheel_point is None or len(self.wheel_point) != 2
                or any(type(value) is not int for value in self.wheel_point)
                or not 0 <= self.wheel_point[0] < self.frame_size[0]
                or not 0 <= self.wheel_point[1] < self.frame_size[1]
                or assessment.get("flight_point") is not True):
            return {"state": "blocked", "reason": "assessed_reduced_speed_wheel_point_required",
                    "shutdown": {key: True for key in ("input_idle", "dispatcher", "recognition", "source")}}
        stopped_distance = expected_target["distance"]
        if stopped_distance >= 1550:
            path = output.with_name(output.name + "-cruise")
            cruise = run_approach_probe(path, **common(path, 5., stopped_distance),
                clear_corridor_assessed=assessment.get("clear_corridor") is True,
                aligned_heading_assessed=assessment.get("aligned_heading") is True,
                safe_stopped_scene_assessed=assessment.get("safe_stopped_scene") is True,
                brake_at=1300., max_thrust_seconds=None, sample_interval=5., decision_interval=5.,
                baseline_seconds=12., stop_seconds=35.)
            legs.append(cruise)
            if cruise["state"] != "proved" or not cruise.get("final_stop_observed"):
                return self._result(legs, "blocked", cruise["reason"])
            stopped_distance = cruise["final_stop_distance"]
            # Do not carry target identity across a new capture merely because
            # a previous probe returned success. Its next baseline is range-
            # pinned too; cancellation blocks the next leg before any input.
            if not may_deliver() or Path(cancel_file).exists():
                return self._result(legs, "blocked", "canceled_between_approach_legs")
        if stopped_distance <= 800:
            return self._result(legs, "recorded", "already_in_range_after_cruise")
        path = output.with_name(output.name + "-slow")
        slow = run_throttle_probe(path, **common(path, 5., stopped_distance),
            wheel_point=self.wheel_point, wheel_steps=1,
            clear_corridor_assessed=assessment.get("clear_corridor") is True,
            aligned_heading_assessed=assessment.get("aligned_heading") is True,
            safe_stopped_scene_assessed=assessment.get("safe_stopped_scene") is True,
            flight_point_assessed=assessment.get("flight_point") is True,
            sample_interval=5., baseline_seconds=12., stop_seconds=35., brake_at=800.)
        legs.append(slow)
        return self._result(legs, slow["state"], slow["reason"])

    @staticmethod
    def _result(legs, state, reason):
        shutdown_names = {key for leg in legs for key in leg.get("shutdown", {})}
        shutdown = {name: all(leg["shutdown"][name] is True for leg in legs if name in leg.get("shutdown", {}))
                    for name in shutdown_names}
        return {"state": state, "reason": reason, "legs": legs, "shutdown": shutdown,
            "complete_diagnostics": all(leg.get("complete_diagnostics") is True for leg in legs),
            "final_stop_observed": legs[-1].get("final_stop_observed", False) if legs else False,
            "final_stop_distance": legs[-1].get("final_stop_distance") if legs else None,
            "mining_ready": False,
            "native_composed_approach_acceptance": "pending"}

    def _verify_already_stopped(self, path, cancel_file, target, may_deliver):
        """Two newly acquired, consistent zero images; no input object exists."""
        diagnostic = DiagnosticRun(path, mode=self.scope + "_manual_stopped_verification",
            configuration={**self.configuration, "maximum_image_age": 2.,
                "input_disabled": True, "manual_assist": True}, max_frames=8)
        store = SnapshotStore()
        source = worker = None
        rows = []
        reason, error = "stopped_verification_timeout", None
        shutdown = {"input_idle": True, "dispatcher": True}
        try:
            source = self.source_factory(.5)
            worker = RecognitionWorker(source, PinnedRecognizer(self.recognizer_factory(), target,
                baseline_distance=target["distance"]), store, diagnostics=diagnostic)
            diagnostic.attach(worker=worker, store=store, coordinator=None, backend=None)
            diagnostic.update_execution(phase="baseline", processing="verifying two fresh stopped images",
                waiting_for="unchanged selected target, full hull and zero speed",
                expected_next_step="report already in range without requesting any input")
            worker.start()
            deadline, sequence, first = self.clock() + 3., 0, None
            while self.clock() < deadline:
                if not may_deliver() or Path(cancel_file).exists():
                    reason = "canceled_before_stopped_verification"
                    break
                snapshot = store.wait_for_sequence(sequence, .05)
                if snapshot is not None:
                    sequence = snapshot.sequence
                    reading, invalid = target_reading(snapshot, self.clock())
                    if invalid:
                        reason = invalid
                        break
                    if reading["distance"] > 800 or abs(reading["distance"] - target["distance"]) > 5:
                        reason = "stopped_range_not_continuous_or_outside_800"
                        break
                    # Capture epoch is new, but two verification images must
                    # share their own context and increasing acquisition times.
                    if first and (not same_target(first, reading)
                            or reading["acquired_at"] <= first["acquired_at"]):
                        reason = "stopped_images_not_consistent"
                        break
                    first = reading
                    rows.append({"sequence": sequence, "acquired_at": snapshot.acquired_at,
                                 "distance": reading["distance"], "speed": 0})
                    if len(rows) == 2:
                        reason = "already_in_range"
                        break
                if worker.end_reason:
                    reason = worker.error or worker.end_reason
                    break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            reason = error
        finally:
            if worker:
                worker.stop()
            shutdown["recognition"] = worker.join(2.) if worker else True
            shutdown["source"] = shutdown["recognition"]
            if source and shutdown["recognition"] and callable(getattr(source, "close", None)):
                try:
                    source.close()
                except Exception as exc:
                    shutdown["source"] = False
                    error = str(exc)
        result = {
            "state": "recorded" if reason == "already_in_range" else "blocked", "reason": reason,
            "final_stop_observed": reason == "already_in_range",
            "final_stop_distance": rows[-1]["distance"] if reason == "already_in_range" else None,
            "stopped_images": rows, "input_requests": [], "mining_ready": False}
        report = diagnostic.close(shutdown=shutdown, error=error, result=result)
        return {**result, "shutdown": report["shutdown"], "complete_diagnostics": report["complete_diagnostics"],
            "error": error, "report_path": str(Path(path) / "report.json")}
