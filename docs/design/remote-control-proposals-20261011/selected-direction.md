# Selected direction: Companion dock + Remote pocket

Decision recorded 2026-10-11 after reviewing [the three directions](proposals.html). The user chose **both B and C**:

- **B · Companion dock** is the in-game helper. It is a slim panel beside the real BSGO window, used while you are playing (Assist).
- **C · Remote pocket** is the remote control for **scheduled farming**. You use it from a phone or another device while the PC runs unattended NPC-farm or mining sessions (Autopilot).

**A · Mission console** is not built as a separate app. Its useful pieces carry into the other two: the plan and step model, the per-step blocker, the findings with freshness labels, and the plain-language activity log. The dock's “More ›” window and the remote's Now tab reuse them.

## One core, two surfaces

```
            ┌─────────────── Wingman core (PC) ────────────────┐
            │ controller · observation · scheduler · stop rules │
            │ status snapshot · event feed · command + ack      │
            └───────────────┬───────────────────┬───────────────┘
                 loopback   │                   │  LAN / tailnet, paired
                  ┌─────────▼────────┐   ┌──────▼──────────┐
                  │ B Companion dock │   │ C Remote pocket │
                  │ playing, Assist  │   │ away, Schedule  │
                  └──────────────────┘   └─────────────────┘
```

Both surfaces read the same **status snapshot**: one status sentence, owner, mode, phase or step, progress, vitals, and freshness. They also read the same **event feed** of findings, decisions, stops, and run milestones, and they send the same **commands** (pause, resume, decide, start, or skip a scheduled run). Each command carries an acknowledgement. Neither surface owns logic. If something works in one surface, it works in the other.

## Rules both surfaces must share

1. **Pause wins from anywhere.** A Pause from the dock, a hotkey, or the phone takes effect immediately. It is acknowledged and shown on every surface along with where it came from (“Paused from phone · 0.4 s”).
2. **One owner at a time, always visible.** The owner is You, App, or Stopping/unknown. A scheduled run never starts while the human is flying. If the user starts playing during a run, the app yields and reports it.
3. **Every scheduled run has stop rules**, such as hull below a threshold, an enemy player nearby, the game window lost, or a time limit. Hitting a rule stops the run, hands control back, and sends a notification.
4. **Honest states carry over** from the current app: never show unknown as zero, label stale data with its time, and mark features that are not yet available on their entry. For now, live NPC farming and Autopilot are still unavailable. The schedule must not offer them until they are verified.
5. **Nothing is drawn into the game image.** The dock sits outside the game window. Toasts are separate overlay windows.

## Surface responsibilities

| | B · Companion dock | C · Remote pocket |
| --- | --- | --- |
| When | You are at the PC, playing | You are away; scheduled or long runs |
| Main jobs | Scan, approach, auto-scan, quick findings, ship vitals | Monitor, decide, pause, schedule, review yield |
| Input | Global hotkeys (F8 Pause, F9 primary, F10, F11), large buttons | Large thumb buttons, notifications |
| Screens | Dock, toast; “More ›” opens history/diagnostics window | Now · Alerts · Schedule · Summary |
| Theme | Dark, compact, 300 px | Light, phone-first |
| Reach | Loopback only | Paired device over LAN or tailnet |

## Proposed build order

1. **Core contract.** Define the status snapshot, event feed, and command/ack API on the existing server. Existing [observation](../../../tools/offline-bot/bsgo_offline/observation_api.py) and [execution status](../../../tools/offline-bot/bsgo_offline/execution_status.py) data feed it.
2. **B dock, first version.** Serve a dock page from the existing server and show it in a narrow always-on-top window positioned beside the BSGO window, reusing the existing window discovery. Add a global Pause hotkey. Retire the long rail.
3. **C remote, read-only plus Pause.** Expose Now and Alerts to a paired device. The server is loopback-only with a Host check today ([observer_ui.py:742](../../../tools/offline-bot/bsgo_offline/observer_ui.py#L742)), so this needs an explicit, opt-in remote listener with pairing.
4. **Scheduler and stop rules.** Start with the NPC-farm rehearsal (offline). Enable live schedules only after the live capability is verified.
5. **Remote decisions and scheduling** from the phone, and push notifications.

## Open questions for the user

- **Remote reach:** home Wi-Fi only, or also away from home? Tailscale is installed on this PC, so a tailnet-only listener is a simple and private option.
- **Notifications:** browser push, or a phone-friendly service such as ntfy or Telegram?
- **Dock shell:** a small native wrapper around the web dock (for example WebView2 or pywebview) versus a pure Python/Tk window. A web dock keeps one UI codebase for both surfaces.
- **Hotkeys:** confirm that F8–F11 are free in your BSGO keybinds.

Live gameplay verification, remote input, and enabling scheduled live farming each need separate authorisation, as before.
