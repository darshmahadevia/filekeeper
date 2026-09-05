# Learn FileKeeper

Start with `python3 demo.py`, then read the code in this order.

1. Read `main` in `filekeeper/__main__.py`. Trace a scan, a preview, and an applied cleanup. Notice that caching requires an explicit path.
2. Read `scan` in `filekeeper/core.py`. Follow a duplicate pair through size grouping and hash grouping. Explain why equal size does not prove equal content.
3. Read `digest` in `filekeeper/fs.py`. Explain chunked reads, SHA-256, and before-and-after metadata checks. Identify which filesystem races still remain.
4. Read `HashCache` in `filekeeper/cache.py`. Explain its composite key and the fingerprint fields. Run the same-size edit test and explain why change time matters when modification time is restored.
5. Read `_execute` in `filekeeper/operations.py`. Draw the file locations before linking, after linking, and after unlinking. Explain why the filesystem can be ahead of the last saved checkpoint.
6. Read `tests/test_recovery.py`. Its subprocesses call `os._exit` at selected boundaries. Explain why that tests something different from an ordinary caught exception.
7. Read `benchmarks/run.py`. Identify what falls inside elapsed timing, how peak memory is measured, and what cold cache actually means here.

## Run focused checks

```bash
python3 -m unittest discover -s tests -p test_cache.py -v
python3 -m unittest discover -s tests -p test_recovery.py -v
python3 -m unittest discover -s tests -p test_benchmarks.py -v
```

## Engineering questions

Why does SQLite use root and relative path as its key? Why include inode and change time in the fingerprint? Why rehash during cleanup even if the cached metadata matches? What happens if the cache is corrupt? Why can caching make some scans slower?

What happens if a process exits after creating a hard link but before removing the old name? Why are two independent equal-content files still a restore conflict? Why do OS locks release after a crash? Why is a saved phase insufficient to determine whether a move happened? Why can overlapping roots still need external coordination?

What does peak RSS include? Does the benchmark clear operating-system disk caches? Can you infer a general speedup from a 32 MiB fixture? Why should a CI test check benchmark correctness without requiring a specific runtime?

## Make a contribution you can explain

- Add exclusion patterns and test nested directories.
- Add a keeper rule with deterministic tie-breaking.
- Add a progress callback without coupling scanning to terminal output.
- Measure larger benchmark workloads on a second machine and compare the raw samples.

Predict the behavior, add a meaningful test, make the change, and explain the tradeoff in your own words.
