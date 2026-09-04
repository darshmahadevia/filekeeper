"""Run a complete scan, quarantine and restore using temporary sample files."""
import tempfile
from pathlib import Path
from filekeeper.core import quarantine, restore, scan

with tempfile.TemporaryDirectory(prefix="filekeeper-demo-") as folder:
    root = Path(folder)
    (root / "notes.txt").write_text("Python filesystem demo\n")
    (root / "notes-copy.txt").write_text("Python filesystem demo\n")
    (root / "unique.txt").write_text("Keep this separate document.\n")
    report = scan(root)
    print(f"Found {len(report['groups'])} duplicate group; {report['files_hashed']} files hashed.")
    run = quarantine(report)
    print(f"Quarantined duplicates: {len(scan(root)['groups'])} duplicate groups remain.")
    print(f"Restored {restore(run)} file; all sample files are back.")
