# HUD decision-loop timing research

Scope: read-only profiling of the saved `trial-2s` artifacts and an offline warm-process benchmark over the ten saved HUD fixtures. No game input was sent. The capture loop, HUD reader, and approach controller were not changed. The benchmark script is [performance_benchmark.py](performance_benchmark.py); it creates and removes uniquely named scratch files in this directory and starts no worker process.

## Findings

The two-second cadence in `capture-loop.mjs` is primarily intentional. Each completed observation waits `max(0, intervalMs - elapsed)` before the next one, with `intervalMs` defaulting to 2,000 ms. In the six rows of `runs/trial-2s/capture-1790802778008.jsonl`, measured start intervals were 2,002.233–2,007.335 ms. The work completed inside each interval was much shorter:

| Stage | Trial range | Median | Notes |
| --- | ---: | ---: | --- |
| Sky state capture (`capture_ms`) | 125.532–423.740 ms | 252.948 ms | Largest measured stage; six observations only |
| Capture through OCR response (`cycle_ms`) | 162.084–456.626 ms | 295.842 ms | Includes frame write and request/response handoff |
| OCR recognition (`recognition_ms`) | 1.147–1.506 ms | 1.181 ms | About 1 ms, so tuning recognition is not a useful first move |
| Frame age at returned response | 146.57–439.31 ms | 277.135 ms | Capture timestamp is set before the Sky call |

All six rows report `deadline_missed: false`. The sample is too small for a stable tail estimate, and its HUD rows show the same `screenshot-0` id. The current source has a repeated-id invalidation guard; the saved run rows are still marked valid. That discrepancy must be reconciled before using those rows to claim valid live decisions. The active fix is outside this research.

## Offline fixture benchmark

Ran 100 iterations on each of the ten 2560×1440 fixture images (1,000 observations per image-processing metric) in one warmed Python 3.12.14 process with Pillow 12.3.0 and NumPy 2.3.5. `time.perf_counter_ns()` timed each operation. The files called `fixtures/live` are saved images; none of these timings involve the live game.

| Operation | p50 | p95 | max |
| --- | ---: | ---: | ---: |
| Pillow open + full PNG decode | 11.37 ms | 12.14 ms | 14.00 ms |
| In-memory `HudReader.read`, including frame-age validation | 1.43 ms | 1.66 ms | 2.39 ms |
| Write a fixture-sized frame file (~0.4 MB) | 0.40 ms | 0.59 ms | 0.81 ms |
| Publish request JSON by temp-write + rename | 0.60 ms | 0.76 ms | 1.28 ms |
| Read + parse request JSON | 3.16 ms | 4.39 ms | 6.19 ms |
| Publish response JSON by temp-write + rename | 0.86 ms | 1.07 ms | 3.11 ms |
| Read + parse response JSON | 3.56 ms | 6.49 ms | 14.58 ms |
| Append capture log + publish latest JSON | 0.86 ms | 1.13 ms | 1.41 ms |
| Full simulated frame/file handoff in one process | 25.51 ms | 29.83 ms | 36.73 ms |

The file handoff includes a frame write, request and response file exchange, Python image decode/read, and capture/latest logging. It excludes Sky capture, a separate worker process, the worker's 10 ms watcher sleep, OS scheduling between processes, and model turns. It therefore estimates component costs; it is not a live cycle measurement. The frame file write and log updates are each around 1 ms or less at p95. Avoid PNG/file/IPC redesign unless a live stage breakdown shows those stages are materially longer.

## Recommended next measurements and cadence

1. Keep all decision logic local and deterministic. The current Python reader is around 1.5 ms on saved frames; calling a model to re-read each HUD sample would replace a millisecond local step with a multi-second round trip. Use slower model turns for candidate choice or route review, then keep the selected-target braking loop local.
2. After the repeated screenshot-id issue is resolved, try a 1,000 ms start-to-start interval. The six recorded cycles peaked at 457 ms, which leaves about 543 ms of nominal margin at 1,000 ms. Treat that as a proposed observational trial, not a live cadence guarantee. Retain one in-flight sample/control at a time, the existing deadline and stale-frame rejection, and fail-closed behavior. Require at least 100 consecutive observations with p99 `total_cycle_ms` ≤500 ms, p99 `frame_age_ms` ≤750 ms, and zero deadline misses before relying on this setting.
3. Consider 500 ms only for the near-brake phase after a separate timing soak records p99 `total_cycle_ms` ≤250 ms and p99 `frame_age_ms` ≤250 ms, with zero misses over at least 200 consecutive samples and no reused-frame false positives. The saved run does not meet that gate: its maximum observed pre-hook cycle was 457 ms and maximum frame age was 439 ms. At the configured 44 m/s cap, the nominal distance between samples is 44 m at 1 s and 22 m at 500 ms; this is a sampling-lag comparison, not a stopping-distance or clearance proof. With a 700 m brake threshold, a 1 s mode and a 457 ms sample-cycle bound suggest switching to the fast mode around 765 m (`700 + 44 × (1 + 0.457)`) so the faster cadence is active before the threshold. The braking threshold itself remains controlled by the approach policy.
4. Add per-stage monotonic timings to a later approved run: Sky capture, frame serialization/write, request publication, worker pickup and response, observation hook/input acknowledgement, capture log/latest publication, and actual start interval. Track p50/p95/p99/max and missed deadlines. `setTimeout` delivery is not exact; Node documents that callback timing depends on event-loop work and makes no exact-timing guarantee ([Timers](https://nodejs.org/api/timers.html)). `monitorEventLoopDelay()` can record loop-delay histograms alongside cycle measurements ([Performance hooks](https://nodejs.org/api/perf_hooks.html)); keep its sampling resolution and measurement window in the run record.
5. Only after those measurements, consider replacing the 10 ms directory watcher with a persistent stdio worker or reducing redundant file operations. `hud_reader.py --serve` already offers line-delimited stdio, but it still expects a frame path, so it does not by itself remove the frame write. The offline file benchmark does not justify that change yet.

Timer callbacks may run late, and faster sampling alone does not guarantee timely braking: decision latency also includes frame age and input acknowledgement. The saved six-row run omitted `control_hook_ms` and `total_cycle_ms`; capture/recognition timings therefore do not establish how quickly a live action returns. Do not claim a 1 s or 500 ms live control cadence until those fields and the repeated-frame guard are verified in an authorized live run.
