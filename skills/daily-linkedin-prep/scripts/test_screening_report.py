import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

import screening_report as sr


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def assessment(self, key="1", **extra):
        return {"job_id": key, "stage": "full_jd", "decision": "qualified",
                "score": 80, "eligibility_verified": True, "evidence": "Agent-reviewed criteria",
                "description_source": "observed-jd.json", **extra}

    def cards(self):
        return self.file("cards.json", [{"job_id": "1", "text": "Same company and title"}])

    def test_repeated_cards_separate_unique_and_assessed(self):
        a = self.file("a.json", [{"job_id": "1", "text": "A"}, {"id": "2", "text": "B"}])
        b = self.file("b.json", {"page": 2, "cards": [{"job_id": "1", "text": "A again"}]})
        assess = self.file("assess.json", [self.assessment(), {"job_id": "2", "stage": "screening",
                              "decision": "excluded", "evidence": "Observed remote card"}])
        report = sr.build_report([a, b], [assess], [])
        self.assertEqual(report["counts"]["card_observations"], 3)
        self.assertEqual(report["counts"]["unique_observed_jobs"], 2)
        self.assertEqual(report["counts"]["fully_assessed_jobs"], 1)
        self.assertEqual(report["counts"]["screening_only_jobs"], 1)
        self.assertFalse(report["historical_identity_check_complete"])
        self.assertEqual(len(report["input_provenance"][0]["sha256"]), 64)

    def test_card_text_never_generates_decision(self):
        cards = self.file("cards.json", [{"id": "1", "text": "Senior Remote $10 salary Applied"}])
        r = sr.build_report([cards], [], [])
        self.assertIsNone(r["jobs"][0]["explicit_decision"])
        self.assertEqual(r["jobs"][0]["assessment_label"], "observed_without_assessment")

    def test_explicit_full_jd_without_card_is_labeled(self):
        a = self.file("a.json", [self.assessment("2")])
        r = sr.build_report([self.cards()], [a], [])
        self.assertEqual(r["counts"]["fully_assessed_jobs"], 1)
        self.assertEqual(r["counts"]["assessed_without_card"], 1)

    def test_exact_linkedin_match_does_not_change_decision(self):
        history = self.file("h.json", {"jobs": {"1": {"status": "discovered"}}})
        a = self.file("a.json", [self.assessment()])
        r = sr.build_report([self.cards()], [a], [history])
        self.assertEqual(r["jobs"][0]["explicit_decision"], "qualified")
        self.assertEqual(r["jobs"][0]["historical_identity_matches"][0]["kind"], "linkedin_job_id")

    def test_no_company_title_or_unqualified_ats_number_duplicates(self):
        cards = self.file("c.json", [{"id": "1", "text": "Acme Engineer", "company": "Acme",
                    "title": "Engineer", "ats_identity": "greenhouse:123"}])
        h = self.file("h.json", {"jobs": {"2": {"company": "Acme", "title": "Engineer",
                                              "ats_identity": "workday:123"}}})
        self.assertEqual(sr.build_report([cards], [], [h])["counts"]["jobs_with_historical_identity_matches"], 0)

    def test_exact_ats_matches_across_linkedin_ids(self):
        cards = self.file("c.json", [{"id": "1", "text": "A", "ats_identity": "ashby:acme:uuid"}])
        h = self.file("h.json", {"jobs": [{"identity": {"job_id": "2", "ats_identity": "ashby:acme:uuid"},
                                          "status": "submitted"}]})
        r = sr.build_report([cards], [], [h])
        self.assertEqual(r["jobs"][0]["historical_identity_matches"][0]["kind"], "ats_identity")

    def test_normalized_url_tracking_and_ashby_application(self):
        cards = self.file("c.json", [{"id": "1", "text": "A", "application_url":
                     "https://jobs.ashbyhq.com/acme/post/application?utm_source=li&src=keep#x"}])
        h = self.file("h.json", {"jobs": {"2": {"url": "https://jobs.ashbyhq.com/acme/post?src=keep"}}})
        self.assertEqual(sr.build_report([cards], [], [h])["counts"]["jobs_with_historical_identity_matches"], 1)

    def test_identity_bearing_query_not_stripped(self):
        self.assertNotEqual(sr.normalize_url("https://example.com/careers?ashby_jid=one"),
                            sr.normalize_url("https://example.com/careers?ashby_jid=two"))
        self.assertNotEqual(sr.normalize_url("https://example.com/jobs?id=1"),
                            sr.normalize_url("https://example.com/jobs?id=2"))

    def test_distinct_ats_postings_and_employers_not_collapsed(self):
        for left, right in [("https://jobs.ashbyhq.com/a/one", "https://jobs.ashbyhq.com/a/two"),
                            ("https://jobs.ashbyhq.com/a/one", "https://jobs.ashbyhq.com/b/one")]:
            self.assertNotEqual(sr.normalize_url(left), sr.normalize_url(right))

    def test_history_snapshots_and_prior_versions(self):
        c = self.file("c.json", [{"id": "1", "text": "A", "ats_identity": "gem:new"}])
        h = self.file("h.json", {"jobs": {}, "history": [{"snapshot": {"jobs": [
             {"job_id": "2", "status": "blocked", "prior_history": [{"job_id": "2", "ats_identity": "gem:new"}]}]}}]})
        self.assertEqual(sr.build_report([c], [], [h])["counts"]["jobs_with_historical_identity_matches"], 1)

    def test_duplicate_identical_assessments_count_once(self):
        a = self.file("a.json", [self.assessment(), self.assessment()])
        r = sr.build_report([self.cards()], [a], [])
        self.assertEqual(r["counts"]["fully_assessed_jobs"], 1)
        self.assertEqual(len(r["jobs"][0]["assessments"]["full_jd"]["sources"]), 2)

    def test_conflicting_assessments_fail_closed(self):
        a = self.file("a.json", [self.assessment(), self.assessment(score=85)])
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            sr.build_report([self.cards()], [a], [])

    def test_screening_and_full_jd_preserved_separately(self):
        a = self.file("a.json", [{"job_id": "1", "stage": "screening", "decision": "hold", "evidence": "Need JD"}, self.assessment()])
        r = sr.build_report([self.cards()], [a], [])
        self.assertEqual(r["counts"]["fully_assessed_jobs"], 1)
        self.assertEqual(r["counts"]["screening_only_jobs"], 0)
        self.assertEqual(len(r["jobs"][0]["assessments"]), 2)

    def test_invalid_card_records(self):
        for record in [{"id": 1, "text": "A"}, {"id": "", "text": "A"}, {"id": "1", "text": ""},
                       {"id": "1", "job_id": "2", "text": "A"}, {"id": "1", "text": "A", "ats_identity": "123"}]:
            with self.subTest(record=record):
                c = self.file("bad.json", [record])
                with self.assertRaises(ValueError):
                    sr.build_report([c], [], [])

    def test_invalid_assessments(self):
        variants = [dict(stage="unknown"), dict(stage="screening"), dict(score=60), dict(score=True),
                    dict(score=float("nan")), dict(eligibility_verified=False), dict(description_source=""),
                    dict(evidence=""), dict(decision="ready_for_review")]
        for changes in variants:
            with self.subTest(changes=changes):
                a = self.file("bad.json", [self.assessment(**changes)])
                with self.assertRaises(ValueError):
                    sr.build_report([self.cards()], [a], [])

    def test_duplicate_json_keys_rejected(self):
        p = self.root / "bad.json"
        p.write_text('[{"id":"1","id":"2","text":"A"}]')
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            sr.build_report([p], [], [])

    def test_same_batch_path_not_counted_twice(self):
        c = self.cards()
        with self.assertRaises(ValueError):
            sr.build_report([c, c], [], [])

    def test_invalid_old_url_warns_no_false_complete_check(self):
        h = self.file("h.json", {"jobs": {"1": {"url": "http://old.example/job"}}})
        r = sr.build_report([self.cards()], [], [h])
        self.assertFalse(r["historical_identity_check_complete"])
        self.assertEqual(len(r["historical_identity_warnings"]), 1)
        self.assertEqual(r["counts"]["jobs_with_historical_identity_matches"], 1)

    def test_credentials_and_http_rejected(self):
        for url in ["http://example.com/job", "https://user:password@example.com/job", "/job"]:
            with self.assertRaises(ValueError):
                sr.normalize_url(url)

    def test_atomic_output_inputs_unchanged_and_deterministic(self):
        c = self.cards()
        h = self.file("h.json", {"jobs": {}})
        before = h.read_bytes()
        r = sr.build_report([c], [], [h])
        out = self.root / "report.json"
        sr.atomic_write(out, r)
        first = out.read_bytes()
        sr.atomic_write(out, sr.build_report([c], [], [h]))
        self.assertEqual(first, out.read_bytes())
        self.assertEqual(before, h.read_bytes())
        self.assertEqual(list(self.root.glob("report.json.*")), [])
        with self.assertRaises(ValueError):
            sr.atomic_write(h, r)
        alias = self.root / "alias.json"
        alias.symlink_to(h)
        with self.assertRaises(ValueError):
            sr.atomic_write(alias, r)

    def test_cli_validation_preserves_existing_output(self):
        c = self.file("bad.json", [{"id": "1"}])
        out = self.file("output.json", {"keep": True})
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            sr.main(["--cards", str(c), "--output", str(out)])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(json.loads(out.read_text()), {"keep": True})

    def test_current_pipeline_normalization_parity_when_available(self):
        path = Path(__file__).with_name("pipeline_queue.py")
        if not path.exists():
            self.skipTest("installed helper unavailable")
        spec = importlib.util.spec_from_file_location("reference_pipeline_queue", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for url in ["https://jobs.ashbyhq.com/abc/uuid/application?utm_source=x&src=y",
                    "https://EXAMPLE.com/careers/?ashby_jid=one&trackingId=x#anchor",
                    "https://example.com/job?b=2&a=&a=1&source=linkedin"]:
            self.assertEqual(sr.normalize_url(url), module.normalize_url(url))


if __name__ == "__main__":
    unittest.main()
