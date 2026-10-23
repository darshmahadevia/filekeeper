import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from filekeeper import fs, operations
from filekeeper.__main__ import main
from filekeeper.core import SafetyError, quarantine, restore, resume, scan


class ByteVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in ('a', 'b', 'c'):
            (self.root / name).write_bytes(b'original content')

    def test_normal_verified_cleanup_and_restore(self):
        run = quarantine(scan(self.root), verify_bytes=True)
        self.assertTrue(json.loads((run / 'manifest.json').read_text())['verify_bytes'])
        self.assertFalse((self.root / 'b').exists())
        self.assertEqual(restore(run), 2)

    def test_digest_collision_rejected_by_bytes(self):
        (self.root / 'c').write_bytes(b'different bytes!')
        with patch('filekeeper.core.digest', return_value='0' * 64), patch('filekeeper.operations.digest', return_value='0' * 64):
            report = scan(self.root)
            self.assertEqual(len(report['groups'][0]['duplicates']), 2)
            with self.assertRaisesRegex(SafetyError, 'Byte comparison failed'):
                quarantine(report, verify_bytes=True)
        self.assertTrue((self.root / 'b').exists())
        self.assertTrue((self.root / 'c').exists())

    def test_default_does_not_compare_bytes(self):
        with patch('filekeeper.operations.same_bytes', side_effect=AssertionError('unexpected byte verification')):
            run = quarantine(scan(self.root))
            self.assertEqual(restore(run), 2)

    def test_final_comparison_is_repeated_immediately_before_each_move(self):
        compare = operations.same_bytes
        calls = []
        def checking(left, right):
            calls.append(left.name)
            if len(calls) == 3:
                (self.root / 'b').write_bytes(b'changed content!')
            return compare(left, right)
        with patch('filekeeper.operations.same_bytes', checking):
            with self.assertRaisesRegex(SafetyError, 'Byte comparison failed'):
                quarantine(scan(self.root), verify_bytes=True)
        self.assertEqual(calls, ['b', 'c', 'b'])
        self.assertTrue((self.root / 'b').exists())
        self.assertTrue((self.root / 'c').exists())

    def test_comparison_io_error_never_removes_source(self):
        with patch('filekeeper.operations.same_bytes', side_effect=OSError('read failed')):
            with self.assertRaisesRegex(SafetyError, 'read failed'):
                quarantine(scan(self.root), verify_bytes=True)
        self.assertTrue((self.root / 'b').exists())
        self.assertTrue((self.root / 'c').exists())

    def test_resume_retains_verification_policy(self):
        report = scan(self.root)
        with patch('filekeeper.operations.os.link', side_effect=OSError('interrupted')):
            with self.assertRaises(SafetyError):
                quarantine(report, verify_bytes=True)
        run = next((self.root / '.filekeeper-trash').glob('*/manifest.json')).parent
        (self.root / 'c').write_bytes(b'different bytes!')
        with patch('filekeeper.operations.digest', return_value=report['groups'][0]['sha256']):
            with self.assertRaisesRegex(SafetyError, 'Byte comparison failed'):
                resume(run)
        self.assertTrue((self.root / 'b').exists())
        self.assertTrue((self.root / 'c').exists())

    def test_comparison_detects_mutation_during_read(self):
        real_fdopen = os.fdopen
        class MutatingReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                self.stream.__enter__()
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def fileno(self):
                return self.stream.fileno()
            def read(self, size):
                data = self.stream.read(size)
                (self.root / 'b').write_bytes(b'mutated')
                return data
        def opened(fd, mode):
            wrapper = MutatingReader(real_fdopen(fd, mode))
            wrapper.root = self.root
            return wrapper
        with patch('filekeeper.fs.os.fdopen', opened):
            with self.assertRaisesRegex(SafetyError, 'changed during byte comparison'):
                fs.same_bytes(self.root / 'a', self.root / 'b')

    def test_comparison_detects_replacement_before_open(self):
        real_open = os.open
        def replaced(path, flags):
            if path == self.root / 'a':
                (self.root / 'replacement').write_bytes(b'original content')
                os.replace(self.root / 'replacement', self.root / 'a')
            return real_open(path, flags)
        with patch('filekeeper.fs.os.open', replaced):
            with self.assertRaisesRegex(SafetyError, 'changed before byte comparison'):
                fs.same_bytes(self.root / 'a', self.root / 'b')

    def test_comparison_rejects_symlinks_and_handles_chunk_boundaries(self):
        (self.root / 'c').unlink()
        (self.root / 'c').symlink_to(self.root / 'a')
        with self.assertRaises(SafetyError):
            fs.same_bytes(self.root / 'a', self.root / 'c')
        for name in ('a', 'b'):
            (self.root / name).write_bytes(b'x' * (fs.CHUNK + 1))
        self.assertTrue(fs.same_bytes(self.root / 'a', self.root / 'b'))
        (self.root / 'b').write_bytes(b'x' * fs.CHUNK + b'y')
        self.assertFalse(fs.same_bytes(self.root / 'a', self.root / 'b'))

    def test_cli_verify_bytes_flag(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['clean', str(self.root), '--apply', '--verify-bytes', '--json']), 0)
        run = Path(json.loads(output.getvalue())['quarantine_run'])
        self.assertTrue(json.loads((run / 'manifest.json').read_text())['verify_bytes'])

    def test_invalid_manifest_verification_policy_is_rejected(self):
        run = quarantine(scan(self.root))
        path = run / 'manifest.json'
        data = json.loads(path.read_text())
        data['verify_bytes'] = 'false'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(SafetyError, 'verification policy'):
            resume(run)
