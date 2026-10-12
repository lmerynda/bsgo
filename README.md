# BSGO automated piloting

This project collects skills and observations for learning to play BSGO automatically.

- [Mining runtime improvement plan](docs/architecture/mining-runtime-improvement-plan.md): ordered implementation tasks, concrete contracts, offline acceptance checks, and deferred BSGO validation.
- [Mining runtime offline results](docs/architecture/mining-runtime-offline-results.md): complete simulated cycle, 216 passing tests, 11 accepted threaded scenarios, and the remaining game-image perception work.
- [Standalone mining application architecture review](docs/architecture/standalone-mining-app.md): proposed thread-based runtime, published state, control ownership, and assumptions found in the current prototypes.
- [Building block experiment plan](docs/architecture/building-block-experiments.md): capture, input, OCR, small local models, Ollama planning, and shared RTX 4070 performance.
- [Decision windows and local model research](docs/architecture/decision-windows-and-local-models.md): measured request overhead, task deadlines, and Hugging Face candidates for recognition and planning.
- [Offline bot harness](tools/offline-bot/README.md): screenshot replay, independent shared state, recording-only input, a synthetic receiver, delayed decisions, and fault tests.
- [Offline experiment results](docs/architecture/offline-bot-experiments.md): observed scheduling independence, action/effect traces, cancellation, stale state, and missing brake acknowledgement.
- [Native Windows backend experiments](docs/architecture/native-backend-experiments.md): accepted client capture and guarded input delivery against an owned receiver, independent pixel observation, and remaining BSGO compatibility.
- [Ship-control skill](.agents/skills/bsgo-ship-control/SKILL.md): live flight, camera, targeting, systems, and safe stopping.
- [Mining skill](.agents/skills/bsgo-mining/SKILL.md): scan, choose, approach, and mine asteroids with the current guns in slots 3–6.
- [276 Exrun safe zone](docs/navigation/276-exrun-safe-zone.md): ring/sector operating boundary, updated October 1, 2026.
- Future scenarios such as combat can have their own skill.

Test images live packed in [tools/test-fixtures](tools/test-fixtures/restore.py): one sprite sheet plus an index. Test modules that need them import `fixture_data`, which restores them to their original (git-ignored) paths. The repository keeps no other images or captured data; runtime output goes to `logs/`.

Use both skills for mining navigation. The current game UI and the user's corrections govern equipment and controls.
