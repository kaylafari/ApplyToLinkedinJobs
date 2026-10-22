import json
import unittest
import test_ledger


class PreparedRefreshTests(test_ledger.LedgerTests):
    # Reuse the established CLI fixture; inherited regression tests still apply.
    def setup_busy(self):
        owner = self.begin()
        self.prepared(owner, 'a')
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'in_progress')
        return owner

    def test_refresh_prepared_to_ready_while_other_job_active(self):
        owner = self.setup_busy()
        before = self.cli('status')['jobs']['b']
        self.assertEqual(self.prepared(owner, 'a', 'ready_for_review')['prepared_total'], 1)
        self.assertEqual(self.cli('status')['jobs']['b'], before)
        self.assertEqual(self.prepared(owner, 'a')['prepared_total'], 1)

    def test_refresh_does_not_allow_new_application_or_reentry(self):
        owner = self.setup_busy()
        for job, status in [('c', 'qualified'), ('a', 'in_progress'), ('a', 'resume_ready')]:
            self.cli('record', '--owner', owner, '--job-id', job, '--status', status, ok=False)

    def test_refresh_keeps_ready_and_pair_gates(self):
        owner = self.setup_busy()
        for updates in ({'resume_attachment_verified': False}, {'required_unresolved': ['salary']},
                        {'tab_preserved': False}, {'final_control_visible': False}):
            self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'ready_for_review',
                     '--evidence-file', self.evidence_file(ready=True, **updates), ok=False)

    def test_refresh_cannot_share_active_tab(self):
        owner = self.begin()
        self.prepared(owner, 'a')
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'in_progress',
                 '--evidence-file', self.evidence_file(application_tab={'id':'tab-b','kind':'application','url':'https://example.test/b'}))
        self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'ready_for_review',
                 '--evidence-file', self.evidence_file(ready=True, application_tab={'id':'tab-b','kind':'application','url':'https://example.test/b'}), ok=False)


if __name__ == '__main__':
    unittest.main()
