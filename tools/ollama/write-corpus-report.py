"""Publish completed model comparisons and explicitly record user-stopped work."""
import json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'test-data/vision/v1'
RUN = ROOT / 'logs/vision-corpus-v1'
REPORT = ROOT / 'docs/architecture/vision-corpus-benchmark.md'

def write():
    dataset = json.loads((DATA / 'manifest.json').read_text())
    summaries = json.loads((RUN / 'summary.json').read_text())['models']
    status = json.loads((RUN / 'status.json').read_text()) if (RUN / 'status.json').exists() else {}
    stopped = status.get('status') == 'stopped_at_user_request'
    if not stopped and (len(summaries) != 10 or any(r['requests'] != r['expected_requests'] for r in summaries)):
        raise SystemExit('Comparison is incomplete; refusing to publish a completed report')
    by_model = {}
    for row in summaries:
        by_model.setdefault(row['model'], {})[row['variant']] = row
    cpu = json.loads((RUN / 'reader-baseline.json').read_text())
    labeled_cpu = [r for r in cpu['results'] if r['kind']=='real_capture' and all(r['score'][f]!='unlabeled' for f in ['distance','speed'])]
    cpu_correct = sum(all(v=='correct' for v in row['score'].values()) for row in labeled_cpu)
    cpu_median = statistics.median(r['wall_seconds'] for r in labeled_cpu)
    manifest_by_id = {r['id']:r for r in dataset['rows']}
    names = ['qwen3.5:0.8b','qwen3-vl:4b-instruct','qwen3-vl:2b-instruct','FastVLM-0.5B','SmolVLM-500M']
    if stopped:
        names = [name for name in names if name in by_model and all(
            by_model[name][v]['requests']==by_model[name][v]['expected_requests'] for v in ['full','crop'])]
        if set(names) != set(status['completed_models']):
            raise SystemExit('Stopped-run status does not match complete model records')
    introduction = ('All five local vision models were run on the frozen saved-image corpus: 304 distinct real game images and three damaged-field fixtures. Each received 307 full images and 306 HUD crops, for 3,065 requests in total.' if not stopped else
        'Testing stopped at the user\'s request after all three Qwen models completed their passes. Each completed model received 307 full images and 306 HUD crops, for 1,839 completed-batch requests. FastVLM completed 106 full-image requests before it was stopped; it had no crop pass. SmolVLM was not started. Partial results are retained but excluded from the completed accuracy comparison.')
    lines = ['# Expanded saved screenshot model benchmark', '',
        introduction + ' The frozen corpus contains 304 real images and three damaged-field fixtures. The 77 independently labeled real frames provide the numeric accuracy comparison; the remaining 227 real images retain model observations without invented ground truth.', '',
        f'The unchanged calibrated CPU reader read both fields correctly on **{cpu_correct}/{len(labeled_cpu)} labeled frames**, with a median decode-and-read time of **{cpu_median*1000:.1f} milliseconds**. The larger model results below replace the accuracy impression from the initial nine-frame sample. No game inputs were sent.', '',
        '## Results on all labeled frames', '',
        'A correct pair requires both distance and speed to match the manual labels. Strict acceptance requires a completed JSON object with exactly those two keys and numeric or null values. Diagnostic scoring can recover numeric strings or a leading object followed by extra text, but does not turn that response into a valid control input. Timing columns use the same 77 labeled images; whole-corpus medians and observed p95 values are recorded in the raw summary.', '',
        '| Model | Full image correct pairs | Full median | Crop correct pairs | Crop median | Diagnostic crop pairs |',
        '| --- | --- | --- | --- | --- | --- |']
    for name in names:
        full, crop = by_model[name]['full'], by_model[name]['crop']
        lines.append(f'| {name} | {full["strict_both_correct"]}/77 | {full["median_labeled_seconds"]:.3f} s | {crop["strict_both_correct"]}/77 | {crop["median_labeled_seconds"]:.3f} s | {crop["diagnostic_both_correct"]}/77 |')
    lines += [f'| Calibrated CPU reader | {cpu_correct}/77 | {cpu_median:.4f} s | Uses native calibrated regions | Same reader | Not needed |', '',
        'The completed Qwen paths match the initial comparison: Ollama 0.35.0 with Qwen3.5 0.8B Q8_0 and Qwen3-VL 2B/4B Instruct Q4_K_M. The installed FastVLM runtime uses pinned Apple weights with PyTorch CUDA FP16 SDPA, the publisher image processor, and black square padding. The SmolVLM runtime uses Q8_0 with an F16 projector in llama.cpp CUDA. Started models use the same numeric question and 96-token limit; prompts were not tuned per model.', '',
        '| Model | Complete crop responses on labeled frames | Truncated requests across corpus | Request errors |',
        '| --- | --- | --- | --- |']
    for name in names:
        full,crop=by_model[name]['full'],by_model[name]['crop']
        lines.append(f'| {name} | {crop["strict_complete_labeled"]}/77 | {full["truncated"]+crop["truncated"]}/613 | {full["errors"]+crop["errors"]} |')
    lines += ['', 'Format and completion failures are distinct from wrong visual readings. A model that cannot complete this prompt remains unevaluated for other tasks such as scene classification. These are measured model/runtime combinations on this desktop, rather than a general ranking of vision capability.', '',
        'In the small Qwen model\'s three real crop errors, it returned target health 3770 instead of distance 2026 or 2011, and read speed 13.7 as 3.7. Those are field selection and digit omissions on visible labeled evidence. The 4B model read all 77 clear pairs correctly, but its damaged-field responses still contain false accepts.', '',
        '## Capture groups and existing splits', '',
        'The 77 labeled frames cover 17 source groups: 44 development frames, seven calibration frames, and 26 held-out frames. The holdout contains only two runs, including 25 correlated frames from one approach. Earlier development may have exposed some of these pixels; this is a reproducible development benchmark, not a new prospective production accuracy estimate.', '',
        '| Model using crops | Development | Calibration | Held out | Mean accuracy across 17 groups |',
        '| --- | --- | --- | --- | --- |']
    for name in names:
        crop=by_model[name]['crop']; splits=crop['splits']
        values=[f'{splits[s]["correct"]}/{splits[s]["pairs"]}' for s in ['development','calibration','held_out']]
        lines.append(f'| {name} | '+ ' | '.join(values)+f' | {crop["mean_group_accuracy"]*100:.1f}% |')
    lines += ['', 'The group mean gives each source group equal weight; the frame counts above give each image equal weight. Neither establishes reliability across unseen gameplay. New labels and scenes should go into another dataset version, with complete capture runs kept together.', '',
        '## Damaged evidence', '',
        'Each fixture has an independently specified affected field that must be unknown. The table shows crop responses on that field. An invalid or truncated response is listed separately from a completed null response.', '',
        '| Model | Speed erased | Distance final digit erased | Speed decimal removed |',
        '| --- | --- | --- | --- |']
    case_order=['synthetic-absent-speed','synthetic-erased-final-digit','synthetic-missing-decimal']
    for name in names:
        challenges={Path(manifest_by_id[r['id']]['source']).stem:r for r in by_model[name]['crop']['challenge']}
        values=[]
        for case in case_order:
            row=challenges[case];field=next(iter(row['expected']))
            if row['parsed'] is None: values.append('Invalid response')
            elif row['parsed'][field] is None: values.append('null')
            else: values.append(str(row['parsed'][field]))
        lines.append('| '+name+' | '+' | '.join(values)+' |')
    cpu_challenge=[r for r in cpu['results'] if r['kind']=='synthetic_challenge']
    if all(all(v in ['correct_unknown','unlabeled'] for v in r['score'].values()) for r in cpu_challenge):
        lines += ['| Calibrated CPU reader | Unknown | Unknown | Unknown |']
    lines += ['', 'A guessed zero must not be interpreted as proof that the ship has stopped. Clear-image accuracy does not justify replacing the numeric reader without correct rejection of missing evidence.', '',
        '## Interpretation and limitations', '',
        'Keep the calibrated CPU reader for supported numeric state and its explicit field rejection behavior. Focused visual requests are useful candidates for contextual tasks, but numeric success and response speed do not establish target selection, resource recognition, obstacle detection, or safe planning. Those tasks need their own labels and evaluation.', '',
        'The original images include later HUD captures, maps, and other scenes. Fixed crops on those scenes remain unscored observations. The one 3840 by 2160 map has no fixed HUD crop. Synthetic fixtures are reported separately from real-image accuracy. No model output or CPU-reader prediction was used to label an unlabeled image.', '',
        'Runs were sequential by model on the RTX 4070 with 12,282 MiB of device memory. Desktop load was not controlled. Requests include image encoding and HTTP where applicable; FastVLM includes decoding and preprocessing. Setup and model loading are recorded separately, though first-use vision initialization can remain in the first request. The CPU baseline excludes construction and ran while the first model was starting. The smaller and larger comparisons therefore do not establish a CPU performance regression or a live action deadline.', '',
        '## Saved data and reproduction', '',
        '- [Frozen corpus and instructions](../../test-data/vision/v1/README.md)',
        '- [Manifest with original paths, labels, and hashes](../../test-data/vision/v1/manifest.json)',
        '- [Complete summary](../../logs/vision-corpus-v1/summary.json)',
        '- [CPU baseline responses](../../logs/vision-corpus-v1/reader-baseline.json)',
        '- [Runtime environment](../../logs/vision-corpus-v1/environment.json)',
        '- [Benchmark script](../../tools/ollama/benchmark-saved-vision.py)',
        '- [Dataset freeze script](../../tools/ollama/freeze-vision-dataset.py)', '',
        'Each started model has a JSONL file containing every completed request, response, image hash, latency, strict score, and diagnostic score, plus a metadata file with its configuration and runtime identity. The old nine-frame experiment remains preserved separately. The [run status](../../logs/vision-corpus-v1/status.json) records the requested stop and partial counts.', '',
        '```powershell',
        "& 'tools/model-runtimes/fastvlm-venv/Scripts/python.exe' 'tools/ollama/benchmark-saved-vision.py' --all",
        '```', '',
        'If further testing is requested, this resumes the recorded batch. To repeat inference without replacing its records, pass `--run-dir` with a new result directory. Frozen inputs are verified before each model request. The current batch is stopped, its Python processes have been terminated, and its Ollama models are unloaded. SmolVLM was not started in this expanded run. Installed runtimes and weights remain available.', '']
    REPORT.write_text('\n'.join(lines),encoding='utf-8')
    print(REPORT)

if __name__ == '__main__': write()
