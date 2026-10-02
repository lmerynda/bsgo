# Saved HUD accuracy audit

Audit: 2026-10-01 UTC. Machine-readable evidence: `accuracy-results.json`;
manual labels and source splits: `fixtures/accuracy-manifest.json`.

The five reported production faults are fixed on the original saved pixels.
The related earlier 3338 preflight is also fixed. Two missing HP font raster
variants came from independently labeled current-side 4366/3338 crops; their
source runs and related same-target calibration sessions are excluded from the
holdout. The remaining faults were visible scene highlights through translucent
HUD regions, outside the text or extending one complete distance glyph by one
pixel. Their corrections are position- and shape-bounded. The font acceptance
thresholds remain mismatch <=0.20 and next-digit margin >=0.035.

| Real asteroid result | Before | After |
| --- | ---: | ---: |
| Correct distance | 70/71 | 71/71 |
| Correct speed | 70/71 | 71/71 |
| Correct maximum HP and semantic identity | 67/71 | 71/71 |
| Correct accepted complete reading | 65/71 (91.55%) | 71/71 (100%) |
| False rejection | 6 | 0 |
| Wrong accepted complete reading | 0 | 0 |
| Coverage | 91.55% | 100% |

Six real planetoid images are separate negatives: all six remain rejected with
no asteroid identity. Malformed glyphs, separator/decimal faults, occlusion,
wrong layout and synthetic cases are separate software regression evidence;
they are never included in real-frame accuracy. All 8 identity, 10 speed and
4 new observed-background test groups pass; the legacy validator passes 82
checks in three rounds. Existing controller checks were not weakened.

The archive contains 77 distinct real images and 77 distinct HUD pixel arrays
across 17 source groups; no mock, synthetic or duplicate images are counted.
The 71 asteroid frames cover 11 maximum-HP values, distances 205–3635 m,
18 observed speeds from 0.0 through 44.0, 45 moving and 26 stopped readings.
All share one 2560×1440 client layout. Real damage-to-current-HP coverage is
limited; current-HP decrease has separate synthetic regression coverage.

The development holdout consists of complete source runs
`miner-dry-relaunch-2141` and `sol-tylium-live-2321`: 26/26 exact readings,
zero rejections and zero wrong accepted reads, both before and after the fix.
It contains two HP identities, 23 moving and three stopped frames; 25 images
are consecutive samples of one approach. It verifies regression retention,
not the new corrections across an independent distribution. Older developer
exposure to these historical runs is possible. All images used to choose the
fix are counted as development/calibration, not held out.

**Representative 99% useful accuracy has not been demonstrated.** Even if all
26 successes were independent, the one-sided 95% lower bound would only be
89.1%; the temporal correlation makes that illustrative bound optimistic.
At least 300 untouched representative real frames from new natural production
runs, varied HP digits/ranges/speeds/backgrounds and frozen-reader scoring are
needed. False rejections and wrong accepted readings must both remain failures.
No extra game captures or model turns were added to flight to meet a quota.

One manual contact-sheet transcription, speed 24.3, was corrected to visibly
24.0 after inspecting the original crop at higher magnification. The manifest
retains the revision and links `inspect-heldout-speed.png`; recognition code
did not change because of this label correction.

Paired same-process warm timings use 780 calls per version with alternating
version order on preloaded holdout images. Median recognition improved
1.546→1.098 ms, p95 1.916→1.392 ms, p99 2.176→1.675 ms. Deduplicated NumPy
font-bank scoring offsets the added pixel checks and templates. Startup and
PNG decoding are excluded; single-pass PNG decoding median was about 11 ms.
The frozen before-reader and font-bank reconstruction are in
`fixtures/accuracy-baseline-reader.py` and `accuracy-baseline-templates.json`.
Artifact hashes and benchmark summaries are retained for reproducibility.

Historical live timings are separate, unchanged observations: 55 logged real
capture rows have median capture 275 ms and capture/save/read cycle 315 ms;
p95 values are 1272 ms and 1309 ms. Action-bearing event files retain original
input timing, including the later ~31 ms PageUp/PageDown calls. These data are
not a new live verification of the updated reader. The persistent Python worker,
local request/response loop and flight checks remain intact; a worker already
running must be restarted to load this reader version.
