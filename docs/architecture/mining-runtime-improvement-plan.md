# Mining runtime improvement plan

This plan turns the existing approach experiment into an extensible mining runtime. It is written for an implementer who has not participated in the architecture discussion. Complete the numbered tasks in order, preserve existing behavior during extraction, and use the acceptance checks to decide whether a task is finished.

Status: offline runtime implemented and verified, with perception work continued October 7, 2026. Source inspection baseline: commit `6b441eba`. Tasks 1 through 8 are complete offline. Task 9 now includes a limited offline resource-notification reader, source-linked native crop evaluation, and live display-only ship HP/speed/energy reads. Passive exact-window capture and a separate desktop cockpit preview are implemented; mission-ready mining perception remains incomplete. Task 10 remains deferred for the full native mission. [Offline results](mining-runtime-offline-results.md) and [subsequent live probe results](mining-runtime-live-results.md) record the distinct acceptance boundaries.

## Current task status

| Task | Status | Implementation and evidence |
| --- | --- | --- |
| 1 Baseline | `offline_verified` | 159 original tests passed; nominal, ignored-brake and 25-frame replay reports retained under `logs/offline-bot/plan-baseline-*` |
| 2 Context and selection | `offline_verified` | `state.py`, `tracking.py`, recognition provenance, and local-track planner dependencies; loss/reacquisition and context-change tests |
| 3 Observed events | `offline_verified` | `events.py` and atomic store views; cursors, deduplication, inventory-conflict events, overflow and concurrent publication tests |
| 4 Routines and execution | `offline_verified` | `routines.py`, `execution.py`; original coordinator uses the approach adapter; generation, expiry, deduplication and final native dispatch guards tested |
| 5 Stop and pause | `offline_verified` | New mining coordinator observes bounded cleanup independently of target selection; pause/resume and already-dequeued native key release tested |
| 6 Synthetic mission | `offline_verified` | `mission.py`, `mining_simulation.py`; one preselected-target cycle, resource eligibility, activation, depletion candidate and reward confirmation |
| 7 Optional advice | `offline_verified` | `planning.py`; bounded fake advice, cancellation, dependency checks, no-op recording and deterministic continuation |
| 8 Status and acceptance | `offline_verified` | `simulate-mining`, immutable status, report/source identity, journal traces; 216 tests and 11 threaded acceptance scenarios passed |
| 9 Saved mining recognition | `in_progress` | Frozen v1/v2 preserved. Limited `reward_notice` reader tested on 17 reviewed native crops from three firing runs; separate-run evaluation: one correct accept, no wrong accepts, one rejected visible notice and three absent rejects. Resource/amount text remains separate from inventory and causal reward events. Scan semantics, firing state, inventory and attribution remain incomplete. |
| 10 BSGO acceptance | `planned` and deferred for full mission | Subsequent supervised capture/input/scan/approach/firing probes have partial live evidence in the live report; autonomous native mining-cycle integration and perception acceptance remain open. |

Implementation is committed incrementally. The implementation steps below remain the specification and handoff checklist; completion labels apply only within their recorded scope. The existing approach-only `simulate` command preserves its legacy failure reporting, while `simulate-mining` uses the new bounded recovery phases.

## Scope and completion boundaries

The first deliverable is one complete simulated cycle with a preselected target: assess scan information, approach, verify stopping, mine, and confirm the outcome. It must also terminate accurately on cancellation, stale observations, changed selection, and missing effects. Synthetic success establishes control orchestration only.

The later live deliverable is the same cycle in a known operating area with a supported window layout and equipment configuration. General target discovery, free navigation, combat, automatic relocation, and unattended recovery across reconnects are later extensions.

Keep Python, the independent recognition worker, immutable snapshots, the existing input backends, and the asynchronous diagnostic journal. Do not add a database, remote service, behavior-tree framework, new model runtime, or language migration for these tasks. Do not rename the `bsgo_offline` package during this work; a packaging change can follow once behavior is stable.

## Read before implementing

All code paths below are relative to `tools/offline-bot/bsgo_offline/` unless stated otherwise. New paths are proposed files, not existing components.

| Existing file | What to preserve or understand |
| --- | --- |
| `state.py` | Deeply immutable `FieldValue` and `Snapshot`; short-lock `SnapshotStore` publication |
| `recognition.py` | Recognition owns its worker and does not call the coordinator; failures publish unknown observations |
| `policy.py` | Pure `ApproachPolicy`, current limits, two-second decision cadence, observed stop requirements |
| `coordinator.py` | Approach-specific orchestration, proposal validation, cancellation, and current fault behavior |
| `input.py` | Recording-only requests, bounded pending queue, reserved cleanup capacity, cancellation and rolling history |
| `native_input.py` and `native_capture.py` | Foreground and geometry guards; native dispatch is different from observed game effects |
| `simulation.py` | Independent synthetic receiver and invented motion model |
| `__main__.py` | Existing `simulate` and `replay` entry points and shutdown/report behavior |
| `trace.py` and `artifacts.py` | Nonblocking diagnostic submission, bounded memory, archived traces and reports |
| `../tests/` | Existing policy, concurrency, input, native mock, history and integration regressions |

Read [the architecture](standalone-mining-app.md), [offline results](offline-bot-experiments.md), [native limits](native-backend-experiments.md), and the corpus limitations (deleted). Before a future live task, also read the current project ship-control and mining skills. Do not copy stale key mappings or equipment assumptions out of old logs.

## Target responsibilities

The observation path captures and recognizes frames, then publishes observations and short-lived observed events. A deterministic interpretation component maintains local selection continuity. The mission coordinator owns routine progress and validates requests. A single input executor maps accepted intents to backend calls. A planner can propose choices but cannot issue input. A recorder and status interface consume state without driving control.

Logical components do not each require a thread. Retain the recognition worker, coordinator loop, existing native dispatcher where needed, and journal writer. Planner work must be independent and bounded. No capture, disk write, network call, or long input hold may execute while holding the snapshot publication lock.

Three kinds of state remain separate:

- Observations: values read from evidence, including validity and source time.
- Interpretation: local selection track and uncertainty, derived from observations and explicit selection/takeover events.
- Execution: current routine, pending action, cancellation, deadline, and result.

## Shared contracts

Introduce only the types required by each task. Use frozen dataclasses and enums for published contracts. Do not replace the working snapshot with an unrelated schema in one step.

| Contract | Required content and rules |
| --- | --- |
| `ObservationContext` | Session epoch, window identity when available, layout/profile, frame dimensions, and an ownership generation. The synthetic backend supplies explicit synthetic identities. A change invalidates dependent ordinary work. |
| `SelectionTrack` | Local monotonically increasing track ID, context, establishing evidence, last evidence time, and continuity status `confirmed`, `uncertain`, or `lost`. This is a local tracking hypothesis, never a world object ID. |
| `ObservedEvent` | Sequence, context, source acquisition sequence, observation time, kind, immutable payload, and selection track when relevant. Receipt time must not replace source time. |
| `EventBatch` | Events after a cursor, next cursor, oldest retained sequence, and explicit gap indicator. Publish events and the snapshot/event watermark consistently. |
| `RoutineUpdate` | Status `running`, `succeeded`, `failed`, or `cancelled`; stable reason code; immutable progress; zero or more semantic intents. A routine does no I/O. |
| `ActionIntent` | Unique action ID, routine generation, context, selection dependency when needed, semantic operation, creation time, expiry, and immutable parameters. Initially permit only operations used by the implemented routines. |
| `ActionStatus` | Requested, dispatched, rejected, expired, or cancelled; backend request sequence linkage and times. Observed completion is a separate routine result tied to source evidence. |
| `RuntimeStatus` | Mission phase, routine phase, observation age/health, ownership, last reason, pending action IDs, and cleanup result. It must distinguish paused with observed stop from paused with stop unverified. |

All control timestamps use one injected monotonic clock. Test fixtures must not compare simulated timestamps with real wall-clock timestamps. Real worker joins may use bounded real durations, but must identify them separately from simulated control deadlines. UTC is for human-readable records only.

## Task 1 Establish the offline baseline

Dependencies: none.

1. Inspect repository instructions and working-tree changes. Preserve unrelated edits.
2. Read the existing test discovery files before running them. Confirm native tests use fake APIs and do not open windows or inject input. Do not invoke `native_test.py` or any opt-in native receiver session while execution is unavailable.
3. Run the existing offline suite and retain a short baseline result. If a failure exists before edits, reproduce and record it; do not silently relax an assertion.
4. Save a nominal simulation report and an ignored-brake report in new run directories. These are regression references, not measured game behavior.

Acceptance: existing test status and report locations recorded; no game access or model request; no change to policy constants. If the environment cannot run even offline tests, continue source work but mark all execution checks unverified.

## Task 2 Add context and selection continuity

Dependencies: Task 1. Files: extend `state.py`; add `tracking.py`; adapt recognition/synthetic fixtures and proposal validation. Add `test_tracking.py`.

1. Add backward-compatible provenance needed for context and source-frame correlation. Record capture start/completion where the backend exposes them; keep actual frame production time optional. Do not invent a frame timestamp from an acquisition sequence.
2. Implement a pure `SelectionTracker.update(observation, control_events)` owned by the coordinator's interpretation stage. It runs before routine decisions. Recognition continues to publish raw signature evidence only.
3. Start a new local track on initial supported selection. End or invalidate the previous track on explicit reselection, loss of selection, session/layout change, takeover, impossible continuity, or an observation gap beyond configured limits.
4. Do not merge an old track into a new one just because HP and portrait match. Unchanged signatures support continuity only within uninterrupted valid observation. Identical objects that switch without observable evidence remain an explicitly documented limitation.
5. Replace planner/routine dependencies on HP-derived identity with the local track plus context. Keep signatures as supporting observations and preserve existing approach checks.

Acceptance cases: continuously observed target preserves a track; target A lost then visually identical B produces a new track; explicit reselection invalidates pending work; session/layout/takeover changes invalidate work; uncertainty blocks new target-dependent actions; newer snapshot sequence alone does not invalidate a proposal. No claim of persistent world identity appears in reports.

## Task 3 Add a bounded observed event stream

Dependencies: Task 2. Files: add `events.py`; extend publication in `state.py` and `recognition.py`; add `test_events.py`.

1. Extend the store with an atomic read view containing latest snapshot, immutable retained event batch, and watermark. Keep `latest()` compatibility for existing consumers. One producer publishes frame observations and their events consistently.
2. Use a bounded deque and independent consumer cursors. Assign event IDs once when evidence transitions into an event; repeatedly seeing the same scan panel must not produce new scan results every frame.
3. Implement gap detection: if a requested cursor predates retained history, return a gap explicitly. Mission logic must reacquire needed evidence or fail that phase; it cannot silently count missing rewards.
4. Keep this stream separate from `TraceJournal`. Diagnostic loss does not become gameplay event loss, and successful disk recording does not repair an observation gap.
5. Define initial event kinds for scan result, selected-target disappearance, and resource change. Unknown or malformed evidence produces no successful gameplay event.

Acceptance: a transient event between coordinator reads is retained; duplicate visible evidence counts once; consumer overflow reports a gap; a context change prevents old events satisfying new work; publication never pairs a newer event watermark with an older snapshot. Rejected recognition and journal failure do not create successful events.

## Task 4 Extract routines and semantic input intents

Dependencies: Tasks 2 and 3. Files: add `routines.py`, `execution.py`; refactor `coordinator.py`; wrap `policy.py` without changing its decisions. Add `test_routines.py` and `test_execution.py`.

1. Define routine operations `start(context, now)`, `update(view, action_statuses, now)`, and `cancel(reason, now)`. They return `RoutineUpdate` promptly and never sleep, capture, inject input, or call a model.
2. Implement `ApproachRoutine` as an adapter around the existing policy. Continue validating freshness between its two-second decisions. Preserve all current numeric defaults and policy tests.
3. Move key mapping into `execution.py`. Initial semantic operations are maximum throttle and idle throttle. Later operations require an explicit configured binding and their own evidence checks.
4. The coordinator validates each intent's context, generation, expiry, selection dependency, and required fields. The executor serializes requests and returns statuses linked to backend records. Deduplicate a submitted action ID so repeated ticks cannot press the same key repeatedly.
5. Ordinary queued commands must be revalidated immediately before delivery, including after a dispatcher delay. Extend backend metadata/dispatch guards as necessary; coordinator-only validation cannot authorize indefinitely queued work. Keep existing cancellation checks and cleanup priority.
6. Preserve public `Coordinator` behavior through a compatibility wrapper while tests and entry points migrate. Avoid maintaining two copies of the approach policy.

Acceptance: old nominal approach and failure cases still pass; routine tests need no threads or OS; duplicate action ID submits once; expired or obsolete queued commands never reach the fake receiver; cancel racing submission cannot emit later ordinary acceleration; backend delivery is never labeled observed completion.

## Task 5 Implement bounded stopping and pause handling

Dependencies: Task 4. Files: `routines.py`, `coordinator.py`, `execution.py`, existing input backends; add `test_recovery.py`.

1. Replace immediate fault return with coordinator phases `active`, `stopping`, and `terminal`. Fault or pause cancels ordinary work, invalidates its generation, and retains recognition while cleanup is evaluated.
2. Request the existing owned cleanup stop through its reserved path at most once per cleanup attempt. Do not reopen ordinary input merely to brake a cancelled backend. Preserve key-release behavior and ownership guards.
3. Use a separate stop observer requiring valid fresh speed and compatible window/session/layout context. It must not require an asteroid to remain selected. A target-loss fault must still be able to observe stopping.
4. For cleanup after motion or a stop request, require a speed observation acquired after the cleanup request. A historic zero, dispatch acknowledgment, reused snapshot, or invalid speed does not complete cleanup. A stationary pause before any movement can report the distinct outcome `already_stopped` using valid current evidence.
5. Add a bounded configurable cleanup deadline. Return `stopped`, `stop_unverified`, or `control_unavailable`, preserving the original fault reason. Default offline deadline: 30 control seconds; this is a test/configuration choice, not a live braking guarantee.
6. If ownership is lost, prohibit new ordinary input and use only cleanup permitted by the verified backend contract. Do not steal focus or promise a stop. Resume requires reacquisition and a new execution generation; never revive old queued actions.

Acceptance: target lost but speed readable allows observed cleanup; ignored braking ends unverified; stalled recognition reaches the deadline; cancellation during approach releases holds; lost ownership prevents ordinary input; paused status accurately distinguishes observed stop; a fault remains a failed mission even when cleanup succeeds. Shutdown reports every worker join outcome.

## Task 6 Build the synthetic mining mission

Dependencies: Task 5. Files: add `mission.py`; extend `routines.py`, `simulation.py`, and CLI; add `test_mission.py`.

1. Implement phases `preflight`, `scan`, `assess`, `approach`, `verify_stop`, `mine`, `confirm_outcome`, and terminal success/failure/cancellation. Each phase has required observations, a deadline, a reason code, and a defined recovery path.
2. Preflight requires an explicitly preselected target, compatible context, fresh zero speed, configured equipment, and an explicit route assessment for that context. Synthetic route evidence is marked synthetic. Do not infer a clear corridor from distance alone.
3. Extend the synthetic receiver to model scan completion, resource eligibility, mining activation, target depletion, and a resource increment. Give effects independent delays and fault switches. Keep all simulated values labeled invented.
4. Add scan and mining semantic intents with fake bindings for simulation. Missing live bindings must reject live execution rather than borrow synthetic controls.
5. Treat unsupported or empty resource results as an explicit ineligible outcome. Do not proceed to approach or mining. Lost target alone does not prove successful mining.
6. Outcome confirmation requires a fresh resource-change event after mining began, in the same context, with no relevant event gap. Record the observed delta. Conflicting inventory activity makes attribution uncertain; fail confirmation rather than manufacture a reward.
7. After one confirmed cycle, terminate. Automatic target acquisition and repeating patrols are outside this milestone.

Acceptance: one complete cycle succeeds from synthetic observations; zero input after mission termination; no scan result times out; ineligible resource prevents approach; lost selection prevents mining; disappearance without resource evidence fails confirmation; duplicate reward evidence counts once; unrelated or old resource event cannot complete the mission; every active phase supports cancellation and bounded cleanup.

## Task 7 Make delayed advice reusable and optional

Dependencies: Task 6. Files: extract `planning.py` from `coordinator.py`; adapt tests. No model server is required.

1. Define a planner port with request, poll, cancel, and bounded close. Keep a fake delayed implementation. Permit at most one outstanding request and tag every result with request ID and execution generation.
2. A proposal carries relevant context, track, evidence times, expiry, and a bounded allowed decision. Initially allow a choice among coordinator-supplied eligible candidates or an observe/no-op decision; do not accept arbitrary keypresses or executable text.
3. Revalidate relevant dependencies on arrival. Allow unrelated snapshot advancement. Reject late results after cancellation, session change, selection change, or expiry.
4. Define deterministic operation with no planner. Missing, malformed, failed, or timed-out advice must not stall recognition, cleanup, or the synthetic mission.

Acceptance: recognition advances during delayed advice; slow advice cannot postpone braking; late results do not affect a new mission; planner absent gives a complete deterministic cycle. Ollama integration is deferred until a concrete decision problem and labeled scenarios justify it.

## Task 8 Expose status and produce offline acceptance evidence

Dependencies: Task 7. Files: CLI, `artifacts.py`, runtime status contracts, README, integration tests.

1. Add a distinct recording-only `simulate-mining` mode while preserving existing `simulate` and `replay`. Do not silently select a native backend when a synthetic source is unavailable.
2. Export mission/routine transitions, context and track IDs, action request/disposition links, observed completion evidence, cleanup result, event gaps, journal loss, and shutdown outcomes. Include code revision and configuration in the report.
3. Expose immutable status through a callable read method and CLI/report output. Start/pause/resume requests enter the coordinator; the status UI must not issue backend commands directly. A graphical UI is optional later work.
4. Run the acceptance matrix below. Update this plan with actual status and evidence paths, retaining the distinction between implemented, tested offline, and tested in BSGO.

Acceptance: a reader can reconstruct why the mission advanced or failed; report says synthetic physics; every test-owned worker exits or reports a bounded shutdown failure; legacy replay and simulation remain usable. No game access, model download, or native injection is needed for this milestone.

## Offline acceptance matrix

Use deterministic clocks and fake backends for state transitions. Keep a small number of threaded integration tests for independent recognition, publication, dispatch cancellation, and shutdown. Avoid real sleeps in pure routine tests.

| Scenario | Required observable result |
| --- | --- |
| Nominal cycle | One reward confirmation and successful terminal state |
| Recognition stops publishing | Freshness failure, bounded cleanup, no stale completion |
| Target disappears during approach | Ordinary work cancelled; stopping can use speed alone |
| Identical-looking target reacquired | New track; old proposal/action rejected |
| Window, session, layout or ownership changes | Dependent input rejected; reacquisition needed |
| Planner delays or fails | Observation/control continue; no deadline extension |
| Cancellation before dispatch | Pending ordinary actions are discarded |
| Input reports dispatch but no effect | Routine waits or fails; no claimed completion |
| Brake ignored | Deadline reached with stop unverified |
| Old zero-speed frame reused | Does not confirm cleanup after motion |
| Event stream overflows | Gap reported; outcome cannot be silently confirmed |
| Journal queue fills or disk fails | Control continues; diagnostic loss reported |
| Repeated update on same evidence | No duplicated input, scan result or reward |
| Pause then resume | Fresh preflight, new generation, no old input revived |
| Worker refuses to join | Shutdown marked incomplete; no claim all workers stopped |

Baseline commands from the repository root follow. These are execution instructions for future implementation, not evidence that they ran when this plan was written. Confirm offline-only test behavior in Task 1 first. If the existing interpreter is missing, use the documented environment setup in the offline-bot README; do not launch model tooling just to obtain Python.

```powershell
$env:PYTHONPATH = (Resolve-Path 'tools/offline-bot').Path
$offlinePython = 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe'
& $offlinePython -m unittest discover -s tools/offline-bot/tests -v
& $offlinePython -m bsgo_offline simulate --output logs/offline-bot/plan-baseline-nominal
& $offlinePython -m bsgo_offline simulate --ignore-brake --output logs/offline-bot/plan-baseline-no-brake
& $offlinePython -m bsgo_offline replay --output logs/offline-bot/plan-baseline-replay
git diff --check
```

Choose new output directories if these paths already exist. Inspect `report.json`; successful process exit alone does not establish successful mission behavior. Add the new mining-mode command to the README only after Task 8 implements it.

## Task 9 Add missing recognition from saved evidence

Dependencies: Task 8 for integration; individual evidence inventory can happen earlier. Live execution remains deferred.

1. Inventory retained images for scan results, resource values, target depletion, mining state, and inventory/reward displays. Label supported fields manually with source-run provenance. Keep unknown and absent cases.
2. Implement one recognizer at a time with explicit layout constraints and unknown behavior. Reuse existing CPU techniques first; a learned model is a separate measured decision.
3. Publish accepted readings through the same field and event contracts. Test crop coordinates and original frame provenance. Preserve the frozen v1 corpus; add a separate version for new labels and fixtures.
4. If retained images cannot support a field, mark its live recognizer blocked on evidence. The synthetic routine can remain complete. Do not substitute invented values, generated images, or model guesses as game ground truth.

Acceptance per field: labeled positive, absent, ambiguous and damaged evidence where available; per-field accepted-correct, accepted-wrong and rejected counts; development/calibration versus independent evaluation groups identified; source-to-event replay checked. Missing independent evidence remains a stated limitation.

October 7 startup extension: nonnative window sizes now automatically register
the selected asteroid panel, own hull and speed regions across three newer
captures using existing font guards. This includes independent region movement
and different aspect ratios. Synthetic geometry transport is offline coverage,
not new native numeric accuracy evidence; Task 9 and new-size native controls
remain incomplete until independent labeled game captures support them.

## Task 10 Perform deferred BSGO acceptance

Dependencies: Tasks 8 and 9, game availability, and current user authorization for live control. Read current piloting skills before choosing the operating location or controls. None of these checks can be marked complete from a synthetic receiver.

1. Verify read-only capture against the actual game window and supported layout. Record correct pixels, geometry, frame timing limits, and foreground/focus/minimize behavior. If GDI cannot provide usable frames, investigate a replacement behind the capture interface before any motion test.
2. Verify configured non-motion input and observed game response. Establish owned stop/release behavior before acceleration. Record requested, dispatched and observed evidence separately.
3. Exercise a bounded approach in the agreed operating area with a preselected target, verified route and current ship configuration. Measure observation age and actual braking behavior; keep unvalidated timing settings conservative and explicit.
4. Verify scan interpretation, mining activation, and reward confirmation individually, then perform one complete cycle. Stop on unresolved ownership, perception, or input faults.
5. Retain original captures and reports. State exactly which layout, ship, equipment, controls and operating conditions passed. Do not generalize this to covered/minimized operation or general navigation.

Acceptance: independently observed complete cycle and cleanup under the stated conditions; documented failures and unsupported conditions; no unresolved issue hidden by weakening a guard. If the game remains unavailable, leave this entire task deferred and deliver the completed offline work.

## Later improvements

The user requested HUD radar interpretation on October 3, 2026. Treat this as
a separate perception and route-evidence experiment, specified in
[the radar calibration plan](radar-calibration-plan.md). Radar is currently
unused by the recognizer. Calibrate heading/contact semantics on the enlarged
HUD, validate forward-path cases, and preserve unknown results before allowing
radar evidence to participate in native route decisions. Current live progress
and its partial acceptance boundaries are in [the live report](mining-runtime-live-results.md).

After the first accepted cycle, measure progress over elapsed time to remove the approach policy's fixed decision-cadence dependency. Keep this change separate from extraction: compare the old and proposed policies on timestamped traces, observation gaps and variable sampling before changing live defaults.

Then consider candidate discovery, repeated mining, route interpretation, and optional AI advice against explicit evidence sets. A behavior tree may become useful if nested interruptions and reusable behaviors make ordinary state objects difficult to maintain. Process isolation is justified if blocking capture/inference or crash containment becomes a demonstrated problem.

October 7 extension: the user requested asteroid discovery through OCR or map
selection. [Map contact discovery and target-HUD verification](asteroid-map-discovery.md)
records the chosen combined mechanism. A read-only map-glyph locator, local
overlay CLI and 67 native map-only patches now provide offline geometry evidence.
Native acquisition/scan execution and cockpit nearby-scan availability remain
pending; this does not complete Task 9 or authorize map coordinates as route
clearance or unscanned-asteroid identity.

## Handoff and progress record

For each task, record status as `planned`, `in_progress`, `implemented_unverified`, `offline_verified`, `live_verified`, or `blocked_on_evidence`. Include changed files, test commands and results, report paths, unresolved limitations, and the next task. Never translate `offline_verified` into `live_verified` in summaries.

Work in small reviewable changes: contracts/tracking; events; routine extraction; stopping; synthetic mission; planner/status; saved-image recognition; live acceptance. Finish the relevant tests before starting dependent behavior. Do not remove regression assertions merely because a refactor changes the result; explain whether the new result is an intended contract change.

When paused, leave the working tree and evidence intact, stop task-owned workers, and name the exact unfinished task. If a test cannot run, record the reason and avoid claiming that inspection substituted for execution.
