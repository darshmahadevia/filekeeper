# Resume and interview notes

Use these bullets after you understand the implementation, run it yourself, and make a contribution you can explain. Adapt the verbs to your own work. Do not claim ownership, users, or deployments you cannot discuss.

**FileKeeper | Python, SQLite, SHA-256, unittest, GitHub Actions**

- Built a Python duplicate-file CLI with SQLite hash caching, streaming SHA-256, and metadata-based cache invalidation.
- Implemented resumable quarantine and restore using atomic checkpoints, filesystem reconciliation, and process locks; tested abrupt process exits at three move boundaries in both directions.
- Created a reproducible benchmark suite; measured a 2.24× repeat-scan speedup and zero content bytes hashed on a local 32 MiB duplicate-heavy dataset across five repetitions.

Use the measured bullet only with its scope. The unique-size workload became slower with caching, and this benchmark does not establish a general speed guarantee. The suite contains 38 tests, including cache invalidation, recovery, and benchmark consistency checks.

## A short interview introduction

"FileKeeper finds identical files using size filtering and SHA-256. Repeat scans can reuse hashes from SQLite when file metadata matches. Cleanup still rehashes the files before moving them into quarantine. I added a journal and recovery logic so a process can stop between filesystem steps and resume without overwriting another file. I also built benchmarks to measure when caching helps and when it adds overhead."

Rewrite that in your own voice and explain your contribution.

## Before applying

Run the demo and tests, work through the learning guide, and make an extension you can discuss. Use the actual GitHub Actions result when describing CI. Link to the repository and raw benchmark results. Be ready to explain that quarantine preserves bytes and does not free disk space.
