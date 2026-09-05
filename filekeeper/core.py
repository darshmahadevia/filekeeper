"""Group by size, then hash candidates or reuse metadata-validated cache entries."""
import os
import stat
from collections import defaultdict
from pathlib import Path
from .cache import HashCache
from .fs import SafetyError, digest, fingerprint, safe_path

TRASH = ".filekeeper-trash"


def scan(folder, cache_path=None):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise SafetyError("Scan root must be a directory")
    cache = HashCache(cache_path) if cache_path is not None else None
    success = False
    try:
        report = _scan(root, cache)
        success = not report["errors"]
        return report
    finally:
        if cache:
            cache.close(success)


def _scan(root, cache):
    sizes = defaultdict(list)
    seen = set()
    candidates = set()
    errors = []
    count = hashed = bytes_hashed = hits = 0
    excluded = {path.resolve() for path in cache.artifacts} if cache else set()

    def walk_error(exc):
        errors.append(str(exc))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if d != TRASH and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            try:
                if path in excluded:
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                count += 1
                if info.st_size:
                    stamp = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    sizes[info.st_size].append((path, stamp))
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    groups = []
    for size, paths in sorted(sizes.items()):
        if len(paths) < 2:
            continue
        hashes = defaultdict(list)
        for path, stamp in paths:
            relative = path.relative_to(root).as_posix()
            candidates.add(relative)
            try:
                safe_path(root, relative)
                if fingerprint(path) != stamp:
                    raise SafetyError(f"File changed since discovery: {path}")
                value = cache.lookup(root, relative, stamp) if cache else None
                if value is None:
                    value = digest(path)
                    hashed += 1
                    bytes_hashed += size
                    if fingerprint(path) != stamp:
                        raise SafetyError(f"File changed during scan: {path}")
                    if cache:
                        cache.store(root, relative, stamp, value)
                else:
                    if fingerprint(path) != stamp:
                        raise SafetyError(f"File changed during cache lookup: {path}")
                    hits += 1
                hashes[value].append(relative)
            except (OSError, SafetyError) as exc:
                errors.append(f"{path}: {exc}")
        for value, names in sorted(hashes.items()):
            if len(names) > 1:
                names.sort()
                groups.append({"sha256": value, "size": size, "keep": names[0], "duplicates": names[1:]})
    if cache and not errors:
        cache.prune(root, candidates)
    return {"root": str(root), "files_scanned": count, "files_hashed": hashed,
            "bytes_hashed": bytes_hashed, "cache_hits": hits,
            "duplicate_bytes": sum(g["size"] * len(g["duplicates"]) for g in groups),
            "groups": groups, "errors": errors}


# Preserve the public imports used by the original demo and callers.
from .operations import quarantine, restore, resume, status  # noqa: E402
