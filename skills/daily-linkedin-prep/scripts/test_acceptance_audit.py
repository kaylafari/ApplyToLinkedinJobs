import copy
import unittest
import shutil
from acceptance_audit import audit
import test_handoff_receipt


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_handoff_receipt.HandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.first = copy.deepcopy(self.fixture.receipt)
        self.first.update(test_cycle_id='test-two', final_live_verification=True,
                          required_unresolved=[], inaccessible_steps=[], technical_unresolved=[],
                          supported_fields_complete=True, answers_evidence_checked=True,
                          unsupported_answers_present=False)
        self.second = copy.deepcopy(self.first)
        self.second.update(job_id='67890', review_url='https://example.com/jobs/67890',
                           application_tab={'id': '43', 'kind': 'application', 'url': 'https://example.com/jobs/67890'})
        self.m1 = copy.deepcopy(self.fixture.manifest)
        self.m2 = dict(self.m1, job_id='67890')
        second_root = self.fixture.root / 'second'
        second_root.mkdir()
        for path in list(self.fixture.root.iterdir()):
            if path.is_file():
                shutil.copyfile(path, second_root / path.name)
        second_pdf = str(second_root / 'Kayla_Farivar_Resume.pdf')
        self.second['resume_path'] = second_pdf
        self.m2['artifact_path'] = second_pdf

    def run_audit(self):
        return audit([(self.first, self.m1), (self.second, self.m2)], 'test-two', 2)

    def test_two_successes(self):
        self.second['ats_identity'] = None
        self.assertTrue(self.run_audit()['success'])

    def test_one_success_is_not_two(self):
        self.assertFalse(audit([(self.first, self.m1)], 'test-two', 2)['success'])

    def test_manual_attachment_allowed_not_inaccessible_pages(self):
        self.second.update(handoff_kind='manual_upload', attachment_verified=False, ledger_status='blocked',
                           missing_upload_reason='chooser timeout', upload_recovery_exhausted=True,
                           upload_evidence_reference='upload-result.json')
        result = self.run_audit()
        self.assertTrue(result['success'])
        self.assertEqual(result['attachments_verified'], 1)
        self.second['inaccessible_steps'] = ['Page 2 requires upload']
        self.assertFalse(self.run_audit()['success'])

    def test_missing_live_check_and_questions_fail(self):
        for field, value in [('final_live_verification', False), ('test_cycle_id', 'old'),
                             ('final_control_visible', False), ('required_unresolved', ['salary'])]:
            with self.subTest(field=field):
                second = dict(self.second, **{field: value})
                self.assertFalse(audit([(self.first, self.m1), (second, self.m2)], 'test-two', 2)['success'])

    def test_shared_tab_rejected(self):
        self.second['application_tab']['id'] = '42'
        self.assertFalse(self.run_audit()['success'])

    def test_duplicate_job_rejected(self):
        self.assertFalse(audit([(self.first, self.m1), (self.first, self.m1)], 'test-two', 2)['success'])

    def test_whitespace_tab_and_shared_pdf_rejected(self):
        self.second['application_tab']['id'] = '42 '
        self.assertFalse(self.run_audit()['success'])
        self.second['application_tab']['id'] = '43'
        self.second['resume_path'] = self.first['resume_path']
        self.m2['artifact_path'] = self.m1['artifact_path']
        self.assertFalse(self.run_audit()['success'])

    def test_ats_alias_duplicate_rejected(self):
        self.first['ats_identity'] = self.second['ats_identity'] = 'ashby:example:abc'
        self.assertFalse(self.run_audit()['success'])
        del self.first['ats_identity'], self.second['ats_identity']
        self.first['review_url'] = self.first['application_tab']['url'] = 'https://jobs.ashbyhq.com/example/abc/application?utm_source=x'
        self.second['review_url'] = self.second['application_tab']['url'] = 'https://jobs.ashbyhq.com/example/abc'
        self.assertFalse(self.run_audit()['success'])

    def test_conflicting_required_question_lists_rejected(self):
        self.second.update(handoff_kind='manual_upload', attachment_verified=False, ledger_status='blocked',
                           missing_upload_reason='chooser timeout', upload_recovery_exhausted=True,
                           upload_evidence_reference='upload-result.json', unanswered_question_labels=['salary'])
        self.assertFalse(self.run_audit()['success'])


if __name__ == '__main__':
    unittest.main()
