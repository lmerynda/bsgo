# Cockpit critique prototype validation

Review date: 2026-10-11. This package is a design proposal. Production UI and controller source were not changed.

## Review the proposal

Open [prototype.html](prototype.html) directly in a browser, or serve this directory on loopback:

```powershell
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' -m http.server 8876 --bind 127.0.0.1 --directory 'docs/design/cockpit-critique-20261011'
```

Then open <http://127.0.0.1:8876/prototype.html>. The Python path above is the existing local runtime; another Python installation can serve the same static files.

Use **Preview state** for six simulated conditions and **Layout** to compare a cockpit containing the game view with a panel alongside the game. The compact layout omits the duplicate game-view area; the original game remains in its own window. This is an alternate proposal, not a replacement for the accepted game-view layout.

The page has no game/controller requests or image requests. Its content policy blocks network connections. Permission, Start, Stop, Pause, Retry, mode choices, and assessments change only local illustrative state. The static server serves this design directory and has no connection to the production dashboard.

## Current UI evidence

The running dashboard was inspected at the current launcher's URL, `http://127.0.0.1:8765/`. It reported no visible game window, paused actions, unknown input ownership, and unavailable readings.

At 1280 × 720 with the default visible-scan task, the assessment checkbox began at y=872.5 and Start began at y=931.9. At 1280 × 800 after choosing Approach, Start began at y=841.2, below the visible viewport in the scrollable rail. Selecting a task in this review did not enable actions or start gameplay.

The remaining findings in [critique.md](critique.md) are grounded in current source. The earlier October 7 implementation record remains a dated record, not evidence that its default task and capability list are still current.

## Rendered prototype checks

Inspected through the Codex internal browser:

| Viewport / layout | Result |
| --- | --- |
| 1280 × 720 / cockpit | Task selector, next instruction, four Approach checks, Start/Stop, and Pause visible together. No horizontal overflow. |
| 1920 × 1080 / cockpit | Controls and supporting findings fit. Extra width goes to the game-view area. No horizontal overflow. |
| 620 × 850 / panel alongside game | Selected task, next instruction, scene check, permission action, and Pause visible. No horizontal overflow. |
| 390 × 844 / cockpit | Vertical scrolling is intentional. No horizontal overflow. Pause and acknowledged authority remain sticky; execution controls follow the task in normal flow and do not cover the image. |

At 1280 × 720, the Approach selector occupied y=270.7–314.7, the next instruction y=324.7–411.4, the assessment area y=425.0–538.3, and Start/Stop y=548.3–592.3. Start, Stop, and Pause have 44px button heights; checkbox labels have at least 44px hit areas. These are prototype measurements, not production changes.

The native task selector replaces the initial expanded choices because those choices pushed the current task or instruction out of view. Arrow Down changed the selected task without starting it and retained unchecked assessments. Focus styling was inspected; opening Diagnostics focused Close details and closing it returned focus to Diagnostics. Full keyboard, screen-reader, and browser-zoom acceptance remain open.

## State and interaction checks

| Simulated condition | Observed behavior |
| --- | --- |
| Disconnected | Current readings show dashes, input owner is Unknown, Retry is the next action, Start is blocked. |
| Stale capture | Current readings clear, assessment and action permission are blocked. |
| HUD readable / control blocked | Useful HUD values remain visible with a calibration limitation; Start and permission remain blocked. |
| Scan running | Actual task state and Scanner ownership are visible; Stop and global Pause remain available. |
| Stop unconfirmed | Ownership is Unknown; Start stays blocked; Pause does not imply confirmed release. |
| Ready for review | Actions are paused and checks are unchecked. Choosing a task does not start it. |

The explicit Approach flow was exercised: allow actions, choose Approach, check all four assessments, then Start. Before the simulated acknowledgment, status remained Start pending and ownership remained Human. Switching to NPC rehearsal during the pending request retained Stop and Pause. Pause canceled the pending transition and showed stopping/unconfirmed ownership before the simulated cleanup acknowledgment.

Changing tasks or workspaces clears assessments. Assessment expiry is illustrated with a 30-second timeout; this review did not wait through a full expiry cycle. Production expiry must continue to use the controller's actual context and deadline. The prototype is not an implementation specification for exact timings or acknowledgment transport.

NPC rehearsal and unavailable Autopilot were reviewed as capability disclosures. The prototype does not reproduce the rehearsal engine, flight-point inspection, actual scan evidence, or complete controller behavior.

## Sampled contrast

Calculated from rendered foreground/background values:

| Combination | Ratio |
| --- | --- |
| Primary text / canvas | 15.87:1 |
| Secondary text / raised surface | 7.37:1 |
| Secondary text / task surface | 8.81:1 |
| Primary action text / blue button | 10.79:1 |
| Pause text / raised surface | 7.08:1 |

Current production secondary text on its main surface measured 8.38:1. This review identifies small type and density as readability problems; it does not claim that production secondary text fails contrast.

These are sampled text combinations, not a complete accessibility audit. Non-text boundaries, disabled appearance, every focus combination, zoom, keyboard sequencing, assistive technology, and live operational usability require further validation before implementation acceptance.

## Evidence limits

The internal browser reported no console warnings or errors in the final prototype. Local screenshots are retained under `logs/design-critique-20261011/` and excluded from the design commit as generated artifacts.

No game input, live task, resource reward, controller enable request, or production connection retry was performed. The game-view area is a placement placeholder, and the telemetry/findings are explicitly illustrative. No nearby discovery, working Autopilot, or live NPC farming capability is implied.
