# FileKeeper

[![Tests](https://github.com/darshmahadevia/filekeeper/actions/workflows/tests.yml/badge.svg)](https://github.com/darshmahadevia/filekeeper/actions/workflows/tests.yml)

FileKeeper finds files with identical contents, even when their names differ. It shows which copy it will keep and can move the extras into a hidden quarantine folder. Each cleanup records the original paths so you can put the files back.

Cleanup is a preview until you add `--apply`. Quarantine keeps the bytes on disk, so it does not free storage space. There is no permanent-delete command.

The project uses Python 3.11 or newer and has no runtime dependencies. Cleanup, recovery, and benchmarks target macOS and Linux.

## Try it

```bash
git clone https://github.com/darshmahadevia/filekeeper.git
cd filekeeper
python3 demo.py
```

The demo creates sample files in a temporary directory, quarantines a duplicate, and restores it. It removes the sample directory when it finishes.

```text
Found 1 duplicate group; 2 files hashed.
Quarantined duplicates: 0 duplicate groups remain.
Restored 1 file; all sample files are back.
```

You can run every command from the checkout with `python3 -m filekeeper`. To install the shorter `filekeeper` command:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
filekeeper --help
```

## Find and quarantine duplicates

Start with a sample folder. Leave its contents unchanged while FileKeeper is working.

```bash
python3 -m filekeeper scan ./sample-folder
python3 -m filekeeper clean ./sample-folder
python3 -m filekeeper clean ./sample-folder --apply
```

The first two commands only inspect files. The third moves extra copies into `.filekeeper-trash` and prints the directory for that cleanup run.

FileKeeper sorts the relative paths in each duplicate group and keeps the first one. It does not choose by age or filename meaning. Check the preview before applying it.

For a JSON report:

```bash
python3 -m filekeeper scan ./sample-folder --json
```

Exit codes are `0` for success, `1` for scan or operation errors, and `2` for invalid command arguments.

## Restore files or finish an interrupted cleanup

Use the run directory printed by cleanup in place of `RUN_ID`:

```bash
python3 -m filekeeper status ./sample-folder/.filekeeper-trash/RUN_ID
python3 -m filekeeper restore ./sample-folder/.filekeeper-trash/RUN_ID
```

`status` prints saved progress and current file locations as JSON. `restore` puts files back without overwriting an existing file.

If cleanup stops partway through, you can finish it:

```bash
python3 -m filekeeper resume ./sample-folder/.filekeeper-trash/RUN_ID
```

You can also use `restore` to undo the partial cleanup. If restoration itself stops, run `restore` again. Once restoration starts, `resume` refuses to restart cleanup. After an abrupt process exit, the run directory remains under `.filekeeper-trash` even if the command never printed it.

## Cache hashes between scans

Pass a SQLite database path to avoid rereading unchanged candidate files:

```bash
python3 -m filekeeper scan ./sample-folder --cache ./hashes.sqlite3
python3 -m filekeeper scan ./sample-folder --cache ./hashes.sqlite3 --json
```

Caching is optional. Without `--cache`, scans do not create a database. Keep the database outside the scanned folder. FileKeeper excludes the selected cache and its sidecar files, but a later scan without `--cache` would treat them as ordinary files.

A cached hash is reused only when the file's device, inode, size, modification time, and change time match. FileKeeper checks those values again after lookup. Each cache entry belongs to a root directory and relative path; successful scans remove entries that are no longer candidates.

Reports include `files_hashed`, `bytes_hashed`, and `cache_hits`. Byte counts cover successful content hashing during the scan. They exclude metadata reads, SQLite I/O, and cleanup verification.

Cleanup always rehashes the duplicate and retained copy before moving them, even if the scan used cached hashes. A corrupt or unsupported cache produces an error. Use a new database path to rebuild it.

## How it works

Files must have the same size to be duplicates. FileKeeper groups by size first, then reads candidates in 1 MiB chunks and compares SHA-256 hashes. Files with unique sizes need no hashing. It skips empty files, symbolic links, repeated hard-link identities, and directories named `.filekeeper-trash`.

Scanning takes O(N + B) time apart from sorting, where N is the file count and B is the candidate bytes read. It stores O(N) paths and uses a fixed-size read buffer. Cleanup performs additional content checks.

Before moving files, FileKeeper writes a manifest containing the plan and expected hashes:

```text
sample-folder/
├── notes.txt
└── .filekeeper-trash/
    └── <run-id>/
        ├── manifest.json
        └── files/
            └── 00000000
```

Each move creates a hard link at the destination, saves progress, and removes the source name. If the process stops between those steps, both names may point to the same file. Recovery checks their inode identities and finishes the remaining step. Two independent files are a conflict even if their contents match.

Checkpoints use a flushed temporary file and atomic replacement. A per-root OS lock prevents two FileKeeper operations from changing the same root at once and releases when the process exits. Recovery can read version 1 manifests and upgrades them at the next checkpoint.

## Benchmark results

Run the benchmark from the repository root:

```bash
python3 -m benchmarks.run --files 128 --size-kib 256 --repeats 5 \
  --output benchmarks/results/local.json
```

It generates repeatable folders and measures each scan in a fresh process. Results include elapsed time, peak process memory, bytes hashed, and cache hits. It checks that every mode returns the same duplicate groups.

These are local measurements on Python 3.12.12, macOS arm64, with 128 files per folder and five repetitions:

| Dataset | Uncached median | First cached median | Repeat cached median | Content bytes, uncached to repeat |
| --- | ---: | ---: | ---: | ---: |
| Unique sizes | 1.04 ms | 2.27 ms | 1.86 ms | 0 to 0 |
| Duplicate-heavy | 22.02 ms | 25.95 ms | 9.81 ms | 32 MiB to 0 |
| Mixed | 11.58 ms | 14.30 ms | 5.95 ms | 16 MiB to 0 |

Repeat cached scans were about 2.24 times as fast on the duplicate-heavy folder. Caching made the unique-size folder slower because those files already needed no hashing.

These are small workloads on one machine. The benchmark does not clear OS disk caches, and peak memory includes the Python interpreter. See the [methodology and raw samples](benchmarks/README.md) before using the numbers in a comparison.

## Tests and source

```bash
python3 -m unittest discover -s tests -v
```

The 38 tests cover duplicate detection, cache invalidation, file changes, restore conflicts, and manifest validation. Recovery tests force subprocesses to exit after linking, saving a checkpoint, and unlinking. Benchmark tests check fixture contents and matching results across modes.

GitHub Actions runs the suite on Linux with Python 3.11, 3.12, and 3.13.

| File | What to look for |
| --- | --- |
| [core.py](filekeeper/core.py) | Size grouping, cache use, and scan reports |
| [cache.py](filekeeper/cache.py) | SQLite storage and metadata checks |
| [fs.py](filekeeper/fs.py) | Path validation and streaming hashes |
| [operations.py](filekeeper/operations.py) | Manifests, locks, cleanup, and recovery |
| [\_\_main\_\_.py](filekeeper/__main__.py) | Command arguments, output, and exit codes |
| [benchmarks/run.py](benchmarks/run.py) | Dataset generation and measurements |

The [learning guide](LEARNING.md) walks through the code. [Interview notes](RESUME.md) cover the design choices and measured results.

## Limits and known issues

Use one local filesystem with hard-link support. Nested mount points can cause moves to fail. The lock coordinates FileKeeper commands using the same root; it does not control other applications or operations on overlapping roots. Checks and filesystem changes are separate steps, so concurrent path changes can still cause races.

Recovery tests cover process exits, not hardware failure or power loss. Renaming the root after cleanup makes the manifest root check fail. `status` does not verify contents or provide a consistent view while another operation is running.

Cached metadata cannot detect a content change that the filesystem does not reflect in those fields. Use an uncached scan when you need fresh reads. Matching SHA-256 hashes count as equal content without a final byte-for-byte comparison. Manifests contain original file paths and should be treated as private if the folder is private.

Two issues from the code review remain open. A completed run can block an unrelated cleanup if a recorded path later becomes a symlink. Also, every checkpoint rewrites the full manifest, so journal writes grow quadratically with the number of duplicates. Both need regression tests before changing the recovery code.
