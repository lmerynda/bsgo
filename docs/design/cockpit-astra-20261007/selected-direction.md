# Selected direction: modes first

Decision recorded 2026-10-07 after reviewing the eight Astra proposals. The user accepted **07 Signal first** as the foundation, using **01 Flight deck's** spacious original game-view arrangement. **02 Mission stepper** supplies a task sequence within a mode; **08 Adaptive command** remains a reference for narrow layouts.

The key product decision is that the application can assist someone who is playing or operate the mining cycle automatically. Its operating mode is therefore central to the interface. Individual scanning and approach routines belong within those modes rather than defining the entire application workspace.

## Intended operating modes

| Mode | Responsibility | Main workspace |
| --- | --- | --- |
| Assist | The human controls flight; the app handles the configured visible/nearby asteroid scanning and presents useful findings. | Scanner state, scanning scope, discovered asteroids/resources, useful targets, and actionable blockers. |
| Autopilot | The app performs the configured automatic mining cycle. | Objective, current target, cycle phase such as searching/scanning/approaching/mining, progress, blockers, and cancellation/cleanup state. |

These are product responsibilities, not claims that nearby discovery or full automation already works. The current attached controller supports a limited Manual-assist workflow; unsupported scanning/discovery and Autopilot capabilities must stay clearly unavailable until implemented and verified.

## Interface hierarchy

1. **Top:** prominent Assist / Autopilot selection, acknowledged active mode, active/paused/pending state, flight/input authority, and persistent Pause.
2. **Left:** the untouched original game capture, preserving its HUD, native aspect ratio, and colors. All app instruments, labels, and warnings stay outside its pixels.
3. **Right:** a workspace specific to the current mode, emphasizing what the app is doing and what the operator needs to do next.
4. **Below or in a supporting panel:** discovered asteroids and useful results, with freshness and verification status. Diagnostics and recorded history remain accessible without dominating normal operation.

**Pause is separate from the mode selector.** Selecting Autopilot must not immediately start flying. An explicit activation step and acknowledgment distinguish selected, requested, and actual operation. When authority is transferring or cleanup is unresolved, expose that state rather than implying Human ownership or a completed stop.

In Assist, surface scanning scope clearly: selected target, visible targets, or nearby discovery only when those capabilities exist. In Autopilot, the operator should see the overall objective and current cycle phase rather than having to infer them from individual low-level routine buttons. Routine selection, human assessments where required, Start, and Cancel remain explicit within their appropriate mode.

## What is preserved

All eight original PNGs, exact generation prompts, Astra's initial comparison, and the read-only prototype are kept unchanged as dated inspiration and evidence. The [gallery](gallery.html) links every proposal. Useful alternatives include the guided steps in 02, instrument grouping in 03, daylight theme in 04, generous image area in 05, contextual instructions in 06, and responsive arrangement in 08.

Generated game scenes are placeholders, not proposals to redraw or annotate the actual game. Unsupported confidence percentages, fictional HUD marks, and incorrect button states in an artwork are not requirements.

The existing prototype demonstrates the earlier routine-centered synthesis and local state transitions. It does not yet implement this mode-centered selection. [Astra's initial study](design-review.md) remains useful for observation, freshness, assessment, and control contracts; this decision takes precedence for product hierarchy.

## Adjustments after real verification

Use the preserved proposals as references, not as fixed pixel specifications. For each adjustment, record:

- The actual task and operating mode being verified.
- The observed problem, with screenshot/recording or reproducible evidence where available.
- The change and the proposal that inspired it.
- The validation result, remaining uncertainty, and any control-capability limitation.

Prioritize whether the operator can identify the active mode, control authority, scanning scope or mining phase, trustworthy findings, blockers, and next action. Verify the workflow in both modes at practical window sizes. Keep synthetic/prototype checks distinct from real gameplay evidence; perform live-input verification only when separately authorized.
