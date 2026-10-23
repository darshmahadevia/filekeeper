# Benchmarks

Run from the repository root. These commands create deterministic temporary files and disposable cache databases. They remove only their own temporary directory.

```bash
python3 -m benchmarks.run --scale balanced --repeats 5 --os-cache warm \
  --output benchmarks/results/balanced-warm.json
```

## Workload sizes

The seed is 42. Each directory contains at most 100 files. Each scale runs all three content profiles unless `--profile` selects one. Repeat `--profile` to select several.

| Scale | Files per content profile | Base file size | Approximate bytes per content profile |
| --- | ---: | ---: | ---: |
| `smoke` | 128 | 256 KiB | 32 MiB |
| `small-files` | 20,000 | 4 KiB | 78 to 269 MiB |
| `balanced` | 2,048 | 1 MiB | 2 GiB |
| `large-files` | 128 | 32 MiB | 4 GiB |

`balanced` is the default. `--files` and `--size-kib` override the scale and label the result `custom`. `--repeats` defaults to five. All numeric options must be positive. `--temporary-parent` chooses the volume that holds the fixtures. One content profile exists at a time.

The content profiles are:

- `unique-sizes` gives each file a different size. The scan walks every file but hashes no content.
- `duplicate-heavy` uses the same size for every file. Adjacent pairs contain identical bytes. All candidates require hashing without a populated hash cache.
- `mixed` gives approximately half the files paired content. The remainder have unique sizes.

An odd count can leave an unmatched candidate. Random bytes are generated separately and written through ordinary buffered writes. The generator does not use sparse files, hard links, clones, or copies that could share file data blocks. It computes expected duplicate groups from the generated content before measurement.

These sizes represent metadata-heavy folders, gigabytes of ordinary files, and larger sequential reads. They do not model every document library or storage device. They fit below this machine's 16 GiB RAM; size alone does not make an OS-cold workload.

## Independent cache settings

Each OS setting runs all three FileKeeper hash-cache settings:

- `hash-none` scans with no FileKeeper database.
- `hash-empty` scans with a new database and includes its creation and population in scan timing.
- `hash-populated` scans with a database primed by a validated, unmeasured scan in another process. The timed process opens that persistent SQLite database and reuses hashes. Metadata validation and SQLite work remain timed.

`--os-cache warm` performs a sequential buffered pre-read of every fixture file and the existing SQLite database immediately before scan timing. This requests warm file data. It cannot guarantee residency or warm every filesystem metadata cache. The pre-read is outside timing and its bytes and elapsed time are recorded.

`--os-cache cold` clears system-wide OS caches before each measured scan, after fixture generation and any SQLite priming. It also performs a preflight eviction before creating fixtures. A successful eviction is mandatory. Failure exits nonzero before recording a cold sample and never falls back to warm results.

On macOS, cold mode tries `/usr/sbin/purge` directly first. If it fails for a non-root process, it tries `sudo -n /usr/sbin/purge`. Each sample records the command and attempts. On Linux, it requires direct root execution and runs `sync` followed by writing `3` to `/proc/sys/vm/drop_caches`. A read-only proc filesystem or missing capability produces an explicit failure. Linux support is tested with simulated platform and subprocess responses; it has not been measured on this macOS host.

Use a quiet measurement window. Cold mode affects other applications and benchmarks. Authenticate macOS sudo separately if permitted, then run:

```bash
python3 -m benchmarks.run --scale balanced --repeats 5 --os-cache warm --os-cache cold \
  --output benchmarks/results/balanced-warm-cold.json
```

OS-cold means the operating system completed its eviction command. Eviction does not clear SSD controller caches or guarantee that every physical read reaches flash. A populated FileKeeper hash cache can avoid content reads even after OS eviction. The benchmark does not use macOS `F_NOCACHE` as a substitute for eviction.

## Measurement and correctness

Every sample runs in a fresh Python process. `perf_counter` surrounds the complete `scan` call, including traversal, metadata validation, hashing, and SQLite setup, transactions, and close. It excludes interpreter startup, imports, fixture generation, priming, and OS cache preparation. This is the library scan path that the CLI calls; CLI startup and report serialization are excluded.

Mode order rotates each repetition. The JSON preserves repetition and execution order, all raw samples, median and minimum/maximum elapsed times, and median peak RSS. RSS includes interpreter imports and OS preparation. It is not an isolated scanner-allocation measurement.

Each timed scan must match the generator's expected groups digest, duplicate bytes, duplicate-group count, file count, hashed-file count, bytes hashed, cache hits, and zero errors. The priming scan is validated too. A skipped hash read or an incorrect duplicate report fails the run. `bytes_hashed` counts content bytes read successfully by FileKeeper, not metadata or SQLite I/O. It does not prove those reads reached storage. CPU time and input-block counters cover the scan; block counters depend on OS accounting and are not a substitute for physical-device telemetry.

Environment fields include Python, OS, architecture, core count, RAM on macOS, beginning and ending load averages and timestamps, free space on the temporary volume, git revision/status, source-file checksums, and the invocation. There are no timing thresholds in CI.

## Historical smoke run

[The original local run](results/local.json) uses schema version 1 and 128 files of approximately 256 KiB per profile. Its `cold-cache` label means a fresh FileKeeper SQLite database. It did not evict OS caches. Those short runs are retained as historical raw data and do not establish realistic-scale or OS-cold performance.

## Initial OS-cold permission probe

[The actual permission probe](results/os-cold-permission-2026-10-23.json) ran on macOS 27.0.1 arm64 as uid 501 after the cleanup benchmark worker finished. Both commands exited with code 1:

```text
/usr/sbin/purge
Unable to purge disk buffers: Operation not permitted

sudo -n /usr/sbin/purge
sudo: a password is required
```

This initial unauthenticated probe contains both raw errors and an empty samples array. It recorded no OS-cold timings. After the user authenticated sudo locally, the later balanced-scale run completed successfully. The initial failure artifact is retained as historical evidence; the measured OS-cold results are below.

## Recorded scale runs

The following warmed runs used Python 3.12.12, macOS 27.0.1, an Apple M4 with 10 cores, and 16 GiB RAM. Each cell shows median elapsed milliseconds and the minimum to maximum of five runs. These compare FileKeeper cache configurations of the same scanner.

| Scale | Content profile | No hash database, ms | Populated hash database, ms |
| --- | --- | ---: | ---: |
| small-files | unique-sizes | 162.09 [160.58 to 181.98] | 161.46 [160.43 to 166.29] |
| small-files | duplicate-heavy | 1925.20 [1917.32 to 2049.96] | 1602.12 [1588.79 to 1899.05] |
| small-files | mixed | 1039.74 [1018.50 to 1073.28] | 882.40 [877.65 to 940.36] |
| balanced | unique-sizes | 16.13 [15.96 to 16.57] | 17.22 [16.93 to 17.80] |
| balanced | duplicate-heavy | 985.74 [971.97 to 1021.32] | 165.25 [161.86 to 172.42] |
| balanced | mixed | 501.72 [494.84 to 521.85] | 91.18 [87.82 to 95.38] |
| large-files | unique-sizes | 2.18 [1.93 to 2.54] | 2.70 [2.57 to 3.23] |
| large-files | duplicate-heavy | 2072.56 [2005.85 to 3181.16] | 12.59 [12.13 to 13.05] |
| large-files | mixed | 1107.46 [1072.74 to 1127.04] | 7.43 [7.16 to 7.70] |

Raw samples and complete summaries include the empty-database mode:

- [20,000 small files per profile](results/small-files-warm-2026-10-23.json).
- [2 GiB per profile](results/balanced-warm-2026-10-23.json).
- [4 GiB per profile](results/large-files-warm-2026-10-23.json).
- [Environment commands and outputs](results/environment-2026-10-23.json).

Every sample passed content and work validation. Duplicate-heavy uncached scans hashed 81,920,000 bytes, 2,147,483,648 bytes, and 4,294,967,296 bytes at the three scales. Populated scans hashed zero content bytes and reused 20,000, 2,048, and 128 hashes respectively. Mixed profiles hashed half those bytes without a populated database. Unique-size scans hashed no content in any mode.

The populated database reduced measured elapsed time for duplicate-heavy and mixed profiles. The small-files unique-size ranges overlap, so that case shows no measurable difference. Other unique-size cases include extra database overhead despite having no candidate hashes. These are warmed scan results, not physical SSD throughput measurements. A separate measured OS-cold run is described below.

The larger duplicate-heavy uncached range reaches 3.18 seconds against a 2.07-second median. Desktop activity, scheduling, and filesystem behavior were not isolated. Modes rotated over repetitions; there were no pinned cores, thermal controls, confidence intervals, or cross-machine runs. Peak RSS includes preparation and the interpreter. These measurements do not establish a memory improvement.

Reproduce the recorded scans from the repository root:

```bash
python3 -m benchmarks.run --scale small-files --repeats 5 --os-cache warm \
  --output benchmarks/results/small-files-warm-2026-10-23.json
python3 -m benchmarks.run --scale balanced --repeats 5 --os-cache warm \
  --output benchmarks/results/balanced-warm-2026-10-23.json
python3 -m benchmarks.run --scale large-files --repeats 5 --os-cache warm \
  --output benchmarks/results/large-files-warm-2026-10-23.json
python3 -m unittest discover -s tests -p test_benchmarks.py -v
```

The matching `.log` files retain the actual invocations and console output. JSON records source hashes at measurement time. After those warm measurements, the Linux-only shell command was rewritten using a raw Python string and `printf '%s\n' 3` to make its newline escape explicit. A new test runs that generated command against a temporary file with `sync` removed and verifies exact bytes `b"3\n"`. It performs no root or proc filesystem actions. Warm measurement logic did not change, and the timing data was not rerun after that edit. Ten benchmark tests pass.

## Recorded OS-cold run

[Raw OS-cold samples](results/balanced-cold.json) were collected after local sudo authentication on the same Python 3.12.12, macOS 27.0.1, Apple M4, 16 GiB machine. The balanced scale contains 2,048 files and approximately 2 GiB per content profile. All 45 measured samples passed the independently generated content and work expectations. The successful `sudo -n /usr/sbin/purge` attempt is recorded before each timed sample, after fixture generation and any hash-database priming. OS preparation time is recorded separately and excluded from scan timing.

Each cell below shows median seconds and the minimum to maximum of five runs. Hash-cache mode order rotates within repetitions.

| Content profile | No hash database, s | New hash database, s | Populated hash database, s |
| --- | ---: | ---: | ---: |
| unique-sizes | 0.032 [0.027 to 0.035] | 0.062 [0.046 to 0.091] | 0.050 [0.044 to 0.062] |
| duplicate-heavy | 3.388 [2.885 to 4.495] | 3.174 [3.006 to 3.379] | 0.223 [0.220 to 0.228] |
| mixed | 1.736 [1.642 to 2.000] | 1.841 [1.602 to 2.288] | 0.132 [0.129 to 0.155] |

Duplicate-heavy scans without a populated cache hashed 2,147,483,648 bytes. Populated-cache scans hashed zero content bytes and reused all 2,048 hashes. Mixed scans hashed 1,073,741,824 bytes without a populated cache and reused 1,024 hashes with one. Unique-size scans hashed no content in any mode. Every sample has zero errors and the same expected duplicate groups as the corresponding warm fixture.

The duplicate-heavy cold no-database median was 3.388 seconds, compared with the earlier warm median of 0.986 seconds. Its median process CPU time was 1.122 seconds against 3.388 seconds elapsed, consistent with substantial filesystem waiting after OS eviction. This is an inference from process timing, not a device-I/O measurement. The existing warm profiles identify hashing and path checks as CPU costs; no separate cold profile or physical-device trace was collected. All cold input-block counters were also zero on this macOS host, so those counters cannot establish physical reads or bandwidth. OS eviction does not clear SSD controller caches.

The no-database and new-database cold duplicate-heavy ranges overlap. That difference does not support a database-initialization performance claim. Populated-cache scans avoid candidate content reads, and their 0.220 to 0.228 second range is well below the no-database range of 2.885 to 4.495 seconds. Cached scans still incur traversal, metadata validation, and database reads.

Warm and cold results came from separate sessions rather than interleaved OS-cache modes. Compare those sessions descriptively; do not attribute the exact timing difference solely to OS caching. The raw cold file records source checksums, execution order, timestamps, and starting and ending load averages. Its harness checksum includes the Linux-only newline correction described above; the scanner, hash cache, and filesystem helper checksums match the warm run. There are 180 validated timed samples across the recorded warm and cold runs.

Reproduce in one terminal with sudo authentication available:

```bash
sudo -v
python3 -m benchmarks.run --scale balanced --repeats 5 --os-cache cold \
  --output benchmarks/results/balanced-cold.json
```

## Diagnostic profiles

[The reusable profiler](profile_scan.py) records separate duplicate-heavy scans with no hash database and a populated database at the small-files and balanced scales. [Raw cProfile output](results/profile-scan-2026-10-23.txt) includes validated counters and the hottest functions by cumulative and internal time. These instrumented runs are excluded from all reported timing samples.

```bash
python3 -m benchmarks.profile_scan
```

In the 20,000-file profiles, path validation, path manipulation, and filesystem metadata calls dominate. Eliminating content reads still leaves those per-file operations. In the balanced uncached profile, SHA-256 updates are the largest individual cost; populated scans retain path and metadata work. These profiles explain why the small-file cache benefit is modest while the 1 MiB-file benefit is larger.

The measured balanced uncached duplicate-heavy samples process approximately 2.18 GB/s of content with a median CPU-time-to-wall-time ratio near 1.00. All measured input-block counters are zero. Along with the explicit pre-read and hash-byte counters, this supports a warmed, single-process CPU/path workload. It does not measure the storage device's physical bandwidth or an OS-cold scan. Neither the profiler nor the benchmark identifies a universal limit across other filesystems, machines, or cold storage.

## Cleanup comparison

Run the applied-cleanup comparison separately from scan measurements:

```bash
python3 -m benchmarks.cleanup
python3 -m benchmarks.cleanup --profile
```

[The harness](cleanup.py) loads the original operations module from commit `38b654050f26de074ee51fe809b601e3aae57d82` with `git show` and compares it with the current implementation in the same Python process. That commit must exist in the checkout. Each side receives a newly generated, scanned fixture with 250 content groups and five copies per group. Cleanup moves 1,000 duplicates totaling 57,344,000 bytes. Byte verification is off, matching the original default behavior.

Five runs alternate before and after. The timer includes the entire `quarantine(report)` call, including rehashing, plan validation, manifest writes, directory syncs, links, and unlinks. Fixture generation, the initial scan, and result validation are outside timing. Every result must contain 1,000 stored files with the same bytes as their retained copies, and a fresh scan must contain no duplicate groups.

[The recorded samples](cleanup-results.json) have a median of 7.216 seconds before and 1.173 seconds after, with ranges of 7.186 to 7.250 seconds and 1.162 to 1.214 seconds. [The separate profile](cleanup-profile.json) identifies full-manifest JSON serialization as the original limiter. Its 2,003 full-plan saves become three in the fixed cleanup path. Path validation and filesystem checks remain. Profile times are instrumented diagnostic results and are excluded from reported cleanup timings.

No OS eviction was performed. The fixture had just been generated and scanned, so these results do not establish cleanup performance on cold storage. The original run captured ending load but omitted starting load; the artifact preserves that omission. Alternating runs reduce ordering bias but do not isolate desktop activity. Source hashes identify the timed implementation and the subsequent removal of an unused in-memory assignment. Neither correction to the harness metadata nor that code deletion reran the recorded timings.
