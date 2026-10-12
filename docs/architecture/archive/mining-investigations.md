# Historical mining investigations: 2026-09-30 to 2026-10-02

This summary preserves dated observations from early live mining logs, rather
than claims about the current client, reader, controller, or gameplay state.
See the [archive index](README.md) for the archive's purpose and recovery policy.

## Sources and interpretation

The original detailed artifacts remain recoverable in Git history, at source
snapshot `d674711ad6ea3be28540edb54b43fa8e09a6e25f`:

- `logs/mining/2026-09-30-collision-rule-audit.md`
- `logs/mining/2026-09-30-operation.md`
- `logs/mining/2026-10-01-operation.md`
- `logs/mining/line-20261002.md`

Use `git show <snapshot>:<path>` to inspect an original. HUD/game-clock readings
are not exact UTC measurements; UTC is explicit where recorded. Rewards use the
displayed award without adding included faction bonuses again. Squad rewards
and unobserved gap progress are excluded from recorded own-harvest outcomes.

## 2026-09-30: productive mining and collision failures

The first verified post-restart Water harvest returned 1,548 Water at stopped
672 m with full hull. Later own rewards included 6,716 Tylium, 1,779 Water,
1,632 Water, and 5,440 Tylium. Later Water targets took minutes as energy reached
its regeneration-limited minimum; blocked gun arcs also reduced firing.

Scanning established resources beyond firing range, including a Titanium rock
at 2,495 m. Purple Titanium and red barren results were skipped. Small silhouette
selection misses, camera settling, and large camera drags added substantial
decision time; a selected or scanned target did not establish a usable route.

By 2026-10-01 03:03 UTC, the September operation log reported a cumulative own
baseline of 25 destroyed asteroids, 47,021 Tylium, and 25,344 Water. The belt
pass was still incomplete; those totals do not establish complete belt coverage.

The collision audit identified these specific failures:

| Recorded game-clock event | Observation | Interpretation |
| --- | --- | --- |
| 16:37-16:40 | Brake input at 92 m, stop at 117 m; subsequent turn and thrust preceded lethal asteroid damage. | Close-range thrust and missed braking defeated intended separation. |
| 17:07 | Brake drags at 1,063/808 m failed; thrust persisted across a long decision gap; 1,644 asteroid damage followed. | Input acknowledgement and bounded stopping were missing. |
| 18:09-18:10 | Right-click and speed 5 at 85 m preceded 2,016 asteroid damage. | An escape heading had not been verified before movement. |
| 18:18 onward | Further damage occurred with displayed speed 0.0 and no subsequent thrust recorded. | Zero speed did not prove clearance; embedding was suspected, not established. |
| 20:35-20:36 | A 21-second observation gap took the view from 927 m/44 to 131 m/40.1; final stop at 205 m. | Slow observation cycles missed the intended brake threshold even without a collision. |

Selected-target range was not a measurement of intervening obstacle clearance.
Tangential passes without damage did not calibrate straight-line stopping
distance. The audit also could not establish exactly which routing instructions
existed at every earlier event; it did not attribute all collisions to one rule.

## 2026-09-30: OCR and bounded approach evidence

Ten manually labeled stationary screenshots supplied distance digits 0-9 and
speed 0.0; all ten were exact, with six planetoid frames diagnostic-only. Six
stationary capture cycles ran at 2.002-2.007 seconds, with 126-424 ms capture and
1.15-1.51 ms warm recognition. These measurements did not validate moving OCR.

The historical approach controller used approximately two-second observations,
acted before disk logging, and checked freshness, cadence, target identity, and
closure. It commanded zero on invalid reads or incomplete bounded runs. Route
and heading prerequisites still depended on visual evidence outside its HUD
crops; they were not automatic obstacle detection.

Two live movement trials stopped on invalid speed glyphs before braking;
fresh visual observations later confirmed zero speed and full hull. Semantic
maximum-HP identity replaced an exact pixel mask after unchanged-target frames
differed by two pixels. Invalid or ambiguous identity still rejected the read.

At 23:22:53-23:23:41 UTC, a full-speed approach to a 3,230-HP Tylium asteroid
started at 2,191 m. A 617 m/44 observation triggered the intended 700 m brake;
zero input returned in 277 ms and final observed range/speed was 438 m/0.
Actual input-time range was unmeasured. No collision damage was observed.
At HUD 23:27:20, destruction and an own award of 4,785 Tylium were confirmed.

## 2026-10-01: production outcomes and profile drift

These verified own cycles are not a throughput benchmark. Full hull and zero
speed were observed at these reward boundaries:

| Recorded UTC verification | Outcome | Relevant condition |
| --- | --- | --- |
| By 14:15:21 | 997 Water | Stationary firing at 946 m; energy-limited cycle. |
| 15:05:29 | 8,383 Tylium | Approach from 1,586 m; brake observed at 696 m/44; stop at 530 m. |
| 15:13:48 | 1,959 Water | Own destruction confirmed after the wanted-resource cycle. |
| 20:34:26 | 1,368 Water | Stationary firing at 491 m after reader-profile repairs. |
| 22:01:58 | 6,704 Tylium | Own destruction confirmed; 609 faction bonus already included. |

Stationary 530 m Tylium firing took roughly 45 seconds with four guns; an earlier
946 m Water cycle took longer and exhausted energy. Different HP, resources,
equipment, and observation delays prevent a causal range-efficiency conclusion.

A restarted client remained responsive but changed HUD raster calibration.
Subsequent motion trials rejected separator, distance, speed, title, and HP
glyphs; several stopped safely after movement and required renewed route evidence.
An explicit restart profile added narrowly calibrated glyph shapes, title crops,
separator brightness, and exterior-noise filters while retaining completeness,
identity, freshness, and stop guards. Development replays and regression checks
improved; they were not independent held-out accuracy or new live acceptance.

Frozen HUD captures could not establish freshness. Logical screenshot dimensions
also differed from the calibrated raster during recovery. Relaunch, movement,
selection loss, or scene changes invalidated earlier target and route evidence.
Measured distant planetoids resolved some apparent overlap, but other unknown
depths and uncertain headings still caused approaches to be deferred.

## 2026-10-02: line-ship observations and remaining limits

The line-ship log observed 5,005 hull and 800 energy capacity. Firing at 1,450 m
reduced one Water asteroid to 19 HP before it disappeared; no reward was observed,
so that event was not a verified own harvest. Later operation used a historical
preferred range near 680 m and an 800 m firing limit; the earlier event did not
validate that range policy.

A Water approach from 1,450 m braked on an 899 m/33 observation, then encountered
an invalid HUD read during deceleration. Cleanup zero was sent; fresh visual
evidence showed 667 m/0 and full hull. Stationary destruction subsequently
confirmed an own award of 1,180 Water and 112 experience.

A later 1,825 m approach failed on an incomplete distance glyph before braking.
Zero input took 42 ms; fresh visual evidence showed 1,673 m/0 with full hull.
A subsequent interrupted retry had no fresh final-speed verification.

These investigations did not establish automatic corridor detection, reliable
recovery from collision overlap, representative OCR accuracy, optimal equipment
or mining range, complete belt coverage, or unattended operation across client
restarts. Command dispatch, offline checks, replay agreement, visual stopping,
own rewards, and final interrupted-state verification remain distinct evidence.
