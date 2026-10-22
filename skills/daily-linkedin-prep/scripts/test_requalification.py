import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ledger', Path(__file__).with_name('ledger.py'))
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


class RequalificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'state.json'
        self.ref = self.root / 'source.json'
        self.ref.write_text('{"job_id":"4468516220","verified":true}')
        self.evidence = {'job_id':'4468516220', 'prior_reason':'Card exclusion: outside_exact_New_York_NY',
            'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            **{k:True for k in ('prior_reason_superseded','current_eligibility_verified','current_description_verified','duplicate_check_clear','no_application_effects','no_human_outcomes')},
            'references':{k:str(self.ref) for k in ('current_description','eligibility','duplicate_check')}}
        self.job = {'status':'automatic_disqualification','run_id':'old','note':self.evidence['prior_reason'],
            'evidence':{'card_exclusion_evidence':[{'quote':'New York City Metropolitan Area (On-site)','rule':'outside_exact_New_York_NY'}]},
            'transitions':[{'old_status':'discovered','new_status':'automatic_disqualification'}]}
        self.state = {'version':1,'owner':{'run_id':'current','token':'TEST'},'runs':{'current':{'status':'active','target':15}},'jobs':{'4468516220':self.job},'history':[]}
        self.receipt = self.root / 'receipt.json'

    def run_case(self, receipt=True, decision='retry'):
        self.path.write_text(json.dumps(self.state))
        self.receipt.write_text(json.dumps(self.evidence))
        argv=['--state',str(self.path),'reconcile','--owner','TEST','--job-id','4468516220','--decision',decision,'--note','Fresh JD explicitly SoHo NYC']
        if receipt:
            argv += ['--requalification-evidence-file',str(self.receipt)]
        before=self.path.read_bytes()
        try:
            return ledger.transact(self.path, ledger.parser().parse_args(argv))
        except (ValueError, OSError, TypeError):
            self.assertEqual(before,self.path.read_bytes(), 'Rejected transaction must leave ledger bytes unchanged')
            raise

    def test_success_preserves_original_and_history(self):
        old=copy.deepcopy(self.job)
        result=self.run_case()
        self.assertEqual(result['status'],'eligible_after_reconciliation')
        saved=json.loads(self.path.read_text())['jobs']['4468516220']
        self.assertEqual(saved['prior_history'][0]['evidence'],old['evidence'])
        self.assertEqual(saved['transitions'][:-1],old['transitions'])
        self.assertEqual(saved['requalifications'][0]['evidence'],self.evidence)
        self.assertEqual(saved['evidence'],old['evidence'])
        self.assertIsNone(saved['run_id'])

    def test_missing_receipt_rejected(self):
        with self.assertRaises(ValueError): self.run_case(False)

    def test_invalid_flags_ids_reason_and_freshness(self):
        cases=[('job_id','different'),('prior_reason','different'),('checked_at','2020-01-01T00:00:00+00:00'),('checked_at','2026-01-01')]
        cases += [(k,v) for k in ('prior_reason_superseded','current_eligibility_verified','current_description_verified','duplicate_check_clear','no_application_effects','no_human_outcomes') for v in (False,1,'true',None)]
        original=copy.deepcopy(self.evidence)
        for key,val in cases:
            with self.subTest(key=key,val=val):
                self.evidence=copy.deepcopy(original); self.evidence[key]=val
                with self.assertRaises(ValueError): self.run_case()

    def test_application_effects_and_nested_outcomes_rejected(self):
        cases=[{'resume_path':'old.pdf'},{'verified_form':True},{'evidence':{'application_tab':{'id':'1'}}},
            {'prior_history':[{'status':'submitted'}]},{'transitions':[{'old_status':'ready_for_review'}]},
            {'evidence':{'observed_status':'Application Submitted'}},{'prior_history':[{'status':'Interview'}]},
            {'evidence':{'browser_actions':1}},{'evidence':{'browser_actions':'0'}},
            {'evidence':{'submission_performed':True}}]
        original=copy.deepcopy(self.job)
        for change in cases:
            with self.subTest(change=change):
                self.state['jobs']['4468516220']={**copy.deepcopy(original),**change}
                with self.assertRaises(ValueError): self.run_case()

    def test_bad_references_rejected(self):
        for ref in ('relative.json',str(self.root/'missing.json'),None):
            with self.subTest(ref=ref):
                self.evidence['references']['duplicate_check']=ref
                with self.assertRaises(ValueError): self.run_case()

    def test_no_effects_and_zero_browser_actions_allowed(self):
        self.job['evidence']['browser_actions']=0
        self.assertEqual(self.run_case()['status'],'eligible_after_reconciliation')

    def test_keep_no_override_unchanged(self):
        self.assertEqual(self.run_case(False,'keep')['status'],'automatic_disqualification')

    def test_override_not_valid_for_keep_or_nondisqualified(self):
        with self.assertRaises(ValueError): self.run_case(True,'keep')
        self.job['status']='blocked'
        with self.assertRaises(ValueError): self.run_case()

    def test_existing_owner_and_current_run_guards_remain(self):
        self.state['owner']['token']='OTHER'
        with self.assertRaises(ValueError): self.run_case()
        self.state['owner']['token']='TEST'; self.job['run_id']='current'
        with self.assertRaises(ValueError): self.run_case()

    def test_imported_outcome_for_exact_job_rejected(self):
        self.state['history']=[{'snapshot':{'jobs':[{'job_id':'4468516220','status':'submitted'}]}}]
        with self.assertRaises(ValueError): self.run_case()

    def test_different_imported_job_does_not_block(self):
        self.state['history']=[{'snapshot':{'jobs':[{'job_id':'different','status':'submitted'}]}}]
        self.assertEqual(self.run_case()['status'],'eligible_after_reconciliation')

    def test_serialized_historical_screening_fixture(self):
        source = Path(__file__).with_name('fixtures') / 'historical-screening.json'
        self.state['jobs']['4468516220'] = json.loads(source.read_text())
        self.state['jobs']['4468516220']['run_id'] = 'old'
        # All evidence files are generated inside this test's temporary directory.
        self.evidence['references'] = {key: str(self.ref) for key in
            ('current_description', 'eligibility', 'duplicate_check')}
        self.assertEqual(self.run_case()['status'], 'eligible_after_reconciliation')


if __name__ == '__main__': unittest.main(verbosity=2)
