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

For a game client area that is exactly 3840×2160, add `--source-size 3840x2160`.
The HUD adapter then explicitly resamples to its 2560×1440 crop coordinate
space while retaining the native frame size in snapshot context and the raw
4K pixels in the run package. The transformation and its experimental status
appear in each frame's `ocr_evidence`. Converted OCR is not yet validated for
control, so the preflight capability report continues to block those fields.

The tests cover concurrent publication, defensive immutability, per-field timestamps, rejected OCR fields, source/recognizer faults, target/session/layout changes, delayed planning, queued input cancellation, backlog overflow cleanup, ignored braking, and fresh stop acknowledgement. Rolling history and journal failure cases distinguish telemetry loss from control failure. Worker joins report whether shutdown finished; a blocked recognizer is not falsely reported stopped.

An optional browser smoke test checks the HTML receiver with Playwright and an isolated headless Chromium browser. With Playwright available to Node, run `node tools/offline-bot/verify_receiver.cjs logs/offline-bot/nominal "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"`. It saves received events, screenshots, and a verification report alongside the simulation report. It loads the local HTML file directly and closes its browser afterward.

The native test validates only its owned receiver; BSGO capture/input compatibility remains unverified. No mode establishes real braking distances, asteroid acquisition, scanner/resources/rewards, three-dimensional navigation, or model decisions. Saved-pixel OCR and the synthetic control loop are complementary experiments; synthetic motion does not certify OCR accuracy or gameplay safety. [Recorded offline results](../../docs/architecture/offline-bot-experiments.md).
