# Live game-property inspection, October 8, 2026

The installed BSGO launcher initially displayed a blank Login page. A normal
launcher restart restored the page; submitting its existing saved sign-in and
using Launch opened the game. No saved password was revealed or extracted.
The mining dashboard was restarted and connected to the new native 2560 × 1440
game window. Assist actions remained paused throughout this inspection.

## Readings verified in this session

| Visible game property | App observation | Result |
| --- | --- | --- |
| Stopped speed 0.0 | `speed: 0.0` | Matched the HUD. |
| Own hull 5,210 / 5,210 | `hull_current: 5210`, `hull_max: 5210` | Matched the HUD. |
| Regenerated energy 827 / 827 | Display-only `ship_telemetry` 827 / 827 | Matched the HUD; not an action field. |
| Selected planetoid range 8,289 m | `distance: 8289`, `asteroid_selected: false` | Correct range and asteroid exclusion. |
| Selected asteroid range 1,598 m | `distance: 1598` | Matched the HUD. |
| Selected asteroid health 4,859 / 4,859 | Target HP fields 4859 / 4859 | Matched the HUD; identity and portrait were available. |

With no target selected, range and target identity/HP were unavailable. Those
values were absent in the game too; that state did not establish an OCR failure.
The selected asteroid's instrument panel subsequently showed zero field issues.
This is one stationary development observation, not general reader accuracy or
motion validation.

## Scan observations and gaps

The supervisor selected the asteroid, changed the camera and pressed Z while
the ship was stopped with full hull. Energy consumption was visible after Z.
The app itself remained paused and did not start a scanning or mining routine.

The observer's internal `selected_marker_scan_state` became `scanned`, with
reason `measured_bright_scan_texture`. Offline replay of the app's retained
before/after display images independently reproduced an unknown baseline
(`scan_texture_unverified`, zero bright chromatic pixels) and a scanned sample
(1,038 bright chromatic pixels, fraction 0.4213). These JPEG display images are
separate from the observer's original lossless capture evidence. Unknown before
the input does not prove unscanned, and this replay does not establish mineral
composition, scan attribution or a complete mining cycle.

Two concrete gaps remain:

- `ManualAssistController._current()` filters the internal marker scan state out
  of `current_observation.fields`. Consequently `/api/observation` also omits it,
  even though the diagnostic status contains fresh scanned-marker evidence.
- The cockpit's Scan findings panel still says "No verified resource findings.
  Select an asteroid in the game to use selected-target scanning" while an
  asteroid is selected and internal scanned-marker evidence exists. Resource
  composition remains unsupported: `scan_result` reports
  `mining_recognizer_not_validated:scan_result`. A scanned texture alone must not
  be turned into a Water/Tylium finding.

The internal `mining_active`, `inventory` and `inventory_conflict` fields likewise
remain explicitly unsupported. The public API and its issue count project only
the supported cockpit fields; zero field issues does not mean those missing
capabilities work.

## Evidence and next work

Local API snapshots, version-identified display frames and replay results are in
`logs/mining/property-inspection-20261008/`. The passive session is
`logs/mining/live-20261003/manual-dashboard-20261008-180159-fd614872/`, including
its original diagnostic evidence. Full frames and local artifacts are excluded
from this documentation commit.

The next useful change is to expose fresh selected-marker scan evidence through
the observation API and distinguish it from verified resource findings in the
cockpit. Resource composition needs separate image evidence and validation.
The game remains open, the ship stopped, and app actions paused. No approach,
weapon firing, docking, loadout change or spending was tested.
# Scan display implementation

The Manual observation now publishes `selected_marker_scan_state` and
`selection_track_id` from the same captured frame. The read-only API preserves
their freshness checks and rejects a scan state without a fresh selected
asteroid from that image. The local track is continuity evidence, not a world
object identifier.

The cockpit shows current scanned appearance while actions are paused. Missing
color means unverified, not unscanned. Stale images and target loss clear the
current finding; retained routine results are labeled recorded. This change
does not attribute a texture to a particular Z press or supply resource identity.

Validation: three scan-display regressions cover the real loopback HTTP path,
paused control, stale/wrong-frame/invalid-target rejection, and the cockpit's
current versus historical wording. Existing observation and ship-display
regressions also pass. Native texture detection remains the previously measured
reader; this implementation exposes its evidence.

## Selected resource display

`selected_scan_resource` now reports `Water`, `Titanium`, `Tylium`, or `empty`
when a fresh selected asteroid has a uniquely located native world label and
a substantial dominant resource color in its texture crop. The cockpit labels
this as color observed. The API rejects unknown resources, absent scan
appearance, target loss, stale fields, and fields from another image. Quantities,
inventory, rewards, nearby discovery, and automatic mining remain unavailable.

The resource reader uses conservative HSV bands measured from the reviewed
036-038 Water/Titanium fixtures and native 019/025 Tylium/empty captures. A color
needs at least 200 pixels, 20% of the texture area, and 75% of the qualifying
chromatic pixels. Water/Titanium use brightness above 0.65, Tylium above 0.55,
and the much darker red empty texture above 0.25. Hue gaps and these thresholds
deliberately leave dim, small, occluded or ambiguous textures unknown. This is
a development corpus, not a general accuracy estimate. Fixture provenance and
source hashes are retained with texture-only crops that exclude chat/account UI.

Validation: 36 selected-scan tests, 12 launcher tests, 14 Manual controller tests,
and nine Manual scan-toggle tests pass. Replaying the October 8 post-scan JPEG
also yields Water; that is retained-image evidence, not a new live scan.

The updated dashboard was restarted after verifying the previous observer's
workers had drained. The reopened game was docked. The internal browser
confirmed fresh game capture, Human ownership, paused actions, and the new
"No asteroid selected" finding. Automatic approval review rejected Undock
twice, including a retry presenting the skill's recorded standing authorization.
A direct authorization question was sent to the user. Fresh live asteroid
validation is pending that response; the ship remains docked.

The broader 676-test run found an existing archived-preview target-label
mismatch, reproduced independently in its seven-test group. It also overlapped
the final geometry-guard edit and consequently ran the earlier scan UI harness
against the updated script. The final selected-scan group was rerun separately
and all 36 tests pass. The archived-preview mismatch is handled as a separate
fix rather than changing the resource reader to satisfy an obsolete label.

Final verification: all 676 offline-bot tests pass in the subsequent run. The
archived preview now uses the same guarded scanner-only target-health fallback
and distinguishes a verified absent asteroid from an unknown target. The final
running API publishes null scan/resource values while docked, with Human input
ownership and actions paused. Fresh live asteroid validation is still pending
the explicit Undock approval; no rejected Undock action was executed.

## Authorized live validation

The user subsequently explicitly authorized free playing. Undock succeeded
after that authorization. The game opened in Vidofnir beside the Fortified
Cylon Outpost. The ship stayed at 0 m/s while its initially damaged hull
regenerated to 5210/5210 before scanning; energy was 827/827. No approach,
weapon firing, resource reward, or spending was performed during this check.

A selected asteroid showed 5128/5128 HP at 1497 m. One Z scan changed its
visible texture to blue. A fresh API observation then reported both
`selected_marker_scan_state = scanned` and `selected_scan_resource = Water`
as usable, with reader reasons `measured_bright_scan_texture` and
`measured_selected_resource_color`. Range, speed, target health, own hull and
energy also matched the visible HUD. The cockpit displayed "Scanned appearance
observed · Water color observed" with zero field issues. This is a new live
Water check; the other resource classes retain corpus validation only.

Cycling camera views placed the marker outside the bounded search region or
behind the ship model. Those observations kept scan/resource values unknown.
Camera sway also occasionally produced an unknown cockpit finding; no stable
accuracy estimate or continuous recognition guarantee follows from this check.

Switching to a planetoid verified usable `asteroid_selected = false`; the API
cleared scan/resource values to null and the cockpit displayed "No asteroid
selected" rather than retaining Water. Two clicks on the small blue texture
did not restore selection; the planetoid remained selected with the ship
stopped. Human ownership, paused app actions and disabled
Auto-scan were preserved throughout.

Local evidence is retained under
`logs/mining/property-inspection-20261008/`: `live-water-api.json`,
`live-water-current.jpg`, `live-water-cockpit.png`,
`live-target-change-api.json`, and `live-target-change-cockpit.png`.
These captures remain local artifacts and are excluded from this commit.
The API reads are non-atomic and the screenshot/API evidence is sequential,
not a simultaneous snapshot. Quantities, inventory, nearby discovery and
automatic mining remain open features.

## Selected-target auto-scan usability

The watcher can now be armed from a fresh stopped/full-hull scene before an
asteroid is selected. Its scene token cannot start a one-shot scan or approach;
later Z input still uses the guarded selected-target probe. The cockpit explains
watching, scanning, already attempted, out-of-range and restart states and
displays attempt/input counts separately from observed resource appearance.
Input ownership reflects the active scan lease, and stopping disables repeat
stop clicks until cleanup finishes.

Live validation enabled the watcher with no target, selected HP5790 at 1026 m,
and observed one automatic Z delivery and dark red scan texture (resource
reading unknown). A friendly ship briefly overlapped the next intended click;
the actual non-asteroid selection produced no scan. Selecting HP5498 at 1040 m
produced the second automatic Z without restarting the toggle. The texture
turned yellow and the API subsequently reported fresh scanned/Tylium evidence.
No manual Z, movement or firing was performed. Stop and Pause returned the
app to Human ownership with actions paused. Proof remains local in
`auto-scan-first.json`, `auto-scan-second.json` and
`auto-scan-selected-live.png` under the existing evidence directory.

The 42 focused checks pass. The broader 680-test run had one local HTTP
connection abort in the archived preview write-method rejection check; its
seven-test group passes on recheck. All other checks in that run passed.
This increment covers human-selected targets; automatic visible-target
selection is the next requested feature.

## Automatic visible asteroid selection and scanning

The requested visible-target pass is now implemented in Assist. It proposes
small brown textured objects from the stopped native 2560 x 1440 flight view,
excluding HUD, chat, sidebar and the own-ship area. Proposals are not asteroid
identities or evidence that a rock is unscanned. After a click, two fresh
consistent asteroid HUD images and a nearby selected world marker must agree
before the routine can send Z. Unverified selections and targets beyond
4,000 m are skipped. Known scanned markers are recorded without Z. A fresh
readable energy reserve of at least 100 is required before a scan.

The pass checks fresh zero speed, full hull, capture identity and a coarse
stationary-view fingerprint before input. View changes or unreadable ship
state stop the routine. Spatially attempted candidates are suppressed within
one pass; this is not a world-object or cross-view cache. The pass is bounded
to 32 candidates or four minutes, with no flight or camera inputs. Stop and
Pause use the existing cancellation/lease cleanup. Model checks cover stop
after selection, non-asteroids, out-of-range targets, low energy, changed
view, known scanned selection, and temporary post-scan label occlusion.

Visible scanning now has its own stopped-scene assessment token. This prevents
the animated selected-target portrait from repeatedly clearing its Start
assessment. Selected-target scan and approach retain their target-specific
tokens. The default Assist workspace shows the visible pass, its counts,
resource observations, skipped/unreadable results and Stop control.

The first live attempt selected HP5299 at 2590 m and delivered Z, but a
temporarily hidden marker stopped the post-scan check. The subsequent complete
pass selected 15 candidates and delivered seven scans, including a readable
Titanium observation at 2941 m. Its input workers drained cleanly, but its
128-image diagnostic budget omitted 22 frames. Retention was increased to a
bounded 640 frames for the four-minute pass; incomplete packages still report
a blocker rather than success.

The final live pass completed with six delivered selections and two delivered
scans, producing Water observations at 3008 m and 2528 m. Four selections
remained unverified and received no scan input. The run reports
`visible_pass_complete`, complete diagnostics, and drained dispatcher,
recognition, source and input state. Pause then left actions paused and Human
ownership, with speed 0 and hull 5210/5210. No manual Z, thrust, firing or paid
action was used. Several small/dark targets in the earlier pass remained
resource-unknown; candidate recognition is deliberately incomplete.

The 689-test regression run passed. All 65 focused checks pass after the final
retention fix, including the new animation and known-scanned regressions.
Local evidence is retained under `logs/mining/property-inspection-20261008/`:
`visible-pass-v3.json`, `visible-pass-final.json`,
`visible-full-tests.txt`, `visible-final-focused-tests.txt`, and
`auto-scan-visible-live.png`. Native screenshots, game chat and generated
diagnostic packages remain local and are excluded from the feature commit.
