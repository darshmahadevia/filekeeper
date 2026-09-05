import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from filekeeper.core import SafetyError, quarantine, scan
from filekeeper.__main__ import main


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "files"
        self.root.mkdir()
        self.cache = self.base / "cache.sqlite3"
        (self.root / "a").write_bytes(b"same")
        (self.root / "b").write_bytes(b"same")

    def test_warm_scan_skips_hashes_and_matches_uncached(self):
        cold = scan(self.root, self.cache)
        with patch("filekeeper.core.digest", side_effect=AssertionError("unexpected hash")):
            warm = scan(self.root, self.cache)
        self.assertEqual(cold["groups"], warm["groups"])
        self.assertEqual(cold["groups"], scan(self.root)["groups"])
        self.assertEqual(cold["bytes_hashed"], 8)
        self.assertEqual(warm["bytes_hashed"], 0)
        self.assertEqual(warm["cache_hits"], 2)

    def test_same_size_edit_with_restored_mtime_invalidates(self):
        scan(self.root, self.cache)
        path = self.root / "b"
        before = path.stat()
        path.write_bytes(b"edit")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        report = scan(self.root, self.cache)
        self.assertEqual(report["groups"], [])
        self.assertEqual(report["files_hashed"], 1)
        self.assertEqual(report["cache_hits"], 1)

    def test_replacement_with_same_size_and_mtime_invalidates_inode(self):
        scan(self.root, self.cache)
        path = self.root / "b"
        before = path.stat()
        replacement = self.base / "replacement"
        replacement.write_bytes(b"edit")
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        replacement.replace(path)
        self.assertEqual(scan(self.root, self.cache)["groups"], [])

    def test_deleted_entries_are_pruned(self):
        scan(self.root, self.cache)
        (self.root / "b").unlink()
        scan(self.root, self.cache)
        with sqlite3.connect(self.cache) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM hash_cache").fetchone()[0], 0)

    def test_shared_cache_separates_roots(self):
        first = scan(self.root, self.cache)
        other = self.base / "other"
        other.mkdir()
        (other / "a").write_bytes(b"diff")
        (other / "b").write_bytes(b"diff")
        second = scan(other, self.cache)
        self.assertNotEqual(first["groups"], second["groups"])
        self.assertEqual(second["cache_hits"], 0)
        self.assertEqual(scan(self.root, self.cache)["cache_hits"], 2)

    def test_cache_inside_root_is_excluded(self):
        cache = self.root / "hashes.sqlite3"
        scan(self.root, cache)
        report = scan(self.root, cache)
        self.assertEqual(report["files_scanned"], 2)
        self.assertEqual(report["cache_hits"], 2)

    def test_uncached_scan_creates_no_cache(self):
        scan(self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["a", "b"])
        self.assertFalse(self.cache.exists())

    def test_corrupt_cache_returns_cli_error_without_moves(self):
        self.cache.write_bytes(b"not a sqlite database")
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["clean", str(self.root), "--cache", str(self.cache), "--apply"])
        self.assertEqual(code, 1)
        self.assertTrue((self.root / "b").exists())

    def test_foreign_database_is_refused(self):
        with sqlite3.connect(self.cache) as db:
            db.execute("CREATE TABLE important_data(value TEXT)")
        with self.assertRaises(SafetyError):
            scan(self.root, self.cache)
        with sqlite3.connect(self.cache) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_cleanup_rehashes_even_if_cache_is_poisoned(self):
        scan(self.root, self.cache)
        (self.root / "b").write_bytes(b"edit")
        scan(self.root, self.cache)
        with sqlite3.connect(self.cache) as db:
            value = db.execute("SELECT sha256 FROM hash_cache WHERE path='a'").fetchone()[0]
            db.execute("UPDATE hash_cache SET sha256=? WHERE path='b'", (value,))
        report = scan(self.root, self.cache)
        self.assertEqual(len(report["groups"]), 1)
        with self.assertRaises(SafetyError):
            quarantine(report)
        self.assertEqual((self.root / "b").read_bytes(), b"edit")

    def test_cache_change_during_lookup_is_reported(self):
        from filekeeper.cache import HashCache
        scan(self.root, self.cache)
        original = HashCache.lookup

        def changed(cache, root, relative, stamp):
            value = original(cache, root, relative, stamp)
            if relative == "b":
                (root / relative).write_bytes(b"edit")
            return value

        with patch.object(HashCache, "lookup", changed):
            report = scan(self.root, self.cache)
        self.assertTrue(report["errors"])
        with self.assertRaises(SafetyError):
            quarantine(report)

    def test_cli_reports_cache_metrics(self):
        for expected in (0, 2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["scan", str(self.root), "--cache", str(self.cache), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["cache_hits"], expected)
