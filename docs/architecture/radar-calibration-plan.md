# HUD radar calibration experiment

Status: planned, October 3, 2026. No radar recognizer or radar-derived route
authorization is implemented. The user identified the HUD radar as evidence
that an asteroid lies on the ship's direct path. The next experiments will
establish which visible features support that interpretation.

## Current evidence and integration gap

The user supplied a lower-resolution, enlarged-HUD screenshot with a circular
radar and overlapping contact marks. Its visible speed is 33.0, selected target
range 1,032 m, target maximum hull 4,310 and ship hull 5,005/5,005. These are
human readings of the supplied image, not application output or a new live
acceptance run. The enlarged layout requires a new calibration profile.

`HudRecognizer` currently reads speed, target distance, selected-asteroid state,
target signature and portrait. It does not locate the radar or interpret its
marks. `assess_preflight` consumes externally supplied route evidence; the
legacy approach controller likewise requires a visual corridor and heading
assessment. Radar should contribute evidence to that route assessment.

## Experiments, in order

1. Locate the radar circle, center and heading reference in raw native frames
   for the new resolution and HUD scale. Retain the full source frame plus
   calibrated crop coordinates. Identify any zoom/range state and reject
   mismatched, clipped or hidden layouts. Do not scale the entire frame to fit
   an old profile.
2. While stopped, select a visually identified asteroid. Record stationary
   turns that put it ahead, left and right, plus camera-only changes. Verify
   whether contact bearing and heading are relative to ship orientation or
   camera orientation; identify the selected-target mark separately from
   other contacts. A turn request is not proof the turn completed.
3. Compare visually verified clear approaches with a nearer intervening
   asteroid, an asteroid beside the route and an object beyond the target.
   Include ships, planetoids, overlapping marks and rocks at different visible
   heights. Establish whether radar conveys range, vertical displacement and
   object extent, rather than assuming a two-dimensional overlap is a
   collision or that no visible mark proves clearance.
4. Replay labeled cases offline. Measure correctly accepted, wrongly accepted
   and rejected contact/heading/path readings. Include damaged radar crops,
   HUD-scale changes, marker overlap and unrelated scene changes. Reserve
   separate runs for evaluation instead of testing only calibration images.
5. After those readings pass, run one supervised, bounded movement experiment
   with an independently checked corridor and the existing speed/braking
   guards. Verify radar updates during motion and how evidence is invalidated
   by turns, selection changes, movement, takeover and layout changes.

## Proposed observation contract

Publish radar geometry, heading reference and contact measurements through the
existing immutable field/snapshot path, with validity, acquisition time,
context, source-frame identity and rejection reasons. Keep raw measurements
separate from interpreted contact class, selected-target association and
forward-path obstruction. Unknown class, range or height stays unknown.

A calibrated forward corridor must account for the ship's clearance and
turning/braking behavior; a fixed narrow line is insufficient. Expose a
three-state path interpretation: obstruction observed, assessed clear, or
unknown, with its supporting contacts and calibration scope. During the
exploratory stage, radar output has no authority to grant thrust. Combine
validated radar and flight-view evidence in the route contract; never replace
missing route evidence with an empty detected-contact list.

Retain every rejected radar image through the existing failed-recognition
archive, with field reasons and source hashes. Keep full screenshots and
private UI content local; commit only reviewed HUD-only regression fixtures
and summarized results. The next deliverable is calibrated radar observations
and offline replay evidence, followed by supervised route acceptance.
