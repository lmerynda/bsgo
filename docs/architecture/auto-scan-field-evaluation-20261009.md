# Asteroid auto-scan field evaluation, October 9, 2026

## Finding

The attached dashboard was observing the game, but no scanner routine had
started in the current session. Actions remained paused (`session_paused`),
the visible-scan assessment checkbox was unchecked, and Start was disabled.
The session showed no routine package or requested/delivered action. This
explains the inspected session's inactivity; it does not establish what
happened during an earlier user attempt.

Read-only inspection ran approximately 06:48–06:52 UTC against the existing
localhost dashboard and native 2560 × 1440 capture. No gameplay input, action
enable, assessment, or Start request was sent. The game changed from an FTL
jump sequence through a loading screen to flight at 33 m/s during inspection.

## Starting the two scan actions

- **Auto-scan visible asteroids** selects the visible-pass action. It does not
  start it. Enable actions, verify a stopped/full-hull flight view, check
  **Safe stopped scene and clear game view**, then press **Start visible scan**.
  This performs one bounded pass in the current view, without camera movement
  or flight. A different view requires another assessment and Start.
- **Auto-scan OFF / Enable auto-scan** is the separate selected-target watcher.
  It scans human-selected asteroids and does not select rocks itself. It can
  be armed without a selected asteroid from a verified stopped scene.

The inspected UI displayed the visible-pass action selected, actions paused,
the assessment unchecked, and the selected-target watcher OFF. Selecting an
action or opening the dashboard deliberately sends no game input.

## What the field issues mean

| Fields / evidence | Observation | Effect on scanning |
| --- | --- | --- |
| Capture health and freshness | Connected, native-sized, healthy samples, typically under one second old | Capture is operating; health does not mean the flight HUD is readable or that the scene is suitable. |
| Raw `speed`, `hull_current`, `hull_max` | Legacy reader rejected glyphs/geometry (`speed_not_anchored`, `invalid_speed_decimal`, `unverified_speed_raster`, `hull_ratio_geometry_incomplete`) | These are real reader compatibility failures, but scanner admission has a separate restricted fallback. They are not necessarily three scan blockers. |
| `ship_telemetry` and `stationary_scan_evidence` | Later flight images read 0 m/s and hull 5210/5210; stationary evidence reported `stationary_scanner_ready` | The scanner can use this fresh stopped/full-hull evidence. Display telemetry alone never grants navigation permission. At 33 m/s, the stationary guard correctly rejects scanning. |
| Distance, target identity, portrait, target HP | No asteroid selected; target fields unknown | Expected before the visible pass clicks a candidate. Selected-target scans still require verified asteroid selection, range, portrait and HP. |
| Selection continuity, selected marker scan state, resource | Unknown without an asteroid selection and matching marker | Expected absent selection; after a candidate click these checks determine whether Z can be sent and whether a result is readable. |
| Energy | Initially 0/827 during the jump; later recovered through 96–206 and ultimately 827/827 | The visible pass requires a fresh readable reserve of at least 100 before Z. A scene assessment token alone does not establish sufficient energy. |

A twelve-read sample retained nine samples with scanner-ready stopped/full-hull
evidence and scene tokens, followed by three without that evidence during the
loading transition. One sequence was sampled twice. This is a short changing
scene observation, not an OCR accuracy or reliability estimate.

The cockpit continued displaying **11 field issues** even with readable ship
telemetry. `observer.html` counts invalid raw fields without excluding target
fields when no asteroid is selected or accounting for scanner-specific
fallback evidence. Consequently that badge is not a count of independent
scan blockers. The expanded diagnostics also print raw speed/hull as unknown
while the tiles display the separate telemetry values.

## Confirmed diagnostic API mismatch

`observation_api.py` applies selected-target compatibility and the shared
`assessment_context` to every routine. It does not project the visible pass's
`visible_assessment_context`, and `ASSESSMENTS` omits
`scan_nearby_unscanned`. Thus the read-only observation API adds
`fresh_assessment_context_required` and returns an empty required-assessment
list even when a fresh stopped-scene token exists for the visible pass.

An offline replay of captured actions isolated this mismatch: with only the
pause flags and visible routine readiness changed in the copied data, the
raw routine was ready and its fresh scene token existed, but the observation
projection still reported `machine_ready = false` with that blocker. No live
state was changed. The cockpit starts through `/api/manual-actions` and uses
the visible token directly, so this projection defect does not itself prevent
the cockpit's Start request. It makes API diagnosis misleading.

## Candidate and result limitations

The detector proposes brown textured regions, not verified world objects.
Replay of the jump image proposed four locations among the ship silhouettes;
the loading image produced zero. Those proposals were not clicked and cannot
be counted as asteroids. HUD and nearby-marker verification must still pass
twice after each click before scanning. Small/dark rocks, occlusion, HUD
overlap, changed views, unreadable range and low energy can prevent progress.
There is no explicit jump/loading scene classification; missing stationary
evidence and the supervisor's scene assessment currently supply that boundary.

The latest retained completed visible pass from October 8 reports six
selections, two delivered scans and two Water observations, with complete
diagnostics. Four selections were unverified. An earlier fifteen-selection
pass delivered seven scans but had incomplete diagnostics. These retained
reports support prior bounded operation, not success in today's session or
complete coverage of an asteroid field.

## Follow-up priorities and validation

1. Show all current Start prerequisites together: actions paused, missing
   scene assessment, stopped/full-hull evidence, then runtime energy/target
   checks. The current first-reason display masks later conditions.
2. Make field diagnostics distinguish expected missing-target data, raw reader
   failures, display telemetry and effective scanner evidence.
3. Give the observation API routine-specific context/readiness and declare
   the visible pass's required `safe_stopped_scene` assessment.
4. Reproduce the user's exact Start attempt in a stopped asteroid view,
   retaining candidate, input and image-effect evidence. No new live scan was
   executed during this evaluation.

The existing `test_visible_scan`, `test_stationary_scan`, `test_ship_telemetry`,
`test_auto_scan_ui`, and `test_observation_api` groups passed: **33 tests**,
Python exit code 0. They exercise synthetic/fixture behavior; they do not
cover the demonstrated observation projection mismatch or establish today's
live scan success.

Local evidence uses the `logs/mining/auto-scan-evaluation-20261009-` prefix:
initial/latest observation JSON, the twelve-read sample, two native images,
and test output. Screenshots, private game text and generated captures remain
local artifacts. This change records the evaluation only; runtime behavior
is unchanged.
