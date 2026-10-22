import concurrent.futures
import json
import subprocess
import sys
import unittest
import test_ledger as existing


class NamedCycleTests(unittest.TestCase):
    setUp = existing.LedgerTests.setUp
    cli = existing.LedgerTests.cli
    evidence_file = existing.LedgerTests.evidence_file
    prepared = existing.LedgerTests.prepared

    def start(self, cycle="two-job-test", target="2", **kw):
        return self.cli("begin-test", "--cycle-id", cycle, "--target", target, **kw)

    def test_daily_unchanged_test_target_and_completion_cap(self):
        daily = self.cli("begin", "--day", "2026-10-01")
        self.assertEqual(daily["target"], 15)
        self.cli("release", "--owner", daily["owner"])
        before = json.loads(self.state.read_text())["runs"][daily["run_id"]]
        test = self.start()
        self.assertEqual(test["target"], 2)
        self.assertEqual(test["run_id"], "applylinkedin-test-two-job-test")
        self.prepared(test["owner"], "new-1")
        self.prepared(test["owner"], "new-2")
        error = self.cli("record", "--owner", test["owner"], "--job-id", "new-3",
                         "--status", "prepared_for_review", "--resume-path", str(self.resume),
                         "--evidence-file", self.evidence_file(application_tab={"id":"third", "kind":"application", "url":"https://test.example/third"}), ok=False)
        self.assertIn("target", error)
        self.assertEqual(json.loads(self.state.read_text())["runs"][daily["run_id"]], before)
        self.assertNotIn("new-3", json.loads(self.state.read_text())["jobs"])
        self.cli("finish", "--owner", test["owner"])
        state_before = self.state.read_bytes()
        self.assertEqual(self.start()["status"], "complete")
        self.assertEqual(self.state.read_bytes(), state_before)
        self.assertIsNone(json.loads(self.state.read_text())["owner"])

    def test_history_and_daily_jobs_stay_deduplicated(self):
        daily = self.cli("begin", "--day", "2026-10-01")
        self.prepared(daily["owner"], "old-ready")
        source = self.root / "import.json"
        source.write_text(json.dumps({"jobs":[{"job_id":"old-submitted", "status":"submitted"}]}))
        self.cli("import-history", "--owner", daily["owner"], "--source", str(source))
        self.cli("release", "--owner", daily["owner"])
        before = json.loads(self.state.read_text())
        cycle = self.start()
        for job in ("old-ready", "old-submitted"):
            self.assertIn("Duplicate", self.cli("record", "--owner", cycle["owner"], "--job-id", job, "--status", "in_progress", ok=False))
        after = json.loads(self.state.read_text())
        self.assertEqual(before["jobs"], after["jobs"])
        self.assertEqual(before["history"], after["history"])
        self.assertEqual(cycle["prepared_total"], 0)

    def test_owner_exclusive_between_daily_and_test(self):
        daily = self.cli("begin", "--day", "2026-10-01")
        self.assertIn("owner", self.start(ok=False))
        self.cli("begin-test", "--cycle-id", "other", "--target", "2", "--owner", daily["owner"], ok=False)
        self.cli("release", "--owner", daily["owner"])
        cycle = self.start()
        self.cli("begin", "--day", "2026-10-02", ok=False)
        self.start(cycle="another", ok=False)
        self.assertEqual(json.loads(self.state.read_text())["owner"]["token"], cycle["owner"])

    def test_concurrent_test_starts_share_lock(self):
        def begin(cycle):
            return subprocess.run([sys.executable, str(existing.SCRIPT), "--state", str(self.state),
                                   "begin-test", "--cycle-id", cycle, "--target", "2"], capture_output=True, text=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(begin, ("cycle-a", "cycle-b")))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])
        self.assertEqual(len(json.loads(self.state.read_text())["runs"]), 1)

    def test_resume_same_cycle_target_and_cancel(self):
        cycle = self.start()
        self.prepared(cycle["owner"], "new")
        same = self.cli("begin-test", "--cycle-id", "two-job-test", "--target", "2", "--owner", cycle["owner"])
        self.assertEqual(same["prepared_total"], 1)
        self.cli("release", "--owner", cycle["owner"])
        self.start(target="3", ok=False)
        resumed = self.start()
        self.assertEqual(resumed["prepared_total"], 1)
        self.cli("cancel", "--owner", resumed["owner"])
        before = self.state.read_bytes()
        self.assertIn("cancelled", self.start(ok=False))
        self.assertEqual(before, self.state.read_bytes())

    def test_slug_and_explicit_target(self):
        self.cli("begin-test", "--cycle-id", "test", ok=False)
        self.cli("begin-test", "--target", "2", ok=False)
        for cycle in ("../escape", "a/b", "a\\b", "", "a b", "UPPER", "-x", "x-", "x"*65):
            with self.subTest(cycle=cycle):
                self.start(cycle=cycle, ok=False)
        self.start(target="0", ok=False)
        self.start(target="-1", ok=False)
        self.assertFalse(self.state.exists())

    def test_summary_hides_payloads_and_token_full_status_unchanged(self):
        cycle = self.start()
        self.cli("record", "--owner", cycle["owner"], "--job-id", "one", "--status", "blocked", "--note", "PRIVATE_CANDIDATE_ANSWER")
        source = self.root / "history.json"
        source.write_text(json.dumps({"jobs":[], "secret":"PRIVATE_HISTORY"}))
        self.cli("import-history", "--owner", cycle["owner"], "--source", str(source))
        full = self.cli("status")
        summary = self.cli("status", "--summary")
        serialized = json.dumps(summary)
        self.assertNotIn(cycle["owner"], serialized)
        self.assertNotIn("PRIVATE_CANDIDATE_ANSWER", serialized)
        self.assertNotIn("PRIVATE_HISTORY", serialized)
        self.assertNotIn("jobs", summary)
        self.assertNotIn("history", summary)
        self.assertEqual(summary["job_count"], 1)
        self.assertEqual(summary["history_count"], 1)
        self.assertEqual(summary["runs"][cycle["run_id"]]["target"], 2)
        self.assertEqual(self.cli("status"), full)
        self.assertIn("PRIVATE_CANDIDATE_ANSWER", json.dumps(full))


if __name__ == "__main__":
    unittest.main()
