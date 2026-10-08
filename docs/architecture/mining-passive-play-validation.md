# Passive observations during human play

Recorded October 7, 2026 local time (October 8 UTC). The user took over game
control. The mining app retained passive Windows Graphics Capture ownership;
manual actions stayed paused, selected-target auto-scan stayed off, and no
native game input was issued after that handover.

## Findings and changes

| Observation | Change or remaining limitation |
| --- | --- |
| A complete dim ASTEROID label in the retained Exrun image was rejected. | The selected-marker locator now uses brightness 100 against the existing native template. Precision, complete letters, uniqueness and bounded search remain required. The original full image now yields one label and marker box `(1420,747,1476,791)`. |
| The cockpit called a fresh, verified absence of a selected asteroid “Target type unverified.” | It now says “No asteroid selected.” Unknown or stale selection evidence still says unverified. |
| Target HP fallback needed explicit original-frame dimensions. | The actions API now publishes its observation's frame size; the UI rejects scanner-only target ratios from other dimensions, stale frames, mismatched timestamps or conflicting calibrated identity. |
| Restarting the observer could open a browser while the human played. | `easy_launcher --no-browser` starts or reuses the dashboard without opening a browser. New sessions still start paused. |
| Flight instruments disappear while docked. | Values correctly stay unavailable. The UI still lacks an explicit docked-state explanation. |
| Bright scene backgrounds and some selected targets reject HUD ratios. | These remain unknown. The retained Gemino/Exrun HP crops cover two development cases, not all backgrounds or font renderings. |
| Visible rocks and colored resource markers do not create verified findings. | Nearby acquisition, general resource classification and live scan-effect verification remain incomplete. No Z scan was sent during this validation. |

The dim-label fixture contains only a world label and marker fragment. Its source
hash, native crop coordinates and review basis are retained in
`tools/offline-bot/tests/fixtures/selected_scan_markers/annotations.json`.
This is a development regression, not held-out accuracy evidence or unselected
asteroid discovery. No full game image or chat was added to Git.

## Checks

- 92 focused tests passed across selected-marker vision, stationary scanner
  evidence, target display freshness/geometry, launcher lifecycle, manual actions,
  selected scanning and observer APIs.
- The previous passive observer drained every worker and finalized its report
  with zero input requests before restart. The replacement connected with fresh
  native 2560 × 1440 observations, inputs paused, no input owner and auto-scan off.
- The production cockpit rendered “No asteroid selected”, speed 0 m/s, hull
  5,210 / 5,210 and energy 800 / 800 from fresh live observations. Input ownership
  displayed Human. The game image retained 2560 × 1440 original pixels, opacity 1,
  no filter and no desktop horizontal overflow at 1920 × 1080.

These checks verify passive observation and the listed regressions. They do not
verify nearby scanning, live scanner delivery/effect, resource composition or an
automatic mining cycle. Those still require app-owned execution and independent
post-action image evidence when human game control is handed back explicitly.

The [eight preserved concepts](../design/cockpit-astra-20261007/selected-direction.md)
and [mode-centered layout validation](mining-cockpit-ui-results.md) remain the
design references for further changes based on actual play.
