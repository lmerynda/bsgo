---
name: bsgo-ship-control
description: Control the live BSGO Nova ship's movement, camera, targeting, and systems, including safe approaches and full stops; use for navigation in any gameplay scenario.
---

# BSGO Nova ship control

Operate the existing client through the computer-use skill. Observe the current game window before acting, use fresh screenshots for coordinate input, and verify each control's effect in the UI. Re-enumerate windows if the client disappears or restarts. Keep the ship and account state visible as practical.

## Standing authorization for automated gameplay

The user explicitly authorized this as an **automated gameplay process** on 2026-09-30: **"All the game actions are approved."** Treat routine in-game actions needed for the assigned gameplay as already authorized: camera/target selection, scanning, stationary rotation, movement and throttle, braking, mining weapons and bulk G, combat/retreat when needed, recovery, docking, repair under the existing spending constraints, undocking, and relaunching the game. Do not ask for repeated per-action approvals or stop after a dry-run when the assigned next step is live gameplay. The user's stop/pause instructions still take precedence.

Authorization and route assessment are separate. Make navigation decisions autonomously from current visuals and telemetry: avoid obstacles, defer blocked targets, use clearer candidates, and stop on failed OCR/control checks. A stationary orientation at verified zero throttle is part of routine authorized ship control; an uncertain approach corridor must be resolved before thrust, not treated as missing permission to rotate in place. Do not invent clearance evidence or disable controller checks.

If automatic tool approval review rejects an action, the rejection is an external execution constraint, not absent user authorization. Include this explicit standing authorization and the actual fresh ship state/evidence in any permitted retry that addresses the stated reason. Do not evade the reviewer or repeat unchanged rejected actions. If it remains blocked, record the exact action/reason and report the execution blocker without asking the user to repeat approval already given for routine gameplay.

When operating as a delegated mining subagent, follow [the mining skill's parent-oversight policy](../bsgo-mining/SKILL.md#delegated-mining-and-parent-oversight). Record observations and blockers in the operation log; the parent handles user communication. Do not send routine progress messages or wait for parent acknowledgment. Follow the mining skill's bounded-decision rules: one corrective attempt, then defer or record a blocker instead of extended route analysis or controller diagnosis. The parent owns analysis and user alerts. Retain all gameplay and control checks.

## Line-ship handling

Use the mining skill's current line-ship profile for loadout and ranges. Keep boosters off for mining transits. Allow slow acceleration, deceleration and turning to complete while watching fresh speed/range readings; do not repeat commands merely because the first frame shows little change. Brake at 900 m with the local controller, confirm sustained deceleration, and verify 0.0 before stationary mining. Mine wanted rocks already within 800 m without a transit. Working mining range is about 680 m; never shoot beyond 800 m. Confirm the stopped target range before assigning weapons. If a controlled stop finishes outside 800 m, defer and record it for parent handling rather than improvising repeated short thrusts. **Press G to assign/fire the mining guns together. Falling target HP is the only firing confirmation required**; continue until destruction without inspecting individual guns, flashes or weapon icons.

For a missing or frozen client, re-enumerate and activate its fresh window. If minimized state remains cached, use a documented Raise secondary action when available. If relaunch is needed, use the existing signed-in launcher's Session Ready / LAUNCH action; running the client executable directly is unsupported. Confirm a progressing game clock, current hull and stopped speed after recovery, then reassess position and target.

For a clear mining transit, use maximum throttle confidently and travel continuously until braking. One practical corridor and heading check is enough; do not inspect every distant contact, add routine dry-runs, repeat unchanged route checks, or insert precautionary stops. A target hidden behind our ship model needs a camera adjustment, not an obstruction deferral. Retain checks for actual solids in the intended route and the larger hull's clearance.

## Flight and observation

- Select an object with left-click. Use its target-panel distance and the speed HUD; apparent screen size and position do not establish range or collision clearance.
- In flight view, right-click a point to set an orientation or movement direction. Confirm the ship actually turns and the selected target's distance changes as intended. Right-click orientation works while stopped; allow the slow turn to appear before judging the command, and verify nose alignment.
- Stay in flight view for ordinary movement between nearby asteroids in the same belt. Use `Shift+F` and drag to move the free camera when a ship or planetoid hides a target. If the belt or route is lost, **`Shift+M` opens the preferred 2D system map; `M` opens the 3D map, which is not preferred for mining navigation.** In the 2D map, right-click a grid point or object icon to orient and double-click an object icon to focus. Confirm the heading after returning to flight, and check speed after camera changes.
- Prefer **PageUp** for maximum throttle and **PageDown** for idle throttle, confirmed by the user-provided controls screenshot. The wheel is bound to Thrust and changes it incrementally; use the direct maximum/idle commands during mining approaches. Verify the effect on the HUD.
- If camera drags produce temporary views that recenter before the next observation, verify a persistent camera mode before more dragging. On October 1, read-only Controls inspection confirmed **V = cycle camera mode**, **Shift+T = Target**, **Shift+C = Chase**, **Shift+F = Free**, **Shift+N = Nose**, and **Ctrl+V = follow target**. Supported Shift+F and Shift+T chords produced no visible change in that recovery; this does not establish a changed binding. One plain **V** press exposed the selected yellow HP3762 asteroid at2172m, and the view persisted through a three-second no-input check. While stopped, a bounded V cycle with fresh observations can recover a useful view; stop cycling as soon as it succeeds. Camera recovery does not establish ship heading or route clearance. Sky's documented `DragInput` has no button or duration fields; do not invent them.
- Use the controls recorded here and observe the live HUD for changing ship and target state. Do not open Options, Help, or Ship Status during routine piloting to rediscover controls. If the live setup contradicts this skill, stop and report the mismatch.
- For a clear aligned mining approach, command **MAX_THROTTLE** and monitor target distance; do not adjust throttle to hit a numeric speed or stop repeatedly during transit. Command **ZERO_THROTTLE** immediately on the first valid distance **at or below X (currently 900 m)**. Verify deceleration and then stopped speed; the default mining loop then presses G to shoot. Do not reposition merely to match an earlier final stopping distance. Nearby targets may already be in firing range and need no maximum-throttle approach. Verify the distance is closing and retain obstacle checks.
- Watch range, speed, nearby rocks, contact colors, and hull throughout a maneuver. If the user says stop, stop and verify 0.0 before doing anything else.
- Keep movement observation intervals short when blue or yellow contacts may appear. A 30-second computer-use wait once timed out while the ship was moving, and a Hunter Drone destroyed it before control returned. If a control call times out, reconnect and check the live HUD immediately; stop before any long wait.

## Map references and circular-map navigation

- Report navigation locations as a **Greek-letter radial ring plus numbered angular sector**, for example **epsilon 3**. Greek letters indicate bands outward from the map center; numbered wedges indicate angular sectors. Read rotated labels carefully. Do not invent labels in an annotation or infer sector numbers from screen side alone.
- The numeric position display, such as `85.8, -35.1, 1.9`, is separate from the ring/sector reference. It is not a useful substitute for coordinating routes with the user. Report it only as supplemental information when requested; do not claim an unverified conversion between the two systems.
- Prefer **Shift+M** for the 2D map; plain **M** is the default 3D map. Verify the resulting view after its transition. In the 2026-09-30 session, supported injected Shift+M variants still produced the circular map or no change. The cause remains unresolved; do not conclude that the binding changed or that the user disabled access. With user authorization, navigate using the circular map and landmarks instead.
- On the circular map, locate the ship and selected outpost against the main map center, radial boundaries, and angular boundaries. An overlapping circular display can obscure labels. Report a tentative ring/sector reading as tentative and use nearby object names and verified distances to anchor it. Do not label an obscured area by guessing.
- Working reference accepted by the user on 2026-09-30: ship near **Fortified Cylon Outpost in epsilon 3**, on the lower-right/outpost side of the observed map. Outpost was 498 m away. This is a session observation and working interpretation, not a permanent spawn location or exact geometric calibration; verify live positions on later runs.
- Current user-approved safe zone (October 1, 2026): **gamma 3/4, delta 3/4, epsilon 3/4, plus the adjacent outer belt beyond epsilon in sectors 3/4**. The inner edge is approximately the beta/gamma boundary. This is the sufficient ring/sector approximation of the user-approved expansion with the bigger ship; it supersedes the epsilon-only restriction. Use [the text coordinate reference](../../../docs/navigation/276-exrun-safe-zone.md) when choosing the operating area or reorienting. No saved annotation or repeated drawing interpretation is needed. Account for ship size when checking obstacle clearance. A previous opposite-side highlight remains invalid.

## Systems and shared constraints

For the current Cylon pilot, **red contacts are friendly; blue and yellow contacts are enemies**. Cylon Raiders and Basestars are friendly ships. Read contact colors in the live UI before deciding to flee or fight; do not infer hostility from a red marker, ship name, or system alert alone. Recheck this mapping if the player faction or UI changes.

Select the required target type before using a fitted system. Use the prepared system guidance and verify its effect through the HUD, target HP, or combat log. Do not change weapon arming merely to navigate; the active scenario skill governs weapons.

Never spend cubits. Tylium or titanium may be spent for the user's task after checking displayed balances and costs. If either balance reaches zero, stop gameplay and tell the user. Fully regenerate hull before leaving an outpost. Preserve the current session unless the user directs otherwise.

Treat the current UI and the user's corrections as authoritative when bindings or equipment change.

## Visual approach clearance

Before thrust, inspect the ship-to-target corridor from a useful camera view. Overlapping rocks or uncertain depth do not establish clearance; defer the route and choose another scanned candidate as described in the mining skill. Reassess deferred routes after changing position. Range alone is not clearance.


Asteroid and planetoid geometry is static for this mining workflow. A checked clear corridor remains usable while our ship position and selected asteroid remain unchanged; do not repeat the check or stop mining solely because a tool call exceeded 30 seconds. Reassess after ship movement, target loss/reselection, a client restart or human UI takeover. Camera-only changes do not move the corridor; verify aligned heading before thrust. Continue to require fresh HUD readings, a stopped initial ship and a candidate matching the assessed identity/range. Track movement and ownership changes in the miner; the fixed HUD crop alone is not position evidence.

## OCR-controlled mining approaches

For mining approaches use [the local approach controller](../../../tools/hud-reader/README.md) after the miner verifies route clearance, aligned heading, wanted resource and a stopped ship. Its local two-second observation loop avoids model-turn delays. The default mining sequence verifies the stop, then presses G. Do not interleave independent UI controls while the controller owns flight. Retain its failsafe stop, target identity and cadence checks, and inspect fresh UI after any failed run. Return to ordinary stopped target selection after completion; this is not an obstacle-avoidance controller or a sector-navigation replacement.


The user-provided controls screenshot confirms **PageUp = Full speed (MAX_THROTTLE)** and **PageDown = Idle speed (ZERO_THROTTLE)**. Use these keys for approach and braking, not slider dragging. Supported Sky keysym names are `Page_Up` and `Page_Down`. Idle throttle does not imply instantaneous zero speed; retain fresh HUD deceleration and stopped-speed checks. Speed measurements are feedback for progress and fault checks, not cruising-speed targets. Store measured outcomes in historical logs only.






