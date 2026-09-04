"""Use size to prune candidates, then stream SHA-256 over matching sizes."""
import hashlib
import json
import os
import stat
import uuid
from collections import defaultdict
from pathlib import Path

TRASH = ".filekeeper-trash"
CHUNK = 1024 * 1024


class SafetyError(Exception):
    """A filesystem change or unsafe path prevented an operation."""


def fingerprint(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise SafetyError(f"Not a regular file: {path}")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def digest(path):
    before = fingerprint(path)
    h = hashlib.sha256()
    # O_NOFOLLOW prevents following a symlink swapped in before open on Unix.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != before[:2]:
            raise SafetyError(f"File changed before hashing: {path}")
        for block in iter(lambda: stream.read(CHUNK), b""):
            h.update(block)
    if fingerprint(path) != before:
        raise SafetyError(f"File changed while hashing: {path}")
    return h.hexdigest()


def safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise SafetyError(f"Unsafe relative path: {relative}")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise SafetyError(f"Symlink refused: {path}")
    if not path.resolve().is_relative_to(root):
        raise SafetyError(f"Path leaves root: {path}")
    return path


def scan(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise SafetyError("Scan root must be a directory")
    sizes = defaultdict(list)
    seen = set()
    errors = []
    count = 0
    hashed = 0

    def walk_error(exc):
        errors.append(str(exc))

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = sorted(d for d in dirs if d != TRASH and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    continue
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue  # Hard links already share storage.
                seen.add(identity)
                count += 1
                if info.st_size:
                    sizes[info.st_size].append(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    groups = []
    for size, paths in sorted(sizes.items()):
        if len(paths) < 2:
            continue
        hashes = defaultdict(list)
        for path in paths:
            try:
                safe_path(root, path.relative_to(root))
                value = digest(path)
                hashed += 1
                hashes[value].append(path.relative_to(root).as_posix())
            except (OSError, SafetyError) as exc:
                errors.append(f"{path}: {exc}")
        for value, names in sorted(hashes.items()):
            if len(names) > 1:
                names.sort()
                groups.append({"sha256": value, "size": size, "keep": names[0], "duplicates": names[1:]})
    return {"root": str(root), "files_scanned": count, "files_hashed": hashed,
            "duplicate_bytes": sum(g["size"] * len(g["duplicates"]) for g in groups),
            "groups": groups, "errors": errors}


def quarantine(report):
    if report["errors"]:
        raise SafetyError("Scan had errors. Resolve them before quarantining files.")
    root = Path(report["root"]).resolve(strict=True)
    entries = []
    for group in report["groups"]:
        keeper = safe_path(root, group["keep"])
        if digest(keeper) != group["sha256"]:
            raise SafetyError(f"Kept file changed: {keeper}")
        for relative in group["duplicates"]:
            source = safe_path(root, relative)
            if digest(source) != group["sha256"]:
                raise SafetyError(f"Duplicate changed: {source}")
            entries.append({"original": relative, "stored": f"files/{len(entries):08d}",
                            "sha256": group["sha256"], "keep": group["keep"]})
    if not entries:
        return None
    trash = safe_path(root, TRASH)
    trash.mkdir(exist_ok=True)
    run = trash / uuid.uuid4().hex
    run.mkdir(mode=0o700)
    (run / "files").mkdir()
    # Write the whole recovery plan before the first move. A partial run is restorable.
    manifest = {"version": 1, "root": str(root), "entries": entries}
    with (run / "manifest.json").open("x") as stream:
        json.dump(manifest, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for entry in entries:
            source = safe_path(root, entry["original"])
            keeper = safe_path(root, entry["keep"])
            if digest(source) != entry["sha256"] or digest(keeper) != entry["sha256"]:
                raise SafetyError(f"File changed before move: {source}")
            # The destination is inside a newly created private run directory.
            source.rename(run / entry["stored"])
    except (OSError, SafetyError) as exc:
        raise SafetyError(f"Quarantine interrupted. Restore using {run}. Cause: {exc}") from exc
    return run


def restore(folder):
    given = Path(folder).absolute()
    if given.is_symlink():
        raise SafetyError("Quarantine directory cannot be a symlink")
    run = given.resolve(strict=True)
    if run.parent.name != TRASH:
        raise SafetyError("Expected a run inside .filekeeper-trash")
    root = run.parent.parent
    safe_path(root, given.relative_to(root))
    manifest_path = safe_path(root, (run / "manifest.json").relative_to(root))
    data = json.loads(manifest_path.read_text())
    if data.get("version") != 1 or data.get("root") != str(root):
        raise SafetyError("Manifest version or root does not match")
    moves = []
    originals = set()
    stored_names = set()
    for entry in data["entries"]:
        original = Path(entry["original"])
        stored_relative = Path(entry["stored"])
        if TRASH in original.parts or len(stored_relative.parts) != 2 or stored_relative.parts[0] != "files":
            raise SafetyError("Invalid manifest entry")
        if entry["original"] in originals or entry["stored"] in stored_names:
            raise SafetyError("Duplicate manifest entry")
        originals.add(entry["original"])
        stored_names.add(entry["stored"])
        destination = safe_path(root, original)
        source = safe_path(root, (run / stored_relative).relative_to(root))
        if not source.exists():
            if destination.is_file() and digest(destination) == entry["sha256"]:
                continue  # Already restored, or this planned move never happened.
            raise SafetyError(f"Missing quarantine file: {source}")
        if destination.exists():
            raise SafetyError(f"Restore would overwrite: {destination}")
        if digest(source) != entry["sha256"]:
            raise SafetyError(f"Quarantined file changed: {source}")
        moves.append((source, destination))
    for source, destination in moves:
        destination.parent.mkdir(parents=True, exist_ok=True)
        safe_path(root, destination.relative_to(root))
        # Hard-link creation fails if another file appears at the destination.
        # If interrupted before unlink, a retry reports a conflict without losing data.
        os.link(source, destination, follow_symlinks=False)
        source.unlink()
    return len(moves)
