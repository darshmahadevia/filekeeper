# FileKeeper architecture walkthrough

Run `python3 demo.py` to see a scan, quarantine, and restore on temporary files. The command uses the same public functions as the CLI.

## Commands and reports

`main` in `filekeeper/__main__.py` parses the command and calls functions exported by `filekeeper/core.py`. A scan report contains the root, duplicate groups, error messages, and counters for files hashed, bytes hashed, and cache hits.

`clean` previews that report until `--apply` is present. `--verify-bytes` adds a byte comparison to applied cleanup. Inspection and mutation remain separate so a preview can run without creating a quarantine directory.

## Candidate discovery and hashing

`scan` and `_scan` in `filekeeper/core.py` walk the root without following symbolic links. They skip empty files, repeated hard-link identities, and `.filekeeper-trash` directories. Paths are grouped by size before hashing. Matching hashes form duplicate groups, whose relative paths determine the retained copy.

`digest` in `filekeeper/fs.py` reads in 1 MiB chunks. It checks file metadata before and after hashing and uses `O_NOFOLLOW` when available. `safe_path` rejects unsafe relative paths and symlink components. These checks detect many changes but do not prevent another process from changing a path between checks.

## Persistent hash cache

`HashCache` in `filekeeper/cache.py` stores hashes in SQLite. Each entry identifies the root, relative path, and metadata fingerprint. The scanner checks metadata again after a lookup. Successful scans prune entries that are no longer candidates.

Change time matters because a same-size edit can preserve modification time. The cache tests exercise that case. A cache hit avoids content reads, but it still requires traversal, metadata checks, and SQLite work.

## Quarantine and recovery

`quarantine` in `filekeeper/operations.py` verifies the report and writes a recovery plan. `_execute` validates the entire plan before changing names. A move creates a hard link at the destination, syncs the destination directory, and unlinks the source. The manifest records operation boundaries. `status` combines those saved states with current file locations. An interruption can leave both names present.

Recovery uses the current locations and inode identities to finish or reverse a move. It refuses independent files at both locations even when their hashes match. Once restoration starts, cleanup cannot resume that run.

`tests/test_recovery.py` starts subprocesses that call `os._exit` after linking, checkpointing, and unlinking in both directions. This bypasses exception handlers and cleanup code. The tests also cover unfinished-run blocking, completed-run path changes, manifest validation, and checkpoint write growth.

## Benchmark boundaries

`benchmarks/run.py` generates deterministic folders and runs scans in fresh processes. Inspect its timing boundary before comparing results. Peak RSS includes the interpreter, and hashed byte counts exclude metadata and database I/O.

A populated FileKeeper hash database and an OS page cache are separate caches. The [benchmark guide](benchmarks/README.md) identifies both states and explains the cold-cache prerequisites. Cleanup measurements include durable checkpoints and filesystem moves, which scan timings do not measure.

## Run focused checks

```bash
python3 -m unittest discover -s tests -p test_cache.py -v
python3 -m unittest discover -s tests -p test_recovery.py -v
python3 -m unittest discover -s tests -p test_benchmarks.py -v
```

The [design notes](DESIGN.md) explain the safety and performance tradeoffs. The [README](README.md) lists commands and limits.
