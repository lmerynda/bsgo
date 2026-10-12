# BSGO mining cockpit: Astra design review

Design study, 2026-10-07. Eight original concept images, an interactive read-only prototype, and the implementation specification below. This directory does not modify the production mining app or permit game input.

## Initial recommendation

The subsequent accepted [mode-centered direction](selected-direction.md) uses **07 Signal first** as the foundation and **01 Flight deck** for the original game-view arrangement, with Assist / Autopilot central. That decision takes precedence for product hierarchy. The recommendation below and prototype are preserved as the initial study and inspiration; their observation and control contracts remain applicable.

Use **01 Flight Deck** as the layout foundation, **07 Signal First** for typography and operational clarity, and **02 Mission Stepper** for the Select → Assess → Start sequence. Adopt **08 Adaptive Command** for narrow screens. The functional synthesis is `prototype.html`; the images explore visual directions rather than defining execution semantics.

The game screen is an external, immutable source. The user explicitly clarified that we cannot change the game screen and should not draw over it. Preserve the complete original captured frame, its aspect ratio and colors. All app instruments, labels, buttons, freshness indicators and warnings belong outside that rectangle. No overlays, fake crosshairs, recoloring, brightness controls, crop, blur or dimming. A stale retained frame is labeled in its header and footer, while its pixels remain unchanged. The fictional spaceship scenes and HUD marks generated inside the eight concept artworks are illustrative placeholders, not proposed changes to BSGO.

The most useful improvement is a clearer answer to four questions: **What can I trust? Who owns input? What is the selected routine doing? What must I do next?** More decoration would not answer these questions.

## What was inspected

- The actual running production dashboard at `http://127.0.0.1:8765/` through source and supplied screenshot, and the refreshed cockpit at `http://127.0.0.1:8874/` through a rendered 1280 × 800 browser snapshot.
- `tools/offline-bot/bsgo_offline/cockpit_preview.html`, `observer_ui.py`, and `observation_api.py`, plus the prior `docs/design/mining-control-center-concept.md` design contracts.
- Real normalized observation data through the prototype. The observed live state included display-only HUD speed/hull/energy, an unverified asteroid identity and usable generic HUD distance. These are separate facts.

The refreshed cockpit already has a sensible dark theme, a protected full-frame game view, a distinct manual mode, and useful checks. Its weakness is hierarchy: numerous small uppercase labels and large panel headings consume space, the routine's reason is less prominent than the range tape, and `UNKNOWN` target identity sits next to a confident distance. The old displayed issue count could say zero while the normalized observation exposes multiple unusable fields. The new design names the ambiguity directly: **HUD range · Target type unverified**, with **Display-only HUD reading** attached to values that do not qualify as action evidence.

## Eight directions

| Concept | Useful qualities | Tradeoff and what to keep |
|---|---|---|
| 01 Flight Deck | Strong wide-feed / task-rail balance, good grouping, clear persistent actions. | Best foundation. Remove decorative icons and duplicated values. The art's prechecked assessments and bright paused Start are incorrect; the prototype uses unchecked, context-bound checks and disabled live actions. |
| 02 Mission Stepper | Excellent distinction between choosing a task, reviewing the scene, and starting. Clear disabled Start. | Keep the sequence as compact labels in the task rail; its large horizontal stepper takes too much height at 800px. |
| 03 Instrument Bridge | Easy instrument scanning and clear task procedure. | Three columns divide attention and shrink the game image. Event trail belongs in History rather than permanently consuming the feed. |
| 04 Daylight Operations | Light chrome demonstrates a useful alternate reading environment. | Navigation and repeated cards cost too much room. Retain as a possible later theme, with contrast validation; no second navigation system is needed now. |
| 05 Cinematic Focus | Largest image and a strong compact instrument shelf. | Generated labels within the game view violate the user's later constraint. Borrow the generous image area only; place every app status item outside the image. |
| 06 Dual Workspace | Best plain-language next-step prompt and generous assessment explanations. | A 50/50 split makes the feed too small. Generated confidence percentages are unsupported and must not be implemented. Keep the next-step copy and contextual help. |
| 07 Signal First | Clearest text hierarchy and most readable operational status. | The giant warning and repeated authority/vitals create duplication. Keep contrast and readability, reduce repetition. Generated cancellation copy must not imply clearing the game target. |
| 08 Adaptive Command | Good demonstration of desktop-to-narrow priority and persistent routine controls. | Simplify repeated state. The narrow fallback must expose pause and cancellation without covering the original image. |

All eight were generated with eight separate calls to the built-in `image_gen` tool and visually reviewed. `prompts.json` plus the explicit suffix in `generation-note.txt` preserve the exact input prompts. The generated PNGs were removed in the October 12, 2026 cleanup (the repository keeps no images). Generated dates, slogans, fictional game graphics, confidence percentages, duplicated metrics and inaccurate button states are not product requirements.

## Recommended layout

**Header:** short product name, quiet product context, and an unmistakable read-only badge in this prototype. In production, omit the prototype badge. Global authority belongs in the next row rather than in a large hero heading.

**Status strip:** game attachment, reading age, field-health disclosure, actual mode, current input owner, unavailable autopilot, and global Pause actions. Attachment is not a claim of fresh capture, fresh readings or a valid asteroid. A requested mode must appear separately as pending until acknowledgment. Unavailable autopilot is text, not a tempting enabled toggle.

**Desktop main area:** a broad visual region and a fixed-width routine rail. At 1280 × 800 use a roughly 365px rail. The left side consumes the remaining width. At 1920 × 1080 the rail grows only to about 408px, while the game view absorbs the extra width. Do not enlarge text and margins so far that the operator loses useful scene area.

**Instruments:** current target range, speed, target health, hull and energy. On desktop, combine all five in one shelf above the image. At taller desktop sizes, increase shelf type and spacing slightly while preserving the image area. On narrow screens, use a three-part target shelf and a small two-part ship shelf. Every value has a unit and provenance/availability line. No decorative radar, flight path, bearing or invented nearby-target list. A range value by itself is never evidence of a clear corridor or safe flight.

**Original game view:** header with frame freshness, then a pure image canvas, then a footer with source dimensions and frame version. Preserve native aspect ratio using `object-fit: contain`. Black letterboxing is preferable to cropping. The game capture itself may contain BSGO HUD text; the application adds no text inside the image. Image freshness is independent from normalized reading freshness because those providers are collected independently.

**Routine rail:** Select routine; a single prominent state and useful next instruction; Your scene assessment; persistent action area. Scan once, Auto-scan selected and Approach are mutually exclusive choices, not start buttons. Auto-scan explicitly applies only to the human-selected target and never steers, retargets or brakes. The active routine owns the displayed execution state even if a different routine was previously selected. Keep Cancel available for the active routine while Start is blocked.

**Lower navigation:** Diagnostics, History and Settings open focused panels. Avoid permanent debug logs, run paths, schema names or private file paths in the operator surface. History is visibly recorded data and never replaces current instruments. Diagnostics includes rejected fields and reasons, reading provenance, assessment blockers, and the request → delivery → observed-effect chain.

**Narrow layout:** below 850px, stack status, instruments, image, ship condition, then routine workspace. The authority strip remains sticky. The routine action area stays visible at the bottom of its own section with layout space reserved. It must not cover the image. Permit vertical scrolling instead of unreadably shrinking all text or compressing the entire workflow into one phone-height screen.

## Data and interaction contracts

| Situation | Required behavior |
|---|---|
| Known zero | Render `0 m/s` or `0 hp` as a real value, with provenance. Never convert numeric zero into Unknown through truthiness checks. |
| Unknown/rejected value | Render `Unknown`, or `— / known maximum` for a partial pair. Explain the unavailable field in Diagnostics. No last-good number appears as current. |
| Stale normalized reading | Expire in the browser using `valid_for_seconds` and elapsed monotonic client time, even if polling stalls. Replace current numbers with Unknown. Clear assessments. |
| Stale retained image | Leave image pixels unchanged. Put `Retained image · not current` outside the canvas. Do not imply that retained imagery is current evidence. |
| Generic distance, unverified asteroid | Label the number `HUD range` with `Target type unverified`. Use `Selected asteroid range` only with usable selected-asteroid and identity fields. |
| Display-only speed/hull/energy | Show the useful current HUD value with `Display-only HUD reading`. It cannot satisfy calibrated motion, full-hull or readiness requirements. |
| Game disconnected | Connection state says disconnected; current instruments become Unknown. Explain the connection action in the production app. This prototype cannot reconnect the game. |
| Controller status unavailable | Input owner becomes Unknown, and no inferred safe/idle state replaces it. In production keep a pause request available if ownership may still exist. |
| Manual / input owner | Manual identifies the acknowledged control mode. Input owner identifies Human or the current routine. These are different facts. |
| Routine selected | Update help and required checks. Do not emit input or automatically enable/start a routine. |
| Actions paused | Explain pause reason. A production Enable action requires the current session acknowledgment; enabling still does not start anything. |
| Machine ready | Machine readiness does not verify human scene judgments. Start requires fresh bound context plus all required explicit assessments and no competing owner. |
| Assessment checked | Bind to current connection/context/target and expiry. Clear when these change, context expires, selection changes, Start is rejected, or a conflicting action begins. Never precheck. |
| Approach | Require corridor, heading, stopped-scene and flight-point checks. Expose flight-point inspection without moving the pointer. |
| Scan once / Auto-scan selected | Require stopped-scene assessment and current applicable context. Do not pretend the auto-scan toggle searches the world. |
| Start pending | Show pending acknowledgment, prevent duplicate Start, keep the applicable stop route accessible. Do not label as running until accepted/current state proves it. |
| Start rejected | State the current reason in plain language, clear invalid assessments, preserve selected task and offer the correct next step. |
| Running | Show routine, meaningful phase, input owner and next verified step. Distinguish injected input from confirmed image effect. |
| Cancel requested | Show stopping, then confirmed cleanup if returned. Repeated cancellation requests should be bounded. Never claim a complete stop from a request alone. |
| Cancellation unverified | Keep an explicit warning and the production global pause route accessible. Do not render ordinary idle or confirmed stopped state. |
| History opened | Label recorded data. No history reading becomes a fallback for live instruments, ownership or readiness. |

## Prototype scope and review

`prototype.html` reads only same-origin `GET /api/observation`, `/api/live-frame`, `/api/live-image` and `/api/runs` through the isolated preview proxy. It has no gameplay-write calls. Poll failures clear current observation claims; retained game imagery is visibly labeled outside the image. The proxy independently rejects write requests.

The **Preview state** menu demonstrates ready-for-review, stale readings, active approach, rejected start, cancellation unverified and disconnected states. Demo data is prominently identified; live imagery is hidden during demos so simulated telemetry cannot appear to describe a real scene. Demo checks expire after 30 seconds, routine changes clear them, and Start/Cancel/Pause change only in-page state. The live-mode buttons remain disabled. `window.astraPreview.chooseScenario(value)` is provided for repeatable local inspection.

Settings affect interface density only; the game image is always preserved. History currently lists the most recent saved run summaries. Rich run inspection, flight-point visualization outside the capture, production request acknowledgment and live action wiring are subsequent implementation work, not completed functionality in this prototype.

Initial rendered inspection at 1280 × 800 found that stacking a large instrument shelf, image and separate vitals made the contained game image too small. The compact shelf revision measured a **863 × 447px frame area**, with a native-16:9 image of approximately **795 × 447px**, versus approximately 720 × 405px in the prior cockpit screenshot. The page fit in 800px and the routine rail had no internal overflow at that inspection. Root-thread browser QA provides the final validation record; these measurements are one observed desktop configuration, not a universal guarantee.

## Production implementation order

1. Extract a presentation view-model from the normalized read-only observation schema. Keep calibrated and display-only fields separate, and expire values locally.
2. Implement shell, authority strip, immutable image canvas, responsive instrument shelves and responsive task rail. Verify 1280 × 800, 1920 × 1080 and 390px width, including long blockers and browser zoom.
3. Port existing Start/Cancel/Pause contracts without changing backend authorization: bound assessment tokens, acknowledgment, owner arbitration, start rejection and unresolved cleanup behavior.
4. Add Diagnostics, recorded History details and real Settings, preserving their source boundaries. All confirmation and focus behavior should remain explicit.
5. Verify keyboard navigation, visible focus, native dialog dismissal, readable disabled reasons, color-independent status labels, contrast and touch target sizing. Verify actual pointer workflows and every failure state before claiming usability acceptance.
6. Validate with saved/synthetic evidence before any separately authorized live-input acceptance. A polished read-only prototype does not prove control safety or live mining compatibility.

Success is an operator identifying authority, trustworthy readings, task state and next action quickly, without losing the original game scene. The visual direction should serve that workflow.
