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

    def test_history_environment_restores_private_snapshot_without_logging_contents(self):
        history = json.dumps({'version': 1, 'jobs': {'123': {'status': 'submitted', 'note': 'SYNTHETIC_PRIVATE_HISTORY'}}})
        self.env['LINKEDIN_APPLICATION_HISTORY'] = 'gzip-base64:' + base64.b64encode(gzip.compress(history.encode())).decode()
        result = prepare(self.env, self.repo)
        path = Path(result['application_history_path'])
        self.assertEqual(path.read_text(), history)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(result['application_history_configured'])
        self.assertNotIn('SYNTHETIC_PRIVATE_HISTORY', json.dumps(result))
        prepare(self.env, self.repo, check_only=True)

    def test_history_path_reuses_existing_cloud_visible_file_without_copying(self):
        history = self.root / 'restored-ledger.json'
        history.write_text(json.dumps({'version': 1, 'jobs': {'123': {'status': 'submitted'}}}))
        self.env['LINKEDIN_APPLICATION_HISTORY_PATH'] = str(history)
        result = prepare(self.env, self.repo)
        self.assertEqual(result['application_history_path'], str(history))
        self.assertFalse((self.private / 'state/application-history.json').exists())

    def test_missing_laptop_history_path_fails_before_creating_sources(self):
        self.env['LINKEDIN_APPLICATION_HISTORY_PATH'] = str(self.root / 'missing-on-this-machine.json')
        with self.assertRaisesRegex(ValueError, 'not present in this environment'):
            prepare(self.env, self.repo)
        self.assertFalse(self.private.exists())

    def test_stale_history_environment_never_overwrites_newer_restored_ledger(self):
        self.env['LINKEDIN_APPLICATION_HISTORY'] = json.dumps({'version': 1, 'jobs': {}})
        prepare(self.env, self.repo)
        ledger = self.private / 'state/ledger.json'
        current = json.dumps({'version': 1, 'jobs': {'123': {'status': 'submitted'}}})
        ledger.write_text(current)
        with self.assertRaisesRegex(ValueError, 'no history was overwritten'):
            prepare(self.env, self.repo)
        self.assertEqual(ledger.read_text(), current)

    def test_invalid_history_payload_and_repo_paths_are_rejected_before_writes(self):
        for value in ('not-json', '[]', '{"jobs": []}', '{"version": 2, "jobs": {}}',
                      '{"jobs": {}, "score": NaN}', '{"jobs": {"123": {"status": "submitted"}, "123": {}}}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare({**self.env, 'LINKEDIN_APPLICATION_HISTORY': value}, self.repo)
            self.assertFalse(self.private.exists())
        with self.assertRaises(ValueError):
            prepare({**self.env, 'LINKEDIN_APPLICATION_HISTORY_PATH': str(self.repo / 'ledger.json')}, self.repo)

    def test_absent_history_is_reported_without_inventing_an_empty_ledger(self):
        result = prepare(self.env, self.repo)
        self.assertFalse(result['application_history_configured'])
        self.assertIsNone(result['application_history_path'])
        self.assertFalse((self.private / 'state/application-history.json').exists())

    def test_check_only_does_not_materialize_history(self):
        prepare(self.env, self.repo)
        self.env['LINKEDIN_APPLICATION_HISTORY'] = json.dumps({'version': 1, 'jobs': {}})
        with self.assertRaisesRegex(ValueError, 'history is not prepared'):
            prepare(self.env, self.repo, check_only=True)
        self.assertFalse((self.private / 'state/application-history.json').exists())

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
