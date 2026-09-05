"""An optional disposable hash cache. Cleanup never trusts cached content hashes."""
import json
import sqlite3
from pathlib import Path
from .fs import SafetyError


class HashCache:
    def __init__(self, path):
        supplied = Path(path).absolute()
        if supplied.is_symlink():
            raise SafetyError("Cache database cannot be a symlink")
        self.path = supplied.parent.resolve() / supplied.name
        self.artifacts = {Path(str(self.path) + suffix) for suffix in ("", "-journal", "-wal", "-shm")}
        for artifact in self.artifacts:
            if artifact.is_symlink():
                raise SafetyError("Cache paths must not contain symlinks")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version not in (0, 1) or tables - {"hash_cache"} or (version == 0 and tables):
                raise SafetyError("Not a supported FileKeeper cache database; choose a new cache path")
            self.db.execute("""CREATE TABLE IF NOT EXISTS hash_cache (
                root TEXT NOT NULL, path TEXT NOT NULL, fingerprint TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY (root, path))""")
            self.db.execute("PRAGMA user_version=1")
            self.db.commit()
        except BaseException:
            self.db.close()
            raise

    def lookup(self, root, relative, fingerprint):
        row = self.db.execute("SELECT fingerprint,sha256 FROM hash_cache WHERE root=? AND path=?",
                              (str(root), str(relative))).fetchone()
        if row is None or row[0] != json.dumps(fingerprint):
            return None
        value = row[1]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            return None
        return value

    def store(self, root, relative, fingerprint, value):
        self.db.execute("""INSERT INTO hash_cache VALUES (?,?,?,?)
            ON CONFLICT(root,path) DO UPDATE SET fingerprint=excluded.fingerprint,sha256=excluded.sha256""",
                        (str(root), str(relative), json.dumps(fingerprint), value))

    def prune(self, root, candidates):
        paths = self.db.execute("SELECT path FROM hash_cache WHERE root=?", (str(root),)).fetchall()
        self.db.executemany("DELETE FROM hash_cache WHERE root=? AND path=?",
                            ((str(root), row[0]) for row in paths if row[0] not in candidates))

    def close(self, success):
        try:
            if success:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()
