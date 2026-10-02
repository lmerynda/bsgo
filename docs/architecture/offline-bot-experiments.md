# Offline recognition and input experiments

The Python harness implements independent recognition, immutable published state, a guarded coordinator, recording-only keyboard/mouse input, a synthetic receiver, and cancellable delayed planner proposals. This tests the proposed application boundaries without accessing the game. Python remains the current implementation choice. Recent telemetry now rolls over in memory, while a separate disk journal preserves the event stream when logging succeeds. The real OCR replay and synthetic control model are deliberately separate; the latter's physics are invented test parameters.

## Saved pixel experiment

The OCR worker processed all 25 frames from the frozen `sol-tylium-live-2321` run. While the consumer slept for 0.4 seconds, publication advanced from sequence 1 to 13. The finite replay ended with `source_exhausted`, no recognition error, and a successful worker join. This demonstrates scheduling independence in this run; it does not establish a live capture rate or control deadline. The [report](../../logs/offline-bot/saved-frames/report.json) preserves acquisition/publication times, field values, and OCR evidence.

## Synthetic input experiments

| Scenario | Observed result | Evidence |
| --- | --- | --- |
| Target click, approach, brake | Target click consumed; maximum and idle throttle requested; fresh observed speed zero required before completion. Recognition continued during a delayed proposal, which was revalidated against newer state. | [Nominal report](../../logs/offline-bot/nominal/report.json), [receiver viewer](../../logs/offline-bot/nominal/receiver.html) |
| Receiver ignores brake | Coordinator rejected missing deceleration acknowledgement, requested cleanup, and reported `stop_observed: false`. | [Ignored brake](../../logs/offline-bot/ignored-brake/report.json) |
| Recognition stops publishing | Age computed at consumption rejected the unchanged snapshot as stale; cleanup requested without an observed-stop claim. | [Stale state](../../logs/offline-bot/stale-state/report.json) |
| Source throws | Worker published unhealthy unknown fields; coordinator cancelled ordinary work and requested cleanup. | [Capture fault](../../logs/offline-bot/capture-fault/report.json) |

The input trace records frame coordinates, keys, action sequence, request time, context, and receiver disposition. Receiver effects are separate from input requests, and final state is separate from both. The [nominal input JSONL](../../logs/offline-bot/nominal/input.jsonl) exports recent request history; its scope is explained below. All earlier demonstration workers were joined; no background harness service is required.

The self-contained receiver HTML also accepts manual browser input. A headless Chromium check delivered a left click near `(1200, 600)`, a right click, PageUp/PageDown and letter key down/up events, and focus loss while Shift was held. The exported trace contains ten received events with browser trust flags. Export, clearing click markers, and the 256-event limit passed without browser script errors. See the [received event trace](../../logs/offline-bot/nominal/browser-received-events.json), [verification report](../../logs/offline-bot/nominal/browser-verification.json), and [receiver preview](../../logs/offline-bot/nominal/receiver-preview.png). This checks browser event delivery; native receiver results have their [own validation boundary](native-backend-experiments.md).

An earlier Python suite passed 69 tests covering the individual blocks and their integration, including a session reconnect during the running coordinator. That count predates the rolling telemetry changes; use the current test command in the [run instructions](../../tools/offline-bot/README.md) for current validation. The optional browser smoke test is saved as `tools/offline-bot/verify_receiver.cjs` and requires Playwright plus a Chromium browser.

## Recent history and disk traces

Input requests, native delivery records, coordinator events, and synthetic receiver effects each retain 64,000 recent entries in memory by default. Reaching a history limit evicts the oldest entries and increments lifetime/eviction counters; it does not end the routine or cancel input. The receiver backlog remains a separate bounded action queue: an actual backlog overflow still rejects ordinary work and preserves cleanup capacity. Input disposition bookkeeping updates at eviction without rescanning the full retained history on every action.

`input.jsonl` contains only the recent request window, with dispositions for those retained requests. Its `input.jsonl.stats.json` sidecar explicitly records `scope: recent_record_history`, `complete_lifetime_trace: false`, and history statistics. The report and HTML viewer similarly expose recent coordinator/receiver/delivery records rather than an unlimited lifetime audit.

The separate [TraceJournal](../../tools/offline-bot/bsgo_offline/trace.py) receives request/disposition, native delivery, coordinator, and receiver events from configured components. Each disk package contains up to 64,000 events in JSONL segments, with an 8 MiB segment-size threshold. A full package becomes `trace-UUID-package-NNNNNN.zip`, containing its segments and a manifest with event counts, sequence bounds, and sizes. Before removing raw files, the writer closes the ZIP, verifies exact entries, sizes, manifest, and CRCs, and publishes its final name. Recording then continues in a fresh package. A partial package stays raw on shutdown; prior ZIPs and runs are retained. Archive failures preserve raw evidence and report an error. Each request, disposition, delivery, coordinator event, or receiver effect counts as one package event.

Journal submission uses a bounded 64,000-event backlog, and a dedicated writer performs disk I/O and compression. A full backlog, rejected event, write failure, or archive failure is observable in journal statistics and component drop counters. Logging loss does not become an input failure. The journal closes with a bounded wait; `write_complete` is true only after the writer finishes without reported loss or error. Inspect both journal statistics and component drop counters before treating the disk trace as complete. The browser and native receiver fixtures retain their own small event limits for focused test inspection; those fixture limits are separate from application history.

Earlier history validation on October 2, 2026 passed 149 Python tests. Its [retention soak](../../logs/offline-bot/history-soak/soak-report.json) processed 12,000 ordinary requests with only 16 recent requests and 32 recent native delivery records retained; all 36,012 disk events were preserved. The subsequent [64,000-entry soak](../../logs/offline-bot/history-64000/soak-report.json) processed 72,000 ordinary requests plus four hold/release/stop cleanup records. Both recent histories retained 64,000 entries, and all 216,012 events were read back in sequence across three 64,000-event ZIP packages and a 24,012-event active package, without gaps or errors. The native API was mocked throughout; this checks retention and cleanup rather than desktop delivery. A [synthetic approach run](../../logs/offline-bot/history-demo/report.json) also completed with the journal enabled. Blocked-disk, write-failure, rotation, payload-copy, archive-failure preservation, and bounded shutdown cases have unit coverage.

Segment and archive metadata also stay bounded: statistics retain only the latest 128 names, with lifetime counts. Tests verify that 140 segments and 130 ZIP archives remain readable after earlier names leave those recent lists. Use `bsgo_offline.trace.iter_trace_events(directory, run_id)` to stream ZIP packages followed by the active raw files; it avoids duplicate events if raw cleanup failed after ZIP publication. A single event envelope is never split across files. A completed write is ordinary flushed file output, not a guarantee against OS or power failure.

The current suite passes 159 tests, including ZIP write/verification failures that preserve raw files, cleanup-failure deduplication, and input cleanup after a 70,000-record run. The larger-history synthetic approach run completed and joined all workers.

## Assumptions exposed

The approach prototype's fixed per-sample closing check assumes a roughly two-second decision cadence. Faster recognition must not automatically invoke it on every frame. The coordinator keeps the preserved policy cadence while checking freshness, selection, session, and layout between decisions.

Releasing a held key does not reset latched throttle. Cancellation therefore needs an explicit cleanup stop request, with subsequent observation required to claim stopping. Pending ordinary actions must be removed from the receiver queue, while their request evidence is retained. An action already dequeued cannot be recalled and needs a receiver-side cancellation check.

An HP-derived target signature is not a unique object identifier. Delayed proposals also depend on portrait, dimensions, layout, epoch, and freshness. Changing snapshot sequence alone is expected during independent recognition and does not invalidate a proposal whose relevant evidence still applies.

Telemetry can fill up or fail while action cleanup remains necessary. Recent history rolls over, and unavailable disk logging increments visible loss counters while control continues. Action queues separately reserve cleanup capacity, so a real input failure can still release holds and request a stop. Cleanup uses the configured clock when possible; a failed clock falls back to wall monotonic time with an explicit marker.

## Next boundary

The harness now includes [native capture/input implementations and an owned receiver experiment](native-backend-experiments.md). BSGO compatibility, action-effect timing, actual stopping behavior, and additional mining observations still need game-specific acceptance. The fake planner is a delay/failure test double, not evidence of LLM decision quality. [Run instructions and implementation](../../tools/offline-bot/README.md).
