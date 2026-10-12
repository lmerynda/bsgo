# Saved screenshot model comparison

> Historical evidence: original log paths below identify archived records, not current checkout files. Raw packages remain recoverable from Git; see the [artifact policy and recovery guide](archive/README.md). New experiment outputs are local runtime artifacts.

The [expanded benchmark](vision-corpus-benchmark.md) now covers all 77 independently labeled frames for the three Qwen models. Remaining expanded tests were stopped at the user's request. This document preserves the initial nine-frame comparison.

Qwen3.5 0.8B is the most promising small-model candidate in this first numeric HUD comparison. It read distance and speed correctly on all nine focused crops with a median request time of 0.256 seconds. Qwen3-VL 4B Instruct reached the same clear-image accuracy at 0.699 seconds. The frozen calibrated CPU reader also read all nine pairs correctly, with image decoding and recognition taking a median 0.0132 seconds.

The malformed-image challenge changes the conclusion: both Qwen models sometimes accepted incomplete digits as real values. The small model reported speed zero when the speed indicator was erased. These models remain candidates for contextual interpretation; this experiment does not justify allowing their numeric readings to control the ship.

The runs were performed on October 1, 2026, on the desktop RTX 4070. No game inputs were sent. Model outputs, source labels, runtime metadata, and timings are preserved in the experiment directory (`logs/vision-model-comparison-20261001/summary.json`).

## Inputs and task

The frozen dataset (`logs/vision-model-comparison-20261001/dataset.json`) contains nine real screenshots from eight source groups in the existing HUD accuracy manifest. It includes stopped and moving readings, speeds from zero to 44, and an asteroid and a non-asteroid target. Source labels include existing development and held-out splits; this is a development comparison, not a new independent held-out accuracy estimate.

Each model received the same request: return only JSON with the selected target distance and actual ship speed, using null for unreadable values. Expected readings were never included in requests. Output was bounded at 96 tokens. The test compared original 2560 by 1440 screenshots with an 810 by 556 composite of two regions: target HUD and ship speed. Crops were enlarged three times with nearest-neighbor resampling and labeled by region; the original files remain unchanged. Crop boxes and transforms are recorded in the dataset.

This task evaluates numeric extraction and response completion. It does not evaluate choosing targets, interpreting resources, classifying maps or hangars, detecting hostile ships, route safety, or temporal reasoning. Models that fail this narrow request may still be useful for other visual tasks.

## Results on real screenshots

Correct pairs require both distance and speed to match their source labels. Timings are medians of nine requests per variant, with the selected model loaded. Full and cropped inputs were processed in separate passes. The requests normally reused only a few text-prefix tokens, rather than the image cache. First-use vision setup remains part of the first image request; raw responses preserve cache and load information where supplied by Ollama.

| Model and runtime | Full screenshot median | Correct full pairs | Cropped HUD median | Correct cropped pairs | Response qualification |
| --- | --- | --- | --- | --- | --- |
| Qwen3.5 0.8B, Ollama Q8_0 | 0.909 s | 2 of 9 | 0.256 s | 9 of 9 | All crop responses supplied complete numeric JSON. |
| Qwen3-VL 4B Instruct, Ollama Q4_K_M | 2.289 s | 3 of 9 | 0.699 s | 9 of 9 | All crop responses supplied complete numeric JSON. |
| Qwen3-VL 2B Instruct, Ollama Q4_K_M | 1.867 s | 0 of 9 | 0.509 s | 8 of 9 after normalization | Most values were JSON strings. Only one crop response met numeric JSON types, and its distance was wrong. Normalized diagnostic reads are separate from strict acceptance. |
| FastVLM 0.5B, PyTorch CUDA FP16 SDPA | 1.717 s | No completed responses | 1.579 s | No completed responses | Every request hit the output cap. Three crop responses had correct leading numeric pairs before extra text; these were rejected as incomplete. |
| SmolVLM 500M, llama.cpp CUDA Q8_0 with F16 projector | 0.463 s | No usable complete pairs | 0.135 s | No usable complete pairs | Returned captions or partial prose instead of the requested two-field JSON. |
| Calibrated CPU reader | 0.0132 s including decode | 9 of 9 numeric pairs | Uses its own native HUD regions | 9 of 9 numeric pairs | Preserve its separate field errors and overall asteroid validity; numeric success on a non-asteroid does not make that target valid for mining. |

The CPU timing excludes reader construction and uses original images with no network request. Model timing includes request construction, image encoding, HTTP where applicable, or decoding and tensor preprocessing for FastVLM. They compare actual implementation paths, not identical runtime overhead. Whole-desktop load was not controlled, quantization differs between models, and nine samples do not establish reliable tail latency or long-term accuracy.

## Incomplete evidence challenge

Three existing synthetic validation fixtures erase a distance digit, remove the speed decimal, or erase speed entirely. The expected affected field is unknown. The same prompt and crop transform were used with the two best clear-image models. These are separate robustness checks; their load-inclusive timings are not included in the medians above.

| Fixture | Expected affected field | Qwen3.5 0.8B | Qwen3-VL 4B Instruct |
| --- | --- | --- | --- |
| Distance final digit erased | Unknown distance | Accepted 141 | Accepted 141 |
| Speed decimal removed | Unknown speed | Invalid response | Accepted zero |
| Speed erased | Unknown speed | Accepted zero | Returned null |

The frozen CPU reader (`logs/vision-model-comparison-20261001/reader-baseline.json`) rejected the affected field in all three challenge fixtures. Model responses and baseline reasons are retained in the challenge record (`logs/vision-model-comparison-20261001/malformed-challenge.json`). A value of zero is particularly significant: absence of a speed indicator must not become evidence that the ship is stopped.

## Request overhead and runtime identity

The earlier two-second gap is reproducible without inference. Python's request to `http://localhost:11434/api/version` took 2.035 seconds, while `http://127.0.0.1:11434/api/version` took 0.00163 seconds. On this host, localhost resolves to IPv6 before IPv4. The direct address removes the observed connection-path delay; the exact failed-connection mechanism was not traced. Final Ollama timing runs use the direct address. Earlier localhost runs are retained in `localhost-preliminary` and excluded from the table.

Ollama was version 0.35.0. Its model identity files preserve details for the three Qwen checkpoints. FastVLM uses the publisher's pinned Hugging Face revision `16375720c2d673fa583e57e9876afde27549c7d0`, its custom implementation, image processor, black square padding, Torch 2.6.0 with CUDA 12.4, and Transformers 4.48.3. Its processor produces 1024 by 1024 input for both variants. The 0.5B label alone therefore does not imply lower image-encoding work for a small crop.

SmolVLM uses the documented ggml-org GGUF with Q8_0 language weights and an F16 image projector. llama.cpp build 11334, commit `13b4d7135`, was started with all possible layers offloaded and its projector's default GPU offload. The runtime lists the RTX 4070 CUDA device; observed device memory increased during its run. Its local server was stopped after the comparison. Ollama models loaded by the test were unloaded after their runs. Dependencies, model weights, and the isolated FastVLM environment remain under `tools/model-runtimes` for reproduction.

The native and PyTorch runtime paths differ from Ollama. These measurements compare usable model/runtime combinations on this desktop; they should not be presented as pure model architecture rankings or published benchmark reproductions. Publisher sources and capabilities are described in the [model research](decision-windows-and-local-models.md).

## Implementation decision

Keep the calibrated CPU reader for its supported numeric fields and explicit rejection behavior. Qwen3.5 0.8B is the first candidate for a small contextual visual task with focused evidence. Its clear-crop latency is promising for occasional interpretation, but its false numeric accepts preclude replacing the guarded reader based on this test.

Focus model requests on relevant regions and a specific question. Full-screen numeric interpretation was both slower and less accurate across the tested implementations. State-only planning remains a different experiment, and the current results do not establish a decision or action deadline during motion.

Before extending the model's role, evaluate the actual intended gap: scene labels, scanner states, resource icons, or recovery decisions. Include unknown and damaged inputs, independent source groups, model timeouts, and invalidation while a request is running. Measure capture-to-state and action-effect delays in the native application, plus rendering and recognition contention during model inference. Clear-image success is insufficient without correct rejection and bounded behavior.

Reproduction scripts are [compare-vision.py](../../tools/ollama/compare-vision.py), [compare-fastvlm.py](../../tools/ollama/compare-fastvlm.py), [score-comparison.py](../../tools/ollama/score-comparison.py), and [challenge-vision.py](../../tools/ollama/challenge-vision.py). Raw outputs remain available for reviewing failures or trying a separately recorded prompt. The prompt was not tuned individually for the weaker models in this comparison.
