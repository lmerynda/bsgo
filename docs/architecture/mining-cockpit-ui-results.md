# Mode-centered mining cockpit implementation

Implemented 2026-10-07 in `tools/offline-bot/bsgo_offline/observer.html`, following [the accepted direction](../design/cockpit-astra-20261007/selected-direction.md). The normal dashboard at `http://127.0.0.1:8765/` serves this interface. The eight concept images and earlier prototypes remain unchanged.

## Operator workflow

Assist and Autopilot are primary workspace choices. Choosing one changes the local panel and clears previous scene assessments; it sends no activation request. The status strip shows the acknowledged operating mode, action state, input owner, observation freshness, field failures, and persistent Pause. Cancel remains available outside the action panel when a routine is active, including while inspecting the other workspace.

Assist starts on selected-target auto-scan, with Scan once and Approach also available. Each action preserves its existing explicit Start, controller readiness checks, expiring context token, human assessment, and cancellation/cleanup requirements. Acknowledged active routines determine the action panel. Scan findings disclose selected-target scope and whether results are recorded rather than verified current resource composition. Visible/nearby scanning is labeled unavailable.

Autopilot has objective, target, phase, and activation space. Its activation stays disabled when the backend reports no automatic controller. Unreported mission data stays unknown; an individual Assist routine is not presented as an automatic mining cycle. Full automation and nearby discovery are subsequent backend work.

The image fills the available left workspace with its original aspect ratio and colors. App instruments and warnings sit outside the game pixels. Images decode before being displayed, use capture identity/version fences, and retain only previously verified same-session pixels while waiting. A retained frame keeps its own version/dimensions and an explicit age/not-current label. Capture-session changes clear old pixels and reject late image callbacks.

Calibrated control evidence and display-only HUD readings retain their separate status. Display-only values do not hide rejected calibrated fields or enable actions. Diagnostics, recorded history, and settings remain accessible below the main workspace.

POST requests have a five-second bounded wait with an unknown-outcome message on timeout. Pause can be requested while another mutation is pending. New starts and mode activation remain blocked while Pause is pending; controller acknowledgment remains authoritative.

## Validation

- 68 focused tests passed across mode selection, urgent Pause, explicit activation, scene assessment, observation API, frame identity/decoding, preview compatibility, controller admission, cancellation, and input ownership.
- Production browser layout inspected at 1280 × 800, 1920 × 1080, and 390 × 844. Desktop game view and action controls fit the viewport; narrow layout has no horizontal overflow. No duplicate element IDs; image opacity is 1 and filter is none.
- Synthetic browser fixture uses the real `ManualAssistController` with fake observations and runner, explicitly marked SYNTHETIC DEMO. Pointer checks exercise Enable, assessment, Scan once, switching workspaces during an active routine, global Cancel, all four Approach checks, Start approach, and global Pause. The acknowledged mode stays Assist and unavailable Autopilot activation remains disabled. Cleanup returns input ownership to Human and the action state to Paused.
- Viewer tests cover retained-frame provenance, decode races, session changes, failures, age, and no duplicate image request after decoding. UI tests cover pending/rejected activation and Pause during a pending Start.

These checks establish UI and offline controller behavior. They do not establish live nearby scanning, automated mining, game-input compatibility, or a fresh live capture. During rendered inspection the existing game capture was unavailable: the dashboard honestly showed unknown current readings and an aged last-known frame. No game input was sent during this implementation.

Local test output and synthetic QA artifacts are under `logs/offline-bot/`; game screenshots remain local rather than being committed to the design archive. Future live task verification should record mode, task, observed usability problem, adjustment, and result against the preserved proposals.
