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

### Startup calibration command and paused verification

At the user's request, a separate startup diagnostic command was added for a
bounded, read-only calibration and control-readiness check. It composes the
same application capture, recognition, trace and preflight components, verifies
retained image hashes against their specific observations, and writes
`startup-calibration.json`. Its read checks cover capture count, stable window
identity, raw frame retention, recognition health, repeated speed/distance/
selection readings and the configured calibration profile. Its control checks
include ownership, selection continuity, revision, route, equipment, bindings,
recognizer capabilities, worker shutdown and empty input queues. A repeated
reading is marked available, not accurate or authorized for input.

One startup run finished before the user paused live game verification. It
captured six 3840×2160 frames from the expected game window, retained and
verified all six, read speed 0.0 and distance 229 on each, classified asteroid
selection false on each, and recorded no input requests. Its diagnostic trace
and workers completed. Read checks passed; control checks did not, because
ownership and selection revision were unknown, the selected target was not an
asteroid, route and equipment were unset, scan/mining bindings were absent,
and the 4K converted and mining recognizers remain unvalidated for control.
The output stays local at
`logs/mining/live-20261003/startup-calibration-4k/`.

After the pause, no further game calls were made. Offline review tightened
the frame-to-observation hash match and verified the six saved frames against
the new check. The latest full suite passed 237 tests. Startup calibration is
committed in `d7c6e585`, and the exact evidence match in `c0b31721`.

The startup package can now be reassessed offline with
`python -m bsgo_offline.startup_diagnostics --reassess <saved-run>`; it rereads
the recorded trace and verifies each frame hash without opening the game or
rewriting the original assessment. An offline tamper test detects a changed
frame, and the saved live package still passes all six exact frame matches.
The report now exposes distinct values and whether each repeatedly read field
was constant: speed 0.0, distance 229 and asteroid selection false were each
constant across the six frames. This is repeatability, not accuracy across
other situations. The reassessment and variation features passed the full
237-test suite, and are committed in `a9f891da` and `94ad946e`.

### Active motion and braking calibration

The user resumed game verification and requested Computer Use supervision.
The new `bsgo_offline.calibrate` command runs the application's native capture,
recognition, input dispatcher and diagnostic journal. It requires two fresh
stopped images and a manually assessed clear heading, sends PageUp, requests
reserved PageDown on observed motion or an independent 1.5-second deadline,
and requires two fresh stopped images afterward. Input insertion alone cannot
issue its scoped, expiring motion/braking certificate. Each run retains raw
frames, source hashes, request/delivery records and worker shutdown outcomes.
No mining mission runs during this probe.

The running game window was pinned to HWND 33296742, PID 40148 and a
3840×2160 client area. Computer Use inspected full hull, 0.0 speed and the
heading, then oriented the stopped ship away from the belt before the first
probe. The experiments used the explicit restart profile and 4K BOX conversion.

| Local run | Input encoding and duration | Image feedback | Result |
| --- | --- | --- | --- |
| `control-calibration-1` | Scan code, immediate pair | Eight readings of 0.0 | No motion proof; deadline idle; stop verified |
| `control-calibration-50ms` | Scan code, configured 50 ms | Eight readings of 0.0 | No motion proof; deadline idle; stop verified |
| `control-calibration-150ms` | Scan code, configured 150 ms; measured pairs 171/156 ms | Eight readings of 0.0 | No motion proof; deadline idle; stop verified |
| `control-calibration-vk50ms` | Virtual key, configured 50 ms; measured pairs 63/62 ms | 0.0, 0.0, 0.0, 0.6, unknown, unknown, 0.3, 0.0, 0.0 | Motion and stop observed; certificate rejected for two unreadable intermediate images |

Every run above retained complete diagnostics, inserted four Windows input
events, verified a final stop, and joined its watchdog, dispatcher, recognition
worker and capture source. The first three runs demonstrate why delivery
receipts cannot establish control. A separate Computer Use comparison of the
same bindings showed movement with PageUp and a return to 0.0 after PageDown;
that comparison is not counted as application acceptance. Longer scan-code
holds did not fix the failure, while virtual-key input did produce application
motion feedback. The game calibration CLI now defaults to virtual keys with a
50 ms minimum hold; generic receiver tests retain their scan-code default.

Offline inspection of the rejected virtual-key frames identified visible
speeds 1.6 and 1.4. Their complete narrow digit 1 has a different raster extent
after 4K conversion. The reader rejected it as
`incomplete_or_ambiguous_speed_one`; those unknown values were never treated as
zero or replaced with expected speed. This exposed a second calibration issue
after the input encoding issue. All images and transcripts remain local under
`logs/mining/live-20261003/`; only this summarized evidence is committed.

The active runner is committed in `73807402`, bounded key dwell in `82bffdd9`,
virtual-key dispatcher support in `b47ca444`, and the game-specific default in
`799022b9`. The latest suite at that point passed 261 offline tests. These
checks cover ignored commands, stale or changing image context, recognition
stall, cleanup request failure/retry, focus loss, emergency release and matching
key encoding. Heading, targeting, scanner output, mining and inventory feedback
remain unverified by the startup control tool.

The explicit 4K BOX reader correction is committed in `7421100e`. It accepts
the measured complete narrow 1 and four rasters only in the configured restart
BOX mode. Native restart recognition remains strict, and original-profile 4K
construction remains supported. Tests passed for 43 HUD speed/restart/background
cases, 261 offline-bot cases and 26 held-out native speed labels. Two tiny
80×26 HUD crops preserve the regression evidence without private full frames.
Re-reading the earlier nine saved frames now produces
0.0, 0.0, 0.0, 0.6, 1.6, 1.4, 0.3, 0.0, 0.0; the original failed run report
is preserved.

The fresh `control-calibration-vk50ms-corrected` run retained nine frames and
read 0.0, 0.0, 0.0, 0.3, 1.4, 1.7, 0.6, 0.0, 0.0. It issued its narrow
motion/braking certificate, inserted four events, observed the stop and joined
all components with complete diagnostics. The measured key holds were 62 and
78 ms with the configured 50 ms minimum.

**This final run is excluded from clean calibration acceptance.** The
pre-launch Computer Use screenshot already showed hull 4855/5005, blue ships,
missile alerts and electronic warfare. The operator incorrectly launched the
probe in the same tool batch before inspecting the returned screenshot. That
was an execution mistake; the scene no longer met the intended quiet test
conditions. After the probe ended, the ship was destroyed. Recovery at Muninn
returned the client to its interior/NPC scene; further control experiments were
suspended. The original component certificate remains as historical evidence,
and local `acceptance-review.json` records the exclusion.

This exposed missing hull-health, combat and exclusive-input-ownership
observations in the active startup tool. Its result now lists those explicitly
as unverified alongside targeting, scanning, mining and inventory. It continues
to return `mining_ready: false`. The motion/input and scoped OCR changes are
checked in; a clean supervised acceptance run and the remaining startup probes
are still required before real mining. No calibration program remained running
after recovery.

### Relocation to Exrun after recovery

At the user's request, Computer Use repaired the recovered ship at Muninn and
undocked after the repair cooldown. Repair-all cost 29,711 titanium; the UI
confirmed 100% hull and equipment afterward. No cubits were spent. The ship
jumped to the 276 Exrun beacon, whose displayed jump cost was 3,430 tylium.
The arrival screenshot at game clock 19:14:24 confirmed 276 Exrun, hull
5005/5005, speed 0.0 and the friendly Fortified Cylon Outpost at approximately
397 m. No combat alerts appeared in that view. This was supervised relocation,
not another application calibration or mining acceptance run.

### Exrun application restart attempt

The user requested the auto-miner again. Supervision confirmed full hull,
0.0 speed and no combat alerts at the friendly Exrun outpost, then oriented
the stopped ship toward open space for the bounded application control probe.
An initial sandbox launch failed window inspection before capture or input;
native window inspection succeeded outside that desktop restriction. Its
stdout/stderr are retained separately from subsequent empty run directories.

`exrun-control-calibration-2` (run `24098831a28c403fa423d519352bb212`)
read speeds 0.0, 0.0, 0.0, 0.2, unknown, 1.5, 0.5, 0.0, 0.0. The rejected
frame visibly showed 1.3: its complete BOX-converted narrow 1 starts the lower
bar at row 13 rather than row 14. The scoped correction in `1f050b70` accepts
that complete anchored variant only in explicit BOX mode. Its HUD-only
regression crop and damage-rejection tests passed, along with 44 HUD checks
and 261 offline-bot tests. Native restart rules remain strict.

One corrected live retry, `exrun-control-calibration-3` (run
`9635d627bc5a4c188014a5a7b068db67`), read 0.0, 0.0, 0.0, 0.6, 1.7, 1.4,
unknown, 0.0, 0.0. Frame 7 visibly showed 0.4, rejected as
`incomplete_speed_four`. Both runs inserted four input events, requested
braking on observed motion, verified two stopped images, retained complete
diagnostics and joined all components. Both withheld the certificate because
of invalid speed evidence. No further live retries were issued.

`exrun-startup-readiness-1` retained six healthy, hashed native frames, but
read/control checks did not pass. No asteroid was selected, so missing target
distance was expected. Independent integration gaps remained: unknown input
ownership and selection revision, no route/equipment evidence, missing native
scan/mining bindings in this assessment, and unsupported scan, mining and
inventory recognizers. The application's mining-cycle CLI remains synthetic;
its native control probe is not a real mining runner. No mining mission or
harvest occurred in this attempt. The last supervision at game clock 19:27:28
confirmed hull 5005/5005, speed 0.0 and the friendly outpost at about 394 m.

The user suggested reducing graphics quality and resolution. The next proposed
experiment is native 2560×1440 with graphics quality unchanged: it matches the
reader calibration and avoids the experimental 4K BOX conversion. The current
UI showed roughly 60 FPS, so there is no measured evidence that lowering
graphics quality would fix these recognition failures. Neither setting has
been changed yet.

### Failed-image retention and offline contrast trial

At the user's request, the shared bot diagnostic pipeline now queues every
rejected image for persistent retention under `failed-recognition/`, bypassing
the routine frame-count budget. Native RGB is saved as lossless PNG; replay
sources are copied byte-for-byte. Each rejected image has a JSON sidecar with
field rejection reasons, the immutable snapshot and OCR evidence, acquisition
and window context, image SHA-256, run ID and runtime/reader source hashes.
These local artifacts are not rotated or committed. Declared unimplemented
recognizers and verified absent asteroid identity do not trigger retention.

The background writer remains bounded so storage cannot block ship control.
Queue overflow, byte-limit rejection, unavailable pixels and writer errors are
visible; `frames.failed_recognition` reports observed/accepted/written/missing
counts and cannot claim a complete archive when rejected images are missing.
This applies to the shared diagnostics used by native startup/control probes
and synthetic mining. It does not add the still-missing native mining runner.

All 267 offline-bot tests passed, including retention of 20 rejected images
beyond a one-frame routine budget, recognizer exceptions, sidecar disk failure,
missing pixels and backpressure. An offline replay of Exrun retry frame 7
preserved its `incomplete_speed_four` rejection and saved a byte-exact raw copy
with matching SHA-256 and a complete failure archive. It sent zero inputs.

A separate speed-only contrast experiment compared four manually labeled
4K BOX development crops, four derived damage controls and 26 correlated
native holdout frames. Baseline recognized three of the four BOX crops and all
26 native labels. Contrast factors 1.5 and 2 recovered the rejected 0.4 but
rejected the previously readable 1.3, leaving the BOX result at three of four.
Both preserved the native labels and rejected all four damaged controls.
Higher global white thresholds reduced coverage further. No wrong non-null
reading appeared in this small sample; this is exploratory evidence, not a
prospective accuracy result. The raw reader already uses a white mask and
digit/decimal geometry checks. No production contrast or threshold change was
made. Native HUD capture/resolution and controlled local-threshold candidates
remain experiments to validate against retained failures and damage controls.
The script and detailed results remain local in `logs/mining/live-20261003/`.
No gameplay or graphics-setting changes occurred during these experiments.

### Native-resolution Exrun retry

The manual mining agent relinquished UI ownership before thrust: its prepared
reader rejected the selected Tylium asteroid at 1,261 m. No manual harvest
occurred. The stopped, full-hull ship was then used for an application retry.
Game Video settings offered fullscreen but no resolution selector. Disabling
fullscreen produced a native 2560×1440 client, verified by `inspect_window`.
Ultra quality, 8x anti-aliasing and 100% UI scale remained unchanged. The
window's new screen origin was (640, 377); no OS display setting was changed.

`exrun-native2560-startup-1` passed all six image-reading checks with six
healthy hashed native frames. Full control/mining readiness remained false
because ownership, selection revision, route/equipment, native scan/mining
bindings and resource/mining/inventory recognizers are not integrated.

After stationary orientation toward clear space, the bounded application probe
`exrun-native2560-control-1` (run `8b2dab15af7d41309de16d1a4ee69385`)
proved image-observed motion and stop. Speeds were 0.0, 0.0, 0.0, 0.4, 1.9,
1.5, 0.4, 0.0, 0.0; target ranges were 1,261 to 1,259 m. Configured 50 ms
virtual-key holds measured 63 ms for both commands. Braking was requested on
observed motion, all components joined, input was idle and all nine frames
were retained with complete diagnostics. Eight frames also had invalid
non-speed fields and were archived with failure sidecars, without image loss.
The scoped certificate establishes this run's motion/braking feedback only.
No mining action or harvest occurred. Post-run Computer Use at game clock
20:01:34 confirmed 0.0 speed, hull 5005/5005, energy 800/800 and friendly
outpost at 396 m. This clean run supports using native-resolution captures for
the next experiments; it does not establish general OCR accuracy or mining
readiness.
