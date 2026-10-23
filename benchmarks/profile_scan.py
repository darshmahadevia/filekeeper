"""Separate diagnostic profiles, excluded from the benchmark timing samples."""
import cProfile
import io
import json
import pstats
import tempfile
from pathlib import Path

from benchmarks.run import SCALES, generate, groups_digest, measure, validate, warm_os_cache
from filekeeper.core import scan


def main():
    output = io.StringIO()
    print("Command: python3 -m benchmarks.profile_scan", file=output)
    print("Diagnostic cProfile runs only; excluded from timing samples.", file=output)
    with tempfile.TemporaryDirectory(prefix="filekeeper-profile-") as temporary:
        base = Path(temporary)
        for scale in ("small-files", "balanced"):
            root = base / scale
            expected = {}
            generate(root, "duplicate-heavy", *SCALES[scale], oracle=expected)
            cache = base / f"{scale}.sqlite3"
            validate(measure(root, cache), expected, "empty")
            for mode, database in (("none", None), ("populated", cache)):
                warm_os_cache(root, database)
                profiler = cProfile.Profile()
                report = profiler.runcall(scan, root, cache_path=database)
                sample = {key: report[key] for key in ("files_scanned", "files_hashed", "bytes_hashed",
                                                       "cache_hits", "duplicate_bytes")}
                sample.update(errors=len(report["errors"]), duplicate_groups=len(report["groups"]),
                              result_digest=groups_digest(report["groups"]))
                validate(sample, expected, mode)
                print(f"\n{scale} duplicate-heavy hash-{mode}", file=output)
                print(json.dumps(sample, sort_keys=True), file=output)
                for sorting in ("cumulative", "tottime"):
                    pstats.Stats(profiler, stream=output).strip_dirs().sort_stats(sorting).print_stats(18)
    path = Path("benchmarks/results/profile-scan-2026-10-23.txt")
    path.write_text(output.getvalue().rstrip() + "\n")
    print(path)


if __name__ == "__main__":
    main()
