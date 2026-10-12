# Expanded saved screenshot model benchmark

> Historical evidence: original log paths below identify archived records, not current checkout files. Raw packages remain recoverable from Git; see the [artifact policy and recovery guide](archive/README.md). New experiment outputs are local runtime artifacts.

Testing stopped at the user's request after all three Qwen models completed their passes. Each completed model received 307 full images and 306 HUD crops, for 1,839 completed-batch requests. FastVLM completed 106 full-image requests before it was stopped; it had no crop pass. SmolVLM was not started. Partial results are retained but excluded from the completed accuracy comparison. The frozen corpus contains 304 real images and three damaged-field fixtures. The 77 independently labeled real frames provide the numeric accuracy comparison; the remaining 227 real images retain model observations without invented ground truth.

The unchanged calibrated CPU reader read both fields correctly on **77/77 labeled frames**, with a median decode-and-read time of **26.3 milliseconds**. The larger model results below replace the accuracy impression from the initial nine-frame sample. No game inputs were sent.

## Results on all labeled frames

A correct pair requires both distance and speed to match the manual labels. Strict acceptance requires a completed JSON object with exactly those two keys and numeric or null values. Diagnostic scoring can recover numeric strings or a leading object followed by extra text, but does not turn that response into a valid control input. Timing columns use the same 77 labeled images; whole-corpus medians and observed p95 values are recorded in the raw summary.

| Model | Full image correct pairs | Full median | Crop correct pairs | Crop median | Diagnostic crop pairs |
| --- | --- | --- | --- | --- | --- |
| qwen3.5:0.8b | 11/77 | 0.843 s | 74/77 | 0.228 s | 74/77 |
| qwen3-vl:4b-instruct | 23/77 | 2.185 s | 77/77 | 0.702 s | 77/77 |
| qwen3-vl:2b-instruct | 0/77 | 1.964 s | 8/77 | 0.500 s | 73/77 |
| Calibrated CPU reader | 77/77 | 0.0263 s | Uses native calibrated regions | Same reader | Not needed |

The completed Qwen paths match the initial comparison: Ollama 0.35.0 with Qwen3.5 0.8B Q8_0 and Qwen3-VL 2B/4B Instruct Q4_K_M. The installed FastVLM runtime uses pinned Apple weights with PyTorch CUDA FP16 SDPA, the publisher image processor, and black square padding. The SmolVLM runtime uses Q8_0 with an F16 projector in llama.cpp CUDA. Started models use the same numeric question and 96-token limit; prompts were not tuned per model.

| Model | Complete crop responses on labeled frames | Truncated requests across corpus | Request errors |
| --- | --- | --- | --- |
| qwen3.5:0.8b | 77/77 | 0/613 | 0 |
| qwen3-vl:4b-instruct | 77/77 | 0/613 | 0 |
| qwen3-vl:2b-instruct | 11/77 | 0/613 | 0 |

Format and completion failures are distinct from wrong visual readings. A model that cannot complete this prompt remains unevaluated for other tasks such as scene classification. These are measured model/runtime combinations on this desktop, rather than a general ranking of vision capability.

In the small Qwen model's three real crop errors, it returned target health 3770 instead of distance 2026 or 2011, and read speed 13.7 as 3.7. Those are field selection and digit omissions on visible labeled evidence. The 4B model read all 77 clear pairs correctly, but its damaged-field responses still contain false accepts.

## Capture groups and existing splits

The 77 labeled frames cover 17 source groups: 44 development frames, seven calibration frames, and 26 held-out frames. The holdout contains only two runs, including 25 correlated frames from one approach. Earlier development may have exposed some of these pixels; this is a reproducible development benchmark, not a new prospective production accuracy estimate.

| Model using crops | Development | Calibration | Held out | Mean accuracy across 17 groups |
| --- | --- | --- | --- | --- |
| qwen3.5:0.8b | 42/44 | 7/7 | 25/26 | 95.8% |
| qwen3-vl:4b-instruct | 44/44 | 7/7 | 26/26 | 100.0% |
| qwen3-vl:2b-instruct | 2/44 | 1/7 | 5/26 | 9.5% |

The group mean gives each source group equal weight; the frame counts above give each image equal weight. Neither establishes reliability across unseen gameplay. New labels and scenes should go into another dataset version, with complete capture runs kept together.

## Damaged evidence

Each fixture has an independently specified affected field that must be unknown. The table shows crop responses on that field. An invalid or truncated response is listed separately from a completed null response.

| Model | Speed erased | Distance final digit erased | Speed decimal removed |
| --- | --- | --- | --- |
| qwen3.5:0.8b | 0 | 141 | Invalid response |
| qwen3-vl:4b-instruct | null | 141 | 0 |
| qwen3-vl:2b-instruct | Invalid response | Invalid response | Invalid response |
| Calibrated CPU reader | Unknown | Unknown | Unknown |

A guessed zero must not be interpreted as proof that the ship has stopped. Clear-image accuracy does not justify replacing the numeric reader without correct rejection of missing evidence.

## Interpretation and limitations

Keep the calibrated CPU reader for supported numeric state and its explicit field rejection behavior. Focused visual requests are useful candidates for contextual tasks, but numeric success and response speed do not establish target selection, resource recognition, obstacle detection, or safe planning. Those tasks need their own labels and evaluation.

The original images include later HUD captures, maps, and other scenes. Fixed crops on those scenes remain unscored observations. The one 3840 by 2160 map has no fixed HUD crop. Synthetic fixtures are reported separately from real-image accuracy. No model output or CPU-reader prediction was used to label an unlabeled image.

Runs were sequential by model on the RTX 4070 with 12,282 MiB of device memory. Desktop load was not controlled. Requests include image encoding and HTTP where applicable; FastVLM includes decoding and preprocessing. Setup and model loading are recorded separately, though first-use vision initialization can remain in the first request. The CPU baseline excludes construction and ran while the first model was starting. The smaller and larger comparisons therefore do not establish a CPU performance regression or a live action deadline.

## Saved data and reproduction

- [Frozen corpus and instructions](../../test-data/vision/v1/README.md)
- [Manifest with original paths, labels, and hashes](../../test-data/vision/v1/manifest.json)
- Complete summary (`logs/vision-corpus-v1/summary.json`)
- CPU baseline responses (`logs/vision-corpus-v1/reader-baseline.json`)
- Runtime environment (`logs/vision-corpus-v1/environment.json`)
- [Benchmark script](../../tools/ollama/benchmark-saved-vision.py)
- [Dataset freeze script](../../tools/ollama/freeze-vision-dataset.py)

Each started model has a JSONL file containing every completed request, response, image hash, latency, strict score, and diagnostic score, plus a metadata file with its configuration and runtime identity. The old nine-frame experiment remains preserved separately. The run status (`logs/vision-corpus-v1/status.json`) records the requested stop and partial counts.

```powershell
& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' 'tools/ollama/benchmark-saved-vision.py' --all
```

If further testing is requested, this resumes the recorded batch. To repeat inference without replacing its records, pass `--run-dir` with a new result directory. Frozen inputs are verified before each model request. The current batch is stopped, its Python processes have been terminated, and its Ollama models are unloaded. SmolVLM was not started in this expanded run. Installed runtimes and weights remain available.
