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

### Enlarged HUD and lower-quality settings, October 3

The user lowered resolution and graphics quality and enlarged the HUD. Fresh
native capture verified a 2560×1440 client at screen origin (0, 0). The old
restart profile rejected all six startup speed/distance readings because the
HUD had moved. The new explicit `largehud-20261003` profile uses fixed native
geometry and labeled HUD-only development crops. It rejects converted sources;
full frames and their private chat content remain local. A fresh Water target
exposed a complete twelve-row distance six, now accepted with a regression
that rejects its clipped variant. `exrun-largehud-startup-3` then passed all six
read checks: speed 0.0, distance 1,677 m and maximum asteroid HP 3,587, matching
independent visual inspection. Full mining readiness remained false.

Three bounded motion probes withheld certificates because complete speed
glyphs were not yet calibrated: narrow ones, a twelve-column three, and a
twelve-column seven. All rejected images were retained. Each run observed a
final stop, joined its workers and retained complete diagnostics. Measured
native rasters were added with clipping/segment-damage regressions; 286
offline-bot tests passed after the final correction. These development examples
do not establish general OCR accuracy.

After stationary orientation toward clear space, `exrun-largehud-control-4`
(run `741e5a8c0acc443f9fde4b69412189ae`) passed image-observed motion and stop.
The baseline was 0.0 twice, observed motion was 0.9, and the final stop was
0.0 twice. Configured 50 ms virtual-key holds measured 63 ms for acceleration
and 62 ms for idle. Braking was requested on observed motion. All eight frames
were retained with zero rejected readings, no diagnostic loss and complete
worker shutdown; input was idle. Post-run supervision at game clock 21:45:43
confirmed speed 0.0, hull 5005/5005, energy 800/800 and friendly outpost at
370 m. The short-lived certificate proves this session's motion/braking only.

The enlarged HUD/native capture combination is usable for these checks after
explicit calibration. Current displayed FPS remained about 59–60; there is no
isolated measurement proving a performance gain from lowest graphics quality.
Radar, semantic scan results, inventory rewards and a native mining mission
remain separate unfinished steps. The mining agent scanned a Tylium candidate
at 1,274 m and Water at 1,677 m while stopped, then released UI ownership.
Neither was within firing range, and no harvest occurred in these experiments.

The subsequent Computer Use selection attempts did not change the selected
Water asteroid; the cause is unresolved. No input-duration explanation is
proved. `exrun-largehud-scan-1` (run `ff72f7d7cf154daca8dab13622de4429`)
therefore recorded a repeat scan of that existing selection. The application's
single Z down/up pair measured 63 ms and was inserted by Windows. All 21 frames
were retained without rejected HUD readings or diagnostic loss: speed stayed
0.0, distance 1,673 m and maximum HP 3,587. All workers joined and input was
idle. The already-scanned Water display does not establish a new scan effect;
the result correctly remains `scan_effect_unverified`, with mining readiness
false. Post-run supervision at game clock 21:48:46 confirmed full hull/energy,
zero speed and outpost 370 m. No thrust, mining or reward occurred in this
scan probe. The next basic experiment is reliable target selection followed by
a first scan with independently visible before/after resource evidence.

### Restarted application loop: selection, first scans and approach, October 3

The live client restarted in Exrun with the enlarged 2560×1440 HUD. These runs
used the new application's native capture, recognition, input dispatcher and
diagnostic packages. Computer Use supervised the scene and supplied candidate
coordinates and heading assessments; it did not supply motion inputs while an
application approach owned the controls. This remains supervised development,
with full mining readiness false.

Stationary selection initially missed small rocks or selected a planetoid. The
application correctly withheld asteroid-selection proof. A configurable 50 ms
pointer-settling interval followed by a 50 ms mouse hold was added, with fresh
focus, geometry and cancellation checks before button-down. The misses alone
do not establish that input duration caused them. Selection run
`5832231388914adba4143ecbc7a9f21d` (`exrun-loop-restart-selection-6`) then proved
a changed asteroid selection on two fresh images after its delivered click:
maximum HP changed from 3,781 to 3,220, at 3,147 m. The selection probe records
input delivery separately from the observed target change.

Three first-scan experiments delivered Z while stopped. Independent before/after
visual supervision saw gray portraits become purple Titanium for HP 3,781 at
2,625 m and HP 3,220 at 3,147 m, then blue Water for HP 4,166 at 3,098 m. These
were `exrun-loop-restart-scan-3`, `-4` and `-5` respectively; each retained 21
frames with valid HUD readings and complete diagnostics. Titanium was skipped.
The application still reports `scan_effect_unverified`: it does not yet classify
the resource result itself. The Water scan's run ID is
`fb688198d76f40fc8553361fa0d3c405`.

Failures exposed two complete HP glyph layouts: a narrow final one recentered
the label's slash by five pixels, and the HP digit six had twelve rows. Measured
HUD-only crops were added with rejection regressions for clipping. An approach
also exposed a speed crop that cut off the decimal digit in 10.7; widening the
native crop fixed complete two-digit speed readings. Every failed recognition
image remains in its local run package. Full frames, private chat and generated
logs are excluded from commits.

`exrun-loop-restart-approach-1` observed acceleration and closing range but
braked immediately on the rejected 10.7 speed image. It verified two final zero
speeds. Approach 2 reached its 12-second thrust cap and commanded idle, but its
original 12-second coast-down observation window expired before a fresh stop.
Independent supervision subsequently confirmed zero speed. The default stop
observation budget was increased to 35 seconds, retaining a 45-second upper
bound. A delivered idle command is not treated as a stopped ship.

Approach 3 (`7a5cfa9db64c406aa184bf0a1dedce03`) moved toward the Water target from
3,098 to 2,747 m and verified zero speed on sequences 63 and 64. Approach 4
(`d8ac394a45fa4d51ac8afbe8879a7bc9`) used a 30-second thrust cap and stopped at
1,759 m, with final zero readings on sequences 111 and 112. Neither reached
the 900 m brake threshold, so both correctly report
`thrust_limit_before_brake_threshold`, rather than full approach proof. Both
retained all frames, had no recognition failures and joined all components.
Measured cruise speed was 33.0. An explicit thrust cap of up to 45 seconds is
now permitted for the next threshold trial; the short 12-second default remains.
The frame budget covers the longer bounded trial and its coast-down period.

The integrated offline suite passed 317 tests after the selection and glyph
corrections; eight focused approach tests passed after extending the explicit
thrust bound. These checks complement the live observations, without proving
general OCR accuracy, radar avoidance, resource classification or harvesting.

Approach 5 (`eefceca4f70c46f583d8afe885ff3f7c`) then proved the actual threshold
approach and stop. It began at 1,759 m and 0.0 speed, observed cruise speed 33.0,
and requested PageDown at its first threshold sample of 892 m (previous samples
918 and 905 m). Both key holds measured 62 ms. The application verified fresh
zero speeds on sequences 119 and 120, with final distance 654 m. All 120 frames
were retained, none had rejected recognition, diagnostics were complete and all
components joined with input idle. Independent supervision at game clock
22:57:31 confirmed 654 m, speed 0.0, hull 5005/5005 and energy 800/800. This run
supports the 900 m brake setting for this observed clear aligned approach, and
leaves the scanned Water candidate within the user's 760 m optimal gun range.
It does not establish a universal stopping distance or mining readiness.

By game clock 22:59:35 the selected Water target had disappeared while stopped.
No G command had been issued in these restart experiments; its disappearance
has no established cause and is not credited as our harvest. The application
then acquired another asteroid through selection 8
(`6a0550243c0d481894594f17c19d1b0b`): two fresh images proved HP 4,500 at 677 m.
Scan 6 (`af440124aca6483d8f765b54497d754e`) independently changed the gray result
to yellow Tylium, leaving a wanted candidate already within firing range. No
flight was needed. The scanner still records semantic result verification as
unfinished.

The new current-HP reader passed 319 integrated tests on the existing measured
four-digit examples, but this live 4500/4500 label exposed another geometry
rejection: its complete leading four begins one pixel from the crop boundary.
All 21 scan images were retained with `target_current_hp` failure sidecars;
selection, maximum HP, range and speed remained valid. This is live evidence of
a current-HP calibration gap, rather than a failed scan command. The rejected
native frame supplies the next HUD-only development regression.

The leading-four correction accepts its exact measured complete raster with
one blank boundary column, while rejecting first-row damage. It leaves the
maximum-HP and slash guards intact. The correction passed 18 focused HUD tests
and all 320 integrated tests before being committed. These four-digit current
readings are development evidence; lower digit-count HP transitions remain
unsupported until measured in the live pipeline.

### First application-commanded firing effect and verified harvest, October 3

The new supervised `firing_probe` requires four seconds of stable current HP
before issuing one guarded 50 ms G command. The supervisor supplies the wanted
resource and safe-scene assessment; native image evidence must independently
establish zero speed, range 480–760 m, stable context, target identity, portrait
and maximum HP. Proof requires two distinct lower current-HP readings acquired
after the actual G key-up delivery. It neither repeats G nor invents a ceasefire
command. The supervisor remains with the game because assigned autofire can
continue beyond the observation window. Readiness, resource classification,
inventory and autofire-state recognition remain unverified. Seven focused
firing tests and all 327 integrated tests passed before the committed live run.

`exrun-loop-restart-firing-1` (`0c966513e2db4ade9da28aee6d085c8f`) proved the
narrow firing effect on the app-selected and scanned Tylium candidate. Baseline
HP was 4500/4500 at 677 m and zero speed. The application inserted one G pair,
with an actual 78 ms hold, then read HP 3,206 on sequence 12 and HP 2,459 on
sequence 22. Distance remained 677 m and speed remained zero. All 22 images
were retained with no rejected readings, complete diagnostics, joined workers
and no held input. The application correctly keeps `mining_ready` false.

Independent Computer Use supervision continued after the probe. At game clock
23:09:22 the target was destroyed and the game visibly awarded
`+ 6158 X TYLIUM` and 112 experience. Hull was 5005/5005, speed 0.0 and energy
417/800. No supervisor G, thrust or subsequent target selection was issued
between the application's command and this reward. This establishes a real
supervised application-commanded mining harvest. The reward was read by the
supervisor; the application itself has not yet validated destruction or
inventory/reward pixels, nor connected these probes into an autonomous native
mission. The ship was left at this natural stopped boundary in Exrun.

### Continued supervised loop and distance-based transit, October 3

The user requested continued operation until an explicit stop. A single mining
agent owns live game input while the parent supervises and repairs the app.
After a closed client was relaunched, the native 2560×1440 large-HUD profile
again observed full hull and energy in Exrun. Application selection and scan
acquired yellow Tylium, maximum HP 4,384 at 2,902 m.

`continuous-approach-001` (`d5141e88424a48329d9e4a9a976a2140`) requested
PageDown at 894 m and observed a final stop at 658 m. All components joined,
input was idle, and all 205 images and failed-reading sidecars were retained.
Its overall result correctly remains blocked: the final journal event embedded
the complete motion/portrait history, exceeded the event byte limit and dropped
one of 425 submitted events. The terminal event now stores a compact summary
and reference to the full `report.json`; a 205-observation regression verifies
complete evidence without increasing the event limit. This fix applies to
future runs and does not retroactively change the incomplete live report.

The first firing attempt was blocked before G by the 4384 current-HP geometry
guard. Its complete trailing 4 left a six-pixel slash gap. A measured raster
anchor now accepts that complete glyph while a clipped-column regression stays
rejected. All 205 retained approach images replayed with current/max HP 4384
after the correction; 19 focused HUD tests passed. Further scans identified
empty and Titanium targets to skip and blue Water HP 4,102 at 1,786 m to revisit.

`continuous-fire-002` (`a3730a1463ee45c7b79d60baaef0e15a`) then proved the
application's G effect on Tylium 4,384 at 658 m. It observed post-delivery HP
3,377 then 2,576, complete diagnostics and joined workers. The inserted key hold
was 62 ms. Later supervision saw the target and asteroid disappear, speed zero,
hull 5005/5005 and energy 419/800 recovering. The reward amount was not visible
and is recorded as unknown. This is observed damage and subsequent disappearance;
it adds no quantified reward to the earlier verified 6,158 Tylium harvest.

At the user's direction, normal native approach now has no elapsed flight
cutoff. It captures and checks distance every five seconds (`--sample-seconds`
accepts five to ten), retains routine frames without a total count cap, and
brakes at the distance threshold. Cadence-aware image-age and observation-gap
guards still reject missing or invalid observations. Two fresh post-delivery
zero-speed images reject an ignored acceleration command. A separate optional
time bound remains available only when explicitly requested for a diagnostic
trial. The stop observation window verifies cleanup; it does not limit flight.
All 349 offline tests passed, including an actual five-second quiet capture
interval, uncapped threshold completion, ignored input, persistent cancellation
and configured five/ten-second policy observations.

All four supervised probes accept a persistent `--cancel-file` marker. It
blocks pending selection, scan, acceleration and firing input. During flight,
an independent watchdog requests reserved PageDown and verifies later fresh
zero-speed images. The marker persists across subsequent probes. Cancellation
after G cannot establish ceased autofire, so supervision remains necessary.
Resource semantics, reward reading, radar avoidance and full native mission
orchestration remain unfinished. The live loop continues through these
supervised application probes while the next changes are grounded in retained
images and actual control outcomes.

The first two Water 4102 approach attempts were blocked before PageUp by speed
recognition failures. Inspection showed complete 0.0 digits with bright stars
in unused left crop padding, rather than the initially suspected nearby ship
label occlusion. Restricting speed parsing to the measured fixed text region
corrected both retained images. Intrusion into the font region and shifted text
remain rejected; all 351 tests passed and the earlier 205-frame approach still
replayed speeds 0.0–33.0 successfully.

`continuous-approach-004` (`3fb622d5b8ab4799b315d0d4184c91b0`) then exercised
the actual uncapped native path. It began stopped at 1,786 m, read speed 10.7 at
1,761 m and 23.2 at 1,677 m, with exactly five seconds between those motion
acquisitions. A later image at 1,535 m rejected speed as
`clipped_or_shifted_speed`, so the application requested PageDown and verified
fresh zeros on sequences 8 and 9. Independent supervision confirmed a final
stop at 1,304 m, full hull 5005/5005 and energy 800/800. All nine frames, the
failed-image sidecar and all 33 trace events were written, with no frame-count
cap, no drops and complete diagnostics. This is actual uncapped motion and
fault cleanup evidence, not full threshold completion. The failed image shows
complete 33.0 digits with another star in the remaining two-pixel left margin;
that strict parsing gap is the next measured correction.

The margin correction ignores only disconnected scenery components wholly
inside those unused two pixels. Components extending into the font region and
clipped real glyphs still reject. All nine approach-004 images now replay valid
speeds (0.0, 0.0, 10.7, 23.2, 33.0, 22.3, 10.1, 0.0, 0.0), and all 205 prior
approach images still replay correctly. The focused HUD suite passed 22 tests.
The original live fault result is preserved rather than rewritten as success.

`continuous-approach-005` (`ef92785b073b4ec7a1ee0e21722a8738`) then proved
the normal uncapped native threshold path on Water 4102. It started at 1,304 m,
observed 10.8 at 1,279 m, 23.0 at 1,193 m and 33.0 at 1,049 m, then requested
PageDown at 885 m. It verified fresh zeros on sequences 9 and 10. Independent
supervision at game clock 00:16:13 confirmed distance 649 m, speed 0.0, hull
5005/5005 and energy 800/800. All ten images and all 35 trace events were
retained, with no failed readings or drops, no total frame-count cap, complete
diagnostics and joined components with input idle. This completes the measured
five-second distance-check approach requested by the user, with no elapsed
flight cutoff. The Water candidate is inside 760 m optimal range and the
supervised loop continues to firing.

The optional continuous `firing_probe --observe-until-target-gone` now sends G
once and retains images without an elapsed observation or frame-count cap.
It records early damage evidence, then two fresh absent-selection images and
two following fresh zero-speed images. Disappearance cause and reward remain
unverified. A persistent cancel marker is required. Unknown current-HP digit
count is tolerated only after proved damage, with other target guards intact;
a valid field containing `None` cannot authorize baseline or G. Focused firing
tests passed 20 checks and the integrated offline suite passed 362 tests.

`continuous-fire-003` (`d36022576819446195e54953c1756562`) exercised this mode
on the Water candidate at 649 m. It delivered one G pair with a 62 ms hold and
read a post-delivery decline from 4,102 to 3,513. Sequence 22 then rejected the
HP separator, causing invalid maximum HP and identity; the original result
correctly remains blocked with complete diagnostics. The retained image shows
complete 2446/4102 digits with the slash shifted to an unaccepted position, not
a below-1000 HP transition. This supplies the next measured layout regression.

The mining agent's immediate fresh supervision at game clock 00:18:30 saw the
natural explosion, absent target and visible `+1332 X WATER`, plus 112 experience.
Speed was 0.0, hull 5005/5005 and energy 390/800. The parent independently saw
the absent target and stopped full hull at 00:18:37, with energy recovering to
586/800. The reward amount comes from the mining agent's live visual observation;
it was not parsed by the application. No second G was issued. This establishes
an actual 1,332 Water harvest following the successful uncapped approach, while
the application's acceptance remains blocked by the retained HP reading failure.
A separate immediate read-only reward capture was started; its late frames
must not be presented as proving a popup that had already faded. The supervised
loop continues after this natural stopped boundary.
