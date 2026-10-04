# Mining control center concept

Design study dated 2026-10-04. This is a proposed interface, not an implemented feature or evidence of live game state.

## Decision

Replace the scrolling observer page with a compact flight-control sidecar: one authoritative live state, one selected-target instrument, and one contextual action workspace. Keep investigation tools in drawers. Target a 720 x 840 logical-pixel desktop panel, with a compact layout for 600-800 px widths. The generated raster is a visual direction; production layout must use responsive, accessible components.

## Audit

Reviewed `tools/offline-bot/bsgo_offline/observer.html`, `observer_ui.py`, and the local connected/unattached reference screenshots supplied for this study. No game enumeration, input, server changes, or application edits were performed.

The page currently puts connection status in a large startup section, then repeats actual mode, action permission and ownership in a mode rail and again in Manual actions. Four routine cards each reserve space for phase, processing, waiting, next, result and error, even when idle or unavailable. Current readings appear in the Manual area while the selected report adds another execution panel, telemetry set, warnings, command evidence and freshness panel.

The strongest confusion is two independent state sources on one surface: attached-controller state and selected-run diagnostics. The latter can report a live saved run while the current page has no controller attached. Report selection must never change live control authority or provide replacement values for missing live fields.

| Information | Single location in the proposal |
| --- | --- |
| Window attachment; current image age; field issue count | Slim connection strip |
| Actual Manual/Autopilot; action enable; current input owner | Control rail |
| Selected identity; range; speed; hull; target HP | Main target instrument |
| Active routine; phase; actionable wait or next step | One action workspace |
| Start, Cancel/Stop and required human assessment | Contextual action tray |
| Field failures, provenance and command evidence | Diagnostics drawer |
| Run selection, historical telemetry, retained images | Separate History view |
| Focus handover preference and capability availability | Settings |

Remove the permanent explanatory paragraphs, duplicated normal-state acknowledgments, repeated owner/mode labels, empty phase tables, idle UNKNOWN result rows, raw run paths, and code hashes from the cockpit. Hide normal detail rather than losing diagnostic evidence.

## Layout and behavior

1. **Connection strip.** A green Game connection indicator only means attached. Image age measures capture freshness; it never certifies every HUD field. An amber issue count opens the exact rejected fields, reasons and source observations. Disconnected or stale state replaces the strip with one short cause and a Retry action when supported.
2. **Authority rail.** The selected mode represents actual acknowledged state. A requested mode appears only while pending, as a compact transition beside actual mode; rejection yields one actionable message. Manual means human mission decisions, whereas Input: Human / Scan / Approach represents the temporary native-input owner. Keep action enable/pause separate. The enabled state offers Pause actions; the paused state offers Enable actions with the required current-session acknowledgment. Enabling never starts a routine.
3. **Target instrument.** Show values only from the current attached observation. A scalar range tape places one target relative to the 800 m working-range boundary. It does not imply a safe corridor, heading, obstacle knowledge, mining readiness, or a radar reading. Speed is a digital value, hull and HP are compact bars. Unknown current HP is a hatched bar and an em dash; a known maximum remains visible. Every absent/rejected value exposes the explicit UNKNOWN state, reason and provenance on inspection. Never reuse a last-good value as current.
4. **Action workspace.** Scan once and Approach are scoped one-shot routines; Auto-scan is a persistent selected-target toggle. Auto-scan applies only to the human-selected target and waits for safe stopped conditions; it does not discover, retarget or brake. Selecting an action does not execute it. Only the selected or active routine receives expanded controls. One concise phase is always visible while running. Add one waiting cause or next instruction only when it contributes information beyond the phase. Contextual Cancel/Stop remains obvious and enabled for the owned routine, including when starts are blocked; stopping state prevents repeat stop requests.
5. **Contextual assessments.** Scan actions require the human stopped-scene assessment and a fresh bound context. Approach requires all four explicit assessments: clear corridor, aligned heading, safe stopped scene and configured flight point. Checkboxes are never automatically asserted. Keep expiry next to the assessment tray. Expiry, changed target/context or missing prerequisites clears stale assessments and disables Start. A proposed Preview affordance exposes the configured flight point for inspection; it is not an existing live preview capability. While a routine runs, the tray yields space to its current phase and Cancel; supporting assessment evidence remains inspectable.

The main image depicts preflight: Manual, app actions enabled, human input owner, selected Approach, no checks confirmed, Start disabled and Cancel disabled because no routine is active. The illustrative 8 s context assumes valid target identity and maximum HP. Current target HP alone is unknown, shown as `— / 4,618`; a missing maximum would invalidate the approach context and must instead show a blocked/no-token state.

Autopilot integration is currently unavailable. Its mode segment stays visibly locked and unavailable. Nearby automatic discovery and app radar interpretation are unavailable and have no active controls or invented plots in the cockpit. Capability details may explain those omissions. No mining-fire control is invented.

## Diagnostics and history

Diagnostics preserves current field validity, freshness, observation sequence, confidence, rejection reason, controller/run identity, input scope and acknowledgment detail. Requested input, delivered input and observed effect remain three distinct evidence stages inside the drawer; delivered input must never be shown as a verified game outcome. Surface a meaningful delivery/effect mismatch in the single anomaly strip.

History owns the run selector, run lifecycle, recorded telemetry, retained image consent, first failure, command trace, report coverage and writer health. Mark historical values as recorded and retain synthetic/demo labels. Keep current authority available in a fixed live rail or clearly return to Live before an action; historical data cannot arm controls or substitute for a current reading.

Settings includes optional return-to-game behavior, initially off, and explains its Start/Cancel-only scope. Existing focus and pause semantics remain unchanged.

## Visual review and limitations

The selected image is `docs/design/mining-control-center-concept-v1.png`, generated with the built-in image generation tool. It is a local brainstorming artifact and intentionally excluded from the design-brief commit. All metrics are illustrative, visibly labeled CONCEPT / Illustrative data.

Reviewed the rendered output for readable labels, one range value, unavailable Autopilot, separate input authority/action pause, partial field failure, four unchecked human assessments, expiry, disabled preflight buttons, and collapsed diagnostics/history. A targeted edit removed a duplicated range label and redundant instruction, and made the current-versus-maximum HP distinction explicit.

The raster is not a pixel-accurate implementation specification. In particular, its range-marker spacing is illustrative: a production tape must place the 2,102 m marker at exactly 70.07% of a 0-3,000 m scale and its 800 m boundary at 26.67%. The flight-point Preview is a proposed interaction. Responsive sizing, keyboard operation, contrast, accessible UNKNOWN announcements, and actual runtime state transitions still require implementation validation. No live gameplay or application behavior was tested for this design-only change.

## Exact generation prompt

The first prompt creates the base image. The final edit prompt below produces the selected artifact.

```text
Use case: ui-mockup
Asset type: polished desktop application cockpit concept, one flat screen, front-on, not a photographed monitor.
Primary request: Redesign an overlong BSGO mining assistant dashboard as an exceptionally clear compact flight-control sidecar. Less text, no duplicated facts, purposeful game instrumentation. Make a shippable high-fidelity UI mockup, not cinematic science-fiction art.
Composition: one complete 720 by 840 logical-pixel app screen, portrait aspect ratio, enlarged raster resolution for crisp typography. No scrolling, no browser chrome, no surrounding device, no explanatory annotations. Restrained 24px margins, strong spacing and aligned columns. Dark charcoal/navy instrument panel, warm white numbers, desaturated cyan active controls, restrained amber for uncertainty. Flat functional panels with subtle separators and small corner radii. Modern condensed sans labels, tabular numeric values. All labels readable at normal sidecar scale. Use every exact quoted text only once.

Header: small angular ship emblem then app name "MINING CONTROL"; small top-right label "CONCEPT". Beneath the title, a slim status line: green connection dot and "Game"; thin separator; camera icon and "Image 0.4s"; right-aligned amber warning triangle and "1 field issue" with disclosure chevron. Connection status must not imply every field is valid.

Control rail: compact segmented mode selector with selected "Manual", and a visibly disabled "Autopilot" segment with a tiny lock and "Unavailable" below it. On the same rail a small clearly labeled authority value "Input: Human". A separate right-aligned global action-control button reads "Pause actions" with pause icon and restrained amber outline; a tiny green indicator on this button shows that app actions are currently enabled. No repeated mode/status sentences.

Main instrument, largest zone: upper left label "SELECTED TARGET", below "Asteroid". Large confident white range number "2,102 m". Upper right compact digital speed instrument with "SPEED" and "0 m/s". In the center show a simple purposeful one-dimensional horizontal distance tape from the ship marker at 0 on the left, to a single modest asteroid silhouette at the range marker just past 2,000 on the right. Between 0 and the 800 tick, shade a quiet cyan band; label the boundary "800 m" and the band "Working range". Label the far tick "3,000". This is scalar distance only: NO radar, bearings, compass, blips, trajectory path, starfield or fake navigation. The asteroid silhouette is a small monochrome textured glyph, not a dramatic illustration. Directly underneath: a slim full hull bar labeled "HULL" and "5,005 / 5,005"; beside it a hatched unavailable bar labeled "TARGET HP" and amber "UNKNOWN". These are the only hull and target HP displays.

Action workspace: a row of three clear action choices "Scan once", "Auto-scan" with a small OFF toggle, and selected "Approach". Below this a compact phase row with a hollow amber status circle and "Awaiting checks", plus one short next instruction "Confirm route". No phase/process/wait/next tables.

Only this selected Approach workspace expands a contextual safety tray, titled "YOUR ASSESSMENT" with a clock icon and "Expires 08s" at its right. Four compact unchecked checkboxes in a 2 by 2 grid: "Clear corridor", "Aligned heading", "Safe stopped scene", "Flight point". Next to Flight point show a small target-cross icon and tiny "Preview" link; this is a human-inspection affordance, not a sensor assertion. Bottom of tray: a large disabled "Start" button (disabled because the required human checkboxes are unchecked) and a quiet disabled "Cancel" button. No checkboxes prechecked automatically.

Bottom footer: two collapsed disclosure controls "Diagnostics" and "History", then small gear icon. Tiny subdued footer text "Illustrative data". No expanded logs or report selector on the main screen.

Constraints: Exact literal text, no additional paragraph copy or repeated values. One occurrence of each status fact. The final must look beautifully minimal yet useful: an operator should see authority, range, action and safety in one glance. Assessments remain visible contextually and cannot be implied to be automated. This preflight example is not live telemetry. No mining start button; current available routines are scan and approach only. No nearby asteroid discovery, usable Autopilot, radar readout, map, resource rewards, cargo, fuel, charts without data, invented sensor readings, decorative dials, orbital rings, glowing neon, giant sci-fi headings, ornamental HUD clutter, dramatic cockpit scenery, watermark.
```

## Exact final edit prompt

Input: the generated base mockup; preserve its composition and make only these corrections.

```text
Use case: precise-object-edit. Edit this UI mockup with only these three changes, preserving all layout, typography, colors, controls, geometry, readability and wording otherwise:
1. Remove the duplicated small "2,102 m" above the asteroid marker. Keep the large main "2,102 m" at upper left unchanged and keep the marker itself. The range must occur exactly once in this UI.
2. In the TARGET HP row, replace the amber word "UNKNOWN" with the amber exact value "— / 4,618". This means CURRENT target HP is unknown but maximum target HP is known. Make this value fit cleanly by narrowing the adjacent hatched bar as needed. Do not fill the hatched bar. Keep the top "1 field issue" alert and the contextual "Expires 08s": this is an illustrative preflight state with valid target identity and maximum HP but failed current HP reading.
3. In the phase row remove the unnecessary vertical separator and the words "Confirm route"; preserve the icon and "Awaiting checks" as the only state text in that row.

Invariants: All remaining text exact and unchanged, one complete compact no-scroll panel, actual Manual with unavailable Autopilot, Input: Human, Pause actions, same single range tape and 800 m working band, four unchecked human assessments, Start and Cancel disabled, Diagnostics and History collapsed, CONCEPT and Illustrative data labels. No added text, no additional instruments, no radar. This is a carefully corrected design mockup, not live game data.
```
