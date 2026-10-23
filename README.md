# FileKeeper

[![Tests](https://github.com/darshmahadevia/filekeeper/actions/workflows/tests.yml/badge.svg)](https://github.com/darshmahadevia/filekeeper/actions/workflows/tests.yml)

FileKeeper is a Python CLI that finds duplicate files, quarantines extra copies, and restores them to their original paths. Cleanup is a preview until you add `--apply`.

The implementation includes:

- Size filtering and streaming SHA-256 hashing with a 1 MiB read buffer.
- An optional SQLite hash cache that checks file identity and metadata before reusing a hash.
- Restartable quarantine and restore with atomic manifest writes, hard-link reconciliation, and per-root process locks.
- Optional byte-for-byte verification before cleanup, preserved across interrupted runs.

FileKeeper requires Python 3.11 or newer and has no runtime dependencies. Cleanup, recovery, and benchmarks target macOS and Linux. Quarantine preserves file contents on disk and does not free storage space. There is no permanent-delete command.

![FileKeeper finding a duplicate, quarantining it, and restoring the file](docs/images/demo.png)

## Try it

```bash
git clone https://github.com/darshmahadevia/filekeeper.git
cd filekeeper
python3 demo.py
```

The demo creates temporary sample files, quarantines a duplicate, restores it, and removes its sample directory.

```text
Found 1 duplicate group; 2 files hashed.
Quarantined duplicates: 0 duplicate groups remain.
Restored 1 file; all sample files are back.
```

Run commands from the checkout with `python3 -m filekeeper`. To install the shorter `filekeeper` command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
filekeeper --help
```

## Find and quarantine duplicates

Leave the folder unchanged while FileKeeper is working. Start with a scan and preview:

```bash
python3 -m filekeeper scan ./sample-folder
python3 -m filekeeper clean ./sample-folder
```

To move extra copies into `.filekeeper-trash`, add `--apply`. For an additional streaming byte comparison against the retained copy, add `--verify-bytes`:

```bash
python3 -m filekeeper clean ./sample-folder --apply --verify-bytes
```

FileKeeper keeps the first relative path in each sorted duplicate group. It does not choose by age or filename meaning. Check the preview before applying cleanup. Applied cleanup always rehashes the duplicate and retained file, even when the scan used cached hashes.

Add `--json` to `scan` or `clean` for a machine-readable report. Exit codes are `0` for success, `1` for scan or operation errors, and `2` for invalid arguments.

## Restore files or finish interrupted cleanup

Applied cleanup prints a run directory. Use that path in place of `RUN_ID`:

```bash
python3 -m filekeeper status ./sample-folder/.filekeeper-trash/RUN_ID
python3 -m filekeeper restore ./sample-folder/.filekeeper-trash/RUN_ID
```

`status` reports saved progress and current file locations as JSON. `restore` puts files back without overwriting an existing file.

To finish interrupted cleanup:

```bash
python3 -m filekeeper resume ./sample-folder/.filekeeper-trash/RUN_ID
```

You can also use `restore` to undo partial cleanup. Repeat `restore` if restoration stops. Once restoration starts, `resume` refuses to restart cleanup. An interrupted run preserves its byte-verification setting.

After an abrupt process exit, the run directory remains under `.filekeeper-trash` even if the command never printed it.

## Cache hashes between scans

Keep the SQLite database outside the scanned folder:

```bash
python3 -m filekeeper scan ./sample-folder --cache ./hashes.sqlite3
python3 -m filekeeper scan ./sample-folder --cache ./hashes.sqlite3 --json
```

A cached hash requires matching device, inode, size, modification time, and change time. FileKeeper checks metadata again after lookup. Entries belong to a root and relative path; successful scans prune entries that are no longer candidates.

Without `--cache`, a scan creates no database. FileKeeper excludes the selected database and its sidecar files. A later scan without that option would treat them as ordinary files. A corrupt or unsupported cache produces an error; use a new database path to rebuild it.

Reports include `files_hashed`, `bytes_hashed`, and `cache_hits`. Byte counts cover successful content hashing during the scan. They exclude metadata reads, SQLite I/O, and cleanup verification.

## Design and recovery

The scanner groups files by size, then hashes candidates. Unique-size files need no content reads. It skips empty files, symbolic links, repeated hard-link identities, and `.filekeeper-trash` directories.

Scanning takes O(N + B) time apart from sorting, where N is the file count and B is the candidate bytes read. It stores O(N) paths and metadata while using a fixed-size hashing buffer.

Before cleanup, FileKeeper saves a manifest with the planned paths, expected hashes, and verification policy:

```text
sample-folder/
├── notes.txt
└── .filekeeper-trash/
    └── <run-id>/
        ├── manifest.json
        └── files/
            └── 00000000
```

Each move creates a hard link at the destination, syncs its directory, and removes the source name. A process exit can leave both names present. Recovery checks inode identity before finishing the move. Two independent files remain a conflict even when their contents match.

Manifest checkpoints use a flushed temporary file and atomic replacement at operation boundaries. Cleanup writes the full plan three times, so serialized bytes grow linearly with the duplicate count. Recovery reconciles actual locations rather than trusting saved entry states. `status` shows both because saved progress can lag behind file moves.

A per-root POSIX lock prevents competing FileKeeper mutations and releases on process exit. Recovery accepts legacy version 1 and version 2 manifests. See the [design notes](DESIGN.md) for tradeoffs and the [architecture walkthrough](ARCHITECTURE.md) for the code path.

## Measured performance

Measurements use Python 3.12.12 on an Apple M4 with 16 GiB RAM and macOS 27.0.1. Each scan mode has five repetitions in fresh processes. The following duplicate-heavy folders contain paired copies; populated-cache scans hash zero content bytes.

| Folder | OS cache preparation | No hash database, median | Populated hash database, median |
| --- | --- | ---: | ---: |
| 20,000 files, 78.1 MiB | Buffered pre-read | 1.925 s | 1.602 s |
| 2,048 files, 2 GiB | Buffered pre-read | 0.986 s | 0.165 s |
| 128 files, 4 GiB | Buffered pre-read | 2.073 s | 0.013 s |
| 2,048 files, 2 GiB | macOS OS-cache eviction | 3.388 s | 0.223 s |

The OS-cold 2 GiB results range from 2.885 to 4.495 seconds without a hash database and 0.220 to 0.228 seconds with a populated database. Each cold sample records a successful `purge` before scanning. Purge time is excluded. Warm and cold runs used separate sessions, so their differences are descriptive comparisons.

Applied cleanup of 1,000 duplicates improved from **7.216 s to 1.173 s median** after removing full-manifest writes for individual moves. Five alternating runs per version ranged from 7.186 to 7.250 seconds before and 1.162 to 1.214 seconds after. A separate profile identified JSON serialization as the original bottleneck. Every run checked all moved files against their retained copies.

The benchmark suite records **180 validated scan samples** across unique-size, duplicate-heavy, and mixed folders, including no database, new database, and populated database modes. Caching helps most when it avoids large content reads; unique-size folders already avoid hashing and can incur extra database overhead. These local results do not establish a general speedup or physical SSD bandwidth.

Reproduce the scans and cleanup comparison:

```bash
python3 -m benchmarks.run --scale balanced --repeats 5 --os-cache warm \
  --output benchmarks/results/balanced-warm.json
python3 -m benchmarks.cleanup
```

The [benchmark guide](benchmarks/README.md) contains all ranges, raw samples, profiling evidence, cold-cache prerequisites, and measurement limits. Scan timing includes traversal, hashing, and SQLite work. It excludes process startup, fixture generation, and cache preparation. Peak RSS includes the interpreter.

## Tests and source

```bash
python3 -m unittest discover -s tests -v
```

The 63 tests cover duplicate detection, cache invalidation, byte verification, restore conflicts, manifest validation, historical symlink paths, and linear manifest write growth. Recovery tests force subprocess exits at link, directory-sync, unlink, and operation-checkpoint boundaries. Benchmark tests check expected content, work counters, cache modes, and eviction failures.

GitHub Actions runs the suite on Linux with Python 3.11, 3.12, and 3.13.

| File | Responsibility |
| --- | --- |
| [core.py](filekeeper/core.py) | Size grouping, cache use, and scan reports |
| [cache.py](filekeeper/cache.py) | SQLite storage and metadata checks |
| [fs.py](filekeeper/fs.py) | Path validation, streaming hashes, and byte comparison |
| [operations.py](filekeeper/operations.py) | Manifests, locks, cleanup, and recovery |
| [\_\_main\_\_.py](filekeeper/__main__.py) | Command arguments, output, and exit codes |
| [benchmarks/run.py](benchmarks/run.py) | Scan fixtures and measurements |
| [benchmarks/cleanup.py](benchmarks/cleanup.py) | Cleanup comparison against the pinned baseline |

## Limits and known issues

- Use one local filesystem with hard-link support. Nested mount points can cause moves to fail.
- The lock coordinates FileKeeper commands on the same root. It does not control other applications or overlapping roots. Path checks and mutations are separate steps, so concurrent changes can race.
- Recovery tests cover process exits, not power loss or hardware failure. Renaming the root after cleanup makes manifest validation fail.
- `status` does not verify contents or provide a consistent view during another operation.
- Cached metadata cannot detect content changes that the filesystem does not reflect in those fields. Use an uncached scan for fresh reads.
- Matching SHA-256 hashes count as equal content by default. `--verify-bytes` adds a comparison but does not prevent concurrent changes.
- Manifests record original paths. Treat them as private when the folder is private.
