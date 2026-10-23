"""Synthetic hybrid pipeline regressions; no personal inputs or browser needed."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

import cloud_handoff as h
import ledger
from verify_resume import REPORTS


def concurrent_acquire(path):
    try:
        return h.operate(path, "acquire", owner="competing-agent")
    except ValueError:
        return {"rejected": True}


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "local" / "execution.json"
        self.history = self.root / "history.json"
        self.history.write_text(json.dumps({"version": 1, "jobs": {}, "runs": {}, "history": [], "owner": None}))
        h.operate(self.state, "init")
        self.token = h.operate(self.state, "acquire", owner="local-agent")["token"]
        self.first, self.first_id = self.packet("101")
        self.second, self.second_id = self.packet("102")

    def packet(self, job_id, revision=1, job_changes=None):
        source = self.root / ("packet-" + job_id + "-" + str(revision))
        source.mkdir()
        pdf_dir = source / "resume"
        pdf_dir.mkdir()
        pdf = pdf_dir / "Kayla_Farivar_Resume.pdf"
        pdf.write_bytes(b"%PDF-1.4 synthetic " + job_id.encode())
        for name in REPORTS:
            (pdf_dir / name).write_text("synthetic report")
        qa = {"job_id": job_id, "status": "qa_passed", "artifact_path": str(pdf),
              "sha256": h.digest(pdf.read_bytes()),
              "qa": {key: True for key in ("evidence", "ats_text", "layout", "page_count")}}
        (source / "local-qa.json").write_text(json.dumps(qa))
        p = {"schema_version": 1, "applicant_id": "synthetic-applicant", "revision": revision,
             "prepared_at": h.now(), "profile_version": "fixture-v1", "source_sha256": "a" * 64,
             "job": {"linkedin_job_id": job_id, "company": "Example", "title": "Software Engineer",
                     "url": "https://www.linkedin.com/jobs/view/" + job_id,
                     "application_url": "https://jobs.example.com/" + job_id, "ats_identity": "fixture:" + job_id},
             "answers": [{"question": "Example unknown", "value": None, "sources": [],
                          "confidence": "low", "requires_review": True}],
             "form": {"coverage": "uninspected", "observed_at": None, "fingerprint": None, "questions": []},
             "assets": [{"path": "resume/" + pdf.name, "sha256": h.digest(pdf.read_bytes()), "kind": "resume"}]}
        p["job"].update(job_changes or {})
        (source / "manifest.json").write_text(json.dumps(p))
        result = h.operate(self.state, "import", token=self.token, packet=source)
        return source, result["application_id"]

    def call(self, command, **args):
        return h.operate(self.state, command, token=self.token, **args)

    def receipt(self, **changes):
        return {"reference": "private-synthetic-evidence", "checked_at": h.now(), **changes}

    def inventory(self, resources=None):
        keys = h.read_json(self.state)["jobs"]
        return self.receipt(all_runner_resources_observed=True, unassigned_resources=[],
                            resources={key: (resources or {}).get(key, []) for key in keys})

    def preflight(self, resources=None):
        r = {**self.inventory(resources), **{k: True for k in h.PREFLIGHT},
             "browser_session": "synthetic-session", "history_path": str(self.history)}
        return self.call("preflight", receipt=r)

    def claim(self, key=None):
        return self.call("claim", application_id=key or self.first_id)

    def held(self):
        return {self.first_id: [{"id": "application-tab", "kind": "tab"}]}

    def ready(self):
        self.bind(self.first_id)
        self.call("reconcile", receipt=self.inventory(self.held()))
        return self.call("report", application_id=self.first_id, status="ready_for_review",
                         receipt=self.receipt(**{g: True for g in h.GATES}, submission_performed=False,
                                              required_unresolved=[], validation_errors=[], inaccessible_steps=[],
                                              resume_manifest=str(self.first / "local-qa.json")))

    def bind(self, key):
        job = h.read_json(self.state)["jobs"][key]
        return self.call("bind", application_id=key,
                         receipt=self.receipt(identity_verified=True,
                                              application_url=job["identity"]["application_url"],
                                              ats_identity=job["identity"]["ats_identity"]))

    def test_immutable_publication_and_declared_assets_only(self):
        publication = h.publish(self.first, self.root / "handoff")
        destination = Path(publication["packet"])
        self.assertFalse(publication["transport_verified"])
        self.assertFalse((destination / "local-qa.json").exists())
        self.assertEqual(h.publish(self.first, self.root / "handoff")["sha256"], publication["sha256"])
        m = h.read_json(self.first / "manifest.json")
        m["profile_version"] = "changed-without-revision"
        (self.first / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "immutable"):
            h.publish(self.first, self.root / "handoff")

    def test_asset_tamper_and_traversal_rejected(self):
        m = h.read_json(self.first / "manifest.json")
        m["assets"][0]["path"] = "../outside.pdf"
        (self.first / "manifest.json").write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            h.validate_packet(self.first)
        m["assets"][0]["path"] = "resume/Kayla_Farivar_Resume.pdf"
        (self.first / "manifest.json").write_text(json.dumps(m))
        (self.first / m["assets"][0]["path"]).write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            h.validate_packet(self.first)

    def test_no_preflight_no_browser_claim(self):
        with self.assertRaisesRegex(ValueError, "preflight"):
            self.claim()

    def test_new_revision_preserves_human_edits_and_pinned_packet(self):
        self.call("note", application_id=self.first_id, receipt=self.receipt(note="Human-specific correction"))
        _, key = self.packet("101", revision=2)
        job = h.read_json(self.state)["jobs"][key]
        self.assertEqual(job["active_revision"], 1)
        self.assertEqual(job["human_notes"][0]["note"], "Human-specific correction")
        with self.assertRaisesRegex(ValueError, "approval"):
            self.call("activate-revision", application_id=key, revision=2, receipt=self.receipt())
        self.call("activate-revision", application_id=key, revision=2, receipt=self.receipt(human_approved=True))
        self.assertEqual(h.read_json(self.state)["jobs"][key]["active_revision"], 2)

    def test_submission_does_not_release_browser_slot(self):
        self.preflight()
        self.claim()
        self.ready()
        self.call("pilot", application_id=self.first_id, receipt=self.receipt(non_submitting_pilot_passed=True))
        self.call("report", application_id=self.first_id, status="submitted",
                  receipt=self.receipt(confirmation_source="human", confirmation="I submitted this job", submitted_at=h.now()))
        with self.assertRaisesRegex(ValueError, "Close prior"):
            self.claim(self.second_id)
        self.call("reconcile", receipt=self.inventory())
        self.assertEqual(self.claim(self.second_id)["application_id"], self.second_id)

    def test_ready_requires_verified_attachment_and_packet_resume(self):
        self.preflight()
        self.claim()
        self.bind(self.first_id)
        self.call("reconcile", receipt=self.inventory(self.held()))
        r = self.receipt(**{g: True for g in h.GATES}, submission_performed=False,
                         required_unresolved=[], validation_errors=[], inaccessible_steps=[],
                         resume_manifest=str(self.first / "local-qa.json"))
        r["resume_attachment_verified"] = False
        with self.assertRaisesRegex(ValueError, "Ready requires"):
            self.call("report", application_id=self.first_id, status="ready_for_review", receipt=r)
        r["resume_attachment_verified"] = True
        r["resume_manifest"] = str(self.second / "local-qa.json")
        with self.assertRaisesRegex(ValueError, "QA-passed"):
            self.call("report", application_id=self.first_id, status="ready_for_review", receipt=r)

    def test_pilot_blocks_long_queue(self):
        self.preflight()
        self.claim()
        self.call("report", application_id=self.first_id, status="failed", receipt=self.receipt(submission_performed=False))
        with self.assertRaisesRegex(ValueError, "pilot"):
            self.claim(self.second_id)

    def test_submission_cannot_regress_on_reimport_or_report(self):
        self.preflight()
        self.call("report", application_id=self.first_id, status="submitted",
                  receipt=self.receipt(confirmation_source="visible_success", confirmation="Application received", submitted_at=h.now()))
        self.packet("101", revision=2)
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "submitted")
        with self.assertRaisesRegex(ValueError, "cannot regress"):
            self.call("report", application_id=self.first_id, status="failed", receipt=self.receipt(submission_performed=False))
        with self.assertRaisesRegex(ValueError, "held for review"):
            self.claim()

    def test_restart_requires_inventory_and_keeps_held_tabs(self):
        self.preflight()
        self.claim()
        self.ready()
        recovery = self.receipt(owner_stopped=True)
        old = self.token
        self.token = h.operate(self.state, "recover", owner="replacement-agent", token=old, receipt=recovery)["token"]
        with self.assertRaisesRegex(ValueError, "preflight"):
            self.claim(self.second_id)
        with self.assertRaisesRegex(ValueError, "current consumer"):
            h.operate(self.state, "pause", token=old, receipt=self.receipt())
        self.preflight(self.held())
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["resources"], self.held()[self.first_id])

    def test_resource_inventory_rejects_missing_shared_or_excess_pages(self):
        self.preflight()
        incomplete = self.inventory()
        incomplete["resources"].pop(self.first_id)
        with self.assertRaisesRegex(ValueError, "every imported"):
            self.call("reconcile", receipt=incomplete)
        duplicate = self.inventory({self.first_id: [{"id": "shared", "kind": "tab"}], self.second_id: [{"id": "shared", "kind": "tab"}]})
        with self.assertRaisesRegex(ValueError, "two applications"):
            self.call("reconcile", receipt=duplicate)
        excess = self.inventory({self.first_id: [{"id": str(i), "kind": "popup"} for i in range(5)]})
        with self.assertRaisesRegex(ValueError, "cap exceeded"):
            self.call("reconcile", receipt=excess)

    def test_pause_needs_new_preflight_and_resumes_existing_job(self):
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        self.call("pause", receipt=self.receipt())
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "waiting_for_local_browser")
        with self.assertRaisesRegex(ValueError, "preflight"):
            self.claim()
        # Explicit one-job pilot can be retried in a new consumer session.
        self.token = h.operate(self.state, "recover", owner="resumed-agent", token=self.token, receipt=self.receipt(owner_stopped=True))["token"]
        self.preflight(self.held())
        self.assertTrue(self.claim()["reuse_resources"])

    def test_history_guard_blocks_legacy_pipeline_then_leaves_duplicates(self):
        self.preflight()
        with self.assertRaisesRegex(ValueError, "Hybrid browser queue"):
            ledger.transact(self.history, argparse.Namespace(command="begin", target=1, day="2026-10-22", owner=None))
        self.claim()
        self.call("report", application_id=self.first_id, status="failed", receipt=self.receipt(submission_performed=False))
        self.call("release")
        hist = h.read_json(self.history)
        self.assertNotIn("hybrid_owner", hist)
        self.assertEqual(hist["jobs"]["101"]["status"], "reconciliation_needed")
        self.assertEqual(hist["jobs"]["101"]["hybrid_status"], "failed")

    def test_prior_submission_or_active_legacy_owner_blocks(self):
        hist = h.read_json(self.history)
        hist["jobs"]["101"] = {"status": "submitted"}
        self.history.write_text(json.dumps(hist))
        self.preflight()
        with self.assertRaisesRegex(ValueError, "Historical identity"):
            self.claim()
        hist["owner"] = {"token": "legacy-token", "run_id": "legacy-run"}
        self.history.write_text(json.dumps(hist))
        with self.assertRaisesRegex(ValueError, "Another pipeline"):
            self.preflight()

    def test_notion_outbox_survives_failure_and_reimport(self):
        self.preflight()
        self.claim()
        self.ready()
        pending = self.call("outbox")["next"]
        self.packet("101", revision=2)
        self.assertEqual(self.call("outbox")["next"]["id"], pending["id"])
        with self.assertRaisesRegex(ValueError, "readback"):
            self.call("ack-notion", outbox_id=pending["id"], receipt=self.receipt(row_url="https://www.notion.so/fixture"))
        self.assertEqual(self.call("outbox")["next"]["id"], pending["id"])
        self.call("ack-notion", outbox_id=pending["id"], receipt=self.receipt(row_url="https://www.notion.so/fixture", reopened_verified=True, existing_outcomes_preserved=True))
        self.assertIsNone(self.call("outbox")["next"])
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "ready_for_review")

    def test_concurrent_owners_and_state_reset_rejected(self):
        self.call("release")
        with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(concurrent_acquire, [str(self.state)] * 3))
        self.assertEqual(sum("token" in r for r in results), 1)
        with self.assertRaisesRegex(ValueError, "already exists"):
            h.operate(self.state, "init")

    def test_pinned_asset_tamper_rejected_before_claim(self):
        self.preflight()
        (self.first / "resume/Kayla_Farivar_Resume.pdf").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.claim()

    def test_claim_and_time_budgets(self):
        self.preflight()
        state = h.read_json(self.state)
        state["owner"]["claims"] = state["max_claims"]
        self.state.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "budget reached"):
            self.claim()

        state["owner"]["claims"] = 0
        state["owner"]["started_at"] = "2020-01-01T00:00:00+00:00"
        self.state.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "budget reached"):
            self.claim()

    def test_form_observation_roundtrip_uses_same_slot_and_requires_matching_revision(self):
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        form = {"coverage": "complete", "observed_at": h.now(), "fingerprint": "observed-form-v1",
                "questions": ["Example question; required; options A/B"]}
        r = self.receipt(form=form, identity_verified=True, no_values_or_secrets=True, no_human_edits_verified=True)
        output = self.root / "outgoing" / "form-observation.json"
        self.call("observe-form", application_id=self.first_id, receipt=r, output=output)
        self.assertEqual(h.read_json(output)["form"], form)
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "waiting_for_preparation")
        packet, _ = self.packet("101", revision=2)
        acceptance = self.receipt(no_human_edits_verified=True, form_revalidated=True, fingerprint=form["fingerprint"])
        with self.assertRaisesRegex(ValueError, "exact observed"):
            self.call("accept-preparation", application_id=self.first_id, revision=2, receipt=acceptance)
        # A new immutable revision must contain the actual observation.
        packet3, _ = self.packet("101", revision=3)
        manifest = h.read_json(packet3 / "manifest.json")
        manifest["form"] = form
        (packet3 / "manifest.json").write_text(json.dumps(manifest))
        # Imported revisions cannot change: prepare a new revision, not overwrite.
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.call("import", packet=packet3)
        manifest["revision"] = 4
        (packet3 / "manifest.json").write_text(json.dumps(manifest))
        self.call("import", packet=packet3)
        result = self.call("accept-preparation", application_id=self.first_id, revision=4, receipt=acceptance)
        self.assertTrue(result["reuse_resources"])
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["attempts"], 1)

    def manual_upload(self):
        self.preflight()
        self.claim()
        self.bind(self.first_id)
        self.call("reconcile", receipt=self.inventory(self.held()))
        r = self.receipt(**{g: True for g in h.GATES}, submission_performed=False,
                         validation_errors=[], inaccessible_steps=[], manual_upload=True,
                         upload_recovery_exhausted=True, missing_upload_reason="Synthetic upload outage",
                         resume_manifest=str(self.first / "local-qa.json"))
        r["resume_attachment_verified"] = False
        self.call("report", application_id=self.first_id, status="needs_input", receipt=r)

    def test_manual_upload_outbox_retains_pdf_and_does_not_claim_ready(self):
        self.manual_upload()
        pending = self.call("outbox")["next"]
        self.assertTrue(pending["manual_upload"])
        self.assertTrue(pending["allow_create"])
        self.assertEqual(pending["resume"]["path"], str(self.first / "resume/Kayla_Farivar_Resume.pdf"))
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "needs_input")

    def test_lost_review_tab_invalidates_readiness_and_supersedes_pending_write(self):
        self.preflight()
        self.claim()
        self.ready()
        original = self.call("outbox")["next"]
        self.call("reconcile", receipt=self.inventory())
        correction = self.call("outbox")["next"]
        self.assertNotEqual(original["id"], correction["id"])
        self.assertEqual(correction["status"], "needs_input")
        self.assertFalse(correction["allow_create"])
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "needs_input")

    def test_outbox_submission_supersedes_prepared_and_ack_retry_is_idempotent(self):
        self.preflight()
        self.claim()
        self.ready()
        original = self.call("outbox")["next"]
        self.call("report", application_id=self.first_id, status="submitted",
                  receipt=self.receipt(confirmation_source="human", confirmation="Submitted", submitted_at=h.now()))
        current = self.call("outbox")["next"]
        self.assertNotEqual(current["id"], original["id"])
        self.assertEqual(current["status"], "submitted")
        r = self.receipt(row_url="https://www.notion.so/fixture", reopened_verified=True, existing_outcomes_preserved=True)
        with self.assertRaisesRegex(ValueError, "oldest pending"):
            self.call("ack-notion", outbox_id=original["id"], receipt=r)
        self.call("ack-notion", outbox_id=current["id"], receipt=r)
        self.assertTrue(self.call("ack-notion", outbox_id=current["id"], receipt=r)["idempotent"])

    def observe(self, fingerprint="form-v1", suffix="initial"):
        form = {"coverage": "complete", "observed_at": h.now(), "fingerprint": fingerprint,
                "questions": ["Synthetic question; required"]}
        self.call("observe-form", application_id=self.first_id,
                  receipt=self.receipt(form=form, identity_verified=True, no_values_or_secrets=True, no_human_edits_verified=True),
                  output=self.root / (suffix + "-observation.json"))
        return form

    def test_changed_form_can_be_reobserved_and_expired_while_waiting(self):
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        self.observe()
        changed = self.observe("changed-form-v2", "changed")
        job = h.read_json(self.state)["jobs"][self.first_id]
        self.assertEqual(job["form_observation"], changed)
        self.assertEqual(job["resources"], self.held()[self.first_id])
        self.call("report", application_id=self.first_id, status="expired", receipt=self.receipt(submission_performed=False))
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["status"], "expired")

    def test_acceptance_cannot_start_second_active_application(self):
        state = h.read_json(self.state)
        state["max_open_applications"] = 2
        state["pilot_passed"] = True  # Exercise post-pilot configurable-cap workload.
        self.state.write_text(json.dumps(state))
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        form = self.observe()
        packet, _ = self.packet("101", revision=2)
        manifest = h.read_json(packet / "manifest.json")
        manifest.update(revision=3, form=form)
        (packet / "manifest.json").write_text(json.dumps(manifest))
        self.call("import", packet=packet)
        self.claim(self.second_id)
        r = self.receipt(no_human_edits_verified=True, form_revalidated=True, fingerprint=form["fingerprint"])
        with self.assertRaisesRegex(ValueError, "sequential"):
            self.call("accept-preparation", application_id=self.first_id, revision=3, receipt=r)
        self.call("report", application_id=self.second_id, status="needs_input", receipt=self.receipt(submission_performed=False))
        self.call("accept-preparation", application_id=self.first_id, revision=3, receipt=r)
        jobs = h.read_json(self.state)["jobs"]
        self.assertEqual(sum(j["status"] == "in_progress" for j in jobs.values()), 1)

    def test_multiple_sleep_resumes_do_not_exhaust_technical_retries(self):
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        for _ in range(3):
            self.call("pause", receipt=self.receipt())
            self.preflight(self.held())
            self.assertTrue(self.claim()["reuse_resources"])
        job = h.read_json(self.state)["jobs"][self.first_id]
        self.assertEqual(job["attempts"], 4)
        self.assertEqual(job.get("technical_failures", 0), 0)

    def test_crashed_active_owner_recovers_without_manual_state_edits(self):
        self.preflight()
        self.claim()
        self.call("reconcile", receipt=self.inventory(self.held()))
        self.token = h.operate(self.state, "recover", owner="replacement", token=self.token,
                               receipt=self.receipt(owner_stopped=True))["token"]
        self.preflight(self.held())
        self.assertTrue(self.claim()["reuse_resources"])

    def test_technical_retry_exhaustion_requires_changed_evidence(self):
        self.preflight()
        for _ in range(2):
            self.claim()
            self.call("report", application_id=self.first_id, status="failed", receipt=self.receipt(submission_performed=False))
        with self.assertRaisesRegex(ValueError, "technical retry budget"):
            self.claim()
        with self.assertRaisesRegex(ValueError, "changed recovery evidence"):
            self.call("retry", application_id=self.first_id, receipt=self.receipt())
        self.call("retry", application_id=self.first_id,
                  receipt=self.receipt(recovery_verified=True, changed_evidence="Synthetic dependency restored and effects reconciled"))
        self.claim()
        job = h.read_json(self.state)["jobs"][self.first_id]
        self.assertEqual(job["attempts"], 3)
        self.assertEqual(len(job["recovery_receipts"]), 1)

    def test_lost_manual_upload_page_supersedes_create_eligible_intent(self):
        self.manual_upload()
        previous = self.call("outbox")["next"]
        self.call("reconcile", receipt=self.inventory())
        correction = self.call("outbox")["next"]
        self.assertNotEqual(previous["id"], correction["id"])
        self.assertFalse(correction["allow_create"])
        self.assertFalse(correction["manual_upload"])
        self.assertIsNotNone(correction["resume"])
        job = h.read_json(self.state)["jobs"][self.first_id]
        self.assertIn("Review pages closed", job["review_blocker"])
        self.assertEqual(job["last_receipt"]["manual_upload"], True)

    def test_lost_acknowledged_manual_upload_page_queues_existing_row_correction(self):
        self.manual_upload()
        previous = self.call("outbox")["next"]
        r = self.receipt(row_url="https://www.notion.so/fixture", reopened_verified=True, existing_outcomes_preserved=True)
        self.call("ack-notion", outbox_id=previous["id"], receipt=r)
        self.call("reconcile", receipt=self.inventory())
        correction = self.call("outbox")["next"]
        self.assertFalse(correction["allow_create"])
        self.assertFalse(correction["manual_upload"])
        self.assertEqual(h.read_json(self.state)["jobs"][self.first_id]["notion_receipt"], r)

    def test_observed_redirect_binding_prevents_duplicate_and_accepts_new_grounded_url(self):
        packet, key = self.packet("103", job_changes={"ats_identity": None})
        self.preflight()
        self.claim(key)
        with self.assertRaisesRegex(ValueError, "Redirect duplicates"):
            self.call("bind", application_id=key,
                      receipt=self.receipt(identity_verified=True, application_url="https://jobs.example.com/102"))
        destination = "https://jobs.ashbyhq.com/example/unique-posting"
        self.call("bind", application_id=key,
                  receipt=self.receipt(identity_verified=True, application_url=destination, ats_identity="ashby:unique-posting"))
        hist = h.read_json(self.history)["jobs"]["103"]
        self.assertEqual(hist["application_url"], destination)
        self.assertIn("https://jobs.example.com/103", hist["url_aliases"])
        _, revised = self.packet("103", revision=2, job_changes={"application_url": destination, "ats_identity": "ashby:unique-posting"})
        self.assertEqual(revised, key)

    def test_redirect_binding_checks_restored_history_before_filling(self):
        packet, key = self.packet("103", job_changes={"ats_identity": None})
        hist = h.read_json(self.history)
        hist["jobs"]["999"] = {"status": "submitted", "application_url": "https://jobs.example.com/actual-destination"}
        self.history.write_text(json.dumps(hist))
        self.preflight()
        self.claim(key)
        with self.assertRaisesRegex(ValueError, "matches historical work"):
            self.call("bind", application_id=key,
                      receipt=self.receipt(identity_verified=True, application_url="https://jobs.example.com/actual-destination"))
        self.assertNotIn("destination", h.read_json(self.state)["jobs"][key])


if __name__ == "__main__":
    unittest.main()
