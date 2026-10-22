import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from handoff_receipt import build, write_once, REPORTS, FLAGS


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.pdf = self.root / 'Kayla_Farivar_Resume.pdf'
        self.pdf.write_bytes(b'%PDF-1.4\nunit test fixture\n%%EOF')
        digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        for name in REPORTS:
            (self.root / name).write_text('fixture')
        self.manifest = {'job_id': '12345', 'status': 'qa_passed',
                         'qa': dict.fromkeys(('evidence', 'ats_text', 'layout', 'page_count'), True),
                         'artifact_path': str(self.pdf), 'sha256': digest}
        self.receipt = dict.fromkeys(FLAGS, True)
        self.receipt.update({'issuer': 'parent', 'handoff_kind': 'done', 'job_id': '12345',
                             'run_id': 'applylinkedin-2026-10-01', 'company': 'Example',
                             'title': 'Engineer', 'preparation_date': '2026-10-01',
                             'review_url': 'https://example.com/jobs/12345?gh_jid=12345&utm_source=x',
                             'application_tab': {'id': '42', 'kind': 'application',
                                                 'url': 'https://example.com/jobs/12345?gh_jid=12345'},
                             'resume_path': str(self.pdf), 'resume_sha256': digest,
                             'form_evidence_reference': '/local/form-receipt.json',
                             'attachment_verified': True, 'preparation_complete': True,
                             'ledger_status': 'ready_for_review', 'validation_errors': [],
                             'final_control_visible': True, 'submission_performed': False})

    def test_done_deterministic_private_answers_excluded(self):
        self.receipt['private_answers'] = {'phone': 'SECRET_PHONE', 'salary': 'SECRET_SALARY'}
        payload = build(self.receipt, self.manifest)
        self.assertEqual(payload, build(copy.deepcopy(self.receipt), self.manifest))
        self.assertIsNone(payload['properties']['Application Date'])
        self.assertNotIn('ID', payload['properties'])
        self.assertNotIn('SECRET', json.dumps(payload))
        self.assertEqual(payload['properties']['Company Website'], 'https://example.com/jobs/12345?gh_jid=12345')
        self.assertFalse(payload['notion_saved_verified'])

    def test_manual_upload_not_counted_done(self):
        r = self.receipt
        r.update(handoff_kind='manual_upload', attachment_verified=False, ledger_status='blocked',
                 missing_upload_reason='chooser unavailable', upload_recovery_exhausted=True,
                 upload_evidence_reference='/local/upload.json')
        out = build(r, self.manifest)
        self.assertEqual(out['properties']['Application Status'], 'Preparation Blocked')
        self.assertIn('Needs résumé upload', out['body'])
        self.assertFalse(out['attachment_verified'])
        for key in ('missing_upload_reason', 'upload_recovery_exhausted', 'upload_evidence_reference'):
            with self.subTest(key=key):
                bad = dict(r)
                del bad[key]
                with self.assertRaises(ValueError):
                    build(bad, self.manifest)

    def test_flags_exact_booleans(self):
        for flag in FLAGS:
            for value in (False, 1, 'true', None):
                with self.subTest(flag=flag, value=value):
                    r = dict(self.receipt, **{flag: value})
                    with self.assertRaises(ValueError):
                        build(r, self.manifest)

    def test_missing_form_and_url_mismatch(self):
        for tab in (None, {}, {'id': '1', 'kind': 'listing', 'url': self.receipt['review_url']},
                    {'id': '1', 'kind': 'application', 'url': 'https://example.com/other'}):
            with self.subTest(tab=tab), self.assertRaises(ValueError):
                build(dict(self.receipt, application_tab=tab), self.manifest)

    def test_hash_job_and_qa_mismatch(self):
        for updates in ({'sha256': '0' * 64}, {'job_id': '999'}, {'status': 'draft'}, {'qa': {}}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                build(self.receipt, dict(self.manifest, **updates))
        with self.assertRaises(ValueError):
            build(dict(self.receipt, resume_sha256='0' * 64), self.manifest)
        self.pdf.write_bytes(b'not a PDF')
        with self.assertRaises(ValueError):
            build(self.receipt, self.manifest)

    def test_missing_supporting_artifact(self):
        (self.root / REPORTS[0]).unlink()
        with self.assertRaises(ValueError):
            build(self.receipt, self.manifest)

    def test_protected_status_and_submission(self):
        for label in ('Application Submitted', 'Interview', 'Rejected', 'submitted', 'Interview Scheduled', 'Offer Received'):
            with self.subTest(label=label), self.assertRaises(ValueError):
                build(dict(self.receipt, existing_statuses=[label]), self.manifest)
        with self.assertRaises(ValueError):
            build(dict(self.receipt, submission_performed=True), self.manifest)

    def test_done_attachment_and_ready_questions(self):
        for updates in ({'attachment_verified': False}, {'attachment_verified': 1},
                        {'preparation_complete': False}, {'ledger_status': 'blocked'},
                        {'validation_errors': ['invalid']}, {'final_control_visible': False},
                        {'unanswered_question_labels': ['salary']}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                build(dict(self.receipt, **updates), self.manifest)

    def test_prepared_with_questions_not_private_text(self):
        r = dict(self.receipt, ledger_status='prepared_for_review', final_control_visible=False,
                 unanswered_question_labels=['SECRET embedded in a question'])
        out = build(r, self.manifest)
        self.assertNotIn('SECRET', json.dumps(out))
        self.assertIn('Required unanswered questions: 1', out['body'])

    def test_atomic_idempotent_conflict(self):
        out = build(self.receipt, self.manifest)
        target = self.root / 'handoff.json'
        self.assertEqual(write_once(target, out), 'created')
        original = target.read_bytes()
        self.assertEqual(write_once(target, out), 'unchanged')
        self.assertEqual(target.read_bytes(), original)
        with self.assertRaises(ValueError):
            write_once(target, dict(out, job_id='999'))
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.handoff.json-*')), [])

    def test_explicit_optional_question_is_not_required_or_ready_blocker(self):
        r = dict(self.receipt, required_unresolved=[], unanswered_question_labels=['Salary expectation'],
                 question_labels_safe_for_tracking=True)
        r['private_answers'] = {'salary': 'SECRET_ANSWER'}
        out = build(r, self.manifest)
        self.assertIn('Required unanswered questions: 0', out['body'])
        self.assertIn('Optional/other unanswered questions: 1 (Salary expectation)', out['body'])
        self.assertNotIn('SECRET_ANSWER', json.dumps(out))

    def test_required_and_optional_counts_and_labels(self):
        r = dict(self.receipt, ledger_status='prepared_for_review', required_unresolved=['Start date'],
                 unanswered_question_labels=['Start date', 'Salary expectation'],
                 question_labels_safe_for_tracking=True)
        out = build(r, self.manifest)
        self.assertIn('Required unanswered questions: 1 (Start date)', out['body'])
        self.assertIn('Optional/other unanswered questions: 1 (Salary expectation)', out['body'])
        with self.assertRaises(ValueError):
            build(dict(r, ledger_status='ready_for_review'), self.manifest)

    def test_legacy_missing_required_subset_stays_conservative(self):
        r = dict(self.receipt, ledger_status='prepared_for_review',
                 unanswered_question_labels=['Salary expectation'], question_labels_safe_for_tracking=True)
        out = build(r, self.manifest)
        self.assertIn('Required unanswered questions: 1 (Salary expectation)', out['body'])
        self.assertIn('Optional/other unanswered questions: 0', out['body'])

    def test_invalid_required_subsets_rejected(self):
        for required in (None, 'salary', ['Other'], ['Salary', 'Salary'], [1]):
            with self.subTest(required=required), self.assertRaises(ValueError):
                build(dict(self.receipt, ledger_status='prepared_for_review',
                           unanswered_question_labels=['Salary'], required_unresolved=required), self.manifest)

    def test_unsanitized_labels_stay_local_even_when_optional(self):
        for flag in (None, False, 1, 'true'):
            out = build(dict(self.receipt, required_unresolved=[],
                            unanswered_question_labels=['SECRET embedded answer'],
                            question_labels_safe_for_tracking=flag), self.manifest)
            self.assertNotIn('SECRET', json.dumps(out))
            self.assertIn('Optional/other unanswered questions: 1', out['body'])


if __name__ == '__main__':
    unittest.main()
