# Mining runtime live exploration

## October 3, 2026

The user authorized live gameplay and mining with the upgraded ship, reported a 760 m gun optimal range, and requested observations before addressing problems. Water and Tylium remain the mining priorities. The existing 800 m firing ceiling remains in effect; a larger optimal range does not establish a new stopping distance.

This report extends [the offline results](mining-runtime-offline-results.md). The current source is a dirty working tree based on the offline implementation; its changes are preserved. No claim of full live acceptance follows from the offline tests.

### Architecture inspection

- The CLI exposes simulation and saved-image replay, but no real-game mining mission command.
- `MiningHudRecognizer` explicitly publishes scan result, mining activity, inventory and inventory conflict as unknown. These fields cannot drive a successful live mission yet.
- Synthetic scan and mining bindings are test controls and must not be used in the game. The established game procedure uses Z to scan, G to mine, and PageUp/PageDown for throttle.
- Live play in this exploration uses the existing Computer Use workflow. It is evidence collection, not execution or acceptance of the new mining coordinator.

### Recovery observations

At approximately 14:52–14:56 UTC, captures repeated the same game clock (14:52:59), ship and target state. Shift+M, M and one asteroid selection did not produce a corresponding visible game-state change. A fresh window enumeration and activation did not resolve the repeated frame. The last visible HUD showed 0.0 speed, 5,005/5,005 hull and 800/800 energy; these were cached observations, not verified current ship state.

The retained recovery image has actual dimensions 1707×960, confirmed from its PNG header. The existing approach reader requires a calibrated 2560×1440 layout. No automated approach was started against the mismatched image. Raw recovery evidence is retained locally in `logs/mining/live-20261003/recovery-before.png` and excluded from this documentation commit.

A normal Alt+F4 close completed; a subsequent enumeration confirmed the old game window was absent. The existing launcher showed Session Ready. Its LAUNCH action began client verification and relaunch.

The relaunched window produced 2560×1440 frames and advancing clocks from 14:57:54 onward. Fresh HUDs verified 0.0 speed, full hull and energy. Shift+M opened the circular map after its animation; the ship was on the outpost side, approximately epsilon 3. The initial 1707×960 mismatch was therefore resolved by client recovery without modifying OCR or its guards. The initial cause remains unknown.

### Scanning observations

| Candidate | Maximum HP | Range | Observed scan | Decision |
| --- | ---: | ---: | --- | --- |
| A, right-hand rock | 3489 | 1363 m | Purple Titanium | Skip unwanted resource |
| B, large upper rock | 3128 | 816 m | Mineral Analysis: 3858 Tylium; yellow rock | Defer outside 800 m firing ceiling and below 1150 m approach minimum |
| C, map-selected rock | 4309 | 2800 m | No scan result observed | Defer unclassified target |
| D, left-hand rock | 3674 | 3316 m | Not scanned | Defer distant candidate |
| E, small right-hand rock | 3621 | 4483 m | Not scanned | Defer distant candidate |
| F, rock above right-hand planetoid | 3733 | 1206 m | Purple Titanium after Z | Skip unwanted resource |
| H, left-hand rock after camera change | Not populated in capture | 3478 m | Scan attempt interrupted by connection loss | No classification established |

Z reduced visible energy to about 700/800 before it regenerated. Scan result appearance was delayed relative to input; an immediate post-input frame still showed an unclassified rock. The later selected-rock color and, for B, explicit Mineral Analysis text established classification. A scan's displayed resource amount is not a harvest or inventory change.

Several small silhouettes could not be selected reliably from the bobbing flight camera. Failed selections left the previous target's HP and range unchanged. Candidate acquisition must check the target panel rather than assume a click selected its intended rock. Camera V changed the view after a delay while speed remained zero. No thrust or mining input occurred during these scans.

One attempted rock selection instead acquired a Medium Suppression Platform at 11,986 m. No weapon or approach input followed. Later asteroid acquisition was verified from the target panel. A small yellow-looking silhouette below the ship could not be acquired after one corrective click and was skipped; color alone did not establish a wanted target.

### Connection loss and stopping boundary

At approximately 15:10 UTC, after selecting H and sending Z, the client displayed Connection Lost. It reported about 27 seconds without server contact and `WSA 10054 ConnectionReset`, with the connection forcibly closed by the remote host. This does not establish which side caused the outage or that the scan caused it. The immediately preceding game frame showed 0.0 speed, 5,005/5,005 hull and 800/800 energy. The final scan outcome is unknown.

The Close button exited the disconnected client. The signed-in launcher relaunch produced a new game window. At game clocks 15:11:38, 15:11:48 and 15:12:42, the scene and clock advanced, but the player HUD consistently displayed 1/5,005 hull and 1/800 energy, and the nearby asteroid belt was absent. PageDown was sent after recovery; subsequent captures displayed 0.0 speed. No further movement, scanning or firing was attempted. Actual damage, incomplete state synchronization and any other cause of the recovered HUD discrepancy remain unverified. Do not treat the pre-disconnect full hull as current.

Raw evidence is retained locally in `logs/mining/live-20261003/connection-lost.png` and `reconnect-hud.png`, alongside the scan captures. These screenshots contain game chat and are excluded from the commit. No approach worker or new mining-runtime process was launched, and no task-owned worker needs shutdown.

### Outcome and acceptance boundary

- Verified gameplay evidence: client recovery to the supported capture dimensions, target selection, stationary Z scan responses, unwanted-resource skips and one explicit wanted-resource result.
- Harvested resources: none. The 3,858 Tylium reading describes the asteroid, not inventory gained.
- Approach, actual brake distance, final stopping range, G firing, destruction and reward confirmation: not exercised. A complete belt pass was not performed.
- New architecture live acceptance: blocked by the absent live mission integration and unsupported mining recognition; this run did not execute that coordinator or its native input/capture backends.
- Final gameplay boundary: client open, idle command sent, displayed speed 0.0, recovered hull/energy state unresolved. Further mining is blocked until a trustworthy ship state is available.

No software, loadout, calibration or skill changes were made. Existing implementation work is preserved for the user's later problem review. No offline test results are claimed from this exploration.

### Open observations for later review

1. Real-game mission entry point and game bindings remain absent.
2. Scan, firing and reward perception remain explicitly unsupported by the new architecture.
3. Repeated initial captures were resolved by relaunch, but their cause remains unknown. Connection loss and the later persistent 1/1 HUD state require separate investigation.
4. Capture dimensions must match the reader before any OCR-controlled transit; the initial mismatch was resolved without weakening guards.
5. The current procedure defers wanted rocks between 800 and 1,150 m. The 816 m Tylium candidate exposed that gap; no short-throttle workaround was attempted.
6. Upgraded-gun firing and actual stopping behavior need live evidence; 760 m is user-reported equipment information.

### Application test after scope correction

The user clarified that the intended test subject was the newly developed mining application. The gameplay exploration above used the existing piloting workflow and did not satisfy that objective. The following bounded diagnostic uses the new application's actual components directly; it does not replace them with the old approach controller.

At 15:22:54–15:22:56 UTC, a temporary local diagnostic composed `NativeCapture`, `NativeSource`, `MiningHudRecognizer`, `RecognitionWorker`, `SnapshotStore`, `RecordingInputBackend`, `InputExecutor` (inside `MiningCoordinator`) and `MiningCoordinator`. It pinned the current game process/window, used the explicit `restart-20261001` profile, retained original frames and source hashes, and stopped recognition after three observations. No ownership, route, scan or reward evidence was invented. `NativeInputDispatcher` was not started, so this diagnostic does not validate native delivery.

The restricted shell initially could not inspect the game window (`Target window is no longer alive`). Running the same read-only diagnostic on the real desktop resolved that environment restriction. Native inspection confirmed the expected game title/PID and 2560×1440 client geometry. The retained native image visibly showed the client's second Connection Lost screen, reporting about 19 seconds without contact; the original recovery had not restored a stable game session.

| Application boundary | Observed result |
| --- | --- |
| Native capture | Three original 2560×1440 RGB game frames, with acquisition/capture/publication timing |
| Independent recognition | Three observations published; no recognition exception |
| Speed extraction | Invalid in all three frames: `missing_or_invalid_speed_decimal` |
| Mining recognition | Scan, mining activity and inventory observations explicitly unknown |
| Coordinator | Terminal, incomplete; `field_unavailable:speed`, cleanup `control_unavailable`, no observed stop or reward |
| Semantic input/backend | Zero requests before and after diagnostic shutdown; no native delivery attempted |
| Shutdown | Recognition joined successfully; native capture closed |

The recognition result's `healthy` flag was true because the image size was supported, even though usable speed/target fields were absent. The coordinator correctly relied on field validity and did not proceed. This is a verified negative preflight on a disconnected game screen, not acceptance of a successful live mission.

Inspection also found that the current `MiningHudRecognizer` does not publish `owned`, whereas the new coordinator requires that field. Connecting capture/recognition alone therefore cannot authorize a mission even with a readable HUD. Ownership publication, actual game scan/mining bindings, a live mission entry point and validated scan/reward perception still need integration. These are application gaps, separate from the game's connection failures; no fixes were made in this test.

Reproduction command from the repository root (diagnostic and raw artifacts remain local):

```powershell
$env:PYTHONPATH = (Resolve-Path tools/offline-bot).Path
& tools/model-runtimes/fastvlm-venv/Scripts/python.exe logs/mining/live-20261003/application-preflight.py
```

Evidence: `logs/mining/live-20261003/application-preflight/report.json`, `frame-001.png` through `frame-003.png`, and the diagnostic source beside that directory. No synthetic cycle or regression tests were rerun in this follow-up. The current client remains on Connection Lost; no gameplay actions followed the application test.

### Server reconnect retry, 15:54–15:56 UTC

At the user's request, the signed-in launcher was used again. A fresh client entered 276 Exrun with a visible asteroid belt and progressing clock. Hull was 5,005/5,005; energy regenerated from 223/800 during entry to 800/800. PageDown was sent and fresh frames displayed 0.0 speed. This establishes a usable connection during the retry, not a general claim that the server issue is fixed.

The same bounded application diagnostic was rerun at 15:55:28–15:55:29 UTC with the freshly inspected game HWND/PID and a separate output directory. Source hashes matched the earlier test. Native capture and the independent recognition worker published three 2560×1440 observations without exceptions. Speed was accepted as 0.0 in all three; acquisition-to-publication durations were approximately 0.500, 0.359 and 0.219 seconds.

The coordinator now terminated with `field_unavailable:owned`, cleanup `control_unavailable`, incomplete and no observed reward. This directly exercises the previously inspected ownership integration gap on a readable live HUD. The `owned` field was absent in all three observations. The game connection and speed extraction no longer blocked this test; a mining mission still could not begin.

Asteroid preselection was attempted through Computer Use but remained unconfirmed; all three application observations classified `asteroid_selected` as false. Selection/route acceptance was therefore not tested. The coordinator's ownership guard failed before that dependency could authorize input. No fabricated selection or ownership field was supplied to advance the test.

Zero input requests were recorded before and after diagnostic shutdown. Native input delivery was not started. Recognition joined and capture closed successfully; no diagnostic worker remains running. No scan, mining, thrust or application fixes occurred during this retry. A later game frame at 15:56:02 still showed the connected belt scene, full hull/energy and 0.0 speed.

Evidence remains local: `logs/mining/live-20261003/application-retry-1554.py`, `application-retry-1554/report.json`, its three original frames and `retry-final-hud.png`. Earlier evidence was preserved. The next application blocker to address is verified ownership publication into the coordinator's live observation contract; a complete mining cycle remains unaccepted.

### Application experiment 1: live capture, readiness and retained diagnostics

On October 3, after the user confirmed the game was running on another screen,
the new `bsgo_offline.diagnose` command ran against the freshly identified game
window. This is an application experiment using its native capture, recognition
worker, state store, coordinator readiness reader and recording backend. It did
not start a mining mission or a native input dispatcher. Each run wrote a
startup manifest, live status, observation trace, raw frame hashes and a final
shutdown result. All three runs processed and retained six 3840×2160 frames,
had complete diagnostic traces, joined their workers and recorded zero input
requests. The screenshots and trace remain local under
`logs/mining/live-20261003/diagnostics-*`; they contain game chat and are not
committed.

| Experiment | Speed observations | Distance observations | Finding |
| --- | --- | --- | --- |
| Native profile only, `diagnostics-1648` | Six invalid | Six invalid | Reader rejected the 4K client dimensions against its 2560×1440 calibration. |
| Explicit 4K conversion with Lanczos, `diagnostics-4k-explicit` | Six invalid, `invalid_speed_decimal_position` | Six reads of 229 | Conversion retained distance but removed one pixel of the speed decimal. |
| Explicit 4K conversion with area averaging, `diagnostics-4k-box` | Six reads of 0.0 | Six reads of 229 | A fresh live run reproduced the displayed stationary speed and target distance. |

The original 4K pixels remain attached to observation identities in each run.
The converted reader marks its resampling as experimental because six stationary
frames do not validate moving speeds, other targets, changing HUD states or
OCR error rates. The final preflight still blocks control: `owned` and
`selection_revision` are absent, asteroid selection is false, route/equipment
evidence is absent, scan and mining bindings are missing, and scan, firing and
inventory recognition are unvalidated. The current 229 m target was a ship,
not a selected asteroid. This is proof of capture and a stationary HUD reading
in the running client, not proof that the mining app can control or mine it.

The implementation was committed incrementally: `63fcf551` (validated offline
runtime), `fa98dd93` (readiness), `943d35f0` (action histories), `d7a6eb3e`
(diagnostic package), `1d1c493b` (explicit 4K conversion) and `599d78cb`
(decimal-preserving conversion). The tests passed after each completed code
change; the last full suite had 232 passing tests. The next experiment should
establish validated ownership and a harmless reversible input with observed
effect before target selection or motion. This report does not count any
synthetic receipt or manual piloting as application control evidence.

### Observability integration and control guard

The same diagnostic package now runs throughout complete synthetic mining
cycles. In the nominal smoke run it retained 92 observations, 19 action
lifecycle events and 16 mission transitions; all eight requested input records
were accounted for, all workers joined and no trace events were dropped.
Synthetic samples have no pixels, so the package explicitly recorded 92
`source_has_no_pixels` entries. All 11 fault and nominal acceptance scenarios
passed with complete event traces after integration. These are synthetic
control experiments, not live-game mining acceptance. The package can expose
worker stage and elapsed time, live queue depth, observation age, per-field
rejection, preflight blockers, input lifecycle and shutdown completeness.

An additional delivery regression removed an unsafe legacy compatibility path
from the mining coordinator: if ownership is present when an action is
requested but disappears from the snapshot before delivery, that action is
rejected. It cannot be dispatched on missing ownership evidence. The focused
case and full suite passed (233 tests). The generic legacy executor still
supports older snapshots only for its older harness; mining always requires
explicit ownership. No live game input occurred in these tests.
