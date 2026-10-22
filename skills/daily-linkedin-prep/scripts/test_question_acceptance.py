import copy
import unittest
import test_acceptance_audit
from acceptance_audit import audit


class PersonalQuestionTests(unittest.TestCase):
    setUp = test_acceptance_audit.AcceptanceTests.setUp

    def receipt(self):
        r = copy.deepcopy(self.second)
        r.update(ledger_status='prepared_for_review', required_unresolved=['Salary expectation'],
                 unanswered_question_labels=['Salary expectation'],
                 review_note='Please check the blank Salary expectation question.', review_note_preserved=True,
                 unknown_personal_questions=[dict(label='Salary expectation', kind='unknown_personal',
                     left_blank=True, supported_answer_available=False, source_reviewed=True,
                     reason='No saved preference or user correction answers this question.',
                     evidence_reference='candidate-question-source-check.json')])
        return r

    def result(self, r):
        return audit([(self.first, self.m1), (r, self.m2)], 'test-two', 2)

    def test_unknown_personal_with_preserved_note_passes(self):
        r = self.receipt()
        result = self.result(r)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['accepted'][1]['unknown_personal_question_labels'], ['Salary expectation'])
        self.assertEqual(r['ledger_status'], 'prepared_for_review')

    def test_unknown_personal_not_fully_ready(self):
        r = self.receipt()
        r['ledger_status'] = 'ready_for_review'
        self.assertFalse(self.result(r)['success'])

    def test_note_missing_or_not_saved_fails(self):
        for updates in ({'review_note': ''}, {'review_note': 'Check your application'},
                        {'review_note_preserved': False}):
            r = self.receipt(); r.update(updates)
            self.assertFalse(self.result(r)['success'])

    def test_arbitrary_unresolved_or_conflicting_labels_fail(self):
        for updates in ({'unknown_personal_questions': []}, {'required_unresolved': ['Email']},
                        {'unanswered_question_labels': []}, {'unanswered_question_labels': ['Salary expectation'] * 2}):
            r = self.receipt(); r.update(updates)
            self.assertFalse(self.result(r)['success'])

    def test_supported_technical_invented_or_unreviewed_answers_fail(self):
        for key, value in [('kind', 'technical'), ('left_blank', False),
                           ('supported_answer_available', True), ('source_reviewed', False),
                           ('reason', ''), ('evidence_reference', '')]:
            r = self.receipt(); r['unknown_personal_questions'][0][key] = value
            self.assertFalse(self.result(r)['success'])

    def test_technical_incomplete_supported_incomplete_and_unsupported_fail(self):
        for updates in ({'supported_fields_complete': False}, {'answers_evidence_checked': False},
                        {'unsupported_answers_present': True}, {'technical_unresolved': ['lost phone']},
                        {'validation_errors': ['phone invalid']}, {'inaccessible_steps': ['page2']},
                        {'tab_preserved': False}, {'final_control_visible': False}):
            r = self.receipt(); r.update(updates)
            self.assertFalse(self.result(r)['success'])

    def test_manual_upload_and_unknown_personal_stay_separate(self):
        r = self.receipt()
        r.update(handoff_kind='manual_upload', attachment_verified=False, ledger_status='blocked',
                 missing_upload_reason='chooser failed', upload_recovery_exhausted=True,
                 upload_evidence_reference='upload.json')
        self.assertTrue(self.result(r)['success'])
        r['technical_unresolved'] = ['Other field could not be filled']
        self.assertFalse(self.result(r)['success'])


if __name__ == '__main__':
    unittest.main()
