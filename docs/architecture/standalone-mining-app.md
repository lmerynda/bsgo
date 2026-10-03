# Standalone mining application architecture review

This review turns the research discussion into a practical design for a local BSGO mining application. The starting point is one Python application with an independent recognition thread, shared state in memory, and a coordinator that executes mining routines. An optional LLM helps with broader decisions. An [offline harness](../../tools/offline-bot/README.md) now implements and tests the observation/control boundaries using saved screenshots and a synthetic receiver. [Native Windows backends](native-backend-experiments.md) passed acceptance against an owned test receiver; BSGO compatibility remains separate from the live standalone mining runtime, which is not yet complete.

The user confirmed that recognition should run independently of LLM decisions and that a thread is preferable to a separate process when it keeps the application simpler. Other choices below are recommendations or explicitly unresolved questions.

## What exists today

The repository contains cooperating prototypes and gameplay instructions, rather than a complete standalone application:

- [Experiment harness](../../tools/offline-bot/README.md) provides threaded recognition, shared snapshots, the Python approach policy, recorded input, a synthetic receiver, and delayed-proposal revalidation. Its opt-in native receiver test adds Windows client capture and guarded `SendInput`; ordinary replay/simulation modes remain recording-only.
- [hud_reader.py](../../tools/hud-reader/hud_reader.py) recognizes selected-target distance, ship speed, asteroid title, maximum HP, and a portrait signature in calibrated screenshots. Its callable `HudReader.read` method is reusable without the file-watching CLI.
- [capture-loop.mjs](../../tools/hud-reader/capture-loop.mjs) obtains Sky screenshots, saves images, exchanges request and response files with Python, awaits an observation callback, and publishes a latest JSON result.
- [approach-controller.mjs](../../tools/hud-reader/approach-controller.mjs) implements an approach policy and uses Sky for throttle input. It verifies observations and deceleration. It is one routine, not the complete mining coordinator.
- [bsgo-controls.au3](../../tools/control-overlay/bsgo-controls.au3) is a manual control overlay. It does not implement autonomous mining.
- [Mining instructions](../../.agents/skills/bsgo-mining/SKILL.md) describe selection, scanning, approach, firing, navigation, and recovery still performed by an agent.

Hull, energy, resource recognition, general screen classification, candidate acquisition, and reward detection are requirements for the new application; the present HUD reader does not provide that complete state.

## Assumptions found in the review

| Existing or proposed assumption | Evidence and consequence | Design correction |
| --- | --- | --- |
| Independent recognition requires another process | The earlier proposal conflated a logical boundary with a process boundary. | Use one application and a recognition thread. Revisit processes only for a demonstrated isolation or performance need. |
| Already saved state requires a file or database read | The current capture loop writes `latest.json` after each observation callback. | Publish live state in memory. Save snapshots and evidence for debugging separately. Disk persistence is not a requirement for every decision. |
| Recognition can wait for the decision maker | `runCaptureLoop` awaits `onObservation`; the controller callback may await input. | Recognition publishes without calling or waiting for control or LLM code. |
| One validity flag represents all game state | `HudReader.read` combines distance, speed, title, HP, and freshness reasons into one flag. | Publish validity per field. Each action checks the particular fields and compatible observation times it needs. Preserve the existing combined approach check until the replacement is verified. |
| Maximum HP identifies a world object | `target_identity` is `asteroid:hp:<maximum>`; portrait checks add evidence but the reader itself acknowledges collisions. | Treat HP and portrait as signatures. Assign local selection tracks with explicit continuity uncertainty. Never claim a persistent world identity from these signatures alone. |
| A stored age stays accurate | The reader and capture loop calculate frame age at processing time. That number stops increasing in a saved result. | Consumers compute age at use time from monotonic timestamps. Keep UTC for human-readable records. |
| Capture request time is actual frame time | The loop timestamps before requesting a Sky screenshot. | Store capture start, completion, and any backend-provided frame timestamp separately. Use conservative age when frame production time is unknown. |
| A screenshot token proves freshness | The current source notes that Sky screenshot IDs can repeat on fresh captures. A historical timing note describes an older guard. | Give acquisitions local sequence numbers; separately track backend frame timing and scene progress. A new sequence proves a new acquisition attempt, not a newly rendered game frame. |
| Calibrated screenshots represent every layout | The reader requires 2560 by 1440 and explicit original/restart profiles. | Detect layout compatibility. A window, render-scale, or profile change invalidates dependent observations and requires a verified profile. Do not silently resize into validity. |
| Route and heading knowledge is permanent | Current live preflight accepts caller-supplied flags and route evidence; surrounding agent instructions own invalidation after movement or takeover. | Make those dependencies explicit in coordinator state. Evidence carries the session, selection, and movement context where it was assessed. |
| Local execution implies background operation | Existing input/capture compatibility depends on the target surface. | Begin with an explicitly owned foreground game window. Covered, minimized, and concurrent human-use modes remain separate compatibility questions. |
| Every component needs its own worker | Earlier diagrams showed logical responsibilities without specifying scheduling needs. | Begin with recognition plus one coordinator loop. Add an I/O worker only for blocking network or recording work; no thread per module. |
| A generic automation framework is necessary | Research identified useful frameworks but did not test one against BSGO. | Reuse our small reader and policy first. Choose a framework only if a compatibility experiment shows it removes substantial work. |

## Responsibilities and execution

The application has two primary execution paths:

1. The recognition thread captures the owned game window, recognizes the supported fields, updates observation continuity, and publishes a complete snapshot.
2. The coordinator reads the latest snapshot and runs the current mining routine. It schedules short input operations and verifies their effects through subsequent snapshots.

The recognizer owns observations. The coordinator owns goals, routine progress, candidate history, deferred targets, route assessments, and pending actions. This avoids turning recognition into a second decision maker. A small UI reads both observation and coordinator status; it submits start, pause, and configuration requests.

Recognition and input have separate ownership: one capture producer and one serialized input executor. The executor is initially a coordinator-owned component, not another mandatory thread. Network requests run asynchronously or on an I/O worker. Long key holds, waits for screen transitions, and LLM requests must not block the coordinator's freshness and cancellation checks. If the selected backend has blocking calls, bound or isolate those calls rather than assuming they are instantaneous.

Python is the current runtime choice, confirmed by the user on October 2, 2026. The reader already exposes an in-process Python method, and the native Windows backends are callable from Python. Continue building and measuring these components before considering a language migration. Threads provide scheduling independence, not guaranteed timing or crash isolation.

The desktop has an NVIDIA RTX 4070 with approximately 12 GiB of VRAM, verified on October 1, 2026. The user normally serves local AI through Ollama. Use that GPU as an optional resource for recognition and planning: a small classifier can run in the recognition path if its complete latency is suitable, while slower vision-language interpretation runs separately and publishes explicitly interpreted results. Ollama is the preferred LLM and vision-language serving option; specialized classifiers and detectors can use an in-process inference runtime. GPU availability does not make every inference faster or eliminate contention with the game. The [building block experiment plan](building-block-experiments.md) defines the comparisons and records the current server compatibility boundary.

## State publication and interpretation

Publish an immutable snapshot by briefly replacing the shared reference under a lock. Consumers retrieve a consistent reference under the same lock and process it after releasing the lock. Capture, image recognition, disk writes, input, and network calls never run while holding the publication lock. Immutability includes nested values; frame buffers referenced by a snapshot must not be overwritten by later captures.

Each snapshot contains:

- A snapshot sequence, session epoch, acquisition sequence, dimensions, window identity, and calibration profile.
- Capture start and completion times, recognition completion time, and actual frame production time when the backend supplies it.
- Per-field value, observation time, source frame, validity, and rejection reason. Uncertain alternatives remain separate from accepted values; matching scores are not probabilities.
- Recognition health and observation-continuity status.

An invalid current field is unknown. Its last accepted value remains available as explicitly historical information. For example, a failed speed read must not publish the previous zero as the current speed. Age is computed when a consumer uses the field, with action-specific freshness limits. Related fields can come from one frame or from an explicitly permitted time window; combining unrelated stale observations is not a complete current state.

The recognizer publishes unhealthy status when it can report a failure. Independently, the coordinator checks elapsed time since publication so a stalled or terminated recognizer cannot leave the application believing an old snapshot is fresh. A paused coordinator can still inspect recognition updates.

Latest state is sufficient for steering, but it can miss a short-lived scan result or reward between coordinator reads. Use a small bounded sequence of observed events alongside the snapshot. Give events IDs for deduplication. An overflow explicitly marks an observation gap; resynchronize rather than silently counting missed events. Tracking history is evidence, not a complete reconstruction of the game world.

Increment the session epoch on client reconnection and invalidate dependent tracks. Layout changes invalidate the relevant profile; target loss, movement, or human takeover invalidate the appropriate selection and route evidence. A process restart begins with reacquisition. Persisted state may help explain prior work but does not prove the live game has retained it.

## Mining and input behavior

Start with named routines for acquire, select, scan, assess, approach, stop, mine, confirm outcome, and recover. These can be ordinary Python state objects initially; a state-machine library is optional. Each routine specifies required observations, available actions, completion evidence, timeouts, and bounded recovery.

Preserve the current approach policy's validated behavior while separating it from Sky and file IPC. Port its existing tests with it. The numeric defaults in the prototype are configuration under review, not guarantees for every ship or capture backend. Faster recognition cadence must follow measured capture, recognition, scheduling, and input latency. Pause and fault handling must preempt normal work; they cannot sit behind a long queue of ordinary commands.

An input result records requested, dispatched, and observed states separately. A key successfully dispatched is not proof that the ship stopped, a map opened, or an asteroid was destroyed. Lost focus or human takeover prevents new ordinary commands; cleanup follows verified ownership and backend capability. Releasing held keys alone does not establish idle throttle, so a controllable fault during motion requires the stop command and subsequent observation. A fatal application failure cannot guarantee a stopped game through an in-process watchdog.

Stopping recognition and pausing gameplay are different controls. Pause cancels pending routine work, performs the necessary owned stop/release handling, and reports whether a stop was observed. Recognition may continue. Shutdown joins workers and reports unresolved cleanup rather than claiming success from attempted input.

## Optional LLM decisions

An LLM receives a snapshot, relevant recent events, and selected frame evidence from the already running recognizer. It can propose candidate choice, relocation, route interpretation, or recovery. The coordinator validates the proposal against the current session, selection, observations, and routine constraints before accepting it.

A change in snapshot sequence alone does not invalidate a plan: continuous recognition naturally produces newer snapshots. Revalidate the proposal's relevant dependencies. Discard it if the session or target changed, or if its evidence is no longer applicable. Bound requests and retries; planner unavailability is an explicit state with a local continuation or stop policy.

Keep speed monitoring, braking, cancellation, and normal routine transitions local. Broader scene interpretation remains a separate problem: neither OCR nor a general object detector proves a clear three-dimensional corridor from one image. Candidate acquisition, route assessment, and outcome detection need their own measured acceptance before claiming autonomous mining.

HUD radar is another route-evidence source to validate. The current reader does
not use it. [Radar calibration experiments](radar-calibration-plan.md) will
establish ship/camera-relative bearing, contact association and available
range/height evidence before integrating a forward-path interpretation.
Publish uncertain or hidden radar readings as unknown; absent detections alone
cannot establish a clear route.

## Persistence and debugging

The in-memory snapshot is the live source of observations. A recorder can mirror state and events to files and retain the original frames used by recognition and decisions, including rejected inputs, matching the established image-retention requirement. Recording must not gate normal snapshot publication.

Keep recent telemetry in rolling, bounded memory histories and stream the complete event trace to rotating disk files through a bounded writer queue. Filling recent history evicts its oldest entries and never stops control. Expose enqueue gaps, write errors, retained ranges, and lifetime counts; a recent-history export must identify its limited scope. The implemented diagnostic journal does not gate input or cleanup. A future requirement for mandatory audit recording would need a separate explicit operating policy.

The current harness retains 64,000 recent entries in each input, native delivery, coordinator, and synthetic receiver history by default. These are memory windows rather than lifetime action limits. Pending input remains independently bounded and preserves reserved cleanup capacity. Disk packages hold 64,000 events in segments with an 8 MiB size threshold. A full package is compressed to a verified ZIP, its raw files are removed only after verification and publication, and recording continues in a fresh package. Partial packages remain raw on shutdown; archives stay on disk. Compression runs on the writer thread, which has a 64,000-event backlog and reports errors and bounded shutdown. A single event envelope is never split between files. The native injection-tag sequence limit is a separate protocol constraint.

Start with the UI needed to select the game window, start and pause, inspect current readings and their age, and see the routine and failure reason. A web server, database, plugin system, remote control API, and elaborate overlay are optional additions that require an actual use case.

## First implementation boundary

Implement the recognition thread, snapshot publication, coordinator freshness checks, and a minimal inspection interface first. Reuse `HudReader.read` on compatible frames. Validate that recognition continues during a delayed decision, that invalid fields do not become current values, that session/layout changes invalidate dependencies, and that no reader sees a partially updated snapshot.

Then establish capture and input compatibility with BSGO and port the approach routine. Verify complete action-and-observation sequences, not merely successful API calls. Extend recognition and routines for selection, scan results, mining progress, and rewards. Add the LLM adapter once the local observation and control contracts work.

Open questions are the capture and input backends, acceptable observation latency during movement, layout calibration coverage, candidate acquisition and route evidence, and how much scene interpretation needs an LLM. Process separation, mandatory databases, and a framework rewrite are not initial requirements.

## Review validation

This review inspected the current capture loop, reader, approach controller, overlay, gameplay instructions, and retained accuracy/timing reports. Historical benchmarks and test counts are treated as recorded evidence, not fresh verification. No gameplay code was changed and no live input or compatibility experiment was performed as part of this review.
