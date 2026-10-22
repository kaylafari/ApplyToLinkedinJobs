import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from acceptance_audit import audit
import test_acceptance_audit


class RunScopeTests(unittest.TestCase):
    setUp = test_acceptance_audit.AcceptanceTests.setUp

    def items(self):
        return [(self.first, self.m1), (self.second, self.m2)]

    def test_exact_two_same_custom_run(self):
        for receipt in (self.first, self.second):
            receipt['run_id'] = 'applylinkedin-test-oct1-fresh2'
        result = audit(self.items(), 'test-two', 2, run_id='applylinkedin-test-oct1-fresh2')
        self.assertTrue(result['success'])
        self.assertEqual(result['accepted_run_id'], 'applylinkedin-test-oct1-fresh2')

    def test_old_run_rejected_even_with_matching_cycle(self):
        result = audit(self.items(), 'test-two', 2, run_id='applylinkedin-test-oct1-fresh2')
        self.assertFalse(result['success'])
        self.assertEqual(result['successful_applications'], 0)
        self.assertEqual(len(result['rejected']), 2)
        self.assertIn('another ledger run', result['rejected'][0]['reason'])

    def test_mixed_runs_rejected_without_expected_run(self):
        self.second['run_id'] = 'another-run'
        result = audit(self.items(), 'test-two', 1)
        self.assertFalse(result['success'])
        self.assertEqual(result['successful_applications'], 1)
        self.assertIn('Mixed ledger runs', result['rejected'][0]['reason'])

    def test_mixed_runs_rejected_with_expected_run(self):
        self.second['run_id'] = 'another-run'
        result = audit(self.items(), 'test-two', 2, run_id=self.first['run_id'])
        self.assertEqual(result['successful_applications'], 1)
        self.assertFalse(result['success'])

    def test_invalid_receipt_does_not_select_accepted_run(self):
        self.first.update(run_id='old-run', final_live_verification=False)
        self.second['run_id'] = 'fresh-run'
        result = audit(self.items(), 'test-two', 1)
        self.assertEqual(result['accepted_run_id'], 'fresh-run')
        self.assertEqual(result['successful_applications'], 1)
        self.assertFalse(result['success'])  # Any rejected receipt still fails audit.

    def test_three_successes_overflows_exact_two(self):
        third = copy.deepcopy(self.second)
        third.update(job_id='77777', review_url='https://example.com/jobs/77777',
                     application_tab={'id': '44', 'kind': 'application', 'url': 'https://example.com/jobs/77777'})
        folder = self.fixture.root / 'third'
        shutil.copytree(self.fixture.root / 'second', folder)
        pdf = str(folder / 'Kayla_Farivar_Resume.pdf')
        third['resume_path'] = pdf
        manifest = dict(self.m2, job_id='77777', artifact_path=pdf)
        result = audit(self.items() + [(third, manifest)], 'test-two', 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['successful_applications'], 3)
        self.assertEqual(result['rejected'], [])

    def test_manual_upload_same_run_still_separate_from_strict_ready(self):
        self.second.update(handoff_kind='manual_upload', attachment_verified=False, ledger_status='blocked',
                           missing_upload_reason='chooser failed', upload_recovery_exhausted=True,
                           upload_evidence_reference='observed-failure.json')
        result = audit(self.items(), 'test-two', 2, run_id=self.first['run_id'])
        self.assertTrue(result['success'])
        self.assertEqual(result['attachments_verified'], 1)
        self.assertEqual(self.second['ledger_status'], 'blocked')

    def test_invalid_expected_run(self):
        for value in ('', ' ', 2, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit(self.items(), 'test-two', 2, run_id=value)

    def test_cli_expected_run_is_enforced(self):
        args = []
        for index, (receipt, manifest) in enumerate(self.items()):
            manifest_path = self.fixture.root / f'manifest-{index}.json'
            manifest_path.write_text(json.dumps(manifest))
            receipt_path = self.fixture.root / f'receipt-{index}.json'
            receipt_path.write_text(json.dumps(dict(receipt, resume_manifest=str(manifest_path))))
            args.extend(['--receipt', str(receipt_path)])
        command = [sys.executable, str(Path(__file__).with_name('acceptance_audit.py')),
                   '--cycle-id', 'test-two', '--target', '2', '--run-id']
        wrong = subprocess.run(command + ['other-run'] + args, capture_output=True, text=True)
        self.assertEqual(wrong.returncode, 1, wrong.stderr)
        self.assertEqual(json.loads(wrong.stdout)['successful_applications'], 0)
        correct = subprocess.run(command + [self.first['run_id']] + args, capture_output=True, text=True)
        self.assertEqual(correct.returncode, 0, correct.stderr)
        self.assertTrue(json.loads(correct.stdout)['success'])


if __name__ == '__main__':
    unittest.main()
