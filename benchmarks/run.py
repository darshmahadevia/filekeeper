"""Measure each scan in a fresh process; fixture creation is outside scan timing."""
import argparse
import hashlib
import json
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROFILES = ("unique-sizes", "duplicate-heavy", "mixed")


def generate(root, profile, files, size, seed=42):
    root.mkdir(parents=True)
    rng = random.Random(seed)
    total = 0
    previous = None
    for index in range(files):
        duplicate = profile == "duplicate-heavy" or (profile == "mixed" and index < files // 2)
        if duplicate:
            if index % 2 == 0:
                previous = rng.randbytes(size)
            content = previous
        else:
            content = rng.randbytes(size + index + 1)
        (root / f"{index:06d}.bin").write_bytes(content)
        total += len(content)
    return total


def measure(root, cache=None):
    command = [sys.executable, "-m", "benchmarks.run", "--worker", str(root)]
    if cache is not None:
        command += ["--cache", str(cache)]
    return json.loads(subprocess.check_output(command, text=True))


def worker(root, cache):
    from filekeeper.core import scan
    started = time.perf_counter()
    report = scan(root, cache_path=cache)
    elapsed = time.perf_counter() - started
    if report["errors"]:
        raise RuntimeError(report["errors"])
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"seconds": elapsed, "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
            "files_hashed": report["files_hashed"], "bytes_hashed": report["bytes_hashed"],
            "cache_hits": report["cache_hits"], "duplicate_groups": len(report["groups"]),
            "result_digest": hashlib.sha256(json.dumps(report["groups"], sort_keys=True).encode()).hexdigest()}


def run(files, size, repeats):
    result = {"schema_version": 1,
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "machine": platform.machine()},
              "parameters": {"files_per_profile": files, "base_size_bytes": size, "repeats": repeats, "seed": 42},
              "method": "Fresh process per scan; perf_counter excludes process startup. Peak RSS includes the interpreter. Cold/warm refer to the SQLite cache, not OS disk caches. Fixture generation and warm-cache priming are excluded. Mode order rotates across repetitions.",
              "profiles": []}
    with tempfile.TemporaryDirectory(prefix="filekeeper-benchmark-") as temporary:
        base = Path(temporary)
        for profile in PROFILES:
            root = base / profile
            total = generate(root, profile, files, size)
            samples = {mode: [] for mode in ("uncached", "cold-cache", "warm-cache")}
            expected = None
            for repeat in range(repeats):
                modes = list(samples)
                modes = modes[repeat % 3:] + modes[:repeat % 3]
                for mode in modes:
                    cache = None if mode == "uncached" else base / f"{profile}-{mode}-{repeat}.sqlite3"
                    if mode == "warm-cache":
                        measure(root, cache)
                    sample = measure(root, cache)
                    if expected is None:
                        expected = sample["result_digest"]
                    if sample["result_digest"] != expected:
                        raise RuntimeError("Cached and uncached duplicate results differ")
                    samples[mode].append(sample)
            summaries = {mode: {"median_seconds": statistics.median(s["seconds"] for s in rows),
                                "median_peak_rss_bytes": statistics.median(s["peak_rss_bytes"] for s in rows),
                                "median_bytes_hashed": statistics.median(s["bytes_hashed"] for s in rows),
                                "median_cache_hits": statistics.median(s["cache_hits"] for s in rows)}
                         for mode, rows in samples.items()}
            result["profiles"].append({"name": profile, "total_bytes": total, "samples": samples, "summary": summaries})
    return result


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=positive, default=128)
    parser.add_argument("--size-kib", type=positive, default=256)
    parser.add_argument("--repeats", type=positive, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cache", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.cache)))
        return
    result = run(args.files, args.size_kib * 1024, args.repeats)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    for profile in result["profiles"]:
        print(profile["name"])
        for mode, summary in profile["summary"].items():
            print(f"  {mode:12} {summary['median_seconds']:.6f}s  {summary['median_bytes_hashed']:,.0f} bytes hashed  {summary['median_peak_rss_bytes'] / 1048576:.1f} MiB peak RSS")


if __name__ == "__main__":
    main()
