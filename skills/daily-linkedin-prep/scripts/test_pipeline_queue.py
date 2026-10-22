import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

import pipeline_queue as q


def worker(payload):
    path, command, args = payload
    try:
        return q.operate(path, command, **args)
    except ValueError as exc:
        return {"error": str(exc)}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "queue.json"
        self.ledger = Path(self.tmp.name) / "ledger.json"
        self.pdf = Path(self.tmp.name) / "resume.pdf"
        self.pdf.write_bytes(b"test artifact")
        self.state = {"runs": {"run": {"status": "active", "target": 2}},
                      "owner": {"run_id": "run", "token": "parent-secret"}, "jobs": {}}
        self.write_ledger()
        q.operate(self.path, "init", ledger=str(self.ledger), run_id="run", target=2)

    def write_ledger(self):
        with q.locked(self.ledger):
            q.save(self.ledger, self.state)

    def candidate(self, key="123", **changes):
        return {"company": "Company", "title": "Software Engineer", "linkedin_job_id": key,
                "application_url": "https://jobs.example.com/post/" + key,
                "score": 75, "eligibility_verified": True,
                "duplicate_check": {"checked_at": "2026-09-29T12:00:00Z", "result": "clear"}, **changes}

    def add(self, key="123", **changes):
        return q.operate(self.path, "add", candidate=self.candidate(key, **changes))

    def claim(self, role="application", **changes):
        return q.operate(self.path, "claim", role=role, owner="agent", **changes)

    def prepared(self, key="123"):
        self.state["jobs"][key] = {"run_id": "run", "status": "prepared_for_review",
            "resume_path": str(self.pdf), "evidence": {**{g: True for g in q.GATES},
            "sha256": q.hashlib.sha256(self.pdf.read_bytes()).hexdigest(), "company": "Company", "title": "Software Engineer",
            "application_tab": {"id": key, "url": "https://jobs.example.com/post/" + key,
                                "kind": "application"}}}
        self.write_ledger()

    def complete(self, claim):
        return q.operate(self.path, "complete", job_id=claim["job_id"], owner="agent",
                         token=claim["token"], ledger_owner="parent-secret")

    def test_concurrent_duplicate_adds(self):
        payload = (str(self.path), "add", {"candidate": self.candidate()})
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, [payload] * 8))
        self.assertEqual(sum(not r.get("duplicate", False) for r in results), 1)
        self.assertEqual(len(json.loads(self.path.read_text())["jobs"]), 1)

    def test_concurrent_claims_and_stale_owner(self):
        self.add()
        self.add("124")
        payload = (str(self.path), "claim", {"role": "application", "owner": "agent"})
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, [payload] * 4))
        self.assertEqual(sum(r.get("claimed", False) for r in results), 1)
        with self.assertRaisesRegex(ValueError, "stale owner"):
            q.operate(self.path, "block", role="application", job_id="123", owner="other", token="bad", reason="upload denied")

    def test_notion_only_done(self):
        self.add()
        self.assertFalse(self.claim("notion")["claimed"])
        c = self.claim()
        self.prepared()
        self.complete(c)
        n = self.claim("notion")
        self.assertTrue(n["claimed"])
        receipt = {"row_url": "https://app.notion.com/p/verified-row", "verified": True,
                   "application_date_blank": True, "status": "Prepared for Review"}
        args = {"job_id": "123", "owner": "agent", "token": n["token"], "receipt": receipt}
        q.operate(self.path, "note", **args)
        self.assertTrue(q.operate(self.path, "note", **args)["idempotent"])
        self.assertEqual(len(json.loads(self.path.read_text())["jobs"]["123"]["receipts"]), 1)
        self.assertFalse(self.claim("notion")["claimed"])

    def test_completion_gates_and_parent_capability(self):
        self.add()
        c = self.claim()
        with self.assertRaisesRegex(ValueError, "does not verify"):
            self.complete(c)
        self.prepared()
        with self.assertRaisesRegex(ValueError, "capability"):
            q.operate(self.path, "complete", job_id="123", owner="agent", token=c["token"], ledger_owner="worker")
        self.state["jobs"]["123"]["evidence"]["resume_attachment_verified"] = False
        self.write_ledger()
        with self.assertRaisesRegex(ValueError, "does not verify"):
            self.complete(c)
        self.prepared()
        self.pdf.unlink()
        with self.assertRaisesRegex(ValueError, "does not verify"):
            self.complete(c)

    def test_blockers_allow_next_and_retry(self):
        self.add()
        self.add("124")
        c = self.claim()
        q.operate(self.path, "block", role="application", owner="agent", token=c["token"], job_id=c["job_id"], reason="Employer-specific upload denied; no file attached")
        self.assertEqual(self.claim()["job_id"], "124")
        q.operate(self.path, "retry", job_id="123", ledger_owner="parent-secret", reason="User authorized employer-specific retry and permission is verified")
        self.assertEqual(json.loads(self.path.read_text())["jobs"]["123"]["status"], "ready_for_application")

    def test_target_uses_actual_ledger_not_worker_count(self):
        self.add()
        self.prepared("555")
        self.prepared("556")
        self.assertEqual(self.claim()["reason"], "target_reached")

    def test_adopt_and_add_does_not_reset_done(self):
        self.add()
        self.prepared()
        q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")
        self.assertEqual(self.add()["status"], "done")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.add(company="Another company")

    def test_ats_dedup_and_normalization(self):
        self.add(application_url="https://jobs.ashbyhq.com/co/abc/application?utm_source=x", ats_identity="ashby:abc")
        self.assertTrue(self.add("124", application_url="https://jobs.ashbyhq.com/co/abc")["duplicate"])
        self.assertTrue(self.add("125", ats_identity="ashby:abc")["duplicate"])
        self.assertEqual(q.normalize_url("https://jobs.example.com/open?id=123&utm_source=google&gh_jid=567"), "https://jobs.example.com/open?gh_jid=567&id=123")
        self.assertFalse(self.add("126")["status"] == "done")

    def test_validation_scores(self):
        for score in (60, float("nan"), float("inf"), True, 101):
            with self.assertRaises(ValueError):
                self.add(score=score)
        with self.assertRaises(ValueError):
            self.add(duplicate_check={"result": "clear", "checked_at": "yesterday"})
        with self.assertRaises(ValueError):
            self.add(eligibility_verified=False)

    def test_explicit_recovery(self):
        self.add()
        c = self.claim()
        args = {"role": "application", "job_id": "123", "owner": "agent", "token": c["token"], "ledger_owner": "parent-secret"}
        with self.assertRaisesRegex(ValueError, "owner"):
            q.operate(self.path, "recover", **args)
        q.operate(self.path, "recover", **args, owner_check="Parent confirmed agent stopped and re-observed its tab")
        newer = self.claim()
        self.assertNotEqual(newer["token"], c["token"])
        with self.assertRaisesRegex(ValueError, "stale owner"):
            q.operate(self.path, "block", **{k: v for k, v in args.items() if k != "ledger_owner"}, reason="stale")

    def test_browser_mutation_mutex_and_release(self):
        self.add()
        c = self.claim()
        lease = q.operate(self.path, "claim-browser", role="application", job_id="123", owner="agent", token=c["token"])
        with self.assertRaisesRegex(ValueError, "already held"):
            q.operate(self.path, "claim-browser", role="discovery", owner="researcher")
        self.prepared()
        with self.assertRaisesRegex(ValueError, "Release"):
            self.complete(c)
        with self.assertRaisesRegex(ValueError, "Exact browser"):
            q.operate(self.path, "release-browser", owner="agent", token="stale")
        status = q.operate(self.path, "status")
        self.assertNotIn("token", status["browser_lease"])
        q.operate(self.path, "release-browser", owner="agent", token=lease["browser_token"])
        self.complete(c)

    def test_concurrent_browser_claims(self):
        payload = (str(self.path), "claim-browser", {"role": "discovery", "owner": "researcher"})
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, [payload] * 4))
        self.assertEqual(sum(r.get("claimed", False) for r in results), 1)

    def test_late_binding_detects_duplicate_and_blocks_done(self):
        self.add(application_url="https://jobs.ashbyhq.com/co/ats1", ats_identity="ashby:ats1")
        self.add("124")
        c = self.claim(job_id="124")
        result = q.operate(self.path, "bind", job_id="124", owner="agent", token=c["token"], application_url="https://jobs.ashbyhq.com/co/ats1/application", ats_identity="ashby:ats1")
        self.assertTrue(result["must_stop_application"])
        self.prepared("124")
        with self.assertRaisesRegex(ValueError, "duplicate conflict"):
            self.complete(c)

    def test_late_binding_preserves_initial_identity_and_future_dedup(self):
        self.add()
        c = self.claim()
        q.operate(self.path, "bind", job_id="123", owner="agent", token=c["token"], application_url="https://jobs.ashbyhq.com/co/ats1/application", ats_identity="ashby:ats1")
        self.assertTrue(self.add()["duplicate"])
        self.assertTrue(self.add("125", application_url="https://jobs.ashbyhq.com/co/ats1")["duplicate"])

    def test_notion_block_explicit_retry(self):
        self.add()
        self.prepared()
        q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")
        n = self.claim("notion")
        q.operate(self.path, "block", job_id="123", role="notion", owner="agent", token=n["token"], reason="Notion saved write uncertain; reconcile row before retry")
        self.assertFalse(self.claim("notion")["claimed"])
        q.operate(self.path, "retry", job_id="123", ledger_owner="parent-secret", reason="Reopened exact row; prior body missing, safe to retry verified row update")
        self.assertTrue(self.claim("notion")["claimed"])

    def test_adopt_unknown_from_ledger_without_fake_clearance(self):
        self.prepared()
        result = q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")
        self.assertEqual(result["status"], "done")
        candidate = q.operate(self.path, "status")["jobs"]["123"]["candidate"]
        self.assertNotIn("duplicate_check", candidate)
        self.assertNotIn("score", candidate)

    def test_hash_and_distinct_tab_verified(self):
        self.prepared()
        self.pdf.write_bytes(b"changed content")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")
        self.prepared()
        self.prepared("124")
        self.state["jobs"]["124"]["evidence"]["application_tab"]["id"] = "123"
        self.write_ledger()
        with self.assertRaisesRegex(ValueError, "shared"):
            q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")

    def test_stale_done_invalidation_unblocks_next_notion_job(self):
        self.prepared("123")
        self.prepared("124")
        for key in ("123", "124"):
            q.operate(self.path, "adopt", job_id=key, ledger_owner="parent-secret")
        with self.assertRaisesRegex(ValueError, "still verifies"):
            q.operate(self.path, "invalidate", job_id="123", ledger_owner="parent-secret", reason="not yet invalidated")
        self.state["jobs"]["123"]["evidence"]["tab_preserved"] = False
        self.write_ledger()
        with self.assertRaisesRegex(ValueError, "does not verify"):
            self.claim("notion")
        with self.assertRaisesRegex(ValueError, "capability"):
            q.operate(self.path, "invalidate", job_id="123", ledger_owner="worker", reason="tab closed")
        q.operate(self.path, "invalidate", job_id="123", ledger_owner="parent-secret", reason="Parent verified its application tab was closed; ledger updated")
        self.assertEqual(self.claim("notion")["job_id"], "124")
        job = q.operate(self.path, "status")["jobs"]["123"]
        self.assertEqual(job["status"], "reconciliation_needed")
        self.assertEqual(job["notion_status"], "suspended")
        self.assertIn("ledger_receipt", job)
        q.operate(self.path, "retry", job_id="123", ledger_owner="parent-secret", reason="Parent re-opened draft and verified safe to finish preparation")
        self.assertEqual(q.operate(self.path, "status")["jobs"]["123"]["status"], "ready_for_application")

    def test_invalidate_requires_claim_recovery_and_preserves_receipts(self):
        self.prepared()
        q.operate(self.path, "adopt", job_id="123", ledger_owner="parent-secret")
        n = self.claim("notion")
        self.state["jobs"]["123"]["evidence"]["tab_preserved"] = False
        self.write_ledger()
        with self.assertRaisesRegex(ValueError, "claimed"):
            q.operate(self.path, "invalidate", job_id="123", ledger_owner="parent-secret", reason="tab closed")
        q.operate(self.path, "recover", role="notion", job_id="123", owner="agent", token=n["token"], ledger_owner="parent-secret", owner_check="Parent confirmed stopped worker")
        q.operate(self.path, "invalidate", job_id="123", ledger_owner="parent-secret", reason="tab closed")
        self.assertEqual(len(q.operate(self.path, "status")["jobs"]["123"]["invalidations"]), 1)


if __name__ == "__main__":
    unittest.main()
