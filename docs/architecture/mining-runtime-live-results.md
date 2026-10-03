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
