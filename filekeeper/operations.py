"""Journaled, restartable quarantine and restore for a quiet local filesystem."""
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from .fs import SafetyError, digest, safe_path

TRASH = ".filekeeper-trash"
PHASES = {"quarantining", "quarantined", "restoring", "restored"}


@contextmanager
def _lock(root):
    # OS locks are released on process death; the lock file is never deleted.
    try:
        import fcntl
    except ImportError as exc:
        raise SafetyError("Cleanup and recovery require POSIX filesystem locks") from exc
    trash = safe_path(root, TRASH)
    trash.mkdir(exist_ok=True)
    path = safe_path(root, f"{TRASH}/.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SafetyError("Another cleanup or recovery is active for this root") from exc
        yield
    finally:
        os.close(fd)


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(run, data):
    temporary = run / f".manifest-{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, run / "manifest.json")
        _sync(run)
    finally:
        temporary.unlink(missing_ok=True)


def _load(folder):
    given = Path(folder).absolute()
    run = given.resolve(strict=True)
    if run.parent.name != TRASH:
        raise SafetyError("Expected a run inside .filekeeper-trash")
    root = run.parent.parent
    safe_path(root, given.relative_to(root))
    manifest = safe_path(root, (run / "manifest.json").relative_to(root))
    try:
        data = json.loads(manifest.read_text())
        if data["version"] not in (1, 2) or data["root"] != str(root):
            raise SafetyError("Manifest version or root does not match")
        if data["version"] == 1:
            data = dict(data, version=2, phase="quarantining")
        if data["phase"] not in PHASES or not isinstance(data["entries"], list):
            raise SafetyError("Invalid operation phase or entries")
        originals, stored_names = set(), set()
        for entry in data["entries"]:
            original = Path(entry["original"])
            stored = Path(entry["stored"])
            keeper = Path(entry["keep"])
            if (TRASH in original.parts or TRASH in keeper.parts or original == keeper
                    or len(stored.parts) != 2 or stored.parts[0] != "files"
                    or len(stored.name) != 8 or not stored.name.isascii() or not stored.name.isdecimal()):
                raise SafetyError("Invalid manifest path")
            safe_path(root, original)
            safe_path(root, keeper)
            safe_path(root, (run / stored).relative_to(root))
            if str(original) in originals or str(stored) in stored_names:
                raise SafetyError("Duplicate manifest entry")
            originals.add(str(original))
            stored_names.add(str(stored))
            value = entry["sha256"]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise SafetyError("Invalid content hash")
            entry.setdefault("state", "planned")
            if entry["state"] not in {"planned", "linked", "quarantined", "restored"}:
                raise SafetyError("Invalid entry state")
        # A retained copy cannot also be scheduled for removal in this run.
        if any(str(Path(entry["keep"])) in originals for entry in data["entries"]):
            raise SafetyError("A retained copy is also a duplicate in this manifest")
    except (KeyError, TypeError, ValueError) as exc:
        raise SafetyError("Malformed recovery manifest") from exc
    return root, run, data


def _paths(root, run, entry):
    return (safe_path(root, entry["original"]),
            safe_path(root, (run / entry["stored"]).relative_to(root)))


def _check(root, run, entry, direction):
    original, stored = _paths(root, run, entry)
    has_original, has_stored = original.exists(), stored.exists()
    if not has_original and not has_stored:
        raise SafetyError(f"Both file locations are missing: {original}")
    if has_original and has_stored and not os.path.samefile(original, stored):
        raise SafetyError(f"Conflicting files at original and quarantine locations: {original}")
    content = stored if has_stored else original
    if digest(content) != entry["sha256"]:
        raise SafetyError(f"File content changed: {content}")
    if direction == "quarantining" and has_original:
        keeper = safe_path(root, entry["keep"])
        if digest(keeper) != entry["sha256"]:
            raise SafetyError(f"Retained copy changed: {keeper}")
    return original, stored, has_original, has_stored


def _execute(root, run, data, direction):
    if direction == "quarantining" and data["phase"] in {"restoring", "restored"}:
        raise SafetyError("Restoration has started; finish restore instead of resuming cleanup")
    # Validate the entire plan before beginning new changes. The filesystem, not
    # a possibly stale checkpoint, determines which half of a move remains.
    for entry in data["entries"]:
        _check(root, run, entry, direction)
    data["phase"] = direction
    _save(run, data)
    changed = 0
    for entry in data["entries"]:
        original, stored, has_original, has_stored = _check(root, run, entry, direction)
        source, destination = (original, stored) if direction == "quarantining" else (stored, original)
        source_exists = has_original if direction == "quarantining" else has_stored
        destination_exists = has_stored if direction == "quarantining" else has_original
        if source_exists:
            if not destination_exists:
                destination.parent.mkdir(parents=True, exist_ok=True)
                safe_path(root, destination.relative_to(root))
                os.link(source, destination, follow_symlinks=False)
                _sync(destination.parent)
            entry["state"] = "linked"
            _save(run, data)
            # Recheck identity before deleting a name. Independent equal-content
            # files are a conflict; only two names for this same inode are safe.
            safe_path(root, source.relative_to(root))
            safe_path(root, destination.relative_to(root))
            if not os.path.samefile(source, destination):
                raise SafetyError(f"File identity changed: {source}")
            source.unlink()
            _sync(source.parent)
            changed += 1
        entry["state"] = "quarantined" if direction == "quarantining" else "restored"
        _save(run, data)
    data["phase"] = "quarantined" if direction == "quarantining" else "restored"
    _save(run, data)
    return changed


def quarantine(report):
    if report["errors"]:
        raise SafetyError("Scan had errors. Resolve them before quarantining files.")
    root = Path(report["root"]).resolve(strict=True)
    entries = []
    for group in report["groups"]:
        for relative in group["duplicates"]:
            if TRASH in Path(relative).parts or TRASH in Path(group["keep"]).parts:
                raise SafetyError("Cannot quarantine internal files")
            for name in (relative, group["keep"]):
                if digest(safe_path(root, name)) != group["sha256"]:
                    raise SafetyError(f"File changed since scanning: {name}")
            entries.append({"original": relative, "stored": f"files/{len(entries):08d}",
                            "sha256": group["sha256"], "keep": group["keep"], "state": "planned"})
    if not entries:
        return None
    with _lock(root):
        # Avoid starting another operation while a previous run needs recovery.
        for manifest in (root / TRASH).glob("*/manifest.json"):
            _, _, previous = _load(manifest.parent)
            if previous["phase"] in {"quarantining", "restoring"}:
                raise SafetyError(f"Recover the unfinished run first: {manifest.parent}")
        run = root / TRASH / uuid.uuid4().hex
        run.mkdir(mode=0o700)
        (run / "files").mkdir()
        data = {"version": 2, "root": str(root), "phase": "quarantining", "entries": entries}
        _save(run, data)
        try:
            _execute(root, run, data, "quarantining")
        except (OSError, SafetyError) as exc:
            raise SafetyError(f"Cleanup interrupted. Resume or restore {run}. Cause: {exc}") from exc
    return run


def _recover(folder, direction):
    root, run, _ = _load(folder)
    with _lock(root):
        root, run, data = _load(folder)
        try:
            return _execute(root, run, data, direction)
        except (OSError, SafetyError) as exc:
            command = "resume" if direction == "quarantining" else "restore"
            raise SafetyError(f"Recovery stopped. Inspect the conflict, then run {command} on {run}. Cause: {exc}") from exc


def resume(folder):
    """Finish an interrupted quarantine. Never restart a restored run."""
    return _recover(folder, "quarantining")


def restore(folder):
    """Restore or continue restoring a run, reconciling interrupted hard links."""
    return _recover(folder, "restoring")


def status(folder):
    """Read the last checkpoint and current locations without changing files."""
    root, run, data = _load(folder)
    entries = []
    for entry in data["entries"]:
        original, stored = _paths(root, run, entry)
        entries.append({"original": entry["original"], "checkpoint": entry["state"],
                        "original_exists": original.exists(), "quarantine_exists": stored.exists(),
                        "same_inode": original.exists() and stored.exists() and os.path.samefile(original, stored)})
    return {"run": str(run), "phase": data["phase"], "entries": entries}
