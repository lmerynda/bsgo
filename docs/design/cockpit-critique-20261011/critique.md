# Design critique: BSGO game control panel

Review date: 2026-10-11. This is an evidence-based proposal, not an approved redesign or a live gameplay acceptance record.

The [read-only prototype](prototype.html) illustrates the proposed layout and states; [validation.md](validation.md) records its browser checks. Existing production source and the earlier proposals remain unchanged.

## Overall impression

The large original game view and explicit input ownership give the panel a useful operational foundation. The main opportunity is to turn the crowded control rail into a clear answer to three questions: what can I do, what is blocking it, and what happens next?

The disconnected screen currently gives substantial space to choices, unavailable capabilities, and repeated state while placing the scene check and Start below the first viewport. The next iteration should prioritize recovery and the selected task without weakening explicit activation or assessment.

## Evidence and scope

- Production dashboard: `http://127.0.0.1:8765/`, inspected in the internal browser at 1280 × 720.
- Confirmed state: no visible game window, actions paused, and input owner unknown.
- In that viewport, the scene assessment checkbox was at y=872.5; Start visible scan began at y=931.9 and ended at y=971.9.
- Retry and Enable controls measured 26.7px high; Pause measured 34px high.
- Production serves [observer.html](../../../tools/offline-bot/bsgo_offline/observer.html) through [observer_ui.py:24](../../../tools/offline-bot/bsgo_offline/observer_ui.py#L24) and its root route at line 478.
- Other production-state observations below are source-backed findings, not claims that a live mining task was exercised.
- In the prototype's 1280 × 720 Approach state, Task occupied y=270.7–314.7, the next instruction y=324.7–411.4, four unchecked assessments y=425.0–538.3, and Start/Stop y=548.3–592.3. Pause occupied y=80.8–124.8.
- Six synthetic states were reviewed: disconnected, stale, readable HUD/control blocked, scanning, stop unconfirmed, and ready to review. These checks are distinct from live gameplay acceptance.
- Sampled text contrast pairs are measured below. Full contrast coverage, keyboard operation, responsive recovery, and live task usability remain separate acceptance work.
- Both **With game view** and **Panel alongside game** layouts are offered for comparison. Neither layout is described as a confirmed user preference.

## Usability

| Finding | Severity | Recommendation |
| --- | --- | --- |
| The disconnected operator's next action competes with the full routine workspace. Recovery reasons exist, but `connectioncause` is hidden by CSS. | Moderate | Lead with the actionable reason and Retry beside it; keep the selected task visible with its current blocker. |
| At 1280 × 720, scene assessment and Start sit below the initial viewport. | Moderate | Keep task state, assessment, and primary controls visible in the task rail; allow supporting content to scroll. |
| Visible scan looks like an immediate command, while the other routines look like tabs. All select a task without starting it. | Moderate | Use one native Task select followed by one explicit Start/Stop area. |
| Feature enable, action enable, mode selection, and auto-scan enable require the operator to infer several separate dependencies. | Moderate | Show a short preparation sequence and one current next instruction; preserve the backend's separate permission and activation steps. |
| Visible-pass results and selected-target findings occupy separate areas with overlapping scope explanations. | Moderate | Use one findings panel with a clear scope, verification state, resource, range, and current/recorded label. |
| The NPC farming entry has the same weight as live mining, although its workspace is an offline rehearsal. | Moderate | Disclose rehearsal in the feature entry itself and retain the distinct simulation board. |
| Readable HUD values can appear beside blocked control calibration, with generic provenance copy. | Moderate | Keep useful readings and label the control limitation separately where it affects the selected task. |

Source evidence: [recovery and hidden cause](../../../tools/offline-bot/bsgo_offline/observer.html#L135), [feature/mode/task controls](../../../tools/offline-bot/bsgo_offline/observer.html#L204), [task selection behavior](../../../tools/offline-bot/bsgo_offline/observer.html#L602), [findings composition](../../../tools/offline-bot/bsgo_offline/observer.html#L509), and [NPC rehearsal](../../../tools/offline-bot/bsgo_offline/observer.html#L228).

## Visual hierarchy

- **What draws the eye first:** the large game-view rectangle and the dense task rail. The game remains the right visual anchor; the rail needs a stronger selected-task and next-step hierarchy.
- **Reading flow:** connection strip → instruments → feature → mode → feature enable → scope → task choices → status → findings → assessment → Start. This asks the operator to traverse too many layers before acting.
- **Emphasis:** give the task's current state and actionable blocker the strongest text hierarchy in the rail. Show normal explanatory copy quietly and reveal technical evidence on demand.
- **Authority:** keep acknowledged Assist/Autopilot, input owner, and Pause visible. Selecting a workspace must remain distinct from requesting activation.
- **Scene:** preserve the original game image, aspect ratio, colors, and full frame. Put every application label and warning outside the pixels.
- **Results:** place useful scoped findings below or beside the task workspace, instead of interleaving raw pass output with preparation controls.

The accepted [mode-centered direction](../cockpit-astra-20261007/selected-direction.md) remains the foundation. Feature choices and Assist/Autopilot stay beside the game view; this proposal does not move them into a new top navigation system.

The prototype uses one native Task select. Four expanded choices still pushed important assessment and execution content out of view, so the compact selector preserves the visible next instruction and Start/Stop area.

## Consistency

| Element | Current issue | Proposed treatment |
| --- | --- | --- |
| Task choices | A standalone visible-scan button mixes with three tabs; only the tabs have selected-state styling. | One native Task select with an explicit label, scope description, and familiar keyboard behavior. |
| Start labels | Enable actions, Enable asteroid mining, and Enable auto-scan refer to different operations. | Describe feature availability, permission, and task execution with distinct wording and a visible sequence. |
| Status copy | Technical phase strings and generic unavailable statements obscure the next action. | Plain-language state plus one actionable instruction; full reasons remain in Diagnostics. |
| Findings | Live selected-target readings, recorded routine results, and pass counters share prose. | Separate scope and evidence labels within one results panel. |
| Capability disclosure | Autopilot says unavailable; NPC simulation status appears inside its workspace. | Put availability or rehearsal disclosure directly on the relevant entry. |
| Instrument provenance | Display-only values are labeled “Observed game state.” | Use “HUD reading” and explain blocked control calibration when relevant. |

The current source permits display-only telemetry in [displayField](../../../tools/offline-bot/bsgo_offline/observer.html#L335), suppresses some calibrated-field failures from the issue count at line 396, and sets generic provenance at line 453. The [observation schema](../../../tools/offline-bot/bsgo_offline/observation_api.py#L240) explicitly distinguishes these readings from action calibration.

The October 7 [implementation record](../../architecture/mining-cockpit-ui-results.md) is historical evidence. Its selected-target default and unavailable visible-scanning statements do not describe the current visible-scan default; retain the record and document the new iteration separately.

## Accessibility

- **Color contrast:** the sampled pairs below exceed 4.5:1. Current secondary text also has sufficient sampled contrast; size and density are the main readability findings, not an established contrast failure.
- **Touch targets:** observed Retry/Enable controls are 26.7px high and Pause is 34px. Increase operational controls to a comfortable 44px target, including their clickable area.
- **Text readability:** current readout helpers are 10px and much control/supporting text is 12px. Use 13–14px for operational helpers and reserve smaller text for optional evidence.
- **Assessments:** [source styles](../../../tools/offline-bot/bsgo_offline/observer.html#L147) set assessment rows to a 22px minimum. Enlarge the whole label hit area, not only the checkbox.
- **Keyboard:** the current tablist lacks associated tab panels and keyboard handling. The prototype's native Task select is a simpler semantic fit for choosing one task.
- **Status announcements:** announce meaningful state changes and request outcomes without repeatedly announcing every polling update.
- **Focus:** preserve visible focus indicators and provide predictable focus after opening Diagnostics or recovering a connection.
- **Narrow windows:** keep Pause and acknowledged authority reachable while scrolling; verify this behavior instead of inferring it from desktop layout.

| Browser-backed sampled pair | Contrast |
| --- | --- |
| Prototype primary text on canvas | 15.87:1 |
| Prototype secondary text on raised surface | 7.37:1 |
| Prototype secondary text on task surface | 8.81:1 |
| Prototype action text | 10.79:1 |
| Prototype Pause text | 7.08:1 |
| Current secondary `#a6b6c6` on surface `#121b25` | 8.38:1 |

These sampled pairs and target measurements are partial evidence, not a completed accessibility audit. Focus, borders, disabled controls, every state, and keyboard behavior still need coverage.

## What works well

- The original captured game remains intact, with application chrome outside its pixels.
- Input ownership, acknowledged mode, and explicit Pause already form a useful authority model.
- Task selection does not immediately start gameplay; assessment and Start remain explicit.
- Unknown and stale values are treated separately from valid zero readings.
- Recorded history is clearly identified and kept separate from current instruments.
- NPC rehearsal has its own simulation board and no live input authority.
- Earlier concepts, decisions, and validation records are preserved for comparison.

## Proposed operator workflow

1. Read connection, acknowledged mode, input owner, and current action state; use the visible recovery action if blocked.
2. Choose the feature beside the game view. NPC explicitly identifies its rehearsal status.
3. Within asteroid mining, choose Assist or inspect Autopilot availability; activation remains explicit and acknowledged.
4. Use the Task select to choose **Scan this view**, **Scan selected target**, **Watch my selections**, or **Approach selected target**.
5. Read one current state and next instruction; review the original scene and the task-specific checks.
6. Use the persistent Start/Stop controls. Pause remains independently accessible.
7. Review unified scoped findings, then inspect Diagnostics or recorded History when needed.

Task names are proposed operator copy. They must continue to map to the existing controller contracts and bounded scanning scopes.

Prototype interaction checks exercised Allow app actions → four unchecked Approach assessments → Start pending. Switching to NPC during that pending request kept Start/Stop accessible; Pause showed stopping with unknown ownership. Pending Start retained Human ownership until simulated acknowledgment, rather than claiming early controller ownership.

The synthetic state checks preserved null ranges and Retry when disconnected, null ranges and blocked actions when stale, and partial HUD readings with blocked actions when calibration was unavailable. Scanning enabled Stop; unconfirmed stopping showed unknown ownership and blocked Start; ready state began paused with unchecked assessments.

## Visual direction and tokens

| Role | Prototype token/direction | Purpose |
| --- | --- | --- |
| Canvas | Slate `#10191f` | Quiet background around the original game. |
| Surface | Steel `#17242d` | Group task controls and findings. |
| Raised surface | Steel `#20323e` | Distinguish controls and supporting panels. |
| Border | Steel `#3b5362` | Define sections without ornamental frames. |
| Primary text | `#edf3f5` | Readable operational copy. |
| Secondary text | `#b2c4cf` | Supporting context with measured sampled contrast. |
| Action/focus | Blue `#8cd5e3` | Selection, Start, and keyboard focus. |
| Attention / stopping | Amber `#e7b570`; red `#f28b8e`, with text | State remains understandable without color. |
| Typography | Bahnschrift throughout; Segoe UI fallback | Consistent compact headings, numbers, and body copy. |
| Shape/spacing | Modest radii; 8px spacing rhythm | Functional grouping and predictable density. |

Use no neon sci-fi ornaments, decorative radar, fictional confidence percentages, or application overlays inside the game image. Extend the sampled contrast checks to the complete rendered states before accessibility acceptance.

## Priority recommendations

1. **Bring recovery and task execution into view.** At 1280 × 720, present the current blocker, required check, and Start/Stop without a long rail traversal.
2. **Unify task selection and scoped findings.** Make selection visibly distinct from execution and explain which evidence each result describes.
3. **Clarify capability and trust.** Disclose NPC rehearsal at entry, keep unavailable Autopilot honest, and distinguish HUD readability from control readiness.
4. **Validate the proposed hierarchy.** Continue responsive and zoom checks, extend contrast coverage, and exercise keyboard/failure states; preserve results in the validation record.
5. **Keep acceptance evidence scoped.** A read-only prototype and disconnected-screen inspection do not establish live mining usability or gameplay control acceptance.

The first three priorities are this critique's recommendations. Their final order and the two layout alternatives remain open for user review.
