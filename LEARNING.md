# Learn the project

Start with `python3 demo.py`, then read the code in this order. You do not need to know everything before starting.

1. Read `main` in `filekeeper/__main__.py`. Follow how argparse chooses a command and how `--apply` controls changes. Add a `--version` option as a first exercise.
2. Read `scan` in `filekeeper/core.py`. Follow one duplicate pair through the size dictionary and then the hash dictionary. Explain why equal size alone does not prove equal content.
3. Read `digest`. Learn what SHA-256 produces, why reads use chunks, and what the before-and-after file metadata checks detect. Explain why those checks still cannot eliminate every race.
4. Read `quarantine`. Find where the manifest is written and explain why it must happen before moves. Run the interrupted-quarantine test by itself.
5. Read `restore`. Explain why an existing destination blocks restoration. Learn how a hard link differs from a copy and a symbolic link.
6. Read the tests. Each temporary directory keeps the test independent from your real files. Change one rule, predict which test will fail, then run it.

Run one test:

```bash
python3 -m unittest discover -s tests -k partial_quarantine -v
```

## Make it your own

Complete at least one extension and document the design before presenting the project in an interview.

- Add `--exclude` patterns. Test nested directories and patterns that match every file.
- Add a configurable keeper rule, such as oldest modification time. Make tie-breaking deterministic and test it.
- Create a benchmark script with unique-size files and duplicate-heavy files. Report file count, total bytes, candidate bytes, elapsed time, machine, and Python version. Never infer speedup from file counts alone.
- Add a progress callback without coupling the scanning module to terminal output.
- Improve restoration after interruption between link creation and unlink. Recognize matching inode identities while preserving the rule against overwriting unrelated files.

## Questions to answer aloud

Why does the program hash candidates instead of every file? What happens if two different files have the same size? Why is chunked reading useful for a 10 GB file? Why skip hard-link aliases? Does quarantine free disk space? What happens if a duplicate changes between scanning and cleanup? What happens if the process crashes halfway through cleanup? Which filesystem races remain?

A strong explanation includes the limits. This version uses synchronous I/O, stores candidate paths in memory, and assumes a quiet local folder. Explain those choices before proposing parallel hashing or a database.
