import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from filekeeper.core import SafetyError, quarantine, restore, resume, scan, status
from filekeeper.operations import _lock

# Exit immediately, without finally blocks, to model losing the worker process.
CRASH_WORKER = r'''
import os, sys
from pathlib import Path
from filekeeper.core import quarantine, restore, scan
from filekeeper import operations
mode, fault, target = sys.argv[1:]
link, unlink, save = os.link, Path.unlink, operations._save

def broken_link(*args, **kwargs):
    result = link(*args, **kwargs)
    if fault == "link": os._exit(91)
    return result

def broken_unlink(path, *args, **kwargs):
    result = unlink(path, *args, **kwargs)
    if fault == "unlink" and not path.name.startswith(".manifest-"):
        os._exit(91)
    return result

def broken_save(run, data):
    save(run, data)
    if fault == "checkpoint" and any(e["state"] == "linked" for e in data["entries"]):
        os._exit(91)

os.link, Path.unlink, operations._save = broken_link, broken_unlink, broken_save
if mode == "cleanup": quarantine(scan(target))
else: restore(target)
'''


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def setup_files(self, root):
        root.mkdir(exist_ok=True)
        for name in ("a", "b", "c"):
            (root / name).write_bytes(b"matching content")
        return scan(root)

    def run_path(self, root):
        return next(path for path in (root / ".filekeeper-trash").iterdir() if path.is_dir())

    def test_process_death_at_each_move_boundary_is_recoverable(self):
        for mode in ("cleanup", "restore"):
            for fault in ("link", "checkpoint", "unlink"):
                with self.subTest(mode=mode, fault=fault):
                    root = self.root / f"{mode}-{fault}"
                    report = self.setup_files(root)
                    target = root if mode == "cleanup" else quarantine(report)
                    child = subprocess.run([sys.executable, "-c", CRASH_WORKER, mode, fault, str(target)])
                    self.assertEqual(child.returncode, 91)
                    run = self.run_path(root)
                    if mode == "cleanup":
                        resume(run)
                        self.assertEqual(status(run)["phase"], "quarantined")
                        self.assertFalse((root / "b").exists())
                        self.assertEqual(resume(run), 0)
                    restore(run)
                    self.assertEqual(status(run)["phase"], "restored")
                    self.assertEqual(restore(run), 0)
                    for name in ("a", "b", "c"):
                        self.assertEqual((root / name).read_bytes(), b"matching content")

    def test_restore_can_reverse_half_linked_quarantine(self):
        report = self.setup_files(self.root)
        child = subprocess.run([sys.executable, "-c", CRASH_WORKER, "cleanup", "link", str(self.root)])
        self.assertEqual(child.returncode, 91)
        run = self.run_path(self.root)
        restore(run)
        for name in ("a", "b", "c"):
            self.assertTrue((self.root / name).is_file())
        self.assertEqual(scan(self.root)["groups"], report["groups"])

    def test_independent_equal_content_destination_is_still_conflict(self):
        run = quarantine(self.setup_files(self.root))
        (self.root / "b").write_bytes(b"matching content")
        with self.assertRaises(SafetyError):
            restore(run)
        self.assertFalse((self.root / "c").exists())  # Whole-plan validation precedes moves.
        self.assertTrue((run / "files/00000000").exists())

    def test_resume_refuses_run_after_restore_has_started(self):
        run = quarantine(self.setup_files(self.root))
        child = subprocess.run([sys.executable, "-c", CRASH_WORKER, "restore", "link", str(run)])
        self.assertEqual(child.returncode, 91)
        with self.assertRaises(SafetyError):
            resume(run)
        restore(run)

    def test_root_lock_rejects_competing_mutation(self):
        report = self.setup_files(self.root)
        with _lock(self.root):
            with self.assertRaises(SafetyError):
                quarantine(report)
        self.assertTrue((self.root / "b").exists())

    def test_fresh_cleanup_refuses_unfinished_run(self):
        self.setup_files(self.root)
        subprocess.run([sys.executable, "-c", CRASH_WORKER, "cleanup", "link", str(self.root)], check=False)
        with self.assertRaises(SafetyError):
            quarantine(scan(self.root))
        restore(self.run_path(self.root))

    def test_v1_manifest_is_upgraded_on_restore(self):
        run = quarantine(self.setup_files(self.root))
        path = run / "manifest.json"
        data = json.loads(path.read_text())
        data["version"] = 1
        data.pop("phase")
        for entry in data["entries"]:
            entry.pop("state")
        path.write_text(json.dumps(data))
        self.assertEqual(restore(run), 2)
        self.assertEqual(json.loads(path.read_text())["version"], 2)

    def test_failed_checkpoint_leaves_previous_valid_manifest(self):
        from filekeeper.operations import _save
        run = quarantine(self.setup_files(self.root))
        data = json.loads((run / "manifest.json").read_text())
        with patch("filekeeper.operations.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                _save(run, dict(data, phase="restoring"))
        self.assertEqual(json.loads((run / "manifest.json").read_text()), data)
        self.assertEqual(restore(run), 2)

    def test_tampered_stored_path_is_rejected(self):
        run = quarantine(self.setup_files(self.root))
        path = run / "manifest.json"
        data = json.loads(path.read_text())
        data["entries"][0]["stored"] = "../outside"
        path.write_text(json.dumps(data))
        with self.assertRaises(SafetyError):
            restore(run)

    def test_missing_both_locations_is_error(self):
        run = quarantine(self.setup_files(self.root))
        (run / "files/00000000").unlink()
        with self.assertRaises(SafetyError):
            restore(run)
