# Decision windows and local model selection

> Historical evidence: original log paths below identify archived records, not current checkout files. Raw packages remain recoverable from Git; see the [artifact policy and recovery guide](archive/README.md). New experiment outputs are local runtime artifacts.

Three to six seconds is not an established acceptable decision time for the mining application. The first Qwen3-VL tests measured screenshot descriptions, not the application's full recognition and action loop. Motion control needs a deadline derived from closing distance and stopping behavior; choosing a target while stopped can have a longer window. Model selection should follow the task and its deadline.

This research was checked on October 1, 2026. It combines publisher model cards with small local diagnostics on the RTX 4070 through Ollama 0.35.0. A subsequent [saved screenshot comparison](vision-model-comparison.md) exercised five models, including the smaller Qwen, FastVLM, and SmolVLM candidates. The remaining candidates in this research are untested on this desktop.

## What the first timings actually measured

The first scene tests (`logs/ollama-qwen3-vl-20261001/instruct-brief/results.json`) used full 2560 by 1440 images and generated 81 and 91 output tokens. Their wall times were 3.117 and 5.640 seconds, while Ollama reported 1.048 and 3.595 seconds internally. The first reused 3605 of 3652 prompt tokens; the second reused only four. Neither timing is a latency distribution for new gameplay frames.

The model misread a selected distance of 2011 as 2014. The earlier structured HUD request repeated itself and exhausted its output limit. A coherent description therefore does not establish usable state extraction or decision quality.

Two narrower diagnostic requests (`logs/decision-window-research-20261001/results.json`) used the same Qwen3-VL 4B Instruct model:

| Request | Native curl time | Ollama time | Interpretation |
| --- | --- | --- | --- |
| Classify another saved full screenshot using one screen label | 5.123 s | 4.907 s, including 2.594 s loading and 2.194 s prompt evaluation | Four output tokens reduce generation work, but fresh image processing still dominates. |
| Choose the nearer of two already scanned water candidates from text state | 0.469 s | 0.265 s | Removing the image and returning one candidate ID is much cheaper. This example is trivial enough to implement with a rule. |

A paired transport check (`logs/decision-window-research-20261001/paired-transport.json`) submitted the same image request through PowerShell and native curl. Both reused 3635 prompt tokens and returned `SPACE_FLIGHT`. PowerShell took 2.383 seconds with 0.296 seconds reported by Ollama; curl took 0.318 seconds with 0.112 seconds reported by Ollama. The excess was about 2.087 versus 0.206 seconds. This identifies a substantial cost in the PowerShell request path; it does not identify the underlying HTTP cause or prove the future Python client has that cost.

These are individual diagnostics. The model was loaded for the text and paired requests, the paired image was cached, and the text scenario exercised almost no reasoning. Capture, new image preprocessing, state publication, input dispatch, and observed action effects remain outside these measurements. No game input was sent.

## Derive a window for each decision

The [approach prototype](../../tools/hud-reader/approach-controller.mjs) already rejects observations older than two seconds and consecutive observation gaps above four seconds. Its defaults also require braking progress within four seconds. Those are prototype constraints to review, not measured universal requirements, but an extra three to six seconds in that control path conflicts with them.

For a closing target, an illustrative remaining window is:

`remaining time = (distance to limiting boundary - stopping distance - distance uncertainty margin) / closing speed`

The complete delay includes the wait for the next capture, capture and recognition, queues, decision computation, input dispatch, and any delay before the input takes effect. Use measured closing speed and stopping distance; HUD speed alone is not a complete relative-motion model. If the available margin is exhausted or unknown, do not depend on a model returning soon enough.

For illustration only, at 44 metres per second, three to six seconds corresponds to 132 to 264 metres of travel. A 100 metre margin lasts about 2.27 seconds even before subtracting braking distance and uncertainty. The prototype's speed plausibility bound is 44; this calculation is not a new measurement of actual ship motion.

| Decision | Appropriate path | What determines its window |
| --- | --- | --- |
| Brake, monitor speed, handle lost observations, cancel input | Local coordinator with numeric recognition and explicit rules | Closing margin, stopping envelope, recognition reliability, and dispatch delay. A model request cannot gate these actions. |
| Identify flight, map, hangar, loading, or an unknown layout | Templates or a small visual classifier | Must finish before the next routine action depends on that label. Stable stopped screens can tolerate more delay than a transition. |
| Select among already recognized and scanned candidates | Rules first; small text model only for a policy that needs it | Candidate evidence lifetime and whether local control can hold a suitable state while waiting. |
| Interpret an unfamiliar scene or suggest recovery or relocation | Asynchronous VLM or larger planner | Local routines continue monitoring. The proposal has a timeout and its relevant state dependencies are rechecked on arrival. |

As proposed experiment objectives, test local recognition and reaction in a 100 to 250 ms range, bounded state proposals below one second, and broader interpretation in a one to five second range while stopped or otherwise supported by local control. These are useful comparison targets, not established BSGO deadlines or guarantees that every block needs this cadence. Replace them with budgets derived from the measured gameplay envelope.

Request planning when a useful decision window opens. Supply the relevant state, uncertainties, and allowed candidate IDs. Return a proposal such as a candidate ID or recovery routine, rather than mouse coordinates calculated from an old screenshot. The coordinator obtains current coordinates and validates current conditions before acting. If the deadline passes, use the local continuation or stop policy. Do not accumulate screenshots or retry work that is already obsolete.

## Hugging Face candidates by task

The proposed roles in this table are our selection hypotheses. Publisher benchmarks establish capabilities on their datasets and hardware; they do not establish BSGO accuracy or RTX 4070 latency. Parameter labels also differ: a language backbone size may exclude vision components, and active parameters do not describe all resident weights.

| Model and primary source | Published scale | Proposed role and qualification |
| --- | --- | --- |
| [MobileNetV3 Small](https://huggingface.co/timm/mobilenetv3_small_100.lamb_in1k) | 2.5M parameters; 224 by 224 input | A small screen or icon classifier. The supplied checkpoint classifies ImageNet; it needs a BSGO classifier head and labeled examples. Start with simple visual features as the baseline. |
| [MobileCLIP2 S0](https://huggingface.co/apple/MobileCLIP2-S0) | 11.4M image plus 63.4M text parameters | Compare an image embedding with a fixed set of screen descriptions; cache text embeddings. Test a learned head if zero-shot labels are unreliable. Similarity or softmax scores are not calibrated correctness probabilities. |
| [Florence 2 Base](https://huggingface.co/microsoft/Florence-2-base) | 0.23B | Task-specific OCR, regions, and object proposals through task tokens such as `<OCR>` and `<OD>`. Compare with the numeric reader and ordinary OCR. It still generates sequences and is not a broad decision planner. |
| [SmolVLM 500M Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM-500M-Instruct) | 500M | Lightweight single-image questions and screen labels. Its card reports low memory use for its own setup; local latency and small-text accuracy remain untested. Transformers and documented llama.cpp support make it a practical comparison. |
| [FastVLM 0.5B](https://huggingface.co/apple/FastVLM-0.5B) | 0.5B variant | Especially relevant because its FastViTHD vision encoder targets image encoding and visual token costs. Compare short answers on identical crops. Apple's published relative speedups are not predictions for our GPU. |
| [Qwen3 VL 2B Instruct](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct) | 2B variant | The direct smaller comparison against our 4B baseline. Use the explicit instruct variant. Ollama lists `qwen3-vl:2b-instruct`; actual accuracy and latency are unresolved. |
| [Qwen3.5 0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) and [2B](https://huggingface.co/Qwen/Qwen3.5-2B) | 0.8B and 2B variants | Small multimodal candidates, also available in Ollama. Disable thinking for deadline experiments and verify that it took effect. Both cards document potential thinking loops; the 0.8B card positions it for prototyping and task-specific adaptation. |
| [InternVL3.5 1B](https://huggingface.co/OpenGVLab/InternVL3_5-1B) | 1.1B total, including 0.3B vision | An independent small VLM family. Bound the high-resolution tiling budget before comparing timings. Do not assign the family's headline Flash speedups to this standard checkpoint. |
| [Moondream 2](https://huggingface.co/vikhyatk/moondream2) | About 2B | Image queries, detection, and pointing have explicit APIs. Useful for testing candidate localization. It is the previous generation; pin a revision and test actual BSGO objects. |
| [Gemma 4 E2B](https://huggingface.co/google/gemma-4-E2B-it) | 2.3B effective; 5.1B with embeddings | Another compact multimodal comparison with an Ollama tag. The name does not imply a two-billion-weight memory footprint; measure actual allocation with vision and context. |
| [Qwen3 0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) and [SmolLM2 360M Instruct](https://huggingface.co/HuggingFaceTB/SmolLM2-360M-Instruct) | 0.6B and 360M | Text-only bounded policy proposals from published state. Compare with rules on uncertainty, obsolete targets, and recovery cases; a smaller language model is not automatically a competent planner. |

[SmolVLM2 500M Video Instruct](https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Video-Instruct) is a temporal interpretation candidate if a concrete task needs several frames. Adding a video window also adds source age and processing work. First determine whether tracked observations or a few events answer the question more cheaply.

[Moondream 3 Preview](https://huggingface.co/moondream/moondream3-preview) is another relevant architecture, but its 2B active designation accompanies 9B total parameters. Active parameter count reduces computation per token without making the resident model a 2B model. Its integration and fit beside game rendering need separate verification, so it is not an initial low-memory comparison.

## Runtime and request choices

Keep Ollama for supported language and vision-language models. Its catalog explicitly lists [Qwen3 VL 2B Instruct](https://ollama.com/library/qwen3-vl:2b-instruct), [Qwen3.5](https://ollama.com/library/qwen3.5), and [Gemma 4 E2B](https://ollama.com/library/gemma4:e2b). A Hugging Face checkpoint or a GGUF file is not proof of Ollama support for its vision encoder and image projection. Check the exact architecture, revision, and server support before installing a candidate.

Specialized classifiers can run directly in the recognition thread through a measured CPU or GPU runtime. Begin with the publisher's PyTorch implementation; an ONNX export is an optimization to verify. It is unnecessary to serve every model through an LLM server.

For SmolVLM, [llama.cpp's multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md) explicitly lists 256M and 500M GGUF examples. FastVLM supplies its own [PyTorch implementation](https://github.com/apple-aiml-research/ml-fastvlm); do not assume its Apple Silicon deployment timings apply to CUDA. Florence 2 and Moondream have task-specific processing that must be retained by an adapter.

Reduce work before increasing runtime complexity: use one bounded request, keep the selected model warm during active use, send compact relevant state, cap output, and crop or resize only the evidence required for the task. Keep original recognition frames and explicit crop transforms. A scene classifier can use lower resolution; tiny digits often require a native-resolution region. Do not resize the calibrated HUD reader's input or claim that a resized screenshot remains valid for that reader.

Qwen's [pixel-budget documentation](https://github.com/QwenLM/Qwen3-VL#quickstart) exposes visual token control in its Transformers processor. These processor arguments are not automatically Ollama API options. In Ollama, controlled input images are an initial comparison; additional runtime controls require documented support. Shorter output alone cannot eliminate image encoding and prompt evaluation.

## Recommended comparison order

1. Fix or replace the measurement client's request path and record warm, cold, new-image, and repeated-image timings separately. Keep the current 4B model as the reference.
2. Compare rules with a compact text proposal task. Test Qwen3 0.6B or Qwen3.5 0.8B without images before adding a larger planner. Include cases where the only acceptable proposal is to defer or recover.
3. For broad image interpretation, compare Qwen3 VL 2B Instruct, FastVLM 0.5B, and SmolVLM 500M with the 4B reference. Qwen3.5 0.8B and 2B provide a newer multimodal family comparison. Use short task-specific outputs and identical source images.
4. For recurring screen labels, compare templates, MobileNetV3, and MobileCLIP2. For unreadable HUD or new text regions, compare the existing reader, ordinary OCR, and Florence 2. Evaluate these tasks separately from planning.
5. Broaden to InternVL, Moondream, or Gemma if the first group leaves a measured quality gap. Add temporal models only when a labeled task requires temporal evidence.

Use source-session splits and separate prompt-development examples from evaluation. Measure wrong accepted answers, rejection coverage, completion and schema failures, end-to-end latency, sample count, tail latency, deadline misses, and actual memory. Test model and game contention, recognition progress while a request stalls, and proposal invalidation after a target or session changes. Do not choose a model from tokens per second or published laptop/phone timings.

The initial recommendation is independent numeric recognition and local control, a small classifier for a specific recurring visual task, and occasional short state or image proposals from the smallest model that meets the task's quality and deadline. We have not established that full screenshot interpretation must run every recognition cycle, that all decisions need subsecond responses, or that three to six seconds is safe during motion.
