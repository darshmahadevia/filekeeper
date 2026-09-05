# Scan benchmarks

Run from the repository root:

```bash
python3 -m benchmarks.run --files 128 --size-kib 256 --repeats 5 \
  --output benchmarks/results/local.json
```

Use `--files`, `--size-kib`, and `--repeats` to adjust the workload. All must be positive. The benchmark creates files and databases in a temporary directory and removes them afterward. It never cleans or restores your folders. This module is intended to run from the source checkout on macOS or Linux.

## Workloads

The fixed random seed is 42. Each profile contains the requested number of files.

- Unique sizes: each file has a different size, so none need hashing.
- Duplicate-heavy: adjacent file pairs share content. All files have the base size.
- Mixed: approximately half the files form duplicate pairs and the remainder have unique sizes.

Odd file counts may leave an unmatched candidate. Fixture generation is deterministic and excluded from measurements.

## Measurements

Every sample runs a scan in a fresh Python subprocess. Elapsed time uses `perf_counter` around the scan, including SQLite setup, lookups, writes, and closing, but excluding interpreter startup. Peak RSS comes from `resource.getrusage` and includes the interpreter and all imports; it is not an isolated measure of scanner allocations.

The modes are uncached, cold cache, and warm cache. Here cold and warm describe only the FileKeeper SQLite cache. The benchmark does not flush operating-system disk caches. A warm cache is primed by an unmeasured scan. Each repetition uses its own cache database, and mode order rotates between repetitions. A digest of the duplicate groups must match across every mode and repetition.

`bytes_hashed` counts content bytes from successful hash reads. It excludes filesystem metadata and SQLite I/O. `cache_hits` counts reused candidate hashes, not all files. The JSON keeps raw samples, median summaries, Python version, operating-system information, architecture, workload size, and random seed. It does not collect a username, hostname, or absolute scan paths.

## Recorded local run

[Raw samples](results/local.json) were measured on Python 3.12.12, macOS arm64, with five repetitions, 128 files per profile, and a base size of 256 KiB. Each profile occupies approximately 32 MiB.

| Profile | Mode | Median elapsed | Median peak RSS | Median content bytes hashed |
| --- | --- | ---: | ---: | ---: |
| Unique sizes | Uncached | 1.04 ms | 21.7 MiB | 0 |
| Unique sizes | First cached | 2.27 ms | 21.9 MiB | 0 |
| Unique sizes | Repeat cached | 1.86 ms | 21.8 MiB | 0 |
| Duplicate-heavy | Uncached | 22.02 ms | 21.9 MiB | 32 MiB |
| Duplicate-heavy | First cached | 25.95 ms | 22.3 MiB | 32 MiB |
| Duplicate-heavy | Repeat cached | 9.81 ms | 22.0 MiB | 0 |
| Mixed | Uncached | 11.58 ms | 21.9 MiB | 16 MiB |
| Mixed | First cached | 14.30 ms | 22.3 MiB | 16 MiB |
| Mixed | Repeat cached | 5.95 ms | 21.9 MiB | 0 |

Repeat cached scans were about 2.24× faster on this duplicate-heavy fixture and 1.95× faster on the mixed fixture. Caching made the unique-size fixture slower. Memory differences here are too small to support a memory-improvement claim.

These are local measurements of short workloads. Scheduling noise, filesystem behavior, OS caching, and storage hardware influence results. No confidence intervals or cross-machine comparison were computed. Re-run on larger datasets before drawing conclusions about large-folder performance. CI verifies the harness and result consistency, not a timing threshold.
