"""Offline warm-process timing for calibrated HUD fixtures; never connects to BSGO.

Measures fixture image open/decode, in-memory HudReader.read, and a temporary
request/response/logging file handoff. Results are microbenchmarks, not live
capture cadence claims. Example: python performance_benchmark.py --iterations 100
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import uuid
from pathlib import Path

from PIL import Image

from hud_reader import HudReader


ROOT = Path(__file__).resolve().parent
FIXTURES = sorted((ROOT / "fixtures" / "live").glob("live-*.png"))


def elapsed_ms(operation):
    started = time.perf_counter_ns()
    value = operation()
    return (time.perf_counter_ns() - started) / 1_000_000, value


def summary(values):
    ordered = sorted(values)

    def percentile(p):
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)]

    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(percentile(0.95), 4),
        "max_ms": round(max(ordered), 4),
    }


def load_decoded(path):
    with Image.open(path) as image:
        image.load()
        return image.copy()


def atomic_json(path: Path, payload):
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def benchmark(iterations: int):
    if not FIXTURES:
        raise SystemExit("No fixtures/live/live-*.png files found")
    reader = HudReader()

    # Prime imports, Python code paths, image decoder, reader templates and the
    # OS file cache before measuring. Keep full decoded images for read-only HUD
    # measurements; this benchmark never looks at a live game window.
    images = {path: load_decoded(path) for path in FIXTURES}
    for path in FIXTURES:
        reader.read(images[path])

    image_load = []
    hud_read = []
    per_fixture = {}
    for path in FIXTURES:
        image_times = []
        hud_times = []
        for _ in range(iterations):
            ms, decoded = elapsed_ms(lambda: load_decoded(path))
            image_times.append(ms)
            decoded.close()
            ms, result = elapsed_ms(lambda: reader.read(images[path], time.time()))
            hud_times.append(ms)
        image_load.extend(image_times)
        hud_read.extend(hud_times)
        per_fixture[path.name] = {
            "bytes": path.stat().st_size,
            "image_load_decode": summary(image_times),
            "hud_read": summary(hud_times),
            "last_result": {
                "distance": result.get("distance"),
                "speed": result.get("speed"),
                "valid": result.get("valid"),
            },
        }

    file_stages = {
        key: [] for key in (
            "frame_file_write", "request_publish", "request_read_parse", "worker_image_decode",
            "worker_hud_read", "response_publish", "response_read_parse",
            "capture_log_publish", "whole_file_handoff",
        )
    }
    # Use unique scratch files directly under ROOT. Some restricted Windows
    # runners allow workspace file writes but deny writes inside Python-created
    # temporary subdirectories.
    scratch_prefix = f".perf-{uuid.uuid4().hex}"
    capture_log = ROOT / f"{scratch_prefix}-capture.jsonl"
    latest = ROOT / f"{scratch_prefix}-latest.json"
    try:
        for index in range(iterations):
            path = FIXTURES[index % len(FIXTURES)]
            frame_path = ROOT / f"{scratch_prefix}-frame-{index}.png"
            request_path = ROOT / f"{scratch_prefix}-request-{index}.json"
            response_path = ROOT / f"{scratch_prefix}-response-{index}.json"
            request = {"id": index, "path": str(frame_path), "captured_at": time.time()}
            cycle_started = time.perf_counter_ns()

            payload = path.read_bytes()
            ms, _ = elapsed_ms(lambda: frame_path.write_bytes(payload))
            file_stages["frame_file_write"].append(ms)
            ms, _ = elapsed_ms(lambda: atomic_json(request_path, request))
            file_stages["request_publish"].append(ms)
            ms, request_value = elapsed_ms(
                lambda: json.loads(request_path.read_text(encoding="utf-8"))
            )
            file_stages["request_read_parse"].append(ms)

            ms, decoded = elapsed_ms(lambda: load_decoded(Path(request_value["path"])))
            file_stages["worker_image_decode"].append(ms)
            ms, output = elapsed_ms(lambda: reader.read(decoded, request_value["captured_at"]))
            file_stages["worker_hud_read"].append(ms)
            decoded.close()
            output.update(id=index, run_id="offline-benchmark")

            ms, _ = elapsed_ms(lambda: atomic_json(response_path, output))
            file_stages["response_publish"].append(ms)
            ms, response = elapsed_ms(
                lambda: json.loads(response_path.read_text(encoding="utf-8"))
            )
            file_stages["response_read_parse"].append(ms)

            def publish_logs():
                with capture_log.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(json.dumps(response, separators=(",", ":")) + "\n")
                atomic_json(latest, response)

            ms, _ = elapsed_ms(publish_logs)
            file_stages["capture_log_publish"].append(ms)
            file_stages["whole_file_handoff"].append(
                (time.perf_counter_ns() - cycle_started) / 1_000_000
            )
    finally:
        for scratch_path in ROOT.glob(f"{scratch_prefix}-*"):
            scratch_path.unlink(missing_ok=True)

    return {
        "scope": "offline warm-process fixture microbenchmark; no live capture or game state",
        "python_iterations_per_fixture": iterations,
        "fixture_count": len(FIXTURES),
        "fixture_dimensions": list(next(iter(images.values())).size),
        "image_load_decode_all_fixtures": summary(image_load),
        "hud_read_all_fixtures": summary(hud_read),
        "request_file_pipeline_per_iteration": {
            name: summary(values) for name, values in file_stages.items()
        },
        "fixtures": per_fixture,
        "notes": [
            "Percentiles aggregate repeated warm-cache operations, not independent cold starts.",
            "Temporary request/response/log files are removed when the process exits.",
            "File handoff excludes a separate worker process, watcher sleep, Sky capture, and model latency.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 20:
        raise SystemExit("Use at least 20 iterations to reduce timer quantization noise")
    report = json.dumps(benchmark(args.iterations), indent=2)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
