# Mining runtime offline implementation results

The offline runtime now executes one complete mining cycle against an independent synthetic receiver. It observes a scan result, checks resource eligibility, approaches using the existing policy, verifies stopping, observes mining activation, treats target disappearance as a depletion candidate, and requires a later resource change before reporting success. Python unit tests and synthetic receivers were used throughout. No BSGO window was captured or controlled, and no Unity project exists in this repository.

The [implementation plan](mining-runtime-improvement-plan.md) records the task status. Runtime code remains in `tools/offline-bot/bsgo_offline`; its package name is unchanged.

## Implemented architecture

`RecognitionWorker` remains independent of decisions and input. It publishes immutable observations and a bounded gameplay event stream in one store transaction. Consumers read a consistent snapshot and event watermark; missing history becomes an explicit gap. Diagnostic trace loss remains separate from gameplay evidence loss.

`SelectionTracker` maintains local continuity from context, signatures, selection revision when available, and observation time. Lost selection, unknown evidence, a gap, explicit reselection or changed context invalidates old work. Similar HP and portrait do not reconnect an old track after a loss. Visually indistinguishable changes with no observable evidence remain a limitation.

`MiningCoordinator` owns mission progress and updates pure routines. `InputExecutor` maps semantic intents to configured bindings and checks generation, expiry, context, freshness, selection and route permission. Checks run at submission and immediately before synthetic/native delivery. Recorded requests, dispatched actions and observed effects remain separate. An action ID cannot emit another press through repeated ticks; forged or replayed backend records are rejected.

Cancellation invalidates ordinary work and uses the existing reserved cleanup path. The new mining runtime keeps observing speed for a bounded stop result even after the target is lost. A stale zero and an input acknowledgment cannot complete this phase. Pause reports the cleanup result; resume requires a new backend session, fresh preflight and route evidence. Native mock tests also cover release of an inserted key whose matching key-up was already dequeued before cancellation.

Advice is optional and asynchronous. The current fake planner supplies observation advice only to the one-cycle mission; the coordinator records validated advice without using it to drive mining. Request generations, context, local track and evidence expiry prevent old replies affecting new work. No model server or network request was used.

## Validation evidence

The original suite passed 159 tests before implementation. The final suite passed **216 tests**, including the original regression tests and deterministic routine/mission tests. Native capture/input tests use fake Windows APIs and saved pixels. They do not launch a native window or operate the desktop. [Final test log](../../logs/offline-bot/improvement/final-tests.txt).

All **11 threaded mining acceptance scenarios passed**. Every scenario joined recognition, receiver, optional planner and journal workers successfully; complete event traces were retained. Reports include configuration, Git revision, and a hash of the runtime source files so uncommitted code is identifiable. [Acceptance summary](../../logs/offline-bot/improvement/acceptance-complete/acceptance.json).

| Scenario | Result verified |
| --- | --- |
| Nominal without planner | One Water reward of 25 synthetic units; stopped completion |
| Delayed advice | Advice accepted against newer observations while mining proceeds |
| Advice slower than the mission | Mission succeeds and the pending planner is cancelled and joined |
| Ignored brake | Approach fails; cleanup ends with stop unverified |
| Stalled recognition | Stale observation rejected; no claimed observed stop |
| Source fault | Unknown observations; control availability cannot be established |
| Missing scan | Scan timeout; no approach |
| Ineligible resource | Rejected before approach |
| Mining input ignored | Activation/outcome never accepted; bounded timeout and cleanup |
| Missing reward | Target disappearance does not complete the mission |
| Conflicting inventory activity | Reward attribution rejected |

The deterministic tests additionally cover phase cancellation, pause/resume, lost targets while moving, identical reselection, ownership/session changes, stale zero speed, event overflow, transient inventory conflict, delayed/expired input, forged records, unavailable advice, and diagnostic failure. The concurrency tests verify publication consistency and independent workers. Test-owned workers were stopped; there is no background runtime service from these runs.

The existing approach-only simulation also passed after routine extraction. The saved-image replay processed all **25 frames**, advanced while its consumer was delayed, ended with `source_exhausted`, and joined successfully. [Approach regression report](../../logs/offline-bot/improvement/legacy-approach/report.json), [saved mining-field replay](../../logs/offline-bot/improvement/saved-mining-fields/report.json).

An early acceptance run exposed mapping-proxy transition payloads that the JSON journal rejected. The recorder now converts immutable transition values to plain JSON before enqueueing, and a regression verifies that a successful journal receives every transition. The accepted reports above have no diagnostic gaps. Earlier failed development reports are retained under `improvement/acceptance` and must not be mistaken for the final results.

## Saved evidence and incomplete perception

The inventory tool verified all **307 frozen input hashes** and grouped the **304 real captures into 50 source groups**. It produced 78 first/last source-group review candidates. These are inventory samples, not 78 new accuracy labels. [Inventory](../../logs/offline-bot/improvement/evidence-inventory/inventory.json).

Four directly reviewed examples now have scene annotations in a separate [v2 mining evidence manifest](../../test-data/vision/v2-mining/manifest.json): flight, hangar and map. They reference frozen original bytes with hashes and are development examples. The v1 images and labels were not modified. These annotations do not label scan completion, mining activation, inventory or rewards.

The retained screenshots show scanned rock colors, HUD values and combat-log text. They do not yet form an independently labeled, action-linked evaluation set for each new mining field. Color alone does not supply a selected-target locator or the completion time of a requested scan. Damage or disappearance alone does not establish our firing or a reward. A reward line or progress counter must not be treated as an inventory total or repeatedly counted on successive frames.

Accordingly, `MiningHudRecognizer` reuses the validated HUD reader and publishes `scan_result`, `mining_active`, `inventory` and `inventory_conflict` as explicitly unknown with rejection reasons. Saved-frame tests verify that no successful scan/reward event is fabricated. These four game-image recognizers are **not implemented or validated**. The synthetic receiver supplies invented versions to exercise the runtime contracts, which does not substitute for perception acceptance.

The next perception work needs labels that identify the selected object and scan state before/after a request, own firing evidence, depletion/selection transitions, and resource changes with duplicates, gaps and competing activity. Reuse original images where their evidence is sufficient; obtain any missing sequences only when game capture is allowed. Split complete source runs and keep calibration/development separate from an independent evaluation set. Report correct accepts, wrong accepts and rejects per field before wiring a recognizer into the mission.

## Reproduction

From the repository root, the existing Python environment suffices. Choose a new output directory for each acceptance run to preserve evidence.

```powershell
$env:PYTHONPATH = (Resolve-Path 'tools/offline-bot').Path
$offlinePython = 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe'
& $offlinePython tools/offline-bot/run_tests.py --output logs/offline-bot/check-tests.txt
& $offlinePython -m bsgo_offline simulate-mining --no-planner --output logs/offline-bot/check-mining
& $offlinePython tools/offline-bot/acceptance.py --output logs/offline-bot/check-acceptance
& $offlinePython -m bsgo_offline replay --mining-observations --output logs/offline-bot/check-saved-fields
```

The synthetic controls `S` and held `M` are fake bindings. Their executor rejects use on any surface without the synthetic identity. A missing real binding is rejected. There is no command here that starts a real-game mining mission.

## Deferred acceptance

Real BSGO capture/input compatibility, ship-specific timing, route perception, equipment bindings and the complete game mining cycle remain deferred under the user's explicit instruction. The native dispatch changes were checked through unit tests only. Existing earlier native receiver acceptance does not certify these new changes on a real window. The approach policy still uses its retained two-second cadence and numeric defaults; changes to that policy remain a later measured improvement.
