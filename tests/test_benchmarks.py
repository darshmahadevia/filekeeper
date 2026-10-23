import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.run import PROFILES, SCALES, clear_os_cache, cold_command, generate, run, validate, worker
from filekeeper.core import scan


class BenchmarkTests(unittest.TestCase):
    def test_fixtures_have_expected_content_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            for files in (1, 7, 8):
                for profile in PROFILES:
                    with self.subTest(profile=profile, files=files):
                        root = Path(directory) / f"{profile}-{files}"
                        expected = {}
                        total = generate(root, profile, files, 1024, oracle=expected)
                        self.assertEqual(total, sum(p.stat().st_size for p in root.rglob("*.bin")))
                        validate(worker(root, None), expected, "none")
                        self.assertEqual(scan(root)["files_scanned"], files)

    def test_benchmark_reports_consistent_results_and_cache_reads(self):
        result = run(files=4, size=128, repeats=2)
        for profile in result["profiles"]:
            samples = profile["samples"]
            self.assertEqual(len({rows[0]["result_digest"] for rows in samples.values()}), 1)
            self.assertEqual(samples["os-warm/hash-populated"][0]["bytes_hashed"], 0)
            self.assertEqual(samples["os-warm/hash-none"][0]["cache_hits"], 0)
            for mode, rows in samples.items():
                self.assertEqual(len(rows), 2)
                self.assertNotEqual(rows[0]["order"], rows[1]["order"])
                for row in rows:
                    self.assertEqual(row["errors"], 0)
                    self.assertGreater(row["peak_rss_bytes"], 0)
                    self.assertGreater(row["seconds"], 0)
                    self.assertGreaterEqual(row["os_cache_preparation"]["bytes_read"], profile["total_bytes"])
                self.assertLessEqual(profile["summary"][mode]["min_seconds"],
                                     profile["summary"][mode]["median_seconds"])

    def test_os_cold_preparation_runs_before_scan_timer(self):
        events = []
        report = {"errors": [], "groups": [], "files_scanned": 0, "files_hashed": 0,
                  "bytes_hashed": 0, "cache_hits": 0, "duplicate_bytes": 0}
        with patch("benchmarks.run.clear_os_cache", side_effect=lambda: events.append("purge") or {}), \
             patch("benchmarks.run.time.perf_counter", side_effect=lambda: events.append("clock") or len(events)), \
             patch("filekeeper.core.scan", side_effect=lambda *a, **k: events.append("scan") or report):
            worker(Path("unused"), None, "cold")
        self.assertEqual(events, ["clock", "purge", "clock", "clock", "scan", "clock"])

    def test_failed_os_purge_never_runs_scan_or_writes_results(self):
        with patch("benchmarks.run.clear_os_cache", side_effect=RuntimeError("sudo: a password is required")), \
             patch("benchmarks.run.generate") as fixture, patch("filekeeper.core.scan") as scanner:
            with self.assertRaisesRegex(RuntimeError, "password is required"):
                run(4, 128, 1, os_caches=("cold",))
            fixture.assert_not_called()
            scanner.assert_not_called()

    def test_purge_failure_is_explicit(self):
        result = subprocess.CompletedProcess([], 1, "", "sudo: a password is required")
        with patch("benchmarks.run.cold_command", return_value=["sudo", "-n", "/usr/sbin/purge"]), \
             patch("benchmarks.run.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "No cold sample was measured"):
                clear_os_cache()

    def test_macos_prefers_direct_purge_and_records_failed_fallback(self):
        success = subprocess.CompletedProcess([], 0, "", "")
        failure = subprocess.CompletedProcess([], 1, "", "Operation not permitted")
        with patch("benchmarks.run.sys.platform", "darwin"), patch("benchmarks.run.os.geteuid", return_value=501):
            with patch("benchmarks.run.subprocess.run", return_value=success) as execute:
                self.assertEqual(clear_os_cache()["command"], ["/usr/sbin/purge"])
                execute.assert_called_once()
            with patch("benchmarks.run.subprocess.run", side_effect=[failure, success]):
                result = clear_os_cache()
                self.assertEqual(result["command"], ["sudo", "-n", "/usr/sbin/purge"])
                self.assertEqual(len(result["attempts"]), 2)
            with patch("benchmarks.run.subprocess.run", return_value=failure):
                with self.assertRaisesRegex(RuntimeError, "sudo"):
                    clear_os_cache()

    def test_linux_cold_requires_root_and_syncs_before_eviction(self):
        with patch("benchmarks.run.sys.platform", "linux"), patch("benchmarks.run.os.geteuid", return_value=501):
            with self.assertRaisesRegex(RuntimeError, "drop_caches requires root"):
                clear_os_cache()
        with patch("benchmarks.run.sys.platform", "linux"), patch("benchmarks.run.os.geteuid", return_value=0):
            command = cold_command()
            self.assertEqual(command[:2], ["/bin/sh", "-c"])
            failure = subprocess.CompletedProcess(command, 2, "", "Read-only file system")
            with patch("benchmarks.run.subprocess.run", return_value=failure):
                with self.assertRaisesRegex(RuntimeError, "Read-only file system"):
                    clear_os_cache()

    def test_linux_eviction_command_writes_newline_terminated_integer(self):
        with patch("benchmarks.run.sys.platform", "linux"), patch("benchmarks.run.os.geteuid", return_value=0):
            command = cold_command()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "drop_caches"
            harmless = command[2].removeprefix("sync && ").replace(
                "/proc/sys/vm/drop_caches", shlex.quote(str(output)))
            subprocess.run([*command[:2], harmless], check=True)
            self.assertEqual(output.read_bytes(), b"3\n")

    def test_work_validation_rejects_skipped_reads_and_wrong_groups(self):
        expected = dict(files_scanned=2, files_hashed=2, bytes_hashed=256,
                        duplicate_groups=1, result_digest="expected", duplicate_bytes=128)
        correct = dict(expected, cache_hits=0, errors=0)
        for name in ("bytes_hashed", "files_hashed", "files_scanned", "duplicate_groups",
                     "result_digest", "duplicate_bytes", "cache_hits", "errors"):
            with self.subTest(name=name):
                broken = dict(correct, **{name: "incorrect"})
                with self.assertRaisesRegex(RuntimeError, name):
                    validate(broken, expected, "none")

    def test_scale_profiles_and_cli_smoke(self):
        self.assertEqual(SCALES["balanced"], (2048, 1024 * 1024))
        self.assertEqual(SCALES["small-files"][0], 20000)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            subprocess.run([sys.executable, "-m", "benchmarks.run", "--files", "4", "--size-kib", "1",
                            "--profile", "mixed", "--repeats", "1", "--output", str(output)],
                           check=True, capture_output=True)
            result = json.loads(output.read_text())
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["parameters"]["scale"], "custom")
            self.assertEqual(len(result["profiles"]), 1)
