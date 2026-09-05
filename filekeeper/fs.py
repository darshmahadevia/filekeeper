"""Filesystem validation and streaming hashes shared by scanning and recovery."""
import hashlib
import os
import stat
from pathlib import Path

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

