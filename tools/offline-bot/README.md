# Offline bot experiments

This Python harness tests the mining application's observation and control boundaries without opening or controlling BSGO. It supports real OCR on saved screenshots, a synthetic receiver that consumes recorded mouse/keyboard requests, and an opt-in native Windows test against its own receiver window. Python remains the current implementation choice. Memory retains recent telemetry, while a separate disk journal records events beyond those recent windows. The synthetic receiver's motion is an invented test model, not measured game physics.

The new `simulate-mining` mode executes one complete cycle with a preselected synthetic asteroid. It uses semantic input intents, local selection tracking, retained gameplay events, pure routines, and bounded stop/pause handling. The original `simulate` mode remains the approach-only compatibility experiment. [Implementation results and limits](../../docs/architecture/mining-runtime-offline-results.md).

## Run

From the repository root, use Python 3.12 with NumPy and Pillow. The existing experiment environment is ready:

```powershell
$env:PYTHONPATH = (Resolve-Path 'tools/offline-bot').Path
$offlinePython = 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe'
& $offlinePython -m bsgo_offline replay --output logs/offline-bot/saved-frames
& $offlinePython -m bsgo_offline simulate --output logs/offline-bot/nominal
& $offlinePython -m unittest discover -s tools/offline-bot/tests -v
```

The simulation uses the Python standard library. Screenshot replay additionally needs the packages in [requirements.txt](requirements.txt); Torch, Ollama, and model weights are not needed. To use an ordinary Python installation instead of the existing experiment environment:

```powershell
python -m venv tools/offline-bot/.venv
& 'tools/offline-bot/.venv/Scripts/python.exe' -m pip install -r tools/offline-bot/requirements.txt
$offlinePython = 'tools/offline-bot/.venv/Scripts/python.exe'
```

This code uses repository files and Python APIs only. Codex does not need to remain running after a command is launched in your terminal.

## Complete synthetic mining cycle

After setting `$env:PYTHONPATH` and `$offlinePython` as above:

```powershell
& $offlinePython -m bsgo_offline simulate-mining --no-planner --output logs/offline-bot/mining-demo
& $offlinePython -m bsgo_offline simulate-mining --reward-missing --output logs/offline-bot/mining-no-reward
& $offlinePython tools/offline-bot/acceptance.py --output logs/offline-bot/mining-acceptance
& $offlinePython tools/offline-bot/run_tests.py --output logs/offline-bot/mining-tests.txt
```

Use new output directories for repeated experiments. Additional fault options are `--scan-missing`, `--ineligible`, `--mining-ignored`, `--inventory-conflict`, `--ignore-brake`, `--stall-after` and `--fault-after`. `--planner-delay` delays fake observation advice in real seconds; `--no-planner` runs deterministic control alone. All input remains recording-only. Synthetic scan/mining bindings are rejected on other surfaces.

`report.json` records transitions, track/context, action request and delivery links, gameplay events, reward evidence, source hash, journal statistics and worker shutdown. `MiningCoordinator.read_status()` returns immutable current status. `pause()` cancels work and reports its observed cleanup result; `resume()` requires a new drained backend session and fresh route/equipment preflight. The fake reward amount and physics are invented test parameters.

The new runtime observes stopping after a fault using speed alone, independently of target selection. Cleanup ends as stopped, already stopped, stop unverified or control unavailable. This behavior belongs to `simulate-mining`; legacy `simulate` retains its existing approach-only failure result.

## Saved screenshot recognition

A limited offline resource-notification reader and its manually labeled native
crops are documented in [the v3 corpus](../../test-data/vision/v3-reward-notices/README.md).
Run `evaluate_reward_notices.py` to verify and score them without game access.
This reader reports visible notice text only; it is not enabled in the default
runtime and cannot produce inventory deltas, attributed rewards or mission success.

`replay` defaults to the 25-frame `sol-tylium-live-2321` run in the [frozen corpus](../../test-data/vision/v1/README.md). `--source-run`, `--limit`, `--interval`, `--profile`, and `--consumer-delay` configure the experiment. Recognition owns its thread and publishes deeply immutable snapshots into a short-lock shared store. The delayed consumer retrieves already published state and never requests recognition.

Frames are ordered by their original capture names and ordinals, rather than hash filenames. Capture-run boundaries change the session epoch. Acquisition times belong to the synthetic replay schedule; they do not recreate original game rendering timestamps. The explicit original/restart calibration profile is not inferred from successful reads. Invalid current fields remain unknown, with OCR rejection evidence available separately. A normal finite replay preserves its last observation and marks the source exhausted; its age continues to increase. A source or recognizer exception publishes unhealthy state with unknown fields.

`replay --mining-observations` additionally exposes scan, mining activation, inventory and inventory-conflict fields as explicitly unknown. Their game-image recognizers remain unvalidated; the calibrated HUD reader still supplies its supported observations. `inventory_evidence.py` verifies the frozen corpus and produces source-group review sheets without game capture.

## Recording input and receiving it

`simulate` sends a target click at frame coordinates `(1200, 600)` to the recording backend. A separate fake receiver consumes it, selects the synthetic target, and applies PageUp/PageDown requests to its synthetic motion. Recognition publishes that receiver's resulting state independently. The coordinator uses the latest snapshots to approach, brake, and verify a later observed zero speed.

Three layers of evidence remain separate: recorded requests, receiver-reported effects, and published observations. A request or idle-throttle effect alone does not prove stopping. `report.json` contains recent records from these layers, coordinator decisions, state provenance, telemetry statistics, and shutdown results. `input.jsonl` exports recent requests and identifies their dispositions: discarded before receipt, dequeued for the receiver, or still pending. Open `receiver.html` in a browser to inspect recent click locations and the request/effect timeline; it is a self-contained local file with no server or external assets.

The same HTML file is also a manual input receiver: click its frame to focus it, then click or press/release keys. Green markers show received left clicks, and a separate live trace records frame coordinates, mouse buttons, key names/codes, repeats, and focus loss. Download received events exports this trace as JSON. It retains up to 256 events and observes only events delivered to that focused browser frame; it does not install a global keyboard hook or send input to another application. Browser event times have their own clock and are not mixed with simulation timestamps.

## Recent history and disk traces

Recent memory defaults are 64,000 entries each for input requests, native delivery records, coordinator events, and synthetic receiver effects. History eviction increments counters and leaves the routine running. The separate action queue remains bounded and can reject a genuine receiver backlog overflow; filling recent history is not backlog overflow.

`input.jsonl` is a recent-history export. Its `input.jsonl.stats.json` sidecar states `scope: recent_record_history` and `complete_lifetime_trace: false`, with lifetime and eviction statistics. Reports and HTML views also use recent windows; they should not be presented as complete long-running traces.

For events beyond those windows, the simulation and native harnesses configure [TraceJournal](bsgo_offline/trace.py) under the output directory's `trace` folder. The active package holds up to 64,000 events in JSONL segments, with an 8 MiB segment-size threshold. A full package becomes `trace-UUID-package-NNNNNN.zip`, containing its segments and a manifest with event counts, sequence bounds, and sizes. The writer closes and verifies the ZIP's entries, sizes, manifest, and CRCs, publishes it, then removes only its verified raw files. Recording continues in a fresh package. Partial packages stay as raw JSONL on shutdown; archives and earlier runs are retained. An archive failure preserves the raw files and exposes a journal error. Each request, disposition, delivery, coordinator event, or receiver effect counts as one package event.

The writer has a bounded 64,000-event backlog and performs disk I/O and compression on its own thread. `submit` does not perform filesystem calls. Full backlogs and write failures are recorded in journal statistics, with component counters for submissions lost before reaching the journal. These telemetry failures do not stop input or change routine outcomes. Shutdown waits a bounded time for logging; inspect `write_complete`, `dropped`, `pending`, `error`, and component drop counters before considering a disk trace complete. A `trace-UUID-summary.json` accompanies a finished journal when summary writing succeeds.

Reports expose `complete_event_trace` (simulation) or `complete_backend_trace` (native test), combining writer completion, producer shutdown, and component gap counters. Journal statistics retain only the latest 128 segment and archive names plus lifetime counts. `archive_count`, `archived_events`, and `active_package_events` describe package progress. Use `bsgo_offline.trace.iter_trace_events(directory, run_id)` to stream the complete history across ZIPs and active JSONL, including deduplication if raw cleanup failed after ZIP publication. A single event envelope may exceed the segment-size threshold. `unwritten` counts accepted events not written; successful output is flushed normally without a crash-durability guarantee.

The reproducible no-desktop retention soak uses a real disk journal with mocked native input:

```powershell
& $offlinePython tools/offline-bot/tests/test_history_integration.py --output logs/offline-bot/history-64000 --cycles 24000
```

It processes 72,000 ordinary requests, retains 64,000 requests and deliveries in memory, then checks hold release and the cleanup stop pair. See the [saved result](../../logs/offline-bot/history-64000/soak-report.json) and [history validation](../../docs/architecture/offline-bot-experiments.md).

The browser receiver's 256-event cap and native receiver fixture's 1,024-event cap remain limits on those test surfaces, separate from the application's rolling histories and disk trace.

## Native Windows receiver test

```powershell
& $offlinePython -m bsgo_offline.native_test --output logs/offline-bot/native --focus-timeout 120
```

This opt-in test opens its own native receiver and uses real Windows capture and `SendInput`. Leave that test window focused until it closes; if Windows refuses its initial activation, click it manually. The test verifies received click/key messages separately from stream insertion, observes pixel changes on the independent recognition thread, releases a held key on cancellation, and rejects a window resize. It uses no game window and no Codex runtime API. All ordinary replay/simulation commands remain recording-only. [Native experiment details and limits](../../docs/architecture/native-backend-experiments.md).

The fake planner delays a proposal on another thread. It can propose only observation, never flight input. On return, epoch, layout, dimensions, target signature, portrait, observation age, and expiry are checked. New snapshot sequence numbers alone do not invalidate a still-applicable proposal. No LLM or network request occurs.

The retained approach policy still uses a two-second decision cadence, while freshness and target checks run between decisions. Its per-sample closing guard would falsely report stalls if run on every high-rate recognition frame. The synthetic clock defaults to 20 times wall speed so scenarios finish quickly; recorded timestamps use that clock, and any cleanup-clock fallback is explicitly labeled.

## Fault experiments

```powershell
& $offlinePython -m bsgo_offline simulate --ignore-brake --output logs/offline-bot/ignored-brake
& $offlinePython -m bsgo_offline simulate --stall-after 8 --output logs/offline-bot/stale-state
& $offlinePython -m bsgo_offline simulate --fault-after 8 --output logs/offline-bot/capture-fault
```

An ignored brake must not produce a completed stop. Stalled publication ages out even if the latest snapshot is still marked healthy. A source failure publishes unhealthy unknown state. These faults cancel ordinary queued requests and request a bounded cleanup stop; they explicitly report that stopping was not observed.

Cancellation discards pending ordinary acceleration and clicks while preserving evidence in recent history and the configured journal. It releases holds already handed to the receiver and reserves space for an idempotent cleanup PageDown pair. A record already dequeued cannot be recalled; the receiver rechecks cancellation before applying ordinary requests. Normal commands cannot impersonate cleanup. The action queue is bounded; recent telemetry rolls over and logging failures do not prevent cleanup.

## Validation boundary

`MiningCoordinator.read_preflight(capabilities)` returns every readiness blocker
in one read-only assessment: observations, ownership, stopped speed, selection,
distance, revision, route, equipment, bindings and explicitly declared recognizer
capabilities. It never queues input or changes tracking. `MiningHudRecognizer`
declares the unimplemented ownership, revision and mining fields as unsupported.
`read_status()` separates fresh observation health from usable control fields;
unknown or stale ownership is `null`, while observed lost ownership is `false`.
These diagnostics do not override the existing execution guards.

The read-only live experiment uses the application capture and recognizer:

```powershell
$env:PYTHONPATH = (Resolve-Path 'tools/offline-bot').Path
& $offlinePython -m bsgo_offline.diagnose --hwnd <current-game-window-handle> --pid <current-game-pid> --profile restart-20261001 --seconds 6 --frames 6 --output logs/mining/<new-run>
```

Select the current game window and profile before running. The command refuses
an existing nonempty output folder. `manifest.json` is written at startup with
the configuration, Git revision and hashes of the runtime and HUD reader.
`status.json` updates during the run with worker timing, observation age, usable
fields, preflight blockers, frame backlog, action/input counts and journal loss.
The trace stores each published snapshot and observed event with acquisition
identity, plus frame retention and action lifecycle events. Captured pixels go
through a bounded background writer into `frames/`; the trace records their
SHA-256 hashes. A missing frame, queue overflow or writer failure is explicit.
Every rejected image read is queued into `failed-recognition/` instead of the
routine frame folder, bypassing the routine `max_frames` count limit. These raw,
unfiltered images are not rotated or deleted. Each has a JSON sidecar with field
rejection reasons, acquisition/context identity, the full observation including
OCR evidence, image SHA-256, run ID and source-code hashes. Native RGB frames are
saved as lossless PNG; replay images are byte-exact source copies. Unimplemented
recognizers and verified absent asteroid identity do not trigger this archive.
The queue and per-frame byte limit still bound work outside the control loop;
storage errors, overflow and unavailable pixels appear in
`frames.failed_recognition.missing` and make its `complete` flag false. Images
and sidecars remain local run artifacts and must not be committed.
`DiagnosticRun(max_frames=None)` explicitly removes the routine frame count
limit for continuous observation; the writer queue and per-frame byte bound
still apply, and `budget_skipped` remains zero unless a finite limit is set.
`report.json` records the final result, zero or more input requests, diagnostic
completeness and each worker's shutdown outcome. A complete diagnostic record
can still prove that the game control preflight failed. The diagnostic command
never starts a native input dispatcher or the mining mission.

`simulate-mining` also creates this package for its complete synthetic cycle.
Its `manifest.json`, `status.json`, `diagnostics-report.json`, frame references
and trace sit beside the existing `report.json`. Synthetic samples contain no
pixels; `pixels_unavailable` counts them explicitly. A complete event trace
requires the diagnostic writer, action history and input history to finish
without loss. Read `diagnostics-report.json` for worker and queue statistics
and each worker's shutdown result when a scenario fails.

For a repeatable startup check, run the calibration command with the current
window handle and process ID:

```powershell
& $offlinePython -m bsgo_offline.startup_diagnostics --hwnd <current-game-window-handle> --pid <current-game-pid> --profile restart-20261001 --source-size 3840x2160 --seconds 8 --frames 6 --output logs/mining/<new-startup-run>
```

It writes `startup-calibration.json` alongside the diagnostic run package.
The report counts valid and rejected readings across the captured frame sequence,
checks window identity and hashed frame retention, and lists each current
preflight blocker plus worker and queue status. It includes SHA-256 hashes of
the HUD reader, templates and calibration file used at startup. The command
sends no game input. Repeated OCR values show availability and variation;
they do not establish accuracy or native control. Use a fresh empty output
directory for each attempt. A 4K BOX conversion is experimental, so its
recognizer capability checks remain blocked for control.

For the enlarged HUD at native 2560×1440, explicitly select
`--profile largehud-20261003 --source-size native-2560`. This development
profile uses labeled HUD crops and fixed digit, decimal and separator geometry;
it rejects resampled sources. Its current calibration covers stationary
readings, including the measured tall distance six. Motion and full mining
capabilities remain unvalidated and are not granted by selecting this profile.

Recheck a saved startup package entirely offline, including the current hashes
of its retained frames:

```powershell
& $offlinePython -m bsgo_offline.startup_diagnostics --reassess logs/mining/<saved-startup-run>
```

This reads the recorded manifest, observations and frame files without opening
the game. It reports a changed or missing frame as an evidence failure and
leaves the original `startup-calibration.json` untouched.

The active command-response probe is a separate, supervised command:

```powershell
& $offlinePython -m bsgo_offline.calibrate --hwnd <current-game-window-handle> --pid <current-game-pid> --profile restart-20261001 --source-size 3840x2160 --clear-heading-assessed --output logs/mining/<new-control-run>
```

First inspect the live game, establish a clear heading and a stopped ship, and
keep other controllers idle. This probe reads two stopped images, sends PageUp,
then sends PageDown after image-observed motion or its independent 1.5-second
deadline. It requires fresh images of motion and a sustained stop to certify
speed feedback. Window/process, foreground and geometry checks remain active
at delivery. `control-calibration.json` records the outcome, linked commands,
observation sequences, source hashes and worker shutdown. Its short-lived
certificate proves only motion/braking feedback in that session; targeting,
scanning, inventory and mining still need their own calibration probes.
The native probe holds each key for at least 50 ms from confirmed insertion,
including the reserved idle press. Use `--key-hold-ms` to choose another bounded
duration (0–500 ms). Delivery timestamps record the actual dwell; shutdown and
emergency key release can interrupt it.
The game probe defaults to virtual-key encoding. `--keyboard-mode scan_code`
is available for comparison; live scan-code attempts with immediate, 50 ms and
150 ms holds inserted Windows events but produced no image-observed motion.
Virtual-key input with 50 ms holds did move and stop the ship. Its first probe
still rejected the certificate because intermediate speed readings were
ambiguous; this distinction is retained in the report.
The scoped 4K reader correction now accepts the measured complete raster
variants, with damaged-glyph checks retained. The probe does not recognize hull
health, combat or exclusive human/bot input ownership; these appear explicitly
as unverified. A supervisor must inspect the latest scene before launching it.
The later corrected run took place during combat and is excluded from clean
acceptance, despite passing its narrow speed-feedback checks.

For one supervised stationary asteroid selection click, run:

```powershell
& $offlinePython -m bsgo_offline.selection_probe --hwnd <current-game-window-handle> --pid <current-game-pid> --profile largehud-20261003 --x <asteroid-frame-x> --y <asteroid-frame-y> --safe-scene-assessed --output logs/mining/<new-selection-run>
```

The supervisor must inspect a fresh stopped, safe scene and choose the visible
asteroid point in 2560×1440 client coordinates. The probe pins the native
window and capture context, waits up to four seconds for two consecutive fresh
zero-speed images, and sends no click if that baseline fails. A temporarily
unreadable speed before input resets the count and is archived with its
rejection sidecar; observed motion or a changed capture context blocks the run.
It sends one guarded mouse click with a 50 ms settle and hold, then captures
four seconds of images. Two later, matching target-identity images after
confirmed click insertion are required to report the narrow selection effect.
Input insertion alone is insufficient, and post-click invalid speed or context
blocks the result. `selection-probe.json` includes the baseline retries,
before/after observations, delivery, shutdown and blockers. It neither moves
the ship nor sets `mining_ready`.
Under a continuous supervisor, pass `selection_probe --cancel-file <persistent-stop-marker>`.
An existing or newly created marker blocks a pending click or ends the
post-click observation with a blocked result. The marker stays on disk.

The next narrow experiment records a stationary asteroid scan:

```powershell
& $offlinePython -m bsgo_offline.scan_probe --hwnd <current-game-window-handle> --pid <current-game-pid> --profile restart-20261001 --safe-scene-assessed --output logs/mining/<new-scan-run>
```

Inspect the current scene, hull and combat indicators, confirm a stopped ship
with a selected asteroid, and keep other controllers idle before invoking it.
Use a new empty output directory and the current HWND/PID; for a 3840×2160
client, add `--source-size 3840x2160`. The probe requires two fresh, healthy
image readings of zero speed, selection, distance, target identity and target
maximum HP. It sends exactly one Z down/up pair using virtual-key encoding and
a 50 ms hold, then captures approximately eight seconds of images. Context,
target identity, maximum HP and stationary distance remain guarded. It does
not issue thrust, braking, mining or clicks. Native focus, PID and client
geometry are checked again when the input is delivered.

`scan-probe.json` links the before/after observation identities, request and
delivery records, shutdown and blockers. The diagnostic package keeps every
frame, and every rejected image reading has a JSON sidecar. A complete run
reports `scan_effect_unverified` even if Windows accepted Z: the application
does not yet classify scan-result pixels or resources. Inspect the retained
frames independently before adding a semantic scan recognizer. This probe
never sets `mining_ready`.

For a continuous supervised loop, `scan_probe --cancel-file <persistent-stop-marker>`
blocks Z when the marker already exists or appears during the baseline, and
ends post-Z observation with a blocked result if it appears later. The probe
does not remove the marker.

The supervised approach experiment uses the same independent capture and input
workers, plus the existing pure `ApproachPolicy`:

```powershell
& $offlinePython -m bsgo_offline.approach_probe --hwnd <current-game-window-handle> --pid <current-game-pid> --profile largehud-20261003 --clear-corridor-assessed --aligned-heading-assessed --safe-stopped-scene-assessed --output logs/mining/<new-approach-run>
```

For continuous supervised runs, add `--cancel-file logs/mining/live-20261003/continuous-stop.request`.
Creating that marker blocks launch if it already exists; while thrust is active,
the independent 50 ms watchdog requests reserved PageDown, then the probe
continues observing for two fresh zero-speed images. A canceled run remains
blocked even after a verified stop. The marker is never removed by the probe.

The supervisor must inspect the fresh scene and confirm full hull, no combat,
an unobstructed corridor, a heading aligned toward one already selected
asteroid, and a stopped ship. These assertions are recorded separately from
image evidence because combat, corridor clearance and heading are not yet
validated fields. Hull is also checked from fresh HUD readings below.
The native client must be exactly 2560×1440. The probe requires two fresh,
consistent stopped target readings before one PageUp press. It validates target
identity, portrait, maximum HP, context, distance and speed on every new image.
It also requires fresh numeric `hull_current` and `hull_max` readings: full hull
before acceleration, the same hull maximum throughout flight, and no damage.
Missing or invalid hull readings and observed hull loss request PageDown and
block approach proof, while cleanup still verifies two fresh zero-speed images.
Hull checks continue during coast-down after PageDown; damage or unreadable hull
there blocks proof even when the speed-only cleanup confirms a stop.
The same selected-target checks also continue through coast-down; a lost,
changed or unreadable target blocks approach proof while stop cleanup proceeds.
Reports retain the observed brake-start range/speed, every valid coast-down row,
actual brake request/delivery times and final same-target stopped range. The
observed range difference is recorded separately as `observed_braking_distance`.
These measurements support calibration; they do not estimate stopping physics.
The failed hull reading or loss remains in the diagnostic trace; the independent
watchdog records its rejection cause. Combat and obstacle detection still
require supervision.
The normal launch guard requires at least 250 m beyond the brake threshold.
For an explicitly supervised short-approach calibration, `--brake-at 800
--launch-clearance 75` allows a stopped target at 875 m or farther to launch.
Launch clearance accepts 75–250 m and defaults to 250 m; the same guard applies
both to policy authorization and actual PageUp delivery. These settings are
recorded in the report and do not establish a safe stopping distance. Full hull,
fresh images, corridor/heading assessments, cancellation and observed stop
remain required. Use five-second samples for the initial short-distance trial.
At 900 m it requests a reserved PageDown brake. An independent watchdog also
requests PageDown on stale or invalid images. Normal transit has no elapsed-time
cutoff and checks distance every 5 seconds; `--sample-seconds` accepts 5 to 10.
Freshness and observation-gap guards allow that cadence while still rejecting
missing samples. Two fresh zero-speed images after acceleration delivery block
an ignored control. `--max-thrust-seconds` optionally bounds a diagnostic trial
from 0.1 to 180 seconds. After PageDown, the probe
watches deceleration for up to 35 seconds by default; `--stop-seconds` accepts
a bounded value up to 45 seconds. This extends observation only, not thrust.
Continuous approaches retain routine frames without a total frame-count cap.
Bounded trials size their retention budget to the observation cadence and
configured windows, including cleanup headroom. It reports success only
after image-observed closure and speed, injected controls and two fresh zero
speed images after the brake delivery. A short run that stops before 900 m is
retained as a blocked, bounded segment. The gun ranges are recorded as 760 m
optimal and 800 m maximum, but this experiment does not fire, scan or mine.
`approach-probe.json` and the diagnostic package retain every frame, rejected
image sidecars, requests, deliveries, observations, shutdown and blockers.
`mining_ready` remains false even after a successful approach stop.

The explicit two-regime experiment adds `--brake-at 800
--reduced-transition-at 1300 --wheel-x <fresh-flight-area-x>
--wheel-y <fresh-flight-area-y> --flight-point-assessed` to the approach command.
It requires five-second sampling, no `--max-thrust-seconds`, a stopped target at
least 1550 m away, and the usual fresh full-hull/clear-corridor/heading evidence.
At the first fresh range at or below 1300 m, one ordinary PageDown pair reduces
thrust; exactly one upward wheel notch follows only after that pair's confirmed
native delivery. Fault or cancellation preempts the queued wheel with reserved
PageDown cleanup. The ship need not stop between the cruise and reduced phases.

Images must show deceleration after the wheel delivery and three closing
observations at 5.0 ± 0.3 m/s. Post-wheel speed increase or departure from the
proved reduced regime blocks the run and brakes. The first fresh range at or
below 800 m requests the final reserved PageDown. Proof requires two fresh zeros,
continued same-target/full-hull evidence and a final range of 700–800 m. The
report distinguishes transition requests and actual delivery from observed
speed effect. This opt-in mode still requires supervised native acceptance;
the one-notch-from-idle trial does not establish its moving transition.

To measure incremental thrust before choosing distance-based speed regimes,
use the supervised one-notch calibration probe:

```powershell
& $offlinePython -m bsgo_offline.throttle_probe --hwnd <current-game-window-handle> --pid <current-game-pid> --profile largehud-20261003 --wheel-x <fresh-flight-area-x> --wheel-y <fresh-flight-area-y> --wheel-steps 1 --clear-corridor-assessed --aligned-heading-assessed --safe-stopped-scene-assessed --flight-point-assessed --cancel-file logs/mining/live-20261003/continuous-stop.request --output logs/mining/<new-throttle-run>
```

Start stopped with full hull, one selected wanted asteroid and an independently
assessed clear heading. The wheel point must be a fresh flight-area coordinate,
clear of HUD panels. The probe sends exactly one signed notch (`1` or `-1`),
with a 50 ms pointer settle and delivery-time context checks. Direction and
throttle effect are experimental; Windows accepting the input is not proof.
Native images arrive every five seconds with no elapsed-flight or total-frame
limit. Three stable positive speed observations below full speed, with observed
range closure after wheel delivery, trigger PageDown. Full speed, 300 m of
approach travel, stale/invalid images, target/hull change or cancellation also
request cleanup and block proof. Two fresh post-delivery zeros with no movement
block an ignored or ineffective notch; the probe does not repeat it.

Cleanup verifies two fresh zero-speed images independently of a bad target or
hull reading. `throttle-probe.json` and the diagnostic package retain images,
requests, native deliveries, speed/range observations and rejection evidence.
A successful trial establishes one measured wheel effect and stop for that
scene; it does not calibrate other throttle levels or enable mining readiness.

After measuring a useful slow setting, add `--brake-at 800` for the explicit
reduced-speed transit experiment. This mode holds the single notch beyond
300 m of travel, checks every five seconds, and requests PageDown on the first
fresh range at or below 800 m. It has no elapsed-flight or total-frame cap.
Fresh baseline, native delivery dependencies, range closure, hull/target
guards and cancellation still apply. Proof requires three stable partial-speed
observations and two fresh same-target stopped readings within 700–800 m.
The report records the actual final range; this acceptance band is an experiment
setting, not a validated obstacle clearance or firing policy.

For a single supervised mining-gun feedback experiment on an already scanned
Water or Tylium asteroid, run:

```powershell
& $offlinePython -m bsgo_offline.firing_probe --hwnd <current-game-window-handle> --pid <current-game-pid> --safe-stopped-scene-assessed --wanted-resource-assessed Tylium --no-preexisting-firing-assessed --output logs/mining/<new-firing-run>
```

The supervisor must verify the resource independently, a stopped safe scene,
and no preexisting fire or HP loss. Stay with the game after the command:
**G may leave autofire active beyond the probe**, and the application has no
validated ceasefire control. The native client must be 2560×1440. The probe
requires a stable four-second image baseline with unchanged four-digit current
HP, target identity/portrait/maximum HP, and distance between 480 and 760 m.
For an independently assessed safe, stationary closer-target experiment,
`--min-distance 200` explicitly sets a 200 m lower bound. This is a calibration
setting, not a measured minimum gun range; the default remains 480 m and the
maximum defaults to 760 m. For the upgraded-gun trial, `--max-distance 800`
explicitly permits a stopped target up to 800 m. This setting accepts 760–800 m,
is recorded in the manifest and applies to both observations and the actual G
delivery guard. All stopped-image, target and delivery guards still apply.
It then sends one guarded G down/up pair with a 50 ms virtual-key hold. After
confirmed delivery, two fresh images with distinct falling current HP are
required for the narrow `firing_effect_verified` result. `--after-seconds`
can extend the observation from eight to at most 20 seconds; it does not
limit the game's firing duration. Invalid speed, HP, target or capture context
blocks the result. The diagnostic package retains frames, rejected reads,
requests, deliveries and shutdown; `firing-probe.json` explicitly leaves
autofire state, inventory and resource reward unverified, with `mining_ready`
false. Current HP OCR is validated only for measured complete four-digit
rasters; a drop below 1000 will block this finite probe until that raster is
calibrated.
Add `--cancel-file logs/mining/live-20261003/continuous-stop.request` when
running under the continuous supervisor. An existing marker blocks G; a new
marker ends the observation and releases any held input. It cannot establish
that game autofire ended, so the supervisor must remain until a safe boundary.
The marker persists for later probes.

To keep observing the same selected asteroid after the first verified HP
declines, add `--observe-until-target-gone` and a required `--cancel-file`.
This mode has no total observation time or routine frame-count cap. It sends G
only once, records the first two distinct post-delivery HP declines as an early
firing proof, then continues read-only capture while the selected target and
context remain stable. A declared unvalidated current-HP digit count after
proof is kept as unknown rather than treated as a health value; rejected frames
and sidecars remain in the package. Two fresh absent-selection images record
target disappearance, followed by two more read-only frames that may show a
reward popup. A newly validated numeric HP from 1 to 999 is accepted only
on an image acquired after the injected G key-up, even before firing proof;
two distinct declines remain required for proof. The pre-G baseline still
requires four digits. This later value remains subject to
the same target, range, portrait, context and stopped-speed guards.
Disappearance does not prove destruction or reward. A stale
image stream, changed context, bad unrelated HP reading or cancel marker ends
the run as blocked; any earlier scoped firing proof remains visible separately.
The `after` list in the report retains the most recent 256 observations and
its lifetime count, while the trace and frame package retain the full sequence.
The terminal trace event contains a short summary and a pointer to the full
report. The supervisor must remain present because G's ongoing autofire state
is still unverified when this probe exits.
If a declared HP-only OCR rejection occurs after the delivered G pair, the
continuous mode records the first blocker and switches to read-only fault
observation. It never retries G or joins HP values across the unreadable span.
Window identity, client geometry, capture context, fresh zero speed, stationary
distance and portrait still have to validate. If the target later disappears,
the report remains blocked with that first OCR cause while retaining the
absence and follow-up frames for independent review. Other invalid readings
end observation immediately.

For a game client area that is exactly 3840×2160, add `--source-size 3840x2160`.
The HUD adapter then explicitly resamples to its 2560×1440 crop coordinate
space while retaining the native frame size in snapshot context and the raw
4K pixels in the run package. The transformation and its experimental status
appear in each frame's `ocr_evidence`. Converted OCR is not yet validated for
control, so the preflight capability report continues to block those fields.

The tests cover concurrent publication, defensive immutability, per-field timestamps, rejected OCR fields, source/recognizer faults, target/session/layout changes, delayed planning, queued input cancellation, backlog overflow cleanup, ignored braking, and fresh stop acknowledgement. Rolling history and journal failure cases distinguish telemetry loss from control failure. Worker joins report whether shutdown finished; a blocked recognizer is not falsely reported stopped.

An optional browser smoke test checks the HTML receiver with Playwright and an isolated headless Chromium browser. With Playwright available to Node, run `node tools/offline-bot/verify_receiver.cjs logs/offline-bot/nominal "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"`. It saves received events, screenshots, and a verification report alongside the simulation report. It loads the local HTML file directly and closes its browser afterward.

The native receiver test validates only its owned receiver. The separate live
game calibration records bounded motion/braking and current recognition
limitations. These experiments do not establish mining approach braking
distances, asteroid acquisition, scanner/resources/rewards, three-dimensional
navigation, or model decisions. Saved-pixel OCR and the synthetic control loop
are complementary experiments; synthetic motion does not certify OCR accuracy
or gameplay safety. [Recorded offline results](../../docs/architecture/offline-bot-experiments.md).

## Mining dashboard

For normal use on Windows, double-click [Mining Dashboard.cmd](../../Mining%20Dashboard.cmd)
at the project root. The launcher opens the localhost dashboard, looks for a
single visible game window, reads its physical client dimensions and screen
origin, and configures capture for that detected size. It starts with **Manual actions
paused**. If the game is missing, duplicated, or unavailable, the
connection card explains the issue and offers **Retry game connection**.
Opening the dashboard or retrying never focuses the game or sends an input.

For a background restart while the human is playing, pass `--no-browser` to
`python -m bsgo_offline.easy_launcher --workspace C:\Projects\bsgo --port 8765`.
This starts or reuses the observer without opening a browser window. Actions
still start paused; the app remains the owner of passive game capture.

Passive observation now uses exact-HWND Windows Graphics Capture, with the
Windows packages pinned in `requirements.txt`. An overlapping cockpit window
does not replace the game's pixels with desktop content. HWND/PID, physical
client geometry and original source timestamps remain checked; minimized,
resized or stale sources require a fresh connection. Action capture and input
continue to require the original foreground and obstruction guards.

At native 2560 × 1440, the ship instruments additionally read translucent own
hull/energy ratios and the measured speed font across brightness thresholds.
`current_observation.fields.ship_telemetry` carries these fresh **display-only**
readings and rejection reasons. They do not promote the separately calibrated
fields used by scan/approach routines. Unsupported, absent or incomplete text
stays unknown; the station view does not contain flight instruments.

General instrument readings do not depend on asteroid recognition succeeding. The
`GameStateReader` reads the original captured image before any target-panel
registration. At other window sizes it registers own-ship instrument geometry
across three newer images without requiring an asteroid selection. Ship bars
and speed have separate registration and rejection evidence; a lost target or
unreadable instrument cannot erase unrelated verified ship readings. A valid
capture remains healthy when only asteroid calibration fails. Wrong profiles,
changed capture geometry and source failures still invalidate the image.

The control panel uses this independent observation telemetry while actions are paused.
`GET /api/observation` exposes each instrument under `game_state.ship`, with
`value`, `usable`, `reason`, `unit` and `display_only`. The existing
`observation.fields` retain their calibrated action semantics, and routine
readiness never falls back to display telemetry. This separation does not
validate new HUD fonts or authorize input at new window sizes.

The application is a game control panel with optional features. `GameObservationFeed`
owns the immutable `SnapshotStore` and original live-frame store. The observation
worker publishes once; indicators, decision code, and mining routines consume that
state. Mining no longer owns the public game-state projection. `GET /api/game-state`
reads the shared feed directly, even if action-status callbacks fail. The browser
polls it independently of the slower control/status requests.

The **Asteroid mining** switch enables or disables mining recognition and its
controls while capture, own-ship instruments, and the shared state remain active.
`GET /api/features` reports feature availability; same-origin
`POST /api/features` with `{"feature":"mining","enabled":false}` changes it.
Feature changes leave actions paused and discard prior assessment tokens. An
active routine must be stopped before switching. Re-enabling the feature never
resumes input. The switch is session-local and defaults on for compatibility.

In-process decision code reads `observation_feed.latest()` or the store's
`read(event_cursor)`; `game_state_view(snapshot, now)` supplies the same validated
instrument projection as the UI. HTTP consumers use `/api/game-state`, whose
`observation` includes source sequence, epoch, time, geometry, and per-field
rejection reasons. `ship` contains currently usable speed, hull, and energy.
`target` independently reports selected-card HP and range at the measured
3096 × 1704 / 120% HUD profile, including when mining is off. Asteroid title
recognition supplies its kind; other names remain unknown while readable numbers
remain available. Missing or unreadable cards report unknown, never a fabricated
no-selection state. Readings expire with their source image and grant no input.
Freshness and input authorization are separate: legacy `display_only` remains a
compatibility flag, and these observations grant no input authority. No autonomous
planner is attached to the current live session.

This establishes the core state boundary, not complete game understanding. General
target names and other layouts, weapon, and radar readers remain future work. Enabled mining recognition
still runs as an extension in the observation worker; exceptions are isolated,
but separate worker budgets would be needed to isolate a slow extension's latency.

### NPC farm: offline discovery and target return

The **NPC farm** workspace rehearses step 1 using the same bounded decision
engine intended for a future live adapter. It has its own status and simulation
controls, independently of mining. Live activation is unavailable. Simulated
targets, selection and activity stay in the rehearsal board; they never replace
the shared game-state instruments or call a native input controller.

Each pass records the initial target, requests `X`, waits for a fresh acknowledged
selection, and records targets until it returns to the initial identity. It then
chooses the closest living hostile NPC (excluding players), or retains the prior
intended NPC if still eligible. A second cycle leaves the intended target selected
and verifies it. Only then does the next random wait begin (default 2–8 seconds,
configurable 1–60). This is a wait **between complete passes**, not between X
requests. Run one pass, Start demo loop, Pause demo, and Reset rehearsal are
available. Each start initializes the selected scenario from scratch.

`npc_farm.NpcFarm.tick(observation, now)` consumes timestamped observations and
returns at most one `select_next_enemy` intent. `Observation.selection_ack` must
match the pending intent ID, with a newer image sequence and acquisition time.
The adapter must distinguish a confirmed selection response from successful key
delivery alone, including a verified single-target cycle. Target identities must
be confirmed, session-local identifiers that distinguish identical NPCs and
persist across passes; names, HP and distance are not sufficient identifiers.
The existing visual reader does **not** yet provide that guarantee. The offline
adapter supplies explicitly simulated identities and never imports native input.

Bounds are 64 selection requests and 45 seconds per pass, with a 3-second wait
for selection acknowledgment. Ambiguous identities, stale/unreadable evidence,
session changes and targets disappearing during return cannot claim success.
An unknown range blocks a new closest-target decision. Pause discards pending
intents and the countdown. Future live wiring still needs NPC/player recognition,
selection continuity, guarded X delivery, chat/focus checks and shared input
ownership arbitration. It must not reuse mining-only admission conditions.

`GET /api/npc-farm` is a read-only simulation snapshot. Same-origin JSON
`POST /api/npc-farm` accepts `{"action":"start","scenario":"normal",
"interval":[2,8],"repeat":true}`, `{"action":"pause"}`, or `{"action":"reset"}`.
Scenarios also include `empty`, `ambiguous`, `unreadable`, and `target_lost`.
The worker advances independently of browser polling and stops on server close.
These rehearsals verify decision behavior; they do not establish live recognition
or game-input acceptance.

Selected-target scanning also has an independent `stationary_scan_evidence`
reader at native 2560 × 1440. It requires complete measured zero-speed text,
full own hull, a fresh matching frame, and no contradictory calibrated reading.
This evidence authorizes scanner input only. It does not promote display-only
telemetry into action fields or enable Approach; target identity, portrait,
range, pause, ownership and foreground guards remain required. Scan delivery
still does not establish a visible scan result or resource composition.

The same scanner evidence can read complete selected-asteroid HP ratios whose
translucent separator rejects the strict navigation reader. It requires the
independently recognized asteroid title, complete digit geometry and agreement
across brightness thresholds. Two native Gemino/Exrun target crops are retained
as development regressions. The passive selection tracker can use this ratio
while the human flies; scanner input still needs a fresh zero-speed/full-hull
proof. The cockpit labels these target readings **scanner only**.

The dashboard now uses the [mode-centered cockpit](../../docs/design/cockpit-astra-20261007/selected-direction.md):
Assist and Autopilot workspaces, acknowledged active mode and input authority,
persistent Pause/Cancel, original game pixels, and a focused action panel.
Selecting a workspace never activates a controller. Assist exposes the existing
selected-target scan and approach routines, with their assessment requirements.
Visible/nearby discovery and full Autopilot remain explicitly unavailable with
the current controller. Instruments distinguish calibrated action readings
from display-only HUD readings; field failures remain visible.

The selected world-marker locator also accepts the reviewed dim Exrun label at
native brightness 100, with the original template precision and complete-letter
requirements. Missing, clipped and multiple labels remain unknown. Its retained
label crop is a development regression, not evidence of general scene discovery.

The image viewer preserves aspect ratio and colors without overlays. It keeps
verified same-session pixels while a newer frame decodes, labels retained pixels
as not current, and clears them when the capture session changes. Mutation
requests have a bounded wait; Pause remains available during a pending request.
[Implementation and validation](../../docs/architecture/mining-cockpit-ui-results.md)
describe the checks and remaining live verification.

The earlier read-only cockpit study remains available separately:

```powershell
$env:PYTHONPATH = 'tools/offline-bot'
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' tools/offline-bot/preview-cockpit.py
```

Open `http://127.0.0.1:8874/`. This preview uses the desktop width, puts the
latest native game capture above ship systems and keeps the main controls
visible at 1280 × 800 and 1920 × 1080. It includes a mobile fallback. Its server
accepts only bounded local reads and rejects every write, so reviewing the
design cannot start a game action. Use `--upstream-port` if the normal launcher
uses another port. `/api/live-frame` and its identity/version-fenced image URL
retain one latest frame in memory independently of the diagnostic archive;
capture failure leaves an explicitly aged last-known image.

Startup and Retry discover dimensions afresh. At the calibrated 2560 × 1440
size, the enlarged-HUD reader uses native pixels. Asteroid-specific recognition
at other dimensions registers the selected asteroid panel, own hull ratio and
speed decimal together. Select an asteroid and keep its HUD visible for that
recognizer: three distinct, newer captures must
agree on the regions and a supported uniform HUD scale. Existing font, spacing,
ratio and clipping checks must also pass. This supports independently relocated
regions and different aspect ratios without stretching the entire frame.
Original frames, source hashes and observation identities retain the detected
native size. Only private recognition crops are registered to the existing reader.
General ship observation has its own independent registration and does not wait
for this asteroid-specific registration to succeed.
The measured 3096x1704 display profile reads the raw 120% own-ship font without
resampling. Its numeric glyphs came from manually labeled live bars; three later
captures excluded from calibration read hull 5210/5210 and energy 772, 797, and
819 out of 827. A subsequent fresh passive run matched hull 4696/5210, speed
0.0, and energy 774, 799, 824, and 827. These are bounded live checks, not a claim
that every HUD scale is supported. Only small numeric crops are retained as
regression fixtures; no game chat or full frame is included.
The flight point is derived from that size rather than a fixed screen location.

Other dimensions currently provide **observation only**: Enable, routine Start,
focus handover and native input creation are blocked until their HUD accuracy is
validated. Registration is experimental and uses the existing enlarged-HUD fonts;
it does not learn new numeric glyphs. Missing, duplicate, corrupted or unsupported
anchors keep the affected readings unknown. Moved regions, a capture gap over two seconds,
repeated/out-of-order captures or a changed session require new agreement.
The cockpit shows dimensions and calibration status; `/api/startup` and
`/api/observation` include `game.startup_layout.calibration` progress, scale,
region boxes and validation limits. A runtime move or resize
still invalidates the pinned session. Retry creates a fresh session and re-reads
the current size; it does not reuse old scene assessments or enable actions.

The desktop shortcut **BSGO Mining Control** opens the same launcher with a
hidden shell and automatically finds or reuses this workspace's dashboard URL.
You do not need to remember a port or run a terminal command. To install or
recreate it after moving the project, run this once from the project root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/offline-bot/install-desktop-shortcut.ps1
```

The installer uses Windows' actual Desktop location, including a redirected
OneDrive Desktop, and leaves unrelated shortcuts unchanged.

Choose **Enable actions** only when ready. That button acknowledges the
exact existing mining pause for this dashboard session; it neither removes the
pause marker nor starts a scan or approach. A later pause-marker change blocks
actions again. **Pause actions** stops the selected-target scan toggle
and cancels an owned routine while preserving cleanup evidence. The optional
return-to-game setting affects only an explicit routine Start/Cancel and stays
off by default. Overall control remains Manual: each individual action still
needs current image readings and its own safety assessment.

For an observer-only historical view without a connected action controller,
use this advanced command in a separate terminal:

```powershell
$env:PYTHONPATH = 'tools/offline-bot'
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' -m bsgo_offline.observer_ui --runs-root logs/mining/live-20261003 --port 8765
```

Open `http://127.0.0.1:8765/`. The cockpit refreshes current controller data every
second; its instrument panel stays UNKNOWN without an attached live controller.
The collapsed **History** drawer follows the newest real recorded package and
lets you pin a prior or explicitly synthetic run. It shows recorded execution
phase, wait and next step, readings, input request, delivery and observed effect,
plus saved failure evidence. Images are hidden until **Show retained images**
is selected because a game frame may include private chat.
The page distinguishes a live run from a finalized, blocked, or stale package; missing
execution evidence is labeled **UNKNOWN**. The server binds only to loopback,
and accepts image paths only from the selected
run's retained frame directories. A synthetic demonstration is labeled in the
run selector and display and never becomes the automatic latest run. Stop the
observer with Ctrl+C.

The standalone observer command has no attached action controller, so its Manual
action buttons are disabled with `controller_not_attached` and it sends no game
input. The top control-mode display is independent of the selected run. It shows the
controller-acknowledged actual mode, app input and decision availability,
requested/pending mode, and the handover blocker. With this standalone observer
there is no attached game controller: an Autopilot request is rejected with
`autopilot_controller_not_attached`, while actual mode stays Manual and app input stays
disabled. A native Manual-assist session can attach an action controller; only
its explicit, readiness-checked Start requests can launch a bounded routine.
The mode indicator never launches a probe, and selecting a historical
run never changes which live target a Manual action addresses.

Manual actions expose a visible-asteroid scan pass, an auto-scan toggle for
the human-selected target, a one-shot selected-target scan, and a one-shot
approach of the current target.
The default Assist action selects visible brown rock candidates in the native
2560 x 1440 stopped flight view. Each click must yield two fresh matching
selected-asteroid HUD images and a world marker near that candidate before Z
is permitted. It skips unverified selections, targets beyond 4,000 m, and known
scanned markers. It keeps at least 100 readable energy before requesting a
scan. Fresh zero speed, full hull, capture identity and a stable coarse view
are checked at delivery. A camera/view change or unreadable ship state stops
the pass; it never turns the camera or requests movement. Stop and Pause drain
the scanner/input lease before another action can start.

One pass checks at most 32 spatial candidates or four minutes. The UI shows
selection/scan counts, resource colors when readable, skipped selections and
unverified effects. Candidate detection is a bounded color/texture proposal,
not complete scene recognition, a world identity, or proof of an unscanned
asteroid. Small, dark, occluded and HUD-covered rocks can be missed or unreadable.
Change the view manually and assess a new pass to inspect another region.
The legacy API routine name `scan_nearby_unscanned` now runs this bounded visible
pass when its native runner is attached; complete nearby/radar discovery stays
unavailable. Input delivery alone does not verify a resource classification.

The human-selected auto-scan toggle requires a fresh stopped-scene assessment
when enabled, then waits without braking or retargeting while the human moves.
It can be enabled before an asteroid is selected when a fresh healthy image
verifies no selected asteroid, zero speed and full hull. This scene token arms
only the input-free watcher; each later scan still requires a freshly verified
selected target, continuous selection track, range and stopped/full-hull guards.
The cockpit explains waiting, scanning, already attempted, and restart states,
and shows session attempt/input counts separately from observed resource color.
It attempts at most one scan per continuous selected-target track. The last
routine input evidence remains visible after it finishes; View saved run pins
the historical selector only when that diagnostic package is indexed by the
observer.
An approach requires fresh supervisor assertions for clear corridor, aligned
heading, safe stopped scene, and the separately assessed configured flight
point. The controller pins these to a short-lived target/context token and
revalidates before input. Stop/Cancel applies to the active routine; global
Manual mode means the human retains mission decisions while a routine may
temporarily own its stated input scope. Manual actions never inherit readiness
from the selected historical diagnostic package.

For nearby acquisition research, a read-only system-map contact extractor now
proposes native 2560 × 1440 marker locations. It verifies map controls, rejects
unsupported or crowded glyphs, and writes source-hashed JSON plus an overlay:

```powershell
& tools/model-runtimes/fastvlm-venv/Scripts/python.exe tools/offline-bot/discover_map_contacts.py --image SAVED_MAP.png --output logs/offline-bot/map-discovery-NEW-RUN
```

Use a new output directory. These contacts have unknown object type, distance,
scan state and resource; this command sends no game input. The chosen autoscan
route is map-contact selection followed by flight-HUD OCR verification and a
stationary scan. Map markers alone cannot enable the nearby toggle. Native
selection association, freshness while the map hides speed, and execution
acceptance remain pending. See [the mechanism and evidence](../../docs/architecture/asteroid-map-discovery.md).

For advanced pinned native startup, first stop the standalone observer on port
8765. Refresh the game's HWND and PID after every client restart, inspect the
current scene and flight point, and then launch a Manual-assist session with its
own cancellation marker:

```powershell
$env:PYTHONPATH = 'tools/offline-bot'
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' -m bsgo_offline.manual_assist_session --hwnd CURRENT_HWND --pid CURRENT_PID --profile largehud-20261003 --output logs/mining/live-20261003/manual-assist-session-001 --runs-root logs/mining/live-20261003 --pause-file logs/mining/live-20261003/continuous-stop.request --cancel-file logs/mining/live-20261003/manual-assist-session-stop.request --wheel-x ASSESSED_X --wheel-y ASSESSED_Y --flight-point-assessed --port 8765
```

Use `--flight-point-assessed` only after checking that the supplied native
client point is clear in the current scene. The existing continuous mining
pause file is respected and never removed by this command; if it remains
present, action starts are blocked. Session cancellation uses the independent
`manual-assist-session-stop.request` marker. The attached page still reports
Manual as the overall mode, with temporary routine input ownership shown
separately. Input requires the exact pinned foreground game window and a fresh
unchanged target. Optional `--allow-action-focus-handover` permits a bounded
focus request while arming, followed by fresh image revalidation; that path
remains pending native acceptance and may be blocked by a focus race. Without
the option, a non-foreground game window blocks input.

For live manual play, start the input-free passive reader in a separate terminal
with the current game window handle and PID (refresh these after a client
restart):

```powershell
$env:PYTHONPATH = 'tools/offline-bot'
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' -m bsgo_offline.passive_observer --hwnd CURRENT_HWND --pid CURRENT_PID --profile largehud-20261003 --interval 1 --output logs/mining/live-20261003/passive-observer-001 --cancel-file logs/mining/live-20261003/passive-observer-stop.request --routine-frames 64
```

It reads visible game pixels without foreground activation, input dispatch, or
mining decisions. Its `native_passive_observer` package appears in the observer
with a **PASSIVE OBSERVATION** banner, image-reading health, field rejection
reasons, and saved failure images. Stop this reader with Ctrl+C or its separate
`passive-observer-stop.request` marker. The first 64 routine frames are saved by
default; later routine frames are budget-skipped and diagnostic completeness
reflects that limit, while failed-recognition images remain retained. This
cancel marker is separate from the mining pause marker.

### Observation API for local LLM agents

`GET /api/observation` returns a versioned JSON snapshot of the same current
controller data used by the cockpit. It is read-only: polling never enables
actions, focuses the game, scans, moves the ship or asserts a safety check.
Use the **normal launcher's URL**, which may use a different port from the
standalone observer. From the project root:

```powershell
$dashboard = Get-Content 'logs/mining/dashboard-launcher/listener.json' -Raw | ConvertFrom-Json
$state = Invoke-RestMethod ($dashboard.url + 'api/observation')
$state | ConvertTo-Json -Depth 30
```

`GET /api/observation/schema` exposes its JSON Schema. Version 1 identifies
itself as `schema_version: "bsgo-observation-v1"`; consumers should check this
before interpreting the response. Both endpoints use the dashboard's existing
loopback-only listener and request guards. A local LLM tool or client running
on this computer can call them; a remote model needs such a local tool to reach
the listener. Poll about once per second while observing.

| Response member | Meaning |
| --- | --- |
| `connection` | Attachment phase, failure cause, session identity and Retry availability. |
| `control` | Acknowledged actual/requested mode, pending handover, action enable/pause and input ownership. A waiting auto-scan may be ON while input remains human-owned. |
| `observation` | Current image sequence, age, health, usable lifetime, field issues and readings with units/rejection reasons. |
| `state_machine` | Active routine, phase, processing, wait, expected next step and blockers. With no active routine this reports idle/paused/connection state. |
| `routines` | Availability, active state, machine readiness, blockers and required per-routine scene assessments. |
| `evidence` | Last input intent, stream delivery and image-confirmed effect, separately. Delivery does not prove a game effect or reward. |

Treat `observation.fields.<name>.value` as usable only when `usable` is true.
Missing, rejected, nonfinite, semantically invalid, wrong-image or stale readings
are JSON `null`, with an explicit reason. Zero speed is a valid zero; missing
speed is not zero. Unknown current target HP does not erase a usable maximum.
Distance is the actual scalar reading, including values above 3000 m; this API
does not interpret radar or infer a clear flight corridor. `recognition_valid`
and `reader_reason` preserve the reader's result separately from API usability.

In native 2560 × 1440 flight view, `selected_marker_scan_state` can report
`scanned` and `selected_scan_resource` can report `Water`, `Titanium`, `Tylium`,
or `empty`. These are current selected-texture observations, also displayed
under Scan findings while actions are paused. They require a fresh selected
asteroid, a uniquely located world label and sufficient color evidence. Unknown,
occluded, small or ambiguous textures stay null; missing color does not mean
unscanned. A finding does not prove that a particular Z press succeeded or give
resource quantity, inventory, reward, nearby discovery or mining authority.
`selection_track_id` is an ephemeral local continuity identifier, not a world
object ID. Recorded routine results remain separate from current readings.

Images become stale at two seconds. `valid_for_seconds` is the remaining usable
lifetime **at response generation**, not permission to cache a value indefinitely.
Clients should deduct time since receipt and their transport latency, then poll
again. Monotonic timestamps belong to the server process; they are not Unix time
and cannot be compared with another computer's clock. `collection` records the
collection interval and declares `atomic: false`: the providers publish
independently. Re-read before acting if mode, connection or ownership is changing.

`machine_ready` describes machine prerequisites only. It is never a Start
authorization: scene assessments remain explicit, and
`assessments_verified_by_api` is always false. An available assessment context
includes its original expiry and source sequence, but no action token. Polling
cannot renew an already checked assessment. Browser tab selection and checkbox
values are local to that browser; they are not controller state and are not
reported by this endpoint. The active controller routine and every routine's
readiness are available instead.

An unattached or disconnected controller returns `status: "unavailable"` with
null live values; it never substitutes a historical package. Provider exceptions
return HTTP **503** with the same response shape, explicit `source_errors` and
any independently available control/connection data. Read the error body rather
than retaining a previous successful snapshot. Historical diagnostics remain
at `GET /api/runs` and `GET /api/status?run=<name>`; that detail response links
retained images and failure sidecars through the existing image/sidecar APIs.
The live endpoint accepts no query parameters and no mutation requests.

After updating this server code, restart the normal dashboard to load the new
routes. A page reload alone only updates its HTML. Restart begins with Manual
actions paused, as usual.
