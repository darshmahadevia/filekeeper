import tempfile
import unittest
from pathlib import Path
from benchmarks.run import PROFILES, generate, run
from filekeeper.core import scan


class BenchmarkTests(unittest.TestCase):
    def test_fixtures_have_expected_content_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            for profile, expected in zip(PROFILES, (0, 4, 2)):
                with self.subTest(profile=profile):
                    root = Path(directory) / profile
                    total = generate(root, profile, 8, 1024)
                    self.assertEqual(total, sum(p.stat().st_size for p in root.iterdir()))
                    self.assertEqual(len(scan(root)["groups"]), expected)

    def test_benchmark_reports_consistent_results_and_cache_reads(self):
        result = run(files=4, size=128, repeats=1)
        for profile in result["profiles"]:
            samples = profile["samples"]
            self.assertEqual(len({rows[0]["result_digest"] for rows in samples.values()}), 1)
            self.assertEqual(samples["warm-cache"][0]["bytes_hashed"], 0)
            self.assertGreater(samples["uncached"][0]["peak_rss_bytes"], 0)
            self.assertGreater(samples["uncached"][0]["seconds"], 0)
