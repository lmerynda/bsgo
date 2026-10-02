---
name: bsgo-mining
description: Discover, scan, approach, and mine asteroids in live BSGO Nova; use for resource selection, mining-gun arcs, and verifying Water or Tylium rewards.
---

# BSGO Nova asteroid mining

Read [BSGO ship control](../bsgo-ship-control/SKILL.md) for live flight, camera, stopping, and spending rules. Follow the user's current resource priorities. The present objective favors Water and Tylium; leave barren rocks and other resources unless requested.

## Current line-ship procedure

The pilot flies a line ship with 5,005 maximum hull and 800 maximum energy. Keep boosters off during mining. Acceleration, deceleration and turns take time: command once, watch the HUD, and let the maneuver complete. Idle throttle does not mean stopped. Maintain ample clearance for the hull around rocks and planetoids.

Six fitted Excavator Heavy Mining Batteries occupy slots **2-7**. Slots 2/3/6/7 are level 2; slots 4/5 are level 3. They reload in **4 seconds**, cost **50 energy per firing battery**, and have **180-degree arcs**. Their displayed optimal ranges are about 678 m and 706 m. The working mining range is approximately **680 m**, with a strict **800 m maximum**. Never assign or continue mining beyond 800 m. Verify the actual target distance before firing. **Press G to assign/fire the mining guns together.** Falling target HP is the only firing confirmation required. Do not inspect individual gun states, weapon icons or flashes.

Scan visible nearby candidates with **Z while stopped**, exploiting scanner reach before choosing a transit. Observe the resource result rather than imposing the former ship's scan-distance limit. Scanner numeric range has not been verified; an out-of-range result means choose another nearby candidate, not fly toward an unclassified rock. Preserve energy for mining and let it recover while stopped if necessary.

Prefer wanted rocks already within 800 m. For a clear aligned farther target, use the local controller's **900 m brake threshold**, two-second HUD samples and verified final 0.0. A full-throttle approach requires at least 1,150 m initial range. Verify the final distance is at most 800 m before firing. If the stop leaves it farther out, defer it and record the actual braking result for parent adjustment; do not improvise repeated short full-throttle moves. Rocks between 800 and 1,150 m are deferred until a productive route or position change resolves them. Continuous decreasing speed may take up to 30 seconds to finish; keep the existing early brake acknowledgment, stalled-braking and failure checks. This threshold is a conservative operating setting, not a measured stopping-distance claim. Record actual brake and final ranges for parent review.

Do not inspect equipment menus, relearn controls, change loadout, or calibrate readers during production mining. Execute this procedure. Record a concrete mismatch or blocker at a safe stopped boundary for the parent. The parent owns skill changes; when the parent replaces a miner, stop at the requested boundary and relinquish UI ownership. A fresh miner reads these current instructions without an upgrade narrative.
## Mining loop

1. **Scan:** select a nearby asteroid and use Z; read the resource result.
2. **Assess:** choose wanted Water or Tylium and a clear ship-to-rock path. Skip unwanted or obstructed rocks.
3. **Move:** if repositioning is needed, align toward the rock and command MAX_THROTTLE with PageUp. Use the local OCR approach loop to monitor distance.
4. **Stop:** on the first valid distance at or below X (currently 900 m), command ZERO_THROTTLE with PageDown. Verify stopped speed. If already suitably positioned and stopped, continue directly to shooting.
5. **Shoot:** verify range is at most 800 m and speed is 0.0, then press **G** to assign/fire the mining guns together. Check that target HP is falling, then continue until destruction. No other firing checks are required.
6. **Repeat:** select the next rock immediately after destruction. Record concise results between rocks; rewards can be recorded when visible without waiting for them.

This is the default execution sequence. The parent handles user communication; the miner continues the loop autonomously. Previously measured speeds and stopping distances are historical observations, not goals to reproduce. Apply the collision and control checks below when they affect the next action.

## Normal mining transits

Maximum throttle is the normal travel setting for this large, slow line ship. Choose a wanted target, make one practical corridor check, confirm the nose is turning toward it, then execute the live local controller continuously to the brake threshold. Do not wait for a particular speed, add routine dry-runs, insert precautionary intermediate stops, or recheck an unchanged clear route. A clear route needs enough room for the hull; it does not need an empty entire field or analysis of every distant contact.

The ship model covering a target marker is a camera issue, not an intervening rock. Use one useful camera adjustment if needed. Right-click orientation works while stopped; allow the slow turn to appear and verify nose alignment. Defer actual intervening solids or unresolved clearance in the intended corridor. Friendly red ships and the normal presence of surrounding asteroids do not by themselves prevent a clear maximum-throttle transit.

Use mode live for an authorized production approach. Keep two-second telemetry and automatic braking: these provide prompt control without extra model pauses. Verify final 0.0 and range at most 800 m before assigning weapons. The parent adjusts braking settings from logged outcomes and handles software faults; the miner proceeds with the prepared procedure.
## Bounded decisions

Execute the mining loop with simple local choices. The miner does not redesign the workflow, diagnose OCR algorithms, run calibration experiments, or perform open-ended route analysis during production mining; those belong to the parent.

- An unresolved selection or scan gets one corrective attempt, then skip that candidate.
- Assess a wanted candidate from the current view. Use at most one additional useful view or one heading correction to resolve ambiguity. If still unclear, defer the route and choose another candidate. Reuse unchanged static clearance.
- After an OCR/control failure, establish the required stopped state. Retry once only when a fresh observation shows the transient cause has cleared; recurring software failures become a parent-owned blocker, not repeated experiments by the miner.
- If target HP does not fall, retry G once. If HP still does not fall, defer the candidate instead of continuing adjustments.
- Revisit a deferred candidate after a meaningful change, such as ship movement or asteroid respawn. Do not cycle through the same unresolved candidates without new evidence. If no productive option remains, establish a safe state and record the blocker.

At stopped boundaries, leave a short trace with the stage, candidate, outcome, correction used and blocker, plus UTC time and actual tool duration when available. Mark cases requiring analysis as `parent_attention` in the operation log; do not send an outbound message or wait for a reply. Parent guidance can update the task when needed.

The parent flags repeated assessments/corrections with no new evidence, repeated failed retries, or leaving the simple loop for workflow research. A long elapsed interval alone does not establish excessive reasoning: attribute recorded tool waits separately and label unexplained gaps as unknown. The parent handles alerts to the user and engineering analysis; the miner retains gameplay execution and all collision/control checks.

## Standing authorization for automated gameplay

The user explicitly authorized this as an **automated gameplay process** on 2026-09-30: **"All the game actions are approved."** Treat routine in-game actions needed for the assigned gameplay as already authorized: camera/target selection, scanning, stationary rotation, movement and throttle, braking, mining weapons and bulk G, combat/retreat when needed, recovery, docking, repair under the existing spending constraints, undocking, and relaunching the game. Do not ask for repeated per-action approvals or stop after a dry-run when the assigned next step is live gameplay. The user's stop/pause instructions still take precedence.

Authorization and route assessment are separate. Make navigation decisions autonomously from current visuals and telemetry: avoid obstacles, defer blocked targets, use clearer candidates, and stop on failed OCR/control checks. A stationary orientation at verified zero throttle is part of routine authorized ship control; an uncertain approach corridor must be resolved before thrust, not treated as missing permission to rotate in place. Do not invent clearance evidence or disable controller checks.

If automatic tool approval review rejects an action, the rejection is an external execution constraint, not absent user authorization. Include this explicit standing authorization and the actual fresh ship state/evidence in any permitted retry that addresses the stated reason. Do not evade the reviewer or repeat unchanged rejected actions. If it remains blocked, record the exact action/reason and report the execution blocker without asking the user to repeat approval already given for routine gameplay.

## Delegated mining and parent oversight

When mining runs as a subagent, the miner owns gameplay execution and the parent owns oversight and communication. The miner continues selecting, scanning, approaching, firing and completing the belt instructions autonomously. Do not send routine startup, progress, harvest or status messages to the parent or user, request acknowledgments, or wait for parent feedback between rocks.

Leave concise timestamped file traces in the operation log under `logs/mining/`, using the current operation file. Record meaningful transitions, verified harvests, route deferrals, failures and completion; distinguish UTC from game clock, observations from intentions, and own harvests from separate rewards. Reuse controller event logs for flight detail rather than rewriting every sample. Batch routine notes at natural stopped boundaries; logging and communication must not interrupt the critical approach/braking loop.

The parent reads file traces and compact agent lifecycle status without requesting routine report-backs or competing for game UI ownership. The parent handles user-facing updates and sends steering instructions only when the user changes the task or an intervention is needed. Do not require a report after every action or harvest.

For an unresolved blocker, establish a safe state and write the exact blocker and current state to the operation log. Follow the existing gameplay stop/failure rules; this communication policy does not authorize continuing through a failed control or unsafe route. If the agent turn must end, keep its terminal result minimal and point to the log. The parent communicates the blocker or completion to the user. No scheduled monitoring task may be created without the user's explicit approval of that schedule.

In delegated mode, any instruction here or in ship control to "report", "tell the user", or "confirm with the user" means record the issue in the file trace for parent handling; it does not require routine outbound messages from the miner.

## Mining area in 276 Exrun

Use the circular map with **Greek-letter radial rings and numbered angular sectors**, following [BSGO ship control](../bsgo-ship-control/SKILL.md). The current safe mining area is **gamma 3/4, delta 3/4, epsilon 3/4, plus the adjacent outer belt beyond epsilon in sectors 3/4**, with an inner edge approximately at the beta/gamma boundary. See [the text coordinate reference](../../../docs/navigation/276-exrun-safe-zone.md) for reorientation. This sufficient approximation of the October 1, 2026 user-approved expansion with the bigger ship supersedes the earlier epsilon-only restriction. Navigate by these coordinates; no saved drawing or repeated annotation recognition is required. Keep to the Fortified Cylon Outpost side, not the earlier invalid opposite-side highlight.

The earlier 2D references H6, H7, H8, G8, F8, and F6 are superseded for this circular-map workflow. Do not use them for route instructions or assume a numeric-position-to-grid conversion. Prefer Shift+M when opening the 2D map, but verify the resulting view: injected shortcuts did not reliably produce it in the observed session. The user authorized the circular map as the navigation fallback.

Work nearby rocks in flight view without reopening the map between ordinary targets. Sweep gamma, delta and epsilon in sectors 3/4 and their adjacent outer belt; keep targets and routes within this approximate coordinate area. Use the selected outpost and its displayed distance to anchor the return route. Respond to actual threats using ship-safety guidance, and allow clearance for the bigger ship around rocks and planetoids.
Use Mineral Analysis (Z) to read blue Water, yellow Tylium, purple Titanium or red empty. The scanner can reach nearby candidates without flying close just to scan. Mining batteries occupy slots 2-7; press G to assign/fire them together. Use the working range of about 680 m and maximum 800 m, with 180-degree arcs. Use falling target HP to confirm firing, then continue until destruction. Record a reward amount when visible without waiting for it or opening cargo. Do not add ownership, flash or per-gun checks to the loop. Fully regenerate hull before outpost departure and retain the spending rules in ship control.

## Choose a clear route among scanned candidates

Scan several nearby visible rocks from the current position when scanner range and energy permit. Keep a small candidate list: resource, displayed range, visual direction/landmark, and route status. Preserve energy for firing. Prefer a wanted rock already in gun arc within 800 m; otherwise choose a wanted rock with a clear approach. Do not approach just to scan a rock already within scanner range.

Before thrust, inspect the ship-to-target corridor. Small free-camera pans or a second view can separate overlapping silhouettes; verify the camera mode changed. Range and target markers alone do not establish clearance. Intervening rocks, overlaps and uncertain depth mean defer that route. Choose another scanned wanted rock rather than repeatedly trying the blocked path or spending minutes on the same heading. If no clear wanted route exists, scan another nearby candidate while stopped; report a concrete blocker if no productive safe option remains.

Asteroid and planetoid geometry is static for this mining workflow. A checked clear corridor remains usable while our ship position and selected asteroid remain unchanged; do not repeat the check or stop mining solely because a tool call exceeded 30 seconds. Reassess after ship movement, target loss/reselection, a client restart or human UI takeover. Camera-only changes do not move the corridor; verify aligned heading before thrust. Continue to require fresh HUD readings, a stopped initial ship and a candidate matching the assessed identity/range. Track movement and ownership changes in the miner; the fixed HUD crop alone is not position evidence.

After moving or mining another candidate, reassess deferred targets from the new position: the path may now be clear. Revalidate identity/resource if the rock respawned or selection was lost. A changed view is evidence to inspect, not proof of clearance. Do not bypass an automatic approval rejection through different controls for the same maneuver. A permitted retry must address its stated reason with fresh evidence or the standing user authorization above; do not request duplicate routine-gameplay approval.

Command MAX_THROTTLE for clear aligned transits, without routine intermediate stops. Do not regulate throttle to reach a numeric speed. Follow ship-control braking guidance. When already stopped in useful firing range, press G; stationary turns may show their effect after a delay. Log obstruction skips, selection delays and actual approach/brake observations while stopped, distinguishing observed values from intended thresholds.

## Repeat the belt after a complete pass

Asteroids and their resources respawn frequently. Scan every asteroid encountered throughout **gamma 3/4, delta 3/4, epsilon 3/4 and their adjacent outer belt** on each pass, including rocks whose resources are not wanted; mine the Water or Tylium rocks wanted from that pass. Complete the nearby belt sweep before returning to the **Fortified Cylon Outpost**, docking, then undocking to begin another pass. Use the circular map and outpost landmark to find the return route; report ring/sector references rather than the numeric position display. Fully regenerate hull before leaving the outpost. Scan rocks again on the next pass rather than relying on earlier results; respawned resources may differ. Do not interrupt a pass to dock after each rock.

If hostiles or collision risk interrupt an approach, handle ship safety before continuing.

## Local OCR approach loop

Use [the local HUD reader and approach controller](../../../tools/hud-reader/README.md) for the critical approach cycle instead of model-turn screenshot polling. The miner still selects/scans the wanted asteroid and visually verifies a clear corridor and aligned heading while stopped. Do not set these prerequisites true merely to enable the controller.

Start the prepared worker with **`& tools\hud-reader\start-worker.ps1 -RunName <unique-run-name>`**, from C:\Projects\bsgo. It returns immediately with pid, directory and profile, launches hidden, and fixes **restart-20261001** explicitly. Use its returned directory as runApproach outputDirectory. Do not copy the legacy direct-Python command that defaults to original; directory names do not select a profile. Do not wait for the 120-second worker to exit before running the approach. The capture response must say calibration_profile restart-20261001. Then call runApproach through supported Computer Use Node REPL in explicit live mode after the visual prerequisites. If a worker was started with original, stop that worker via its run-directory stop file and use the prepared launcher once in a fresh directory; this is a prescribed setup correction, not calibration. The capture loop automatically retains every original OCR screenshot and request/result, including successful reads, rejections and timeouts. Keep those production run files for offline accuracy evaluation; the miner does not add separate calibration captures, crop processing or manual image-copy steps. Dry-run is the default. Explicit live mode commands MAX_THROTTLE, samples locally every two seconds, and commands ZERO_THROTTLE immediately on the first valid target distance at or below X (currently X = 900 m). Verify deceleration and stopped speed, then press G for the wanted asteroid as the Shoot step. Do not wait to reach a numeric cruising speed or aim for a previously observed final distance. No model turns, chat or separate logging steps occur between local range observation and brake dispatch. Preserve the controller's timestamped capture and event logs; actual brake-input range may still be unknown.

The loop stops on invalid/stale readings, changed target, non-closing or impossible range changes, cancellation, timeout or missed cadence. Inspect its fresh cleanup snapshot after any failure; a zero-throttle command alone is not proof of0.0. Do not continue with manual full-speed polling after a controller failure. Select a clearer candidate or report the concrete blocker. After a verified stop, confirm HP damage/destruction and resume the belt candidate workflow. Check target HP after G. If HP is falling, continue mining without checking assignment state. If HP does not fall, retry G once; if still unchanged, defer. Keep fireWhenBraking false for the normal Stop then Shoot sequence; press G after verifying 0.0 and range at most 800 m.

The user-provided controls screenshot confirms **PageUp = Full speed (MAX_THROTTLE)** and **PageDown = Idle speed (ZERO_THROTTLE)**. Use these shortcuts, not slider dragging. For supported Sky calls use `key: 'Page_Up'` and `key: 'Page_Down'`. Idle throttle is a command, not proof the ship stopped; verify deceleration and stopped speed from fresh HUD readings. Earlier measured speeds and stopping distances belong only in historical logs. Verify braking and stopped speed on every run; keep route selection visual and separate from the fixed distance/speed crops.


## Scheduling preference

The user requires asking before creating any scheduled task. Do not create or recreate mining monitors, heartbeat automations, or scheduled retries without explicit approval of the proposed schedule. Use the running mining subagent and direct status checks for this operation.







