# BSGO visible HUD reader

The local reader now reads selected-target distance and ship speed from visible
screenshots. The supported capture loop produced readings every **2.002–2.007
seconds**, without a model call between frames. It is read-only and issues no
steering, throttle, scanning or firing inputs.

## Uncertain readings for the LLM

Rejected readings now include `ocr_uncertainty` for model context. When a
complete distance or maximum-HP glyph has plausible competing font matches,
the reader supplies up to two choices per digit and four complete numeric
alternatives. Each carries a pixel `match_score`; these scores are **not
calibrated probabilities**. For example, a synthetic ambiguous HP fixture
returns 3995 and 8995, with the first digit reported as 3 or 8.

The original numeric field remains null and `valid` remains false. Alternatives
never become a flight command or substitute target identity. After stopping,
the LLM can use them to interpret context and choose a fresh observation.
Clipped or structurally incomplete glyphs report their reason without invented
numeric candidates. Rejected speed segment/decimal shapes currently report
their reason only; speed alternatives are not implemented.

`test_uncertainty.py` covers bounded alternatives, the synthetic 3/8 example,
partial-glyph rejection and unchanged valid readings. All 47 Python reader test
groups and 26 controller/capture-retention tests pass. The ambiguous fixtures
are development tests, not a measured production accuracy or probability claim.

## Results

The line-ship preflight at 1,220 m exposed a planetoid behind the translucent
speed HUD. It contributed pixels in exterior padding, while the complete speed
readout was 0.0. The restart profile discards only detached components wholly
outside its anchored text area; anything touching a digit or decimal remains
subject to the existing checks. The retained frame now reads 1,220 m / 0.0 /
HP3970. Missing speed strokes, missing decimal and attached noise still reject.
All 50 Python tests pass. Live approach acceptance remains separate from this
stopped-frame regression.

The line-ship run `miner-line04-water3813-a` used keyboard maximum/idle throttle,
observed 33 m/s, and issued idle after an 899 m brake observation. A complete
15.6 speed raster with a two-row middle gap in its narrow 1 initially failed;
the reader now accepts that full anchored shape while still rejecting missing
bar endpoints and lit middle-gap noise. The ship's final stop was visually
verified at 667 m, followed by an own notification of 1,180 Water and 112 XP.
This establishes a productive approach/harvest, while a fresh uninterrupted
OCR-completed stop remains the next live acceptance check.

The next 1,825 m preflight had a single scene pixel in an ordinary gap between
complete distance digits. The restart reader removes only that isolated pixel
between complete neighbors at normal spacing. A missing middle digit with a
remaining pixel still rejects. The retained stopped frame now reads 1,825 m /
0.0 / HP3511, without changing control or cadence checks.

The October 1 client relaunch changed HUD raster positions despite retaining
2560x1440 image dimensions. The explicit `--profile restart-20261001` calibration
uses distance one pixel higher, speed one pixel lower, the newly observed
Asteroid title raster, a complete anchored 3x3 decimal, and the measured HP
separator position. Only the narrow separator region admits its observed red
background blend; digit thresholds remain unchanged. The original profile is
still the default. Profiles reject each other's saved HUDs rather than silently
guessing a layout. Three later stopped captures read 2555m / 0.0 / HP4213 exactly;
they are correlated temporal regressions, not representative motion evidence.
`test_restart_calibration.py` verifies profile mismatch, malformed/partial HUDs,
resolution/freshness guards, and independently drawn speed patterns. All 30
reader test groups and 28 one-pass validation checks pass; the 77-frame original
audit remains exact for 71 asteroids with six planetoids rejected. Live motion
acceptance for this new profile remains pending; every controller fail-stop and
the current approach brake threshold remain active. Use a fresh worker when changing profile.

The first motion trial exposed two faint separator-stroke pixels at brightness
155/157. The restart profile now uses a brightness floor of 155 only in its
calibrated separator region; all digit thresholds and diagonal/extent/position
checks remain intact. The three saved trial frames read exactly 2555/0.0,
2548/8.3 and 2514/23.2 with HP4213 after independent visual inspection.
Missing, vertical, reversed, short and filled separator faults still reject.
All 32 reader test groups pass. These motion frames are development data from
one short failed approach; successful live approach acceptance remains pending.

The next trial exposed a distance-font variant (visually 2439 at 7.2m/s).
`calibrate_restart.py` now builds a separate complete 0–9 font bank for this
profile from eight manually labeled saved HUD regions. It excludes the date's
merged slash/day component. `restart-calibration.json` records every source,
crop and label. The original profile's font bank remains unchanged; ambiguity,
match margins, centering and complete-glyph guards remain required. All 33
reader test groups pass, including both failed motion trials. The calibration
sources are development data; successful live approach acceptance is pending.

Full-speed frame004 of `mining-20261001-2024` visibly shows 2247m / 44.0
and HP4213. This profile's complete 4 occupies 18 rows, unlike the original
profile's 16/17-row phase. Its guard now requires that measured full extent and
both anchored endpoints; clipped endpoints and missing segments reject.
All 34 reader test groups pass. This is another development regression;
successful live approach acceptance remains pending.

The braking run reached 525m at a visually verified 21.7m/s. Its narrow 1 has
the new 18-row phase and a one-row dark middle gap. The restart profile requires
that exact three-column extent, both complete anchored bands and their gap;
missing endpoints/bands and a filled gap reject. The original profile remains
unchanged. All 35 reader test groups pass. The failsafe verified the ship stopped
at491m, so the next handoff can verify identity/zero and mine without more thrust.

The Tylium approach in `mining-20261001-2051` exposed a background star at x933
inside the old oversized title crop. The restart profile now reads only the
calibrated title area (x724..833), keeping the full observed word. It additionally
requires eight complete letter components before the existing template match;
a missing or partial letter, interior highlight, and Planetoid title reject.
The original profile remains unchanged. All 36 reader test groups pass and all
43 retained postrestart approach frames accept on development replay; this is
not an independent accuracy estimate. The last original failure was manually
verified as 478m / 6.4m/s / HP3995. The miner stopped at475m with full hull.

The stopped capture in `mining-20261001-2057-stopped` has an isolated single
background pixel at the distance crop's far-left boundary. The restart profile
excludes only that separate 1x1 boundary component when the digit core begins
at least six columns later. Larger boundary contamination, interior pixels,
complete-glyph checks, centering and match thresholds remain active. All 37
reader test groups pass, including the actual 475m/0.0/HP3995 frame and partial
or erased distance negatives. Flight still requires a valid fresh reader result.
For an already stopped, unambiguously in-range target, normal mining can verify
the complete visible HUD and fire without another approach or OCR flight run.

The HP3762 preflight at2172m exposed a complete HP 2 raster absent from the
original HP font bank. `calibrate_restart.py` adds the manually inspected
current/maximum 2 instances from that saved frame to the restart profile only;
`hp_digit_sources` records the source and crops. Other HP variants, match/margin
thresholds, complete-glyph and separator checks remain unchanged. The original
profile remains unchanged. All 42 reader groups and 26 controller/retention
tests pass, including missing/occluded HP digits, separator loss and a different
real target identity. This frame is development calibration data; a fresh live
worker is required to verify the repaired profile during an approach.

The next live trial passed the HP preflight but stopped on a star in the title's
left padding. The restart crop now begins at the observed first letter x729,
removing exterior padding that extended the A's width from14 to15. The complete
eight-letter and template-match checks remain; missing or half-erased first
letters and Planetoid titles reject. All 43 reader groups, 26 controller tests
and 48 retained development approach frames pass. This replay is development
coverage, not held-out accuracy. Refresh the route after the actual movement
and use a fresh worker before the next live approach.

The following full-speed trial exposed a two-pixel star above the E in the
remaining vertical padding. The restart profile now crops the complete anchored
word rectangle x729..824/y56..65, retaining every outer text stroke while
excluding all exterior padding. Complete-letter and template checks remain.
All 45 reader groups and 26 controller/retention tests pass; exterior noise on
all four sides is tolerated, while one-pixel shifts, missing/half-erased letters,
interior contamination and Planetoid titles reject. All 53 retained development
approach frames replay valid. This does not establish independent accuracy or
successful live approach acceptance. Refresh the moved route and worker.

The next approach kept the title valid for13 readings, then rejected a faint HP
separator at751m/44.0. Actual slash pixels were RGB255/155/170 and255/153/173;
the separator-only mask now admits brightness153 and chroma spread102. Digit
thresholds and every slash position/extent/endpoint/sparsity/diagonal guard
remain. Missing strokes and malformed separators still reject. All 46 reader
groups, 26 controller/retention tests and67 development approach frames pass.
The fail-stop left the miner stationary at580m, where normal visible-HUD
verification permitted mining and a verified6704 Tylium harvest. This is a
productive recovery, not acceptance of a complete uninterrupted approach with
the latest separator calibration; future flight uses a fresh worker.

The second client launch retained the restart HUD raster. Its 23:46 preflight
visibly reads 2730m / 0.0 / HP3034; rejection came from a separate one-pixel
distance highlight at crop column2 and a nine-pixel 3x4 speed highlight in the
extreme lower-left padding. Restart-only filtering now accepts a separate 1x1
distance component in columns0..2 with the existing separation requirement,
and a speed component confined to columns0..4, rows22 onward, at most3x4 and
nine pixels. It never removes a component touching the digit area. Numeric
match thresholds, centering, complete digits/decimal, identity, freshness and
controller fail-stop remain unchanged. Larger padding fragments, missing
digits/decimal, digit-column noise and shifts clipping the crop reject in the
new saved-frame regression. All47 reader groups and26 controller/retention
tests pass;68 retained development approach frames replay valid. This is
development coverage, not held-out accuracy or new live flight acceptance.
Resume with a fresh restart-profile worker.

Day-two production exposed two more speed false rejections: independently
inspected HUDs show **27.1** and **37.3**. A separate bright pixel at speed-crop
column 9 was outside every legal glyph, and the complete 7 ended two columns
before the decimal. The reader now excludes separate components wholly before
column 18 only when they do not reach the decimal rows, and accepts the measured
two-column decimal gap. Complete digit/decimal shapes, interior contamination,
missing digits, layout, speed range and controller checks remain required.
`test_day2_speed.py` covers both raw frames and corrupted decimal/digit/noise
cases. Replay of all 33 ordinary day-two inputs recovers the two rejected frames
and preserves all 31 previously accepted readings; those 31 are agreement checks,
not new independent ground truth. See `day2-replay-results.json`. These fixes use
development data and do not establish 99% representative accuracy.

The latest saved-frame audit is in `accuracy-results.json` with independent
manual labels in `fixtures/accuracy-manifest.json`. All **71 real asteroid
frames** now return exact distance, speed and maximum-HP identity, with no
wrong accepted readings or false rejections; six real planetoids remain rejected.
The preceding reader falsely rejected six of those asteroid frames. These are
**77 distinct real screenshots across 17 source groups**, excluding every
`mock-tests` image and all synthetic cases. Calibration and development examples
are included in that regression total, so it is not a production accuracy estimate.

The whole-run development holdout is **26/26 exact** (two source runs, two HP
identities, 23 moving and three stopped frames). Twenty-five frames are one
correlated approach. This does **not demonstrate 99% representative accuracy**;
even an optimistic independence assumption gives only an 89.1% one-sided 95%
lower bound. More naturally captured production runs must supply at least 300
representative untouched frames before making that claim. No capture quota or
extra model calls are added to flight. A mistaken contact-sheet transcription
of 24.3 was corrected to visibly 24.0 after inspecting the original crop at
higher magnification; the manifest records that correction and its image.

The observed fixes add current-side HP raster variants for 4366/3338 and reject
background contamination by its measured location: a separate streak at the
speed crop's far-left edge, a separate highlight beyond the HP bar, and one
distance top-border pixel only when the complete anchored glyph core matches
strongly. Existing font mismatch/margin thresholds and malformed/occluded,
separator, decimal, layout, freshness and controller guards remain active.
Vectorized, deduplicated template matching offsets the added work. Paired warm
recognition over 780 calls per version measured median **1.55 → 1.10 ms**, p95
**1.92 → 1.39 ms**, p99 **2.18 → 1.68 ms**; PNG decoding is measured separately.
These timings exclude capture and are not a new live-loop measurement. The
historical raw capture/cycle/control timings are retained separately in the audit.

Run the saved-image audit and measured contamination regressions with:

```powershell
& $hudPython tools\hud-reader\accuracy_audit.py
& $hudPython tools\hud-reader\test_observed_background.py
```

The following describes the original development corpus and trials:

- Added **10 real screenshots**, manually labeled in `fixtures/manifest.json`.
  All 10 distance/speed readings were exact. Four asteroid frames were valid;
  six planetoid frames correctly failed the asteroid-selection requirement.
- Six distinct distances: **205, 1416, 2885, 3063, 9091, 10327**. Together these
  cover digits 0–9; these initial ten captures are stationary at **0.0**.
- Five later, independently inspected moving frames show **2205 m / 8.9 m/s**,
  **2173 m / 21.8 m/s**, **2075 m / 15.4 m/s**, **2011 m / 13.3 m/s** and
  **1993 m / 20.0 m/s** (`fixtures/moving-speed-manifest.json`). Two originally failed
  because narrow 1 and full-width 4 trim to 16 pixels high when they have no
  top/bottom horizontal segments; the last frame had a fully lit 2×3 decimal
  instead of 2×2. All five now read exactly; real full-speed and braking coverage
  remains incomplete.
- Original `sample.png` is calibration data, including its 1416 distance and
  independently annotated HP, energy and clock digits. None of the additional
  live frames build templates. This is a small development holdout, not a broad
  production accuracy estimate.
- **17 explicitly synthetic cases** exercise nonzero speeds (including 20.0
  and 44.0), all speed digits, absent target/speed, erased/clipped final digit,
  absent decimal, unknown glyphs, shifted HUD and wrong resolution. They are
  saved separately and never counted as real screenshots.
- Three offline passes plus a stale-frame check: **82 checks passed**. The
  parent's independent one-pass rerun also passed **28 checks**. The latest
  three-pass details are in `validation-results.json` (median **1.49 ms**).
- Six-frame live loop: capture **126–424 ms**, complete capture/save/read cycles
  **162–457 ms**, warm recognition **1.15–1.51 ms**. Every frame read **2885 / 0.0**.
  Evidence: `runs/trial-2s/capture-1790802778008.jsonl`.

## Recognition and rejection

`hud_reader.py` uses calibrated digit templates for the tiny distance font and
seven-segment recognition for speed. It processes three small visible HUD
regions and does not inspect the game process, assets, code or network.

It requires calibrated **2560x1440** layout, complete digit heights, centered
text, an unambiguous font match, a speed decimal and the visible **Asteroid**
title. Partial reads become `distance: null`; older values are not reused.
Planetoid distance can be returned for diagnostics while `valid` remains false.
Confidence is a pixel-match score, not a statistical probability. The old
Windows OCR script remains for comparison.

Timestamped output includes capture duration, cycle duration, frame age, start
interval, OCR request sequence and screenshot ID. Stale/future frames, wrong
request/response IDs and cycles longer than the requested interval are invalid.
Sky screenshot IDs are cache tokens: **fresh captures can all return
`screenshot-0`**. Repeated IDs are accepted and are not evidence of stale pixels.
Freshness uses capture timing, frame age and exact request correlation; this
does not prove the backend never returns a frozen image. Unmoving HUD pixels
can legitimately repeat.

A whole erased final digit in its original position is caught by centering.
An image altered into another perfectly centered valid number cannot be
distinguished from that number using this HUD crop alone. Real moving speed
coverage is currently limited to 8.9, 13.3, 15.4, 20.0 and 21.8. Recalibrate after HUD scale, font, resolution or
layout changes. The two-second reader is not a validated braking controller;
actual input latency and moving-frame accuracy remain separate work.

## Run offline

From `C:\Projects\bsgo`, using bundled Python:

```powershell
$hudPython = 'C:\Users\lmery\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $hudPython tools\hud-reader\hud_reader.py tools\hud-reader\fixtures\live\live-01.png
& $hudPython tools\hud-reader\validate.py
# Replay three passes at two-second cadence; offline, not live capture.
& $hudPython tools\hud-reader\validate.py --rounds 3 --interval 2
```

`--calibrate` rebuilds distance templates from the annotated original reference.
The separate HP-font bank also uses manually labeled **current-HP** crops in
`fixtures/calibration` (3770 from the Sol dry-run and 4292 from `live-02.png`).
The HP bank additionally uses manually labeled current-side 4366/3338 crops
from known failed preflights. These HP crops do not train distance recognition.
The maximum-HP side of calibration images and related same-target frames are
development regressions, not independent accuracy evidence. This is font
calibration on a small development set, not a population accuracy estimate.
`--serve` accepts newline-delimited JSON requests (`path`, `id`, optional
`captured_at` Unix seconds) and returns one JSON response per request.

## Run supported live capture

Use an already selected, freshly verified stationary BSGO window in Computer
Use Node REPL. Read the computer-use skill first. Start the read-only worker
with normal command execution, using a **fresh directory**:

```powershell
& tools\hud-reader\start-worker.ps1 -RunName new-trial
```

Then in supported Node REPL, with `sky` imported and `bsgoWindow` selected from
returned window objects:

```javascript
globalThis.hudLoop = await import('file:///C:/Projects/bsgo/tools/hud-reader/capture-loop.mjs');
globalThis.hudResult = await hudLoop.runCaptureLoop({
  sky, window: bsgoWindow, count: 10, intervalMs: 2000,
  outputDirectory: 'C:/Projects/bsgo/tools/hud-reader/runs/new-trial'
});
nodeRepl.write(JSON.stringify(hudResult.rows));
```

The loop uses only `sky.get_window_state`, then exchanges image paths and JSON
through local files with persistent Python. Starting a child process inside
Node REPL was unavailable (`spawn EPERM`), so the worker starts through the
command tool. OCR requests time out after 1.5 seconds; capture is sequential.
If a cycle exceeds two seconds, it records a miss and starts the next without
catch-up bursts. A stop marker ends the worker on completion/failure;
`--max-seconds` bounds it even after capture-tool interruption. No worker from
the measured trial remains running.

## Mining approach integration

### Retained OCR inputs

Every ordinary live OCR capture is retained automatically in its run directory:
the original full screenshot (`frame-*.png`), request/result JSON, and capture
and controller event logs. Both successful and rejected frames remain available;
there is no automatic deletion. The image is saved before OCR, so a worker
timeout still leaves the input and request for diagnosis. Failure logs identify
the current captured frame; a timeout without a result does not reuse an earlier
observation as that frame's OCR result.

This reuses the image write already needed for OCR; it adds no screenshot calls
or calibration work to the miner. Capture provenance distinguishes real Sky
frames from injected test observations. OCR results are predictions, not ground
truth: independently label retained images and hold out entire source runs for
accuracy evaluation, excluding duplicate and synthetic frames. Preserve the raw
inputs when adding derived crops or regression fixtures.

### Approach workflow

`approach-controller.mjs` adds one bounded approach, with **dry-run by default**.
The miner retains UI ownership and must select and scan a wanted asteroid,
visually establish a clear corridor, and verify the heading before live mode.
The controller does not find routes, turn, select targets or scan. Use this
decision sequence:

1. With the ship stopped and a clear, aligned approach verified, command
   **MAX_THROTTLE**. This sets the control to its maximum; do not adjust throttle
   to hit a numeric speed or wait to reach one before monitoring distance.
2. Sample the selected target locally every two seconds. When the first valid
   distance is **at or below X**, command **ZERO_THROTTLE** immediately. Current
   **X = 900 m**, exported with the sampling interval as `APPROACH_DEFAULTS`.
   The line-ship mining loop verifies the stop and range at most 800 m before pressing G.
3. Verify deceleration, then a stopped speed reading. The final distance is an
   observation; do not reposition just to reproduce an earlier stopping range.

The user-provided controls screenshot confirms **PageUp = Full speed** and
**PageDown = Idle speed**. The controller sends `Page_Up` for MAX_THROTTLE and
`Page_Down` for ZERO_THROTTLE through Sky's keysym-style API. Idle throttle is a
control setting; verify actual deceleration and stopped speed from fresh HUD
readings. The shortcut inputs still need live effect verification. Numeric
maximum speed in the policy is solely a telemetry plausibility bound. Historical speed, braking and
stopping measurements remain in the operation/event logs, not operating targets.

Earlier moving-font OCR failures stopped the controller safely and were fixed
against the saved frames. Keep all freshness, target identity, cadence and
braking-effect checks active on subsequent approaches. Use the default
`fireWhenBraking: false` for Scan → Assess → Move → Stop → Shoot → Repeat;
the miner presses G after a verified stop and range at most 800 m.
Press G to fire the mining guns together. Keep combined brake/G disabled for
the normal stop verification sequence.

Start the same Python watch worker in a fresh run directory first. From the
supported Node REPL:

```javascript
globalThis.approachModule = await import('file:///C:/Projects/bsgo/tools/hud-reader/approach-controller.mjs');
// Read one fresh HUD frame and report the planned action; no game inputs.
globalThis.approachResult = await approachModule.runApproach({
  sky, window: bsgoWindow, mode: 'dry-run',
  outputDirectory: 'C:/Projects/bsgo/tools/hud-reader/runs/approach-canary'
});
```

For the miner's live canary, start another fresh-directory worker and use:

```javascript
globalThis.approachAbort = new AbortController();
globalThis.approachResult = await approachModule.runApproach({
  sky, window: bsgoWindow, mode: 'live',
  corridorVerified: true, headingVerified: true,
  routeAssessment: verifiedRouteAssessment,
  brakeAt: 900, intervalMs: 2000, count: 40, maxDurationMs: 90000,
  wantedResource: true, fireWhenBraking: false,
  signal: approachAbort.signal,
  outputDirectory: 'C:/Projects/bsgo/tools/hud-reader/runs/approach-live-canary'
});
```

Use a tool timeout longer than the bounded 90-second run. The two Boolean
prerequisites describe the miner's verified current view; do not set them from
this example alone. Live mode also requires `verifiedRouteAssessment`:

```javascript
// Fill these fields from the actual latest observation; this is a shape example.
globalThis.verifiedRouteAssessment = {
  captured_at_utc: '<actual screenshot capture UTC>',
  screenshot_ids: ['<actual observed screenshot cache token>'],
  target_identity: '<candidate identity from fresh OCR>',
  candidate_distance: 1500, // Replace with the actual measured range.
  notes: '<visible corridor and heading evidence from inspected views>',
  intervening_objects: [
    { in_corridor: false, range: 10019, notes: '<observed object/range and why outside route>' }
  ]
};
```

Static asteroid clearance has no fixed time expiry. Reuse it during the same
stopped setup while ship position and selected asteroid remain unchanged.
Reassess after ship movement, target loss/reselection, a client restart or
human UI takeover; elapsed tool-call time alone does not require a new check.
Camera-only changes do not move the corridor, but heading must still be verified.
The miner tracks these state changes; the distance/speed crop alone cannot prove
an unchanged ship position. Before thrust, fresh OCR must show a stopped ship
and match the assessed candidate's identity and distance within 30 m. Record
intervening objects, their measured ranges (or `null` when unknown), and whether
they cross the intended corridor. An empty object array means the inspected
views identified no intervening objects; omission is rejected. Unknown depth
inside the corridor is rejected, as is an object before/near the target.
Recorded ranges and Boolean assertions do not replace visual clearance: the
miner still assesses geometry, object size and multiple views. No object
detector or automatic clearance proof is claimed.

Optional `fireWhenBraking: true` performs zero throttle
and G consecutively in the same callback, only with a verified wanted resource.

The initial frame must show a selected asteroid, speed **0.0** and at least
**1,150 m** distance at the default 900 m threshold. The mining workflow prefers
stopped targets around 680 m and never mines beyond 800 m, regardless of the
larger technical range in equipment tooltips. It commands full throttle,
samples continuously, brakes at the first valid range **≤900 m**, then keeps
sampling until the speed reads **0.0**. Because samples are discrete, the first
range can be below the threshold; the log reports the actual last observed distance,
without claiming an exact distance at input time. Firing is never required for
the canary. It logs input start/return timestamps, input durations, pre-command
range/speed, route evidence, final stopped range and failure/cleanup events.

A returned zero-throttle call does **not** establish that braking engaged.
Fresh speed must show a decrease of at least 0.5 or reach zero within **two
samples / four seconds**. One residual full-speed or 40-speed frame is allowed;
a second full-speed frame fails. Any renewed increase greater than 0.3 fails
immediately. Once deceleration starts, a four-second plateau or thirty seconds
without verified zero also fails. Failure reissues the known zero control and
captures fresh state instead of waiting for the overall 90-second bound. A
cleanup screenshot alone leaves OCR zero unverified. These short guard timings
are conservative software bounds, not measured physical braking constants.

Invalid or stale OCR, changed visible target identity, non-closing trajectory,
impossible range changes, a long observation gap, cancellation, failed reads
or a cycle exceeding the requested cadence trigger zero throttle and a fresh
cleanup snapshot. Finishing the count/duration without a verified stop also
commands zero. Unknown OCR never allows continued coasting. The loop awaits
the action callback **before disk logging** and never overlaps input calls.

Target identity is the **recognized maximum HP**, such as
`asteroid:hp:3770`, plus a separate coarse-portrait comparison. It excludes
current HP so hits can decrease it. Maximum-HP digits use the calibrated HP
font, a maximum mismatch of 0.20 and a nearest/next-best margin of 0.035;
incomplete or ambiguous digits invalidate the observation. The current
calibrated format requires a slash and four-digit asteroid maximum HP.
The slash check accepts the observed 10×6 and 9×5 raster variants only when
they occupy the calibrated separator position and form a sparse rising
diagonal with both endpoints and sufficient row coverage. A later real
preflight frame lost one terminal white slash pixel at the recognition
threshold; it still reads **3770/3770, 2211 m, 0.0**. Missing, vertical,
reversed, dense, clipped and displaced separators are regression negatives.
The prior raw 480-bit identity falsely rejected the actual Sol dry/live pair
because two mask pixels changed. Both saved frames now recognize **3770 HP,
2211 m, 0.0**, without relaxing the controller's exact semantic identity check.
Old bitmap route assessments must be refreshed before using the new reader.
It remains a visual guard, not a unique game ID: identical-looking same-HP
targets may coincide. The miner must exclusively own selection.
If cleanup OCR is unavailable, the snapshot is returned by the supported
capture API and the log explicitly leaves zero speed unverified. Backend calls
do not expose cancellation; cleanup executes when a pending call returns.
Abrupt termination of the tool/runtime can bypass JavaScript cleanup.

Controller event histories were tested against mocked Sky controls (no live
inputs), including failure cleanup and exact OCR request/response correlation:

```powershell
$hudNode = 'C:\Users\lmery\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
& $hudNode --test --test-isolation=none tools\hud-reader\controller.test.mjs
```

`--test-isolation=none` avoids child spawning unavailable in this sandbox.
All 22 controller checks pass, including ignored braking, delayed legitimate
deceleration, speed increasing again, braking plateau, route-evidence checks
and repeated fresh `screenshot-0` IDs. Eight additional image-identity tests
cover the actual two-pixel failure pair, the later real slash-raster variation,
different real target HP, non-asteroid type, occluded/missing/noisy maximum HP,
six malformed separator cases and current-HP decrease. The controller regression
also verifies changed HP, portrait and type remain rejected. Run them with:

```powershell
& $hudPython tools\hud-reader\test_identity.py
& $hudPython tools\hud-reader\test_speed.py
```

Ten speed test groups cover all five actual moving frames, all ten fraction
digits and narrow 1 variants, modest brightness/background noise, clipped or
erased vertical bands, a filled middle gap, partial segments, decimal faults,
shifted layout and speeds outside 0–44. The narrow 1 requires both anchored
vertical bands and a dark middle gap; arbitrary short strokes never default
to 1. Decimal position and spacing reject an erased middle integer digit.
Full-width 4 is classified from its fixed-position segment pattern before its
16–17 pixel extent and anchored endpoints are checked; other full-width digits
retain their 17–18 pixel requirement. The actual 4 raster is tested in all
legal integer/fractional positions and with endpoint/segment corruption.
The decimal permits only a fully lit 2-high, 2- or 3-wide rectangle anchored at
rows20–21. Missing corners, misplaced/extra dots and excessive height/width
remain invalid; all decimal-spacing and segment-ambiguity guards are retained.
These tests establish software behavior; the miner still needs a clear-route
live canary to verify the remaining speed range and intended braking.
