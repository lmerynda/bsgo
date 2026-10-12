# Design critique and three directions: BSGO game control app

Review date: 2026-10-11 (evening). This review builds on the [earlier same-day critique](../cockpit-critique-20261011/critique.md), which covered fine-grained layout fixes inside the current cockpit. This review steps back and asks what the app should *be*. The interactive mockups are in [proposals.html](proposals.html).

## Evidence

- I inspected the production dashboard at `http://127.0.0.1:8765/` (served from `tools/offline-bot/bsgo_offline/observer.html`) at a 1280 × 800 viewport. The BSGO window was not open, so the dashboard was in its disconnected state. I opened the Asteroid mining and NPC farming workspaces and expanded Diagnostics, History, and Settings.
- Live mining, approach, and Autopilot states were not exercised. Where the findings below describe running states, they come from source and copy, not from observation.
- The proposal mockups use a saved capture, `test-data/vision/v1/images/25922c9c…png`. Their numbers are illustrative.

## Overall impression

The engineering underneath is honest and careful. Unknowns stay unknown, authority is explicit, and recorded data is kept separate from live data. The surface, however, reads like a **debug console that is also expected to work as a product**. When the game is missing, the most prominent word on screen is **UNKNOWN**, which appears 7 times above the fold. The large black game rectangle takes about 60% of the window while giving no help with recovery. Reaching Start means passing through seven layers of controls.

The biggest opportunity is not tidier spacing. The app needs a clear **mental model**: who flies, what the app is trying to achieve, and what you need to do next.

## Usability

| Finding | Severity | Recommendation |
| --- | --- | --- |
| **Seven stacked control layers before Start**: feature tabs, then Assist/Autopilot, then the “Enable asteroid mining” checkbox, then scope chips, then the big “Auto-scan visible asteroids” button, then the Scan once / Auto-scan / Approach tabs, then the assessment checkbox, then Start/Stop. Several of these look like actions but only select something. | 🔴 Critical | Collapse them into one *intent* control (a mission or task picker) and one *execution* control (Start/Pause). See Direction A. |
| **Disconnected state shows the full operating UI.** Five instrument tiles shout UNKNOWN, the game view is an empty black box, and the actual fix (“Open or restore the game, then Retry connection”) is 12px footer text under the game view. | 🔴 Critical | When there is no game, replace the workspace with a recovery screen that has one sentence, one button, and auto-reconnect. Hide the instruments, or dim them to “—”. |
| **The same status is repeated four times**: “Waiting for game” appears in the header, the game-view caption, the phase card, and Settings. “Actions paused”, “Input: UNKNOWN”, and “Field issues UNKNOWN” sit next to each other. | 🟡 Moderate | Show one status sentence in one place, and one owner indicator (“Flying: You / App”). |
| **Header Pause and Enable actions are disabled, small (27–34px), and placed far right**, but they are the most safety-critical controls. | 🟡 Moderate | Make Pause a 44px+ control with a global hotkey that is always enabled. When nothing is running, it can read “Nothing to pause” instead of looking broken. |
| **“Auto-scan visible asteroids” is a full-width primary-style button that is not the primary action.** The real Start is about 450px further down, below the fold. | 🟡 Moderate | Keep exactly one primary-styled button per state. |
| **NPC farming says “Pass complete” in amber on first load** while no pass is running, and “Live NPC farm unavailable” is shown as a large disabled button. | 🟡 Moderate | Make the default state “Ready to rehearse”. Explain unavailable features in a single line on the entry, not with a dead button. |
| **Diagnostics and History expose raw internals in the operating page**: session hashes, `controller_not_attached`, `game_connection_not_attached`, a literal `[]`, and “Frames UNKNOWN/UNKNOWN, budget-skipped UNKNOWN…”. | 🟡 Moderate | Move them to a separate Engineering view. Keep a human-readable activity log in the operating UI. |
| The name “Game control panel” and the ◇ glyph give no identity or purpose. | 🟢 Minor | Name the product. This review uses the working name **Wingman**. |

## Visual hierarchy

- **What draws the eye first:** the empty black game view and the giant UNKNOWN readings. Neither tells the operator what to do. The eye should land on *status + next action*.
- **Reading flow:** title, then the connection strip (5 controls), then 5 instrument tiles, then the game view, then the right rail, which runs top to bottom and scrolls. The decision point is at the very end of the longest path.
- **Emphasis:** the instrument values are 26px bold while the actionable instruction is 12px. That weight is inverted.
- **Whitespace:** the rail is dense and evenly weighted. Nothing reads as a group-level heading, so everything looks equally important.

## Consistency

| Element | Issue | Recommendation |
| --- | --- | --- |
| Buttons | At least five button treatments: outlined pill, teal selected, dark navy primary, ghost, and amber-outlined Enable. Selected tabs and primary actions share the teal fill. | Define three roles (primary, secondary, danger) and a separate *selected* style for choices. |
| Typography | Mono font for “Results will appear here…”, uppercase tracking for tile labels, and mixed 10/12/13/14/26px sizes. | Use one family with a 4-step scale (13 / 15 / 18 / 24) and tabular figures for numbers. |
| State words | “Unavailable”, “UNKNOWN”, “none reported”, “none”, “OFF”, and “—” all mean roughly “no value”. | Use “—” for no reading, “Not available yet” for capability gaps, and “Stale · 14:28” for old data. |
| Colour | Amber is used for warnings (“Actions paused”), for neutral notes (“Results…”), and for “Pass complete”. | Reserve amber for “needs attention”, red for stop or danger, and green for verified or done. |

## Accessibility

- **Contrast:** sampled body text passes (see the earlier critique). Several disabled and ghost buttons (Enable actions, Pause, Live NPC farm unavailable) fall to roughly 2–3:1, so it is hard to tell whether they are disabled or simply low-contrast.
- **Targets:** Retry and Enable are 26.7px high, and the feature checkbox is 13 × 13px. Aim for at least 44px.
- **Text:** helper text at 10–12px carries the critical instructions. Use 13–15px.
- **Keyboard:** add global hotkeys for Pause and the primary action. They matter more here than tab order, because the user's focus is usually in the game.

## What works well

- The honesty model: unknown is never shown as zero, recorded data is labelled RECORDED, and Autopilot is marked unavailable instead of being faked.
- Explicit control ownership and a separate Pause are the right safety primitives. All three directions keep them.
- The original game pixels are never drawn over.
- NPC farming is clearly labelled as an offline rehearsal.

## Naming: “mining app” vs “remote game control app”

“Mining” is already too narrow because NPC farming exists, and “remote game control” sounds like a remote-desktop tool. The product is a **co-pilot for BSGO** that can assist or fly by itself. The working name used in the mockups is **Wingman**, which fits the game's fiction and covers mining, farming, and future combat. Alternatives: *BSGO Copilot*, *Deckhand*, *CAG* (the Commander, Air Group in BSG).

## Three directions

All three keep the safety primitives (explicit owner, Pause, nothing drawn into game pixels, honest unknowns). Open [proposals.html](proposals.html) and use the **State** switch to compare *Running*, *Needs you*, and *Game not found*.

### A · Mission console: “give it a goal, watch the plan”

The desktop window is organised around a mission, such as “Mine Water until hold full”, displayed as a 6-step plan. The current step is highlighted, and a blocked step turns red with a plain-language reason and a single Resume. The game view stays large and turns into the recovery screen when the game is missing. The bottom area holds a findings table, with the best target starred and freshness shown, next to a human activity log with run totals. Diagnostics move to their own view.

- **Best for:** the Autopilot future and making the bot's reasoning legible.
- **Cost:** a mission/phase model in the controller (`mission.py` and `execution_status.py` are a start). The game image is still duplicated on screen.

### B · Companion dock: “a co-pilot next to my game”

There is no mirrored game view. A 300px dock snaps to the side of the real BSGO window. It contains a status ring with one line of text, four large actions with hotkeys (F8 Pause, F9 Scan, F10 Approach, F11 Auto-scan), the top three findings, and hull, energy, and hold. Decisions arrive as toasts beside the game, each with one primary choice. Everything else opens in a separate window.

- **Best for:** Assist mode while you fly, and single-monitor play.
- **Cost:** a native always-on-top or docking window and global hotkeys that do not clash with game binds.

### C · Remote pocket: “check on the bot from my phone”

This is true remote control. The PC runs the bot, and a phone-sized UI on the LAN has three tabs. **Now** shows a live thumbnail, one sentence, progress, vitals, and a huge Pause. **Alerts** is a decision feed where each item has one action. **Summary** shows yield, rate, and interventions. It uses a light theme for daylight readability.

- **Best for:** long unattended Autopilot or NPC-farm sessions.
- **Cost:** a LAN server with pairing or authentication (loopback-only today), push notifications, and a remote-stop contract with acknowledgement. It is a second surface that pairs with A or B.

### Recommendation

> **Decision (2026-10-11):** the user chose **B + C together**. See [selected-direction.md](selected-direction.md). The original recommendation is kept below for the record.

Build **A as the main desktop app now**. It fixes the critical findings (seven layers, the disconnected state, buried Start) using today's web stack and controller. Design its status and alert model so that **C** can reuse it later as a read-only plus Pause remote. Treat **B** as a later “compact mode” of A when Assist becomes the main way you play.

## Priority recommendations

1. **Replace the seven control layers with intent + execution.** One mission or task picker and one Start/Pause. This removes most of the rail and puts the decision above the fold.
2. **Design the three canonical states first: Running, Needs you, and Game not found.** Each should have one sentence, one owner, and one primary button. The disconnected state is the first screen most sessions see, so it should feel helpful, not broken.
3. **Separate the operator view from the engineering view.** Keep diagnostics, hashes, and frame counters, but on their own screen. In the operating UI, use a plain activity log and findings with freshness labels.
