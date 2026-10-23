"""Reproducible scan workloads with independent OS and FileKeeper cache states."""
import argparse
import hashlib
import json
import os
import platform
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROFILES = ("unique-sizes", "duplicate-heavy", "mixed")
SCALES = {
    "smoke": (128, 256 * 1024),
    "small-files": (20000, 4 * 1024),
    "balanced": (2048, 1024 * 1024),
    "large-files": (128, 32 * 1024 * 1024),
}
HASH_CACHES = ("none", "empty", "populated")


def groups_digest(groups):
    return hashlib.sha256(json.dumps(groups, sort_keys=True).encode()).hexdigest()


def generate(root, profile, files, size, seed=42, oracle=None):
    root.mkdir(parents=True)
    rng = random.Random(seed)
    total = 0
    previous = None
    groups = {}
    sizes = {}
    for index in range(files):
        duplicate = profile == "duplicate-heavy" or (profile == "mixed" and index < files // 2)
        if duplicate:
            if index % 2 == 0:
                previous = rng.randbytes(size)
            content = previous
        else:
            content = rng.randbytes(size + index + 1)
        relative = f"{index // 100:04d}/{index:06d}.bin"
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        total += len(content)
        sizes.setdefault(len(content), []).append(relative)
        if oracle is not None:
            groups.setdefault((len(content), hashlib.sha256(content).hexdigest()), []).append(relative)
    if oracle is not None:
        expected = [{"sha256": digest, "size": length, "keep": names[0], "duplicates": names[1:]}
                    for (length, digest), names in sorted(groups.items()) if len(names) > 1]
        candidates = [length for length, names in sizes.items() for _ in names if len(names) > 1]
        oracle.update(files_scanned=files, files_hashed=len(candidates), bytes_hashed=sum(candidates),
                      duplicate_groups=len(expected), result_digest=groups_digest(expected),
                      duplicate_bytes=sum(g["size"] * len(g["duplicates"]) for g in expected))
    return total


def cold_command():
    if sys.platform == "darwin":
        return ["/usr/sbin/purge"]
    if sys.platform.startswith("linux"):
        if os.geteuid() != 0:
            raise RuntimeError("OS cold cache unavailable: Linux drop_caches requires root. "
                               "Run the benchmark as root; no cold sample was measured.")
        return ["/bin/sh", "-c", r"sync && printf '%s\n' 3 > /proc/sys/vm/drop_caches"]
    raise RuntimeError("OS cold cache unavailable on this platform. Supported systems are macOS and Linux.")


def clear_os_cache():
    command = cold_command()
    commands = [command]
    if sys.platform == "darwin" and os.geteuid() != 0 and command == ["/usr/sbin/purge"]:
        commands.append(["sudo", "-n", "/usr/sbin/purge"])
    attempts = []
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"OS cold cache unavailable: {command!r}: {exc}") from exc
        attempts.append({"command": command, "returncode": result.returncode,
                         "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode == 0:
            return {"command": command, "returncode": 0, "attempts": attempts}
    raise RuntimeError(f"OS cold cache unavailable: {json.dumps(attempts)}. "
                       "No cold sample was measured. On macOS authenticate sudo separately or run as root; "
                       "on Linux run as root with writable /proc/sys/vm/drop_caches.")


def warm_os_cache(root, cache):
    total = 0
    paths = sorted(root.rglob("*.bin"))
    if cache is not None and cache.exists():
        paths.append(cache)
    for path in paths:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
    return {"method": "sequential buffered pre-read of fixture and existing SQLite database",
            "bytes_read": total}


def measure(root, cache=None, os_cache="warm"):
    command = [sys.executable, "-m", "benchmarks.run", "--worker", str(root), "--os-cache", os_cache]
    if cache is not None:
        command += ["--cache", str(cache)]
    return json.loads(subprocess.check_output(command, text=True))


def worker(root, cache, os_cache="warm"):
    from filekeeper.core import scan
    preparation_started = time.perf_counter()
    preparation = clear_os_cache() if os_cache == "cold" else warm_os_cache(root, cache)
    preparation["seconds"] = time.perf_counter() - preparation_started
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    started = time.perf_counter()
    report = scan(root, cache_path=cache)
    elapsed = time.perf_counter() - started
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    if report["errors"]:
        raise RuntimeError(report["errors"])
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"seconds": elapsed,
            "user_cpu_seconds": usage_after.ru_utime - usage_before.ru_utime,
            "system_cpu_seconds": usage_after.ru_stime - usage_before.ru_stime,
            "input_blocks": usage_after.ru_inblock - usage_before.ru_inblock,
            "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
            **{name: report[name] for name in ("files_scanned", "files_hashed", "bytes_hashed", "cache_hits", "duplicate_bytes")},
            "errors": len(report["errors"]), "duplicate_groups": len(report["groups"]),
            "result_digest": groups_digest(report["groups"]), "os_cache_preparation": preparation}


def validate(sample, expected, hash_cache):
    counters = dict(expected)
    counters["errors"] = 0
    counters["cache_hits"] = expected["files_hashed"] if hash_cache == "populated" else 0
    if hash_cache == "populated":
        counters.update(files_hashed=0, bytes_hashed=0)
    for name, value in counters.items():
        if sample[name] != value:
            raise RuntimeError(f"Incorrect {hash_cache} scan: {name}={sample[name]!r}, expected {value!r}")


def environment():
    def read(command):
        return subprocess.check_output(command, text=True).strip()
    return {"python": platform.python_version(), "executable": sys.executable,
            "platform": platform.platform(), "machine": platform.machine(), "cpu_count": os.cpu_count(),
            "load_average": list(os.getloadavg()), "utc": datetime.now(timezone.utc).isoformat(),
            "git_head": read(["git", "rev-parse", "HEAD"]),
            "git_status": read(["git", "status", "--short"]),
            "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (Path("benchmarks/run.py"), Path("filekeeper/core.py"),
                                           Path("filekeeper/cache.py"), Path("filekeeper/fs.py"))},
            "memory_bytes": int(read(["sysctl", "-n", "hw.memsize"])) if sys.platform == "darwin" else None}


def run(files, size, repeats, os_caches=("warm",), profiles=PROFILES, scale="custom", temporary_parent=None):
    if min(files, size, repeats) < 1 or not profiles or not set(profiles) <= set(PROFILES):
        raise ValueError("Positive files, size, repeats and known profiles are required")
    if not os_caches or not set(os_caches) <= {"warm", "cold"}:
        raise ValueError("OS cache must be warm or cold")
    if "cold" in os_caches:
        clear_os_cache()
    result = {"schema_version": 2, "environment": environment(),
              "parameters": {"scale": scale, "files_per_profile": files, "base_size_bytes": size,
                             "repeats": repeats, "seed": 42, "os_caches": list(os_caches)},
              "repro_command": [sys.executable, "-m", "benchmarks.run", *sys.argv[1:]],
              "method": "Fresh process per scan; scan timer excludes imports, fixture generation, SQLite priming, and OS cache preparation. Warm is a buffered pre-read, not guaranteed residency. Cold is successful macOS system-wide purge or Linux sync plus drop_caches=3 before each timed scan. Hash caches are independently none, empty, populated. Mode order rotates each repetition. RSS includes interpreter and OS preparation. Expected content groups come from fixture generation, not a reference scan.",
              "profiles": []}
    with tempfile.TemporaryDirectory(prefix="filekeeper-benchmark-", dir=temporary_parent) as temporary:
        base = Path(temporary)
        result["environment"]["temporary_volume_free_bytes"] = shutil.disk_usage(base).free
        for profile in profiles:
            root = base / profile
            expected = {}
            total = generate(root, profile, files, size, oracle=expected)
            samples = {f"os-{os_cache}/hash-{hash_cache}": []
                       for os_cache in os_caches for hash_cache in HASH_CACHES}
            for repeat in range(repeats):
                modes = list(samples)
                offset = repeat % len(modes)
                for order, mode in enumerate(modes[offset:] + modes[:offset]):
                    os_cache, hash_cache = mode.split("/")
                    os_cache, hash_cache = os_cache[3:], hash_cache[5:]
                    cache = None if hash_cache == "none" else base / f"{profile}-{os_cache}-{hash_cache}-{repeat}.sqlite3"
                    if hash_cache == "populated":
                        priming = measure(root, cache)
                        validate(priming, expected, "empty")
                    sample = measure(root, cache, os_cache)
                    validate(sample, expected, hash_cache)
                    sample.update(repeat=repeat, order=order)
                    samples[mode].append(sample)
            summaries = {mode: {"median_seconds": statistics.median(s["seconds"] for s in rows),
                                "min_seconds": min(s["seconds"] for s in rows),
                                "max_seconds": max(s["seconds"] for s in rows),
                                "median_peak_rss_bytes": statistics.median(s["peak_rss_bytes"] for s in rows),
                                "median_bytes_hashed": statistics.median(s["bytes_hashed"] for s in rows),
                                "median_cache_hits": statistics.median(s["cache_hits"] for s in rows)}
                         for mode, rows in samples.items()}
            result["profiles"].append({"name": profile, "total_bytes": total, "expected": expected,
                                       "samples": samples, "summary": summaries})
            shutil.rmtree(root)
    result["environment"]["load_average_end"] = list(os.getloadavg())
    result["environment"]["utc_end"] = datetime.now(timezone.utc).isoformat()
    return result


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=SCALES, default="balanced")
    parser.add_argument("--profile", choices=PROFILES, action="append")
    parser.add_argument("--files", type=positive)
    parser.add_argument("--size-kib", type=positive)
    parser.add_argument("--repeats", type=positive, default=5)
    parser.add_argument("--os-cache", choices=("warm", "cold"), action="append")
    parser.add_argument("--temporary-parent", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cache", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker:
            print(json.dumps(worker(args.worker, args.cache, (args.os_cache or ["warm"])[0])))
            return
        files, size = SCALES[args.scale]
        result = run(args.files or files, args.size_kib * 1024 if args.size_kib else size, args.repeats,
                     tuple(dict.fromkeys(args.os_cache or ["warm"])),
                     tuple(dict.fromkeys(args.profile or PROFILES)),
                     "custom" if args.files or args.size_kib else args.scale, args.temporary_parent)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    for profile in result["profiles"]:
        print(profile["name"])
        for mode, summary in profile["summary"].items():
            print(f"  {mode:28} {summary['median_seconds']:.6f}s "
                  f"[{summary['min_seconds']:.6f}, {summary['max_seconds']:.6f}]  "
                  f"{summary['median_bytes_hashed']:,.0f} bytes hashed  "
                  f"{summary['median_peak_rss_bytes'] / 1048576:.1f} MiB peak RSS")


if __name__ == "__main__":
    main()
