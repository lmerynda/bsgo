"""Run the five installed models on the frozen corpus; append durable resumable results."""
import argparse, base64, hashlib, importlib.util, json, math, os, re, statistics
import subprocess, sys, time, urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'test-data/vision/v1'
RUN = ROOT / 'logs/vision-corpus-v1'
PROMPT = ('Read the selected target distance: the number below the target portrait, not its health. '
          'Read actual ship speed: the number above the orange throttle bar, not throttle percentage. '
          'Return only JSON with keys "distance" and "speed". Use numbers, or null if unreadable. No explanation.')
FIELDS = ['distance', 'speed']

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)

def api(endpoint, route, body=None, timeout=120):
    request = urllib.request.Request(endpoint + route, data=None if body is None else json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)

def parsed_values(text, truncated):
    clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    strict = None
    if not truncated:
        try:
            obj = json.loads(clean)
            if isinstance(obj, dict) and set(obj) == set(FIELDS) and all(
                    v is None or (type(v) in (int, float) and math.isfinite(v)) for v in obj.values()):
                strict = obj
        except (ValueError, TypeError):
            pass
    try:
        leading, _ = json.JSONDecoder().raw_decode(clean)
    except ValueError:
        leading = {}
    diagnostic = {}
    for field in FIELDS:
        value = leading.get(field) if isinstance(leading, dict) else None
        if isinstance(value, str) and re.fullmatch(r'\d+(?:\.\d+)?', value):
            value = float(value)
        diagnostic[field] = value if type(value) in (int, float) and math.isfinite(value) else None
    return strict, diagnostic

def scores(values, expected):
    result = {}
    for field in FIELDS:
        if field not in expected:
            result[field] = 'unlabeled'
        elif values is None:
            result[field] = 'invalid_response'
        elif expected[field] is None:
            result[field] = 'correct_unknown' if values[field] is None else 'false_accept'
        elif values[field] is None:
            result[field] = 'reject'
        else:
            result[field] = 'correct' if abs(values[field] - expected[field]) < .001 else 'wrong'
    return result

class FastVLM:
    def __init__(self):
        os.environ['HF_HOME'] = str(ROOT / 'tools/model-runtimes/hf-cache')
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TORCH_HOME'] = str(ROOT / 'tools/model-runtimes/torch-cache')
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.torch = torch
        torch.set_num_threads(4)
        torch.cuda.set_device(0)
        self.revision = '16375720c2d673fa583e57e9876afde27549c7d0'
        path = ROOT / 'tools/model-runtimes/hf-cache/hub/models--apple--FastVLM-0.5B/snapshots' / self.revision
        self.model = AutoModelForCausalLM.from_pretrained(str(path), trust_remote_code=True,
            torch_dtype=torch.float16, attn_implementation='sdpa', local_files_only=True).cuda().eval()
        self.tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        self.processor = self.model.get_vision_tower().image_processor
        self.metadata = {'revision': self.revision, 'torch': torch.__version__,
                         'backend': 'PyTorch CUDA FP16 SDPA', 'preprocessing': 'publisher processor after black square padding'}

    def infer(self, path):
        from PIL import Image
        torch, tokenizer = self.torch, self.tokenizer
        with Image.open(path) as source:
            image = source.convert('RGB')
        width, height = image.size
        side = max(width, height)
        padded = Image.new('RGB', (side, side), (0, 0, 0))
        padded.paste(image, ((side-width)//2, (side-height)//2))
        pixels = self.processor.preprocess(padded, return_tensors='pt')['pixel_values'].cuda().half()
        prompt = tokenizer.apply_chat_template([{'role': 'user', 'content': '<image>\n' + PROMPT}],
                                               tokenize=False, add_generation_prompt=True)
        chunks = prompt.split('<image>')
        ids = tokenizer(chunks[0]).input_ids + [-200] + tokenizer(chunks[1]).input_ids
        inputs = torch.tensor([ids], device='cuda')
        with torch.inference_mode():
            output = self.model.generate(inputs=inputs, images=pixels, image_sizes=[image.size],
                do_sample=False, max_new_tokens=96, repetition_penalty=1.1, pad_token_id=tokenizer.eos_token_id)
        torch.cuda.synchronize()
        tokens = output.shape[-1]
        text = tokenizer.decode(output[0], skip_special_tokens=True)
        return text, tokens >= 96, {'text': text, 'output_tokens': tokens, 'processor_tensor_shape': list(pixels.shape)}

def rows_from(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []

def benchmark(model, backend):
    RUN.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (DATA / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    slug = model.replace(':', '-').replace('/', '-')
    outfile = RUN / (slug + '.jsonl')
    metadata_path = RUN / (slug + '-metadata.json')
    config = {'model': model, 'backend': backend, 'dataset_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'prompt': PROMPT, 'max_tokens': 96, 'temperature': 0, 'repeat_penalty': 1.1,
              'variants': ['full', 'crop'], 'timeout_seconds': 120}
    if metadata_path.exists():
        previous = json.loads(metadata_path.read_text())
        if previous['config'] != config:
            raise ValueError('Resume configuration changed')
    completed = {(r['id'], r['variant']) for r in rows_from(outfile)}
    expected_count = sum(1 + ('crop_file' in row) for row in manifest['rows'])
    if len(completed) == expected_count:
        print(model, 'already complete', flush=True)
        return
    endpoint = 'http://127.0.0.1:11434' if backend == 'ollama' else 'http://127.0.0.1:11435'
    process, handles, fast = None, [], None
    started = time.perf_counter()
    try:
        if backend == 'ollama':
            identity = api(endpoint, '/api/show', {'model': model})
            version = api(endpoint, '/api/version')
            warmup = api(endpoint, '/api/chat', {'model': model, 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                'think': False, 'stream': False, 'keep_alive': '10m', 'options': {'num_ctx': 8192, 'num_predict': 8}})
            runtime = {'identity': identity, 'version': version, 'warmup': warmup}
        elif backend == 'fastvlm':
            fast = FastVLM()
            warmup_row = next(row for row in manifest['rows'] if row['source'] == 'tools/hud-reader/sample.png'
                              or 'tools/hud-reader/sample.png' in row['source_aliases'])
            fast.infer(DATA / warmup_row['file'])
            runtime = fast.metadata
        else:
            # Own the server process and stop exactly that process in finally.
            runtime_dir = ROOT / 'tools/model-runtimes/llama-b11334'
            weights = ROOT / 'tools/model-runtimes/smolvlm-500m'
            argv = [str(runtime_dir / 'llama-server.exe'), '-m', str(weights / 'SmolVLM-500M-Instruct-Q8_0.gguf'),
                '--mmproj', str(weights / 'mmproj-SmolVLM-500M-Instruct-f16.gguf'), '--gpu-layers', '99',
                '--ctx-size', '8192', '--parallel', '1', '--host', '127.0.0.1', '--port', '11435']
            try:
                api(endpoint, '/health', timeout=2)
            except OSError:
                pass
            else:
                raise RuntimeError('Port 11435 already in use; refusing to adopt another server')
            for name in ['stdout', 'stderr']:
                handles.append((RUN / ('smol-server-' + name + '.log')).open('a', encoding='utf-8'))
            process = subprocess.Popen(argv, cwd=runtime_dir, stdout=handles[0], stderr=handles[1],
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Smol server exited; inspect server log')
                try:
                    if api(endpoint, '/health', timeout=2).get('status') == 'ok': break
                except OSError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Smol server health deadline exceeded')
            runtime = {'argv': argv, 'version': subprocess.check_output([str(runtime_dir / 'llama-server.exe'), '--version'],
                         stderr=subprocess.STDOUT, text=True), 'backend': 'llama.cpp CUDA Q8_0 F16 projector'}
        if not metadata_path.exists():
            save(metadata_path, {'config': config, 'runtime': runtime, 'setup_seconds': time.perf_counter()-started})
        consecutive_errors = 0
        with outfile.open('a', encoding='utf-8', buffering=1) as stream:
            for variant in ['full', 'crop']:
                for row in manifest['rows']:
                    if (row['id'], variant) in completed or (variant == 'crop' and 'crop_file' not in row): continue
                    path = DATA / row['crop_file' if variant == 'crop' else 'file']
                    expected_hash = row['crop_sha256' if variant == 'crop' else 'sha256']
                    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                        raise ValueError('Frozen input changed: ' + str(path))
                    result = {'id': row['id'], 'variant': variant, 'source_run': row['source_run'],
                              'kind': row['kind'], 'split': row['split'], 'image_sha256': expected_hash, 'expected': row['expected']}
                    start = time.perf_counter()
                    try:
                        if backend == 'fastvlm':
                            text, truncated, response = fast.infer(path)
                        else:
                            encoded = base64.b64encode(path.read_bytes()).decode()
                            if backend == 'ollama':
                                response = api(endpoint, '/api/chat', {'model': model, 'messages': [
                                    {'role': 'user', 'content': PROMPT, 'images': [encoded]}], 'think': False, 'stream': False,
                                    'keep_alive': '10m', 'options': {'temperature': 0, 'num_ctx': 8192,
                                                                  'num_predict': 96, 'repeat_penalty': 1.1}})
                                text = response.get('message', {}).get('content', '')
                                truncated = response.get('done_reason') == 'length'
                            else:
                                response = api(endpoint, '/v1/chat/completions', {'model': model,
                                    'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': PROMPT},
                                        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + encoded}}]}],
                                    'stream': False, 'temperature': 0, 'max_tokens': 96, 'cache_prompt': False})
                                text = response['choices'][0]['message']['content']
                                truncated = response['choices'][0]['finish_reason'] == 'length'
                        strict, diagnostic = parsed_values(text, truncated)
                        result.update(response=response, parsed=strict, diagnostic_numeric_values=diagnostic,
                                      truncated=truncated, score=scores(strict, row['expected']),
                                      diagnostic_score=scores(diagnostic, row['expected']))
                        consecutive_errors = 0
                    except Exception as error:
                        consecutive_errors += 1
                        result.update(error=repr(error), parsed=None, diagnostic_numeric_values=None,
                                      score=scores(None, row['expected']), diagnostic_score=scores(None, row['expected']))
                    result['wall_seconds'] = time.perf_counter()-start
                    stream.write(json.dumps(result) + '\n')
                    completed.add((row['id'], variant))
                    if len(completed) % 20 == 0 or len(completed) == expected_count or 'error' in result:
                        print(json.dumps({'model': model, 'done': len(completed), 'total': expected_count,
                                          'variant': variant, 'last_seconds': result['wall_seconds'],
                                          'error': result.get('error')}), flush=True)
                    if consecutive_errors >= 5:
                        raise RuntimeError('Five consecutive inference failures; results preserved')
    finally:
        if backend == 'ollama':
            api(endpoint, '/api/generate', {'model': model, 'keep_alive': 0})
        if process is not None:
            process.terminate()
            process.wait(timeout=15)
        for handle in handles: handle.close()

def baseline():
    from PIL import Image
    spec = importlib.util.spec_from_file_location('frozen_reader', DATA / 'baseline/hud_reader.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    reader = module.HudReader()
    rows = json.loads((DATA / 'manifest.json').read_text())['rows']
    results = []
    for row in rows:
        start = time.perf_counter()
        with Image.open(DATA / row['file']) as image:
            output = reader.read(image.convert('RGB'))
        results.append({'id': row['id'], 'kind': row['kind'], 'source_run': row['source_run'], 'split': row['split'],
                        'wall_seconds': time.perf_counter()-start, 'output': output,
                        'score': scores({f: output.get(f) for f in FIELDS}, row['expected'])})
    save(RUN / 'reader-baseline.json', {'reader_sha256': hashlib.sha256((DATA/'baseline/hud_reader.py').read_bytes()).hexdigest(),
        'template_sha256': hashlib.sha256((DATA/'baseline/templates.json').read_bytes()).hexdigest(),
        'timing': 'original profile; decode plus read; construction excluded; unlabeled frames not scored', 'results': results})

def summarize():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    expected_counts = {'full': len(manifest['rows']), 'crop': sum('crop_file' in r for r in manifest['rows'])}
    summaries = []
    for path in sorted(RUN.glob('*.jsonl')):
        metadata = json.loads(path.with_name(path.stem + '-metadata.json').read_text())
        for variant in ['full', 'crop']:
            rows = [r for r in rows_from(path) if r['variant'] == variant]
            labeled = [r for r in rows if r['kind']=='real_capture' and all(f in r['expected'] for f in FIELDS)]
            challenge = [r for r in rows if r['kind']=='synthetic_challenge']
            groups = defaultdict(list)
            for row in labeled: groups[row['source_run']].append(row)
            def correct(row, key='score'): return all(row[key][f]=='correct' for f in FIELDS)
            times = sorted(r['wall_seconds'] for r in rows if 'error' not in r)
            summaries.append({'model': metadata['config']['model'], 'variant': variant, 'requests': len(rows),
                'expected_requests': expected_counts[variant], 'labeled_real_pairs': len(labeled),
                'strict_both_correct': sum(correct(r) for r in labeled),
                'diagnostic_both_correct': sum(correct(r,'diagnostic_score') for r in labeled),
                'strict_complete_labeled': sum(r['parsed'] is not None for r in labeled),
                'field_counts_labeled': {f: dict(Counter(r['score'][f] for r in labeled)) for f in FIELDS},
                'source_groups_labeled': len(groups),
                'mean_group_accuracy': statistics.mean(sum(correct(r) for r in group)/len(group) for group in groups.values()) if groups else None,
                'mean_group_diagnostic_accuracy': statistics.mean(sum(correct(r,'diagnostic_score') for r in group)/len(group) for group in groups.values()) if groups else None,
                'splits': {s: {'pairs': len([r for r in labeled if r['split']==s]),
                    'correct': sum(correct(r) for r in labeled if r['split']==s),
                    'diagnostic_correct': sum(correct(r,'diagnostic_score') for r in labeled if r['split']==s)} for s in sorted(set(r['split'] for r in labeled))},
                'median_seconds': statistics.median(times) if times else None,
                'p95_seconds': times[math.ceil(.95*len(times))-1] if times else None,
                'median_labeled_seconds': statistics.median(r['wall_seconds'] for r in labeled if 'error' not in r) if labeled else None,
                'errors': sum('error' in r for r in rows), 'truncated': sum(r.get('truncated',False) for r in rows),
                'challenge': [{'id': r['id'], 'expected': r['expected'], 'parsed': r['parsed'], 'score': r['score']} for r in challenge]})
    save(RUN / 'summary.json', {'dataset_sha256': hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest(), 'models': summaries})
    print(json.dumps(summaries, indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--model')
    parser.add_argument('--backend', choices=['ollama','fastvlm','smol'])
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--summarize', action='store_true')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--dataset-dir', type=Path, default=DATA)
    parser.add_argument('--run-dir', type=Path, default=RUN)
    args = parser.parse_args()
    DATA, RUN = args.dataset_dir.resolve(), args.run_dir.resolve()
    if args.all:
        for model, backend in [('qwen3.5:0.8b','ollama'), ('qwen3-vl:4b-instruct','ollama'),
                               ('qwen3-vl:2b-instruct','ollama'), ('FastVLM-0.5B','fastvlm'),
                               ('SmolVLM-500M','smol')]:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), '--model', model, '--backend', backend,
                            '--dataset-dir', str(DATA), '--run-dir', str(RUN)], check=True)
        summarize()
    elif args.baseline: baseline()
    elif args.summarize: summarize()
    else: benchmark(args.model, args.backend)
