"""Run: python3 -m unittest discover -s work/state-helper -p 'test_*.py' -v"""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("ledger.py")
GATES = ("identity_verified", "supported_answers_persisted", "resume_qa_passed",
         "resume_attachment_verified", "tab_preserved", "preparation_complete", "submission_protection_verified")


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state.json"
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"dummy artifact: content QA is the agent's responsibility")
        self.evidence = self.root / "evidence.json"

    def evidence_file(self, ready=False, **updates):
        data = {key: True for key in GATES}
        data.update(form_open_verified=True, application_tab={"id": "tab-1", "url": "https://employer.test/apply", "kind": "application"}, final_control_visible=ready, required_unresolved=[] if ready else ["Unknown personal answer"],
                    validation_errors=[], tab={"id": "tab-2", "preserved": True})
        data.update(updates)
        self.evidence.write_text(json.dumps(data))
        return str(self.evidence)

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--state", str(self.state), *args],
                                capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def begin(self, day="2026-09-11", **kwargs):
        return self.cli("begin", "--day", day, **kwargs)["owner"]

    def prepared(self, owner, job, status="prepared_for_review", extra=()):
        return self.cli("record", "--owner", owner, "--job-id", job, "--status", status,
                        "--verified-form", "--verified-resume", "--resume-path", str(self.resume),
                        "--evidence-file", self.evidence_file(ready=status == "ready_for_review", application_tab={"id": "tab-" + job, "url": "https://employer.test/apply/" + job, "kind": "application"}), *extra)

    def test_serial_processing_and_pair_gate(self):
        owner = self.begin()
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "qualified")
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "qualified", ok=False)
        self.cli("check-next", "--owner", owner, "--job-id", "a", "--for-resume", ok=False)
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "in_progress",
                 "--evidence-file", self.evidence_file())
        self.cli("check-next", "--owner", owner, "--job-id", "a", "--for-resume")
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "resume_ready",
                 "--resume-path", str(self.resume), ok=False)
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "resume_ready",
                 "--resume-path", str(self.resume), "--evidence-file", self.evidence_file())
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "qualified", ok=False)
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "blocked",
                 "--note", "Upload denied; paired filled tab remains", "--evidence-file", self.evidence_file())
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "qualified")
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "in_progress",
                 "--evidence-file", self.evidence_file(), ok=False)
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "resume_ready",
                 "--resume-path", str(self.resume), "--evidence-file", self.evidence_file(), ok=False)
        self.cli("finish", "--owner", owner, "--exhausted", ok=False)

    def test_explicit_eight_target(self):
        self.cli('begin', '--target', '0', ok=False)
        started = self.cli('begin', '--day', '2026-09-24', '--target', '8')
        owner = started['owner']
        self.assertEqual(started['target'], 8)
        self.cli('begin', '--day', '2026-09-24', '--owner', owner, '--target', '15', ok=False)
        for i in range(8): self.prepared(owner, str(i))
        self.cli('record', '--owner', owner, '--job-id', '9', '--status', 'prepared_for_review',
                 '--resume-path', str(self.resume), '--evidence-file', self.evidence_file(), ok=False)
        self.cli('finish', '--owner', owner)

    def test_orphan_blocks_next_resume_and_audit_detects_untracked_files(self):
        owner = self.begin()
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "blocked",
                 "--resume-path", str(self.resume), "--note", "Tab lost; honest orphan")
        self.cli("record", "--owner", owner, "--job-id", "b", "--status", "qualified", ok=False)
        orphan = self.root / "artifacts" / "unknown" / "r1"
        orphan.mkdir(parents=True)
        (orphan / "Kayla_Farivar_Resume.pdf").write_bytes(b"test")
        audit = self.cli("audit-pairs", "--run-id", "applylinkedin-2026-09-11", "--artifacts-dir", str(self.root / "artifacts"))
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["orphan_job_ids"], ["a"])
        self.assertEqual(len(audit["untracked_pdfs"]), 1)

    def test_listing_and_false_pair_flags_rejected(self):
        owner = self.begin()
        self.cli("record", "--owner", owner, "--job-id", "a", "--status", "qualified")
        for updates in ({"form_open_verified": False}, {"supported_answers_persisted": False},
                        {"tab_preserved": 1}, {"application_tab": {"id": "x", "url": "https://example.test", "kind": "listing"}}):
            self.cli("record", "--owner", owner, "--job-id", "a", "--status", "resume_ready",
                     "--resume-path", str(self.resume), "--evidence-file", self.evidence_file(**updates), ok=False)

    def test_verified_historical_outcome_does_not_block_but_stays_orphan(self):
        owner = self.begin()
        self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'skipped',
                 '--resume-path', str(self.resume), '--evidence-file',
                 self.evidence_file(form_open_verified=False, tab_preserved=False))
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'qualified', ok=False)
        receipt = {'verified': True, 'observed_status': 'Application Submitted',
                   'row_url': 'https://app.notion.com/p/exact-row',
                   'checked_at': '2026-10-02T02:00:00Z',
                   'identity_evidence': 'Exact ATS URL matches historical job'}
        self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'skipped',
                 '--evidence-file', self.evidence_file(form_open_verified=False,
                  tab_preserved=False, historical_outcome_exclusion=receipt))
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'qualified')
        audit = self.cli('audit-pairs', '--run-id', 'applylinkedin-2026-09-11')
        self.assertFalse(audit['ok'])
        self.assertEqual(audit['orphan_job_ids'], ['a'])

    def test_missing_pdf_blocks_next_and_flat_pdf_is_audited(self):
        owner = self.begin()
        self.prepared(owner, 'a')
        self.resume.unlink()
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'qualified', ok=False)
        self.resume.write_bytes(b'restored fixture')
        self.cli('record', '--owner', owner, '--job-id', 'b', '--status', 'qualified')
        (self.root / 'unexpected.PDF').write_bytes(b'untracked fixture')
        audit = self.cli('audit-pairs', '--run-id', 'applylinkedin-2026-09-11', '--artifacts-dir', str(self.root))
        self.assertFalse(audit['ok'])
        self.assertEqual(audit['untracked_pdfs'], [str(self.root / 'unexpected.PDF')])

    def test_verified_closed_posting_releases_serial_work_not_pair_audit(self):
        owner = self.begin()
        source = self.root / 'closed-source.json'
        source.write_text(json.dumps({'job_id': '123', 'observed': 'No longer accepting applications'}))
        receipt = {'verified': True, 'job_id': '123',
                   'observed_status': 'No longer accepting applications',
                   'apply_control_absent': True,
                   'listing_url': 'https://www.linkedin.com/jobs/view/123/',
                   'identity_evidence': 'Fresh exact employer/title/job listing inspected',
                   'checked_at': '2026-10-21T19:00:00Z',
                   'evidence_reference': str(source),
                   'evidence_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        invalid = ({'verified': False}, {'job_id': '999'},
                   {'listing_url': 'https://www.linkedin.com/jobs/view/999/'},
                   {'apply_control_absent': False}, {'observed_status': 'Timed out'},
                   {'evidence_sha256': 'wrong'}, {'checked_at': 'not a date'})
        for changes in invalid:
            self.cli('record', '--owner', owner, '--job-id', '123', '--status', 'skipped',
                     '--resume-path', str(self.resume), '--evidence-file',
                     self.evidence_file(form_open_verified=False, tab_preserved=False,
                                        closed_posting_exclusion={**receipt, **changes}))
            self.cli('record', '--owner', owner, '--job-id', 'next', '--status', 'qualified', ok=False)
        self.cli('record', '--owner', owner, '--job-id', '123', '--status', 'skipped',
                 '--evidence-file', self.evidence_file(form_open_verified=False,
                    tab_preserved=False, closed_posting_exclusion=receipt))
        self.cli('record', '--owner', owner, '--job-id', 'next', '--status', 'qualified')
        audit = self.cli('audit-pairs', '--run-id', 'applylinkedin-2026-09-11')
        self.assertFalse(audit['ok'])
        self.assertEqual(audit['orphan_job_ids'], ['123'])
        self.assertEqual(self.cli('status', '--summary')['runs']['applylinkedin-2026-09-11']['counts']['prepared_total'], 0)

    def test_legacy_qualified_backlog_drains_in_order(self):
        owner = self.begin()
        state = json.loads(self.state.read_text())
        state['jobs'] = {k: {'run_id':'applylinkedin-2026-09-11', 'status':'qualified'} for k in ('a','b')}
        self.state.write_text(json.dumps(state))
        self.cli('check-next', '--owner', owner, '--job-id', 'b', ok=False)
        self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'in_progress',
                 '--evidence-file', self.evidence_file())
        self.cli('check-next', '--owner', owner, '--job-id', 'a', '--for-resume')
        self.cli('record', '--owner', owner, '--job-id', 'a', '--status', 'blocked', '--note', 'No PDF; destination unavailable')
        self.cli('check-next', '--owner', owner, '--job-id', 'b')

    def test_restart_counts_and_cross_day_dedup(self):
        owner = self.begin()
        self.prepared(owner, "101")
        self.prepared(owner, "102", "ready_for_review", ("--required-complete",))
        resumed = self.cli("begin", "--day", "2026-09-11", "--owner", owner)
        self.assertEqual(resumed["prepared_total"], 2)
        self.assertEqual(resumed["target"], 15)
        self.cli("release", "--owner", owner)
        owner = self.begin()
        self.assertEqual(self.cli("status")["runs"]["applylinkedin-2026-09-11"]["counts"]["prepared_total"], 2)
        self.cli("release", "--owner", owner)
        owner = self.begin("2026-09-12")
        self.assertIn("Duplicate", self.cli("record", "--owner", owner, "--job-id", "101", "--status", "in_progress", ok=False))
        self.assertEqual(self.cli("status")["runs"]["applylinkedin-2026-09-12"]["counts"]["prepared_total"], 0)

    def test_new_day_starts_despite_old_blocked_job_without_erasing_history(self):
        owner = self.begin("2026-09-16")
        self.cli("record", "--owner", owner, "--job-id", "blocked-upload",
                 "--status", "blocked", "--note", "Historical upload denied")
        self.cli("release", "--owner", owner)
        before = self.cli("status")
        started = self.cli("begin", "--day", "2026-09-17")
        self.assertEqual(started["run_id"], "applylinkedin-2026-09-17")
        self.assertEqual(started["prepared_total"], 0)
        after = self.cli("status")
        self.assertEqual(after["jobs"], before["jobs"])
        self.assertEqual(after["runs"]["applylinkedin-2026-09-16"],
                         before["runs"]["applylinkedin-2026-09-16"])
        self.cli("record", "--owner", started["owner"], "--job-id", "new-job",
                 "--status", "discovered")
        self.assertIn("Duplicate", self.cli("record", "--owner", started["owner"],
                      "--job-id", "blocked-upload", "--status", "in_progress", ok=False))

    def test_count_gates_and_target(self):
        owner = self.begin()
        original = self.state.read_bytes()
        self.cli("record", "--owner", owner, "--job-id", "1", "--status", "prepared_for_review", ok=False)
        self.assertEqual(self.state.read_bytes(), original)
        self.cli("record", "--owner", owner, "--job-id", "1", "--status", "ready_for_review",
                 "--verified-form", "--verified-resume", "--resume-path", str(self.resume), ok=False)
        self.cli("finish", "--owner", owner, ok=False)
        for i in range(15):
            self.prepared(owner, str(i))
        self.assertEqual(self.prepared(owner, "1", "ready_for_review", ("--required-complete",))["prepared_total"], 15)
        self.cli("record", "--owner", owner, "--job-id", "16", "--status", "prepared_for_review",
                 "--verified-form", "--verified-resume", "--resume-path", str(self.resume),
                 "--evidence-file", str(self.evidence), ok=False)
        self.cli("finish", "--owner", owner)
        self.assertEqual(self.cli("begin", "--day", "2026-09-11")["status"], "complete")

    def test_exclusive_owner_and_no_expiration(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: subprocess.run(
                [sys.executable, str(SCRIPT), "--state", str(self.state), "begin", "--day", "2026-09-11"],
                capture_output=True, text=True), range(2)))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])
        owner = json.loads(next(r.stdout for r in results if r.returncode == 0))["owner"]
        self.cli("begin", "--day", "2027-09-11", ok=False)
        self.cli("release", "--owner", "wrong", ok=False)
        self.assertNotIn("token", self.cli("status")["owner"])
        self.cli("release", "--owner", owner)

    def test_atomic_concurrent_mutation(self):
        owner = self.begin()
        def write(i):
            return self.cli("record", "--owner", owner, "--job-id", str(i), "--status", "discovered")
        def read(_):
            for _ in range(150):
                self.assertEqual(json.loads(self.state.read_text())["version"], 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(write, i) for i in range(30)] + [pool.submit(read, 0)]
            for future in futures:
                future.result()
        self.assertEqual(len(self.cli("status")["jobs"]), 30)
        self.assertEqual(sorted(p.name for p in self.root.glob("state.json.*")), ["state.json.lock"])

    def test_cancellation_and_historical_import(self):
        owner = self.begin()
        self.cli("cancel", "--owner", owner)
        self.assertIn("cancelled", self.cli("begin", "--day", "2026-09-11", ok=False))
        owner = self.begin("2026-09-12")
        source = self.root / "history.json"
        historical = {"runs": [{"status": "cancelled", "id": "old"}], "jobs": [
            {"job_id": "1", "status": "ready_for_review"}, {"job_id": "2", "status": "filling"},
            {"job_id": "3", "status": "automatic_disqualification"},
            {"job_id": "4", "status": "qualification_disqualification"},
            {"job_id": "5", "status": "qualified"}, {"job_id": "6", "status": "resume_ready"},
            {"job_id": "7", "status": "discovered"}]}
        source.write_text(json.dumps(historical))
        self.assertEqual(self.cli("import-history", "--owner", owner, "--source", str(source))["prepared_total"], 0)
        state = self.cli("status")
        self.assertEqual(state["history"][0]["snapshot"], historical)
        self.assertEqual(state["jobs"]["2"]["status"], "reconciliation_needed")
        for job in historical["jobs"][2:]:
            self.assertEqual(state["jobs"][job["job_id"]]["status"], job["status"])
            self.cli("record", "--owner", owner, "--job-id", job["job_id"], "--status", "in_progress", ok=False)
        self.cli("reconcile", "--owner", owner, "--job-id", "3", "--decision", "retry", "--note", "Retry", ok=False)
        self.cli("record", "--owner", owner, "--job-id", "2", "--status", "in_progress", ok=False)
        self.cli("reconcile", "--owner", owner, "--job-id", "2", "--decision", "retry", "--note", "", ok=False)
        self.cli("reconcile", "--owner", owner, "--job-id", "2", "--decision", "retry",
                 "--note", "Inspected preserved tabs; no form or submission exists")
        self.assertEqual(self.prepared(owner, "2")["prepared_total"], 1)
        self.assertTrue(self.cli("status")["jobs"]["2"]["prior_history"])
        self.assertEqual([t["new_status"] for t in self.cli("status")["jobs"]["2"]["transitions"]],
                         ["eligible_after_reconciliation", "prepared_for_review"])

    def test_structured_evidence_and_append_only_corrections(self):
        owner = self.begin()
        for gate in GATES:
            for bad_value in (False, 1):
                self.cli("record", "--owner", owner, "--job-id", "1", "--status", "prepared_for_review",
                         "--resume-path", str(self.resume), "--evidence-file",
                         self.evidence_file(**{gate: bad_value}), ok=False)
        # Unknown personal answers before the final page still count as prepared.
        self.assertEqual(self.prepared(owner, "1")["prepared_total"], 1)
        self.cli("record", "--owner", owner, "--job-id", "1", "--status", "ready_for_review", ok=False)
        for updates in ({"final_control_visible": False}, {"required_unresolved": ["question"]}, {"validation_errors": ["error"]}):
            self.cli("record", "--owner", owner, "--job-id", "1", "--status", "ready_for_review",
                     "--evidence-file", self.evidence_file(ready=True, **updates), ok=False)
        self.prepared(owner, "1", "ready_for_review")
        initial = self.cli("status")["jobs"]["1"]
        self.cli("record", "--owner", owner, "--job-id", "1", "--status", "needs_user_input", "--note", "New required question found")
        corrected = self.cli("status")["jobs"]["1"]
        self.assertEqual(corrected["transitions"][:2], initial["transitions"])
        self.assertEqual(corrected["resume_path"], initial["resume_path"])
        self.assertEqual(corrected["evidence"], initial["evidence"])
        self.assertEqual(corrected["transitions"][-1]["old_status"], "ready_for_review")
        self.assertTrue(corrected["transitions"][-1]["timestamp"])
        self.assertEqual(self.cli("status")["runs"]["applylinkedin-2026-09-11"]["counts"]["prepared_total"], 0)
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(str(self.state) + ".lock").st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
