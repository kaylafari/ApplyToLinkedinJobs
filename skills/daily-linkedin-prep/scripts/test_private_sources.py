import base64
import gzip
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from private_sources import configuration, prepare


class PrivateSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.private = self.root / 'private'
        self.env = {
            'LINKEDIN_PRIVATE_DIR': str(self.private),
            'LINKEDIN_CANDIDATE_CONTEXT': '# Synthetic context\nNew answer: example.\n',
            'LINKEDIN_MASTER_RESUME': 'Synthetic resume\nExample role\n',
        }

    def test_round_trip_two_separate_files_and_private_permissions(self):
        result = prepare(self.env, self.repo)
        for key, name in [('LINKEDIN_CANDIDATE_CONTEXT', 'candidate-context.md'),
                          ('LINKEDIN_MASTER_RESUME', 'master-resume.txt')]:
            path = self.private / name
            self.assertEqual(path.read_bytes(), self.env[key].encode())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.private.stat().st_mode & 0o777, 0o700)
        self.assertTrue(Path(result['state_dir']).is_dir())
        self.assertNotIn(self.env['LINKEDIN_CANDIDATE_CONTEXT'], json.dumps(result))
        self.assertFalse(result['notion_tracker_configured'])

    def test_idempotent_and_preprovisioned_files_need_no_content_variables(self):
        prepare(self.env, self.repo)
        before = {p.name: p.read_bytes() for p in self.private.iterdir() if p.is_file()}
        prepare(self.env, self.repo)
        result = prepare({'LINKEDIN_PRIVATE_DIR': str(self.private)}, self.repo, check_only=True)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.private.iterdir() if p.is_file()})
        self.assertEqual(Path(result['candidate_context_path']), self.private / 'candidate-context.md')

    def test_crlf_and_unicode_are_preserved_across_repeated_setup(self):
        self.env['LINKEDIN_CANDIDATE_CONTEXT'] = '# Synthetic context\r\nRésumé — example\r\n'
        prepare(self.env, self.repo)
        prepare(self.env, self.repo)
        self.assertEqual((self.private / 'candidate-context.md').read_bytes(),
                         self.env['LINKEDIN_CANDIDATE_CONTEXT'].encode('utf-8'))

    def test_single_line_encoded_inputs_round_trip_and_match_existing_plain_text(self):
        plain = dict(self.env)
        for key in ['LINKEDIN_CANDIDATE_CONTEXT', 'LINKEDIN_MASTER_RESUME']:
            self.env[key] = 'base64:' + base64.b64encode(self.env[key].encode()).decode()
        prepare(self.env, self.repo)
        prepare(plain, self.repo)
        self.assertEqual((self.private / 'candidate-context.md').read_bytes(),
                         plain['LINKEDIN_CANDIDATE_CONTEXT'].encode())
        self.assertEqual((self.private / 'master-resume.txt').read_bytes(),
                         plain['LINKEDIN_MASTER_RESUME'].encode())

    def test_compressed_input_round_trip(self):
        plain = dict(self.env)
        for key in ['LINKEDIN_CANDIDATE_CONTEXT', 'LINKEDIN_MASTER_RESUME']:
            self.env[key] = 'gzip-base64:' + base64.b64encode(gzip.compress(self.env[key].encode())).decode()
        prepare(self.env, self.repo)
        prepare(plain, self.repo)
        self.assertEqual((self.private / 'candidate-context.md').read_bytes(), plain['LINKEDIN_CANDIDATE_CONTEXT'].encode())

    def test_invalid_encoded_input_is_rejected_without_values_or_writes(self):
        for value in ['base64:private-invalid-SECRET', 'base64:/w==', 'base64:', 'gzip-base64:', 'gzip-base64:/w==']:
            with self.subTest(value=value), self.assertRaises(ValueError) as result:
                prepare({**self.env, 'LINKEDIN_CANDIDATE_CONTEXT': value}, self.repo)
            self.assertNotIn(value, str(result.exception))
            self.assertFalse(self.private.exists())

    def test_missing_input_writes_nothing(self):
        del self.env['LINKEDIN_MASTER_RESUME']
        with self.assertRaises(ValueError):
            prepare(self.env, self.repo)
        self.assertFalse(self.private.exists())

    def test_empty_nul_and_invalid_unicode_write_nothing(self):
        for value in ['', '  \n', 'text\0hidden', '\ud800']:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                prepare({**self.env, 'LINKEDIN_MASTER_RESUME': value}, self.repo)
            self.assertFalse(self.private.exists())

    def test_newer_context_is_never_overwritten_by_stale_environment(self):
        prepare(self.env, self.repo)
        context = self.private / 'candidate-context.md'
        context.write_text('Newer confirmed answer; preserve this.\n')
        with self.assertRaises(ValueError):
            prepare(self.env, self.repo)
        self.assertEqual(context.read_text(), 'Newer confirmed answer; preserve this.\n')
        self.assertEqual((self.private / 'master-resume.txt').read_text(), self.env['LINKEDIN_MASTER_RESUME'])

    def test_invalid_or_repo_nested_directories_rejected(self):
        for extra in [{'LINKEDIN_PRIVATE_DIR': 'relative'},
                      {'LINKEDIN_PRIVATE_DIR': str(self.repo / 'private')},
                      {'LINKEDIN_STATE_DIR': str(self.repo / 'state')}]:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                prepare({**self.env, **extra}, self.repo)
        self.assertFalse(self.private.exists())

    def test_symlink_to_repo_and_source_symlink_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare({**self.env, 'LINKEDIN_PRIVATE_DIR': str(alias)}, self.repo)
        self.private.mkdir()
        target = self.root / 'target'
        target.write_text('Do not replace')
        (self.private / 'candidate-context.md').symlink_to(target)
        with self.assertRaises(ValueError):
            prepare(self.env, self.repo)
        self.assertEqual(target.read_text(), 'Do not replace')

    def test_check_only_does_not_materialize_sources(self):
        with self.assertRaises(ValueError):
            prepare(self.env, self.repo, check_only=True)
        self.assertFalse(self.private.exists())

    def test_cli_does_not_log_contents_on_success_or_failure(self):
        script = Path(__file__).with_name('private_sources.py')
        env = {**os.environ, **self.env}
        result = subprocess.run([sys.executable, str(script), 'prepare'], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in self.env.values():
            if value != str(self.private):
                self.assertNotIn(value, result.stdout + result.stderr)
        self.env['LINKEDIN_CANDIDATE_CONTEXT'] = 'PRIVATE NEW VALUE MUST NOT APPEAR'
        result = subprocess.run([sys.executable, str(script), 'prepare'], env={**env, **self.env}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(self.env['LINKEDIN_CANDIDATE_CONTEXT'], result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
