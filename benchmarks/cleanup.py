"""Run from the repository root with python benchmarks/cleanup.py."""
import hashlib
import cProfile
import pstats
import platform
import subprocess
import types
import os
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filekeeper import operations
from filekeeper.core import scan

BASELINE_SHA = '38b654050f26de074ee51fe809b601e3aae57d82'
BASELINE_COMMAND = ['git', 'show', f'{BASELINE_SHA}:filekeeper/operations.py']
baseline = types.ModuleType('filekeeper._cleanup_baseline')
baseline.__package__ = 'filekeeper'
exec(compile(subprocess.check_output(BASELINE_COMMAND, text=True), '<baseline-operations>', 'exec'), baseline.__dict__)

def measure(module, label, iteration, profiler=None):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        for group in range(250):
            payload = (f'document {group:04d}\n'.encode() * 4096)
            for copy in range(5):
                folder = root / f'copy-{copy}'
                folder.mkdir(exist_ok=True)
                (folder / f'document-{group:04d}.txt').write_bytes(payload)
        report = scan(root)
        if profiler is not None:
            profiler.enable()
        start = time.perf_counter()
        run = module.quarantine(report)
        elapsed = time.perf_counter() - start
        if profiler is not None:
            profiler.disable()
        stored = list((run / 'files').iterdir())
        assert len(stored) == 1000
        assert len(scan(root)['groups']) == 0
        manifest = json.loads((run / 'manifest.json').read_text())
        assert all((run / entry['stored']).read_bytes() == (root / entry['keep']).read_bytes()
                   for entry in manifest['entries'])
        return dict(side=label, iteration=iteration, seconds=elapsed, moved=len(stored), errors=0,
                    duplicate_bytes=report['duplicate_bytes'])

if __name__ == '__main__':
    if '--profile' in sys.argv:
        profiles = {}
        for label, module in [('before', baseline), ('after', operations)]:
            profiler = cProfile.Profile()
            measure(module, label, 0, profiler)
            stats = pstats.Stats(profiler)
            profiles[label] = [dict(file=key[0], line=key[1], function=key[2], primitive_calls=value[0],
                                    calls=value[1], self_seconds=value[2], cumulative_seconds=value[3])
                               for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]]
        Path('benchmarks/cleanup-profile.json').write_text(json.dumps(profiles, indent=2) + '\n')
        raise SystemExit(0)
    load_start = os.getloadavg()
    rows = []
    for iteration in range(5):
        for label, module in [('before', baseline), ('after', operations)]:
            row = measure(module, label, iteration)
            rows.append(row)
            print(json.dumps(row), flush=True)
    artifact = dict(baseline_sha=BASELINE_SHA, baseline_command=BASELINE_COMMAND,
                    command='python benchmarks/cleanup.py',
                    environment=dict(python=sys.version, platform=platform.platform(),
                                     cores=os.cpu_count(), load_start=load_start, load_end=os.getloadavg()),
                    source_hashes=dict(baseline=hashlib.sha256(subprocess.check_output(BASELINE_COMMAND)).hexdigest(),
                                       after=hashlib.sha256(Path('filekeeper/operations.py').read_bytes()).hexdigest()),
                    workload=dict(groups=250, copies=5, duplicates=1000, payload='document NNNN newline repeated 4096 times',
                                  timing='quarantine(scan_report), scan and fixture creation excluded'),
                    runs=rows)
    Path('benchmarks/cleanup-results.json').write_text(json.dumps(artifact, indent=2) + '\n')
    for label in ('before', 'after'):
        values = [row['seconds'] for row in rows if row['side'] == label]
        print(label, 'median', statistics.median(values), 'range', min(values), max(values))
