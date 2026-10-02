# Mining application building block experiments

Build and evaluate one block at a time using the [reviewed architecture](standalone-mining-app.md). Recognition runs independently in a thread and publishes state in memory. Local routines consume that state; an optional planner proposes broader decisions. These experiments choose the implementations from measured behavior, including CPU and GPU alternatives. This document is a plan, not a record of completed benchmarks.

## Hardware and local serving

On October 1, 2026, the user identified an NVIDIA RTX 4070 desktop and a preference for hosting local AI through Ollama. A read-only check at 2026-10-02 01:05 UTC verified an NVIDIA GeForce RTX 4070 with 12282 MiB total VRAM. The sampled usage was 4309 MiB; that is a transient whole-device reading, not an allocation budget or proof of game usage.

The initial local Ollama inspection returned version 0.6.8, installed `qwen3:8b` with Q4_K_M quantization and `nomic-embed-text:latest`, and no loaded models. The user subsequently updated the WSL service; the running endpoint now reports 0.35.0. Qwen3-VL 4B thinking and instruct variants were downloaded and tested on saved images. The [decision window and model research](decision-windows-and-local-models.md) records those smoke results, request overhead, and the broader Hugging Face comparison plan.

Use the installed Qwen3 model as the first state-only planner baseline. Ollama lists this model as text input, so it is not our screenshot-recognition baseline. The embedding model is not an image classifier or a mining decision policy. [Qwen3 model catalog](https://ollama.com/library/qwen3).

Qwen3-VL 4B Instruct is the current screenshot-interpretation reference; 2B can supply a smaller comparison, and 8B can be evaluated if resource headroom permits. The 4B artifact is listed at about 3.3 GB; Ollama reported about 4.2 GB of GPU allocation with an 8192-token context during the smoke run. Whole-device usage includes other work and temporary buffers. The upgrade resolved the earlier server-version boundary, but neither the successful descriptions nor model fit establish reliable HUD extraction or suitable control latency. Use explicit instruct tags for these comparisons; the generic 4B tag selected the thinking variant. [Qwen3-VL catalog](https://ollama.com/library/qwen3-vl:4b).

The model name written as Jev is unresolved. Keep it as a candidate supplied by the user pending an exact name or link, rather than guessing a model family or runtime.

## Recognition and planning roles

Compare three different jobs rather than treating all local AI as one interchangeable model:

- Numeric HUD recognition and stable UI features can use the existing CPU reader and OpenCV methods.
- A small classifier or detector can label screens, resource indicators, or candidate objects and run through a local inference runtime. It belongs in frequent recognition only if its latency and failure behavior meet the observation contract.
- An Ollama text or vision-language model can interpret a snapshot, choose among candidates, or propose recovery. Run its request independently of the recognition thread. Any model-generated observation keeps its source time and provenance; it does not become a fresh measured fact when the response arrives.

Ollama supports image requests and schema-constrained output. A well-formed response is not proof of correct recognition or a correct decision. Evaluate semantic correctness and unknown handling separately. [Vision API](https://docs.ollama.com/capabilities/vision), [structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

ONNX Runtime provides CUDA acceleration for NVIDIA GPUs, but its CUDA and cuDNN dependencies must match the selected package. Validate the actual execution provider and watch for unintended CPU fallback before labeling a run GPU-accelerated. A CPU implementation is a required baseline, because small inference jobs may not benefit from GPU transfer and scheduling overhead. [CUDA execution provider requirements](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

## Experiment sequence

| Experiment | Isolated question | Comparison and evidence | Completion evidence |
| --- | --- | --- | --- |
| 1 State publication | Does recognition stay independent while a decision is delayed? | Replay retained compatible frames; inject delayed decisions, recognition faults, session changes, and a slow recorder. | Consistent immutable snapshots, reader-side freshness, explicit invalid fields, independent heartbeat checks, and bounded backlog. No live input required. |
| 2 Capture | Which local backend delivers correct, fresh images? | Compare candidate native capture adapters on the same game setup. Record real dimensions, DPI, window and capture bounds, acquisition timings, and available frame timestamps. | Correct target pixels and coordinate mapping in the supported window state; missing, incompatible, or nonprogressing frames handled explicitly. Covered and minimized behavior tested separately. |
| 3 Input | Which backend delivers the controls BSGO actually responds to? | In a controlled stopped setup, verify keys, modifier chords, mouse movement, selection, and bounded drag; observe the resulting game state. | Requested, dispatched, and observed outcomes recorded; ownership, cancellation, and key release verified. Throttle tests follow verified stop capability. No ordinary work can delay cancellation indefinitely. |
| 4 HUD and OCR | Which recognizer gives useful accuracy within the timing budget? | Freeze the current reader; compare it with general OCR on identical original frames. Include CPU and, when supported, GPU runs with decode and preprocessing costs. | Field-level correct reads, wrong accepts, rejects, and latency reported across independent source groups and layouts. A faster engine that introduces incorrect accepted control readings does not replace the baseline. |
| 5 Small visual model | Does a lightweight classifier or detector resolve a concrete recognition gap? | Start with manually labeled flight, map, hangar, loading, and unknown scenes, or a specific resource/icon task. Compare simple visual features with a small learned model on CPU and GPU. | Held-out confusion matrix, unknown handling, complete inference latency, and VRAM usage. Candidate object detection needs BSGO-specific labels; a generic model is not assumed to know our asteroids. |
| 6 State-only planning | Can an installed Ollama model make useful broader decisions? | Feed Qwen3 8B labeled state scenarios with candidate choices, uncertainty, target loss, stale observations, and unavailable routes. Compare against simple rules. | Correct bounded proposals, rejection of unsupported actions, semantic/schema failures, warm and cold latency, and resource usage. Recognition continues during timeouts. Proposals are not executed in this offline evaluation. |
| 7 Image interpretation | Does a small vision model add useful scene information? | Compare the Qwen3-VL 4B Instruct reference with smaller Qwen and Hugging Face candidates using retained screenshots and crops with explicit coordinate transforms. | Scene/resource accuracy, candidate localization, unsupported assertions, unknown handling, warm/cold latency, and memory. A single image does not establish safe three-dimensional depth. |
| 8 Shared GPU load | Do game rendering, recognition, and planning coexist acceptably? | Compare the game/recognition baseline, added small-model inference, and one bounded Ollama request stream. Keep context and image sizes controlled. | Recognition latency distribution, game frame timing, available VRAM, CPU fallback/offload, queueing, and failure recovery. Reduce model size or GPU work if planning degrades the control path. |

Experiments 1 and 4 can begin from retained images without modifying the game. Experiment 6 uses an existing installed model when execution is requested. Experiments 2 and 3 need explicit ownership of the game surface at run time. Experiment 7's earlier server-version dependency has been resolved, and saved-image smoke requests have run; its task-quality and latency acceptance remain outstanding. Finish a block's result and decision before broadening its scope.

## Dataset and measurement contract

Preserve original frames used by recognition, including failures, with labels and source metadata. Split by source session/run so neighboring frames from one approach do not masquerade as independent validation. Keep calibration, training, and prompt-development examples apart from untouched evaluation groups. Model-produced labels need manual verification before becoming ground truth.

Use scene-appropriate datasets. The retained HUD corpus is useful for numeric fields but does not by itself validate hangar detection, maps, scanner colors, reward attribution, or three-dimensional navigation. Include absent, occluded, malformed, shifted, and ambiguous inputs alongside ordinary cases. Report coverage as well as wrong accepted values; an engine that rejects almost everything is not useful merely because it rarely returns a wrong answer.

Every result records code and model identity, quantization, server/runtime versions, execution provider, layout, crop/resize transforms, dataset split, and whether the game and other models were running. Measure capture, preprocessing, recognition, state publication, coordinator delay, and input separately. Record latency distributions, sample count, deadline misses, memory, and any GPU/CPU offload. Separate model load time from warm inference and include image encoding or transfers in end-to-end latency.

Choose the observation and action budgets from the measured approach envelope before relying on a faster control cadence. Do not infer real-time suitability from tokens per second, average latency, or a single successful screenshot. Actual recognition and input effects establish the usable timing budget.

## Selection policy

Start with the existing CPU reader and an independently running state thread. Add a learned recognizer where it solves a measured gap. Keep one bounded planner request outstanding initially; use short task-specific context and avoid unnecessarily retaining multiple large models. Recheck state dependencies when a response arrives. The app should remain observable and controllable when Ollama is stopped, loading, slow, or out of memory.

A successful experiment produces a result note with the selected implementation, its supported conditions, its limits, and the evidence behind that decision. No assumption that the RTX 4070 must host every component, that a larger model is always better, or that an LLM server belongs in every recognition cycle becomes part of the application.

## Current status

The [expanded screenshot benchmark](vision-corpus-benchmark.md) completed all three Qwen passes on the frozen corpus and scored all 77 labeled frames; remaining model tests were stopped at the user's request. The [offline runtime experiments](offline-bot-experiments.md) exercise recognition independence, immutable state, delayed proposals, recording input, a synthetic receiver, and fault cleanup without game access. [Native Windows backends](native-backend-experiments.md) passed acceptance against an owned receiver: all 11 requests matched received messages, pixel observation continued independently, held-key cancellation succeeded, and resize rejected capture and input. BSGO compatibility and the remaining mining observations are still separate acceptance work.

Hardware and serving were inspected, the user upgraded Ollama, and Qwen3-VL 4B variants were downloaded and exercised on saved images. Follow-up diagnostics compared a short text proposal with image classification and measured request-path overhead. The subsequent [saved screenshot model comparison](vision-model-comparison.md) evaluated five model/runtime combinations on nine real images and challenged the best clear-image models with incomplete evidence. CUDA runtime files and a dedicated FastVLM Python environment were installed under the workspace. These development comparisons do not establish general accuracy, native application timing, or live gameplay acceptance. The broader experiments remain planned. No game input was sent.
