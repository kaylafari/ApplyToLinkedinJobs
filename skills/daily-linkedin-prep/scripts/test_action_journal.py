import json
import tempfile
import unittest
from pathlib import Path
from action_journal import apply, transact


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.state = {'version': 1, 'run_id': 'test', 'actions': {}}
        self.intent = {'action_id': 'a', 'job_id': '123', 'operation': 'upload', 'target': 'tab:1', 'evidence_ref': 'plan.json'}

    def result(self, outcome):
        return {'action_id': 'a', 'outcome': outcome, 'evidence_ref': 'observed.json', 'source': 'browser', 'detail': 'Observed result'}

    def test_intent_replay_does_not_authorize_retry(self):
        apply(self.state, 'intent', self.intent)
        self.assertTrue(apply(self.state, 'intent', self.intent)['replayed'])
        self.assertEqual(self.state['actions']['a']['status'], 'in_flight')

    def test_conflicting_id_rejected(self):
        apply(self.state, 'intent', self.intent)
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', dict(self.intent, job_id='456'))

    def test_uncertain_blocks_mutations_but_allows_inspection(self):
        apply(self.state, 'intent', self.intent)
        apply(self.state, 'result', self.result('uncertain'))
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', dict(self.intent, action_id='b'))
        apply(self.state, 'intent', dict(self.intent, action_id='read', operation='inspect'))
        apply(self.state, 'result', self.result('failed'))
        self.assertEqual(self.state['actions']['a']['status'], 'failed')

    def test_final_result_cannot_be_rewritten(self):
        apply(self.state, 'intent', self.intent)
        apply(self.state, 'result', self.result('failed'))
        with self.assertRaises(ValueError):
            apply(self.state, 'result', self.result('succeeded'))

    def test_one_job_uncertainty_does_not_stop_next_job(self):
        apply(self.state, 'intent', self.intent)
        apply(self.state, 'result', self.result('uncertain'))
        apply(self.state, 'intent', dict(self.intent, action_id='other', job_id='456', target='tab:2'))
        self.assertEqual(len(self.state['actions']), 2)

    def test_two_jobs_cannot_mutate_same_uncertain_tab(self):
        apply(self.state, 'intent', self.intent)
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', dict(self.intent, action_id='b', job_id='456'))

    def test_retry_cannot_escape_by_new_id_or_other_job(self):
        apply(self.state, 'intent', self.intent)
        apply(self.state, 'result', self.result('denied'))
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', dict(self.intent, action_id='b'))
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', dict(self.intent, action_id='b', job_id='456', recovery_of='a', changed_evidence_ref='new.json'))

    def test_denial_recovery_requires_changed_evidence(self):
        apply(self.state, 'intent', self.intent)
        apply(self.state, 'result', self.result('denied'))
        retry = dict(self.intent, action_id='b', recovery_of='a')
        with self.assertRaises(ValueError):
            apply(self.state, 'intent', retry)
        apply(self.state, 'intent', dict(retry, changed_evidence_ref='approval.json'))

    def test_submission_and_private_fields_rejected(self):
        for bad in [dict(self.intent, operation='submit'), dict(self.intent, owner='secret'), dict(self.intent, answers={})]:
            with self.assertRaises(ValueError):
                apply(self.state, 'intent', bad)

    def test_file_roundtrip_and_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.json'
            transact(path, 'test', 'intent', self.intent)
            self.assertEqual(transact(path, 'test', 'status', {})['unresolved'], ['a'])
            with self.assertRaises(ValueError):
                transact(path, 'other', 'status', {})
            transact(path, 'test', 'result', self.result('succeeded'))
            self.assertEqual(json.loads(path.read_text())['actions']['a']['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
