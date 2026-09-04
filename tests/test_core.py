import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from filekeeper.core import SafetyError, quarantine, restore, scan
from filekeeper.__main__ import main


class DuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, name, data=b"same content"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def pair(self):
        self.write("a.txt")
        self.write("nested/b.txt")
        return scan(self.root)

    def test_groups_use_content_and_keep_lexicographic_first(self):
        self.pair()
        self.write("c.txt", b"other value!")  # Same size, different bytes.
        report = scan(self.root)
        self.assertEqual(report["groups"][0]["keep"], "a.txt")
        self.assertEqual(report["groups"][0]["duplicates"], ["nested/b.txt"])
        self.assertEqual(report["duplicate_bytes"], 12)
        self.assertEqual(report["files_hashed"], 3)

    def test_unique_sizes_and_empty_files_need_no_hashing(self):
        self.write("one", b"x")
        self.write("two", b"xx")
        self.write("empty1", b"")
        self.write("empty2", b"")
        self.assertEqual(scan(self.root)["files_hashed"], 0)
        self.assertEqual(scan(self.root)["groups"], [])

    def test_symlinks_and_hardlink_aliases_are_skipped(self):
        self.write("a")
        (self.root / "b").symlink_to(self.root / "a")
        os.link(self.root / "a", self.root / "c")
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(scan(self.root)["files_scanned"], 1)
        self.assertEqual(scan(self.root)["groups"], [])

    def test_quarantine_restore_round_trip_and_rescan(self):
        run = quarantine(self.pair())
        self.assertTrue((self.root / "a.txt").exists())
        self.assertFalse((self.root / "nested/b.txt").exists())
        self.assertEqual(scan(self.root)["groups"], [])
        self.assertEqual(restore(run), 1)
        self.assertEqual((self.root / "nested/b.txt").read_bytes(), b"same content")
        self.assertEqual(restore(run), 0)

    def test_changed_duplicate_blocks_quarantine(self):
        report = self.pair()
        self.write("nested/b.txt", b"changed")
        with self.assertRaises(SafetyError):
            quarantine(report)
        self.assertTrue((self.root / "nested/b.txt").exists())

    def test_changed_keeper_blocks_quarantine(self):
        report = self.pair()
        self.write("a.txt", b"changed")
        with self.assertRaises(SafetyError):
            quarantine(report)

    def test_restore_never_overwrites(self):
        run = quarantine(self.pair())
        self.write("nested/b.txt", b"new work")
        with self.assertRaises(SafetyError):
            restore(run)
        self.assertEqual((self.root / "nested/b.txt").read_bytes(), b"new work")
        self.assertTrue((run / "files/00000000").exists())

    def test_tampered_quarantine_is_not_restored(self):
        run = quarantine(self.pair())
        (run / "files/00000000").write_bytes(b"changed")
        with self.assertRaises(SafetyError):
            restore(run)

    def test_manifest_cannot_escape_root(self):
        run = quarantine(self.pair())
        path = run / "manifest.json"
        data = json.loads(path.read_text())
        data["entries"][0]["original"] = "../outside.txt"
        path.write_text(json.dumps(data))
        with self.assertRaises(SafetyError):
            restore(run)

    def test_quarantine_symlink_is_refused(self):
        report = self.pair()
        (self.root / ".filekeeper-trash").symlink_to(self.root / "nested")
        with self.assertRaises(SafetyError):
            quarantine(report)

    def test_preview_does_not_move_files(self):
        self.pair()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["clean", str(self.root)]), 0)
        self.assertTrue((self.root / "nested/b.txt").exists())
        self.assertFalse((self.root / ".filekeeper-trash").exists())

    def test_json_output_is_parseable(self):
        self.pair()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["scan", str(self.root), "--json"]), 0)
        self.assertEqual(len(json.loads(output.getvalue())["groups"]), 1)

    def test_partial_quarantine_can_be_restored(self):
        self.pair()
        self.write("z.txt")
        report = scan(self.root)
        original_rename = Path.rename
        calls = 0

        def interrupted(path, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated interruption")
            return original_rename(path, target)

        with patch.object(Path, "rename", interrupted):
            with self.assertRaises(SafetyError):
                quarantine(report)
        run = next((self.root / ".filekeeper-trash").iterdir())
        self.assertEqual(restore(run), 1)
        self.assertEqual((self.root / "z.txt").read_bytes(), b"same content")
        self.assertEqual((self.root / "nested/b.txt").read_bytes(), b"same content")

    def test_scan_errors_block_changes(self):
        report = self.pair()
        report["errors"] = ["unreadable directory"]
        with self.assertRaises(SafetyError):
            quarantine(report)


if __name__ == "__main__":
    unittest.main()
