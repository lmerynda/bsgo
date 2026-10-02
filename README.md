# BSGO automated piloting

This project collects skills and observations for learning to play BSGO automatically.

- [Standalone mining application architecture review](docs/architecture/standalone-mining-app.md): proposed thread-based runtime, published state, control ownership, and assumptions found in the current prototypes.
- [Building block experiment plan](docs/architecture/building-block-experiments.md): capture, input, OCR, small local models, Ollama planning, and shared RTX 4070 performance.
- [Decision windows and local model research](docs/architecture/decision-windows-and-local-models.md): measured request overhead, task deadlines, and Hugging Face candidates for recognition and planning.
- [Saved screenshot model comparison](docs/architecture/vision-model-comparison.md): five local vision models, full images versus HUD crops, latency, numeric errors, and missing-evidence failures.
- [Frozen screenshot test data](test-data/vision/v1/README.md): preserved game captures, independent labels, hashes, and the reproducible five-model benchmark.
- [Expanded screenshot benchmark](docs/architecture/vision-corpus-benchmark.md): completed Qwen results on all 77 labeled frames, robustness checks, and the requested stop of remaining tests.
- [Offline bot harness](tools/offline-bot/README.md): screenshot replay, independent shared state, recording-only input, a synthetic receiver, delayed decisions, and fault tests.
- [Offline experiment results](docs/architecture/offline-bot-experiments.md): observed scheduling independence, action/effect traces, cancellation, stale state, and missing brake acknowledgement.
- [Native Windows backend experiments](docs/architecture/native-backend-experiments.md): accepted client capture and guarded input delivery against an owned receiver, independent pixel observation, and remaining BSGO compatibility.
- [Ship-control skill](.agents/skills/bsgo-ship-control/SKILL.md): live flight, camera, targeting, systems, and safe stopping.
- [Mining skill](.agents/skills/bsgo-mining/SKILL.md): scan, choose, approach, and mine asteroids with the current guns in slots 3–6.
- [276 Exrun safe zone](docs/navigation/276-exrun-safe-zone.md): ring/sector operating boundary, updated October 1, 2026.
- Future scenarios such as combat can have their own skill.

Use both skills for mining navigation. The current game UI and the user's corrections govern equipment and controls.
