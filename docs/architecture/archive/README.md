# Historical experiment evidence and artifact policy

The repository keeps source, design decisions, technical summaries, and frozen reproducible inputs. New captures, request/response journals, benchmark reports, launcher state, and temporary previews are local runtime artifacts under ignored `logs/` or manual capture directories.

On October 11, 2026, the 265 already-deleted files under `logs/` were removed from the current checkout's versioned contents. Their roughly 55.8 MiB of historical evidence remains in Git history. This change does not rewrite history or remove those objects from the repository.

## Recover an original record

All removed log files are available in commit `6b441ebaee7b76f33c631ba62e4d99a5faf96647`. Paths shown in dated experiment documents identify the original records, rather than promising that a current local run is present.

```powershell
git show '6b441eba:logs/offline-bot/history-64000/soak-report.json'
git ls-tree -r --name-only 6b441eba -- logs/
```

For binary records, use a Git client that exports blob bytes; PowerShell text redirection is unsuitable for recovering images or ZIP packages. New experiment runs write new local artifacts and do not silently replace archived evidence.

| Historical package | Original path prefix | Retained explanation |
| --- | --- | --- |
| Synthetic replay, approach, receiver, and history experiments | `logs/offline-bot/` | [Offline harness](../offline-bot-experiments.md), [native backend](../native-backend-experiments.md) |
| Scene descriptions and transport/decision diagnostics | `logs/ollama-qwen3-vl-20261001/`, `logs/decision-window-research-20261001/` | [Decision windows](../decision-windows-and-local-models.md) |
| Initial nine-frame model comparison | `logs/vision-model-comparison-20261001/` | [Model comparison](../vision-model-comparison.md) |
| Expanded saved-image model benchmark | `logs/vision-corpus-v1/` | [Corpus benchmark](../vision-corpus-benchmark.md) |
| Mining operations and map review | `logs/mining/` | [Curated mining investigations](mining-investigations.md) |

The curated mining summary preserves observed outcomes and unresolved questions without carrying private conversation excerpts into the current documentation. Historical operation permissions and old game state are not current authorization or current telemetry.

## Reproducible inputs

- [Frozen vision corpus](../../../test-data/vision/v1/README.md) retains original image bytes, labels, hashes, source provenance, and its baseline reader.
- [Bootstrap inputs](../../../test-data/vision/bootstrap-20261001/README.md) preserve the original map and baseline files needed to rebuild the corpus without relying on a runtime log directory.
- Existing map-contact crops and the rest of the committed fixture corpus remain in their established locations. Their recorded original log paths are provenance metadata, not runtime dependencies.

Benchmark/report tools still require their generated result files. Prepare or run the relevant experiment first, or recover the historical files before reproducing an old report. Fresh results remain local until useful findings are curated into documentation or deliberate fixture data.
