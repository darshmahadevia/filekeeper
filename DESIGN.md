# FileKeeper design notes

FileKeeper targets a quiet local filesystem. It keeps cleanup reversible and makes every operation inspectable through a saved plan.

## Filter by size before reading content

Files with different sizes cannot be duplicates. The scanner groups paths by size, skips singleton groups, and hashes the remaining candidates with SHA-256. It sorts each matching group and keeps the first relative path. This rule is deterministic but does not infer which filename a person prefers.

Streaming reads bound the hashing buffer to 1 MiB. The scanner still stores candidate paths and metadata in memory, so its memory use grows with the file count.

## Treat hash caching as an explicit optimization

The SQLite cache belongs to a root and relative path. A hit also requires matching device, inode, size, modification time, and change time. Including change time detects ordinary edits even when a program restores modification time.

Metadata cannot prove that bytes are unchanged on every filesystem. Cleanup rehashes both the retained file and duplicate even when a scan used cached hashes. Optional byte verification adds a streaming comparison before quarantine. The extra reads trade performance for an independent equality check.

The cache is optional because it adds SQLite work when size filtering already avoids content reads. See the [benchmark methodology and samples](benchmarks/README.md) for the measured workloads and OS cache conditions.

## Keep quarantine reversible

Quarantine moves names into `.filekeeper-trash` without freeing their storage. Each move creates a hard link, syncs the destination directory, and removes the source name. Operation-boundary checkpoints save the phase and final entry states. Removing redundant per-entry manifest writes keeps serialized bytes linear in the plan size. Recovery accepts two names only when they refer to the same inode. Two independent files with equal bytes remain a conflict because overwriting either could discard a later user action.

An append journal would require replay and torn-record handling. A separate cursor would preserve finer saved progress but add another authoritative state file. FileKeeper uses operation-boundary snapshots because recovery already checks every actual file location. Saved entry states can lag, and `status` exposes current locations alongside them.

A saved checkpoint can lag behind the filesystem when the process exits. Recovery validates the whole plan and reconciles current file locations before making changes. A per-root POSIX lock excludes competing FileKeeper mutations and releases on process death.

Completed operations do not reserve their historical paths forever. A new cleanup checks whether earlier runs need recovery without requiring all paths in a completed plan to retain their old filesystem types. Explicit recovery still validates its paths.

## State the limits

The lock cannot stop other applications or commands using overlapping roots. Path checks and filesystem mutations are separate steps, so the design assumes no concurrent edits. Tests interrupt processes at move boundaries. They do not establish behavior under power loss or faulty storage.

SHA-256 grouping remains the default. Byte verification is optional, applies to cleanup, and does not turn an unlocked filesystem into a transaction. Manifest paths also reveal private filenames.

The [architecture walkthrough](ARCHITECTURE.md) follows the implementation. The [README](README.md) documents commands and current limitations.
