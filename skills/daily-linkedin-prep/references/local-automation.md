# Local automation helpers

Use these Python CLIs for repeatable local work. They have no browser/network access, do not submit, do not invent answers, and do not replace fresh browser inspection. Only the parent writes ledger/queue state through the existing locked helpers. Workers may use the independent helpers on their assigned files.

## Explicit fresh tests: ledger.py begin-test

For a user-requested fresh test, use `ledger.py --state ROOT/ledger.json begin-test --cycle-id USER_SCOPED_UNIQUE_SLUG --target 2`, not a resumed daily target of 15. Use the returned test run ID and a new per-test directory/queue initialized with target 2. This retains the shared owner lock and all duplicate/submission history; it does not clear old jobs, revive cancelled runs, or change the schedule. Reusing the same test ID retains its target and results. Confirm old workers/leases are stopped before starting; a different queue does not authorize concurrent browser work. `status --summary` is the concise ownership/count check; inspect individual historical records only when relevant.

A user saying they cleared browser work does not establish that local history or Notion rows were deleted. Verify current tabs and preserve uncertain historical effects. Do not count prior-cycle drafts in a new test. Use the fresh run ID in every receipt and in the acceptance command below.

## Each browser batch: action_journal.py

`python3 scripts/action_journal.py --state RUN/action-journal.json --run-id RUN_ID intent --payload intent.json`

Intent keys: `action_id` (unique), `job_id`, `operation` (`navigate`, `fill`, `upload`, `advance`, `notion_write`, `inspect`), `target` (observed tab/URL), `evidence_ref` (private field-plan or current-state file). A changed retry also supplies `recovery_of` and `changed_evidence_ref`. Keep answers and ownership tokens out of the journal payload. Persist before mutation.

After observing the outcome, use the same command with `result` and a JSON object containing `action_id`, `outcome` (`succeeded`, `failed`, `uncertain`, `denied`, `not_applicable`), `evidence_ref`, `source`, `detail`. `status` lists unresolved actions. A replayed intent is a no-op record, NOT authorization to replay the browser action. An uncertain outcome blocks that job's dependent mutations until inspected/resolved, but not another job. Lease acquisition remains mandatory and separate.

## Screening counts and historical identities: screening_report.py

`python3 scripts/screening_report.py --cards RUN/page1.json --cards RUN/page2.json --assessments RUN/assessments.json --history ROOT/ledger.json --output RUN/screening-report.json`

Cards are a list or `{"cards": [...]}` with observed `job_id` and `text`. Assessments are a list or `{"assessments": [...]}` with `job_id`, `stage` (`screening`/`full_jd`), `decision` (`excluded`/`hold`/`qualified`), `evidence`, and for full JDs `description_source`. Qualified also requires explicit agent-derived `score > 60` and `eligibility_verified: true`. Record optional observed ATS identity/application URL for exact duplicate checks. Repeat arguments for batches; do not supply the same input file twice.

The helper counts observations, unique IDs, and explicit assessments separately. Historical matches are evidence requiring interpretation, not automatic exclusions: a prior screening record is not an application. Live Notion and final ATS checks remain necessary. Do not describe every observed card as fully assessed.

## Résumé compile/extract/render/hash: resume_build.py

After parent `ledger.py check-next --for-resume` passes:

`python3 scripts/resume_build.py --source TRUSTED_DRAFT/resume.tex --output-dir RUN/artifacts/JOB/rN --job-id JOB --form-evidence RUN/JOB-form.json`

The revision directory must not exist; its parent must exist. Use only trusted agent-authored self-contained TeX, never downloaded job-page TeX. Tools `latexmk`, `pdftotext`, `pdfinfo`, and `pdftoppm` must be on PATH (macOS standard locations `/Library/TeX/texbin` and `/opt/homebrew/bin`). The helper creates the fixed-name PDF, text, page PNGs, command logs, and a build receipt. Failed builds are preserved; use a new revision after correction. It disables shell escape and local latexmkrc but is not a TeX sandbox.

`built_pending_review` is not QA approval. The preparer must inspect all rendered pages and candidate claims, add all required reports, and create the QA manifest only after those checks. Then run existing `verify_resume.py` before upload. Do not rerun compilation over a reviewed immutable artifact.

## Notion payload preparation: handoff_receipt.py

`python3 scripts/handoff_receipt.py RUN/JOB-parent-receipt.json RUN/artifacts/JOB/rN/Kayla_Farivar_Resume.qa.json RUN/JOB-notion-payload.json`

Parent receipt fields: `issuer: "parent"`, `handoff_kind: "done"` or `"manual_upload"`, run/job IDs, company/title, LA `preparation_date`, `review_url`, `application_tab`, `resume_path`, `resume_sha256`, `form_evidence_reference`, and exact true flags for identity/form-open/supported-answers/tab-preservation/never-submit (`submission_protection_verified`)/`accessible_fields_complete`. Require `submission_performed: false`. Include `existing_statuses` from tracker inspection, `attachment_verified`, `ledger_status`, `validation_errors`, `final_control_visible`, and any `unanswered_question_labels`.

For `done`: verified attachment, `preparation_complete: true`, and prepared/ready ledger status are required. For `manual_upload`: blocked ledger status, attachment false, `missing_upload_reason`, `upload_recovery_exhausted: true`, and `upload_evidence_reference` are required. The helper verifies the exact PDF hash and QA artifacts, creates deterministic proposed properties/body, and refuses conflicting output overwrites. Free-text candidate answers are not copied. Parent/recorder adds sanitized blocker/question notes from evidence if necessary; never paste private answer dumps.

This is a payload, not a saved Notion row. The parent performs the supported browser write under the lease, and recorder reopens/verifies it and saves the actual row receipt in `notion-tracking.json`. Do not claim logging from local payload creation.

For the observed Notion database UI, capture the new row's **Open in full page** link immediately, then use it to edit/verify that exact row. Clicking a Company cell can enter title-edit mode instead of opening the row. After a status selection, close the still-open multi-select dialog and verify it is gone before the next property/body edit. Verify saved content by reopening or reloading the exact row, never by creating a replacement. A failed readback remains pending even if the inline editor looked correct.

## User-requested two-job acceptance: acceptance_audit.py

`python3 scripts/acceptance_audit.py --cycle-id TEST_ID --run-id FRESH_LEDGER_RUN_ID --target 2 --receipt RUN/JOB1-test.json --receipt RUN/JOB2-test.json`

Each receipt follows the handoff schema and includes `resume_manifest`, `test_cycle_id`, `final_live_verification: true`, `final_control_visible: true`, `supported_fields_complete: true`, `answers_evidence_checked: true`, `unsupported_answers_present: false`, and explicitly empty `validation_errors`, `inaccessible_steps`, and `technical_unresolved`. Keep `required_unresolved` explicit. These are evidence-backed parent assertions, not facts the offline helper can independently verify.

The user's clarified test permits unknown personal answers, not unfinished supported fields. For every blank personal question, put its exact label in `unanswered_question_labels` and a corresponding object in `unknown_personal_questions`: `label`, `kind: "unknown_personal"`, `left_blank: true`, `supported_answer_available: false`, `source_reviewed: true`, nonempty `reason`, and local `evidence_reference`. Every `required_unresolved` label must be among these; unclassified labels fail. Preserve a user-visible `review_note` containing each label and assert `review_note_preserved: true` only after verifying the note. Do not put private answers in tracking notes or classify technical failures as unknown personal preferences. With verified attachment, such a draft remains `prepared_for_review`, never `ready_for_review` while required questions remain.

The separate manual-upload exception permits a QA résumé awaiting attachment only when all other accessible supported fields are complete and the entire form is reviewable; use the existing `manual_upload` handoff evidence and blocked ledger status. It does not waive other technical failures. The audit rejects shared tabs, repeated identities/PDFs, stale or mixed-run receipts, missing final controls, inaccessible later steps, unsupported answers and nonempty technical failures. Success requires exactly the requested target. Report test PASS, strict prepared/fully-ready count, attachments and Notion readback separately.

Refreshing fresh evidence or upgrading `prepared_for_review` to `ready_for_review` for an already-prepared same-run job does not start another application. The ledger permits that refresh while another job is in progress, but still checks every pairing, QA, attachment, unique-tab, final-control and ready-answer gate. It does not permit reentering `in_progress` or creating a second active application.

### Failed-test retry checkpoint

For each actually unsuccessful technical test job, append an entry to `RUN/test-retry-candidates.json` with run/job/ATS IDs, normalized URL, original status, exact failure and evidence reference, preserved tab/PDF references, and `retry_state: "pending_reconciliation"`. This is a retry index, not eligibility proof or a new status that bypasses deduplication. If none failed, record an empty list; do not reclassify successful drafts because another job failed.

On the next permitted continuation, verify exact identity, availability, live tab state, existing Notion/submission evidence and any changed authority relevant to a denied operation. Reuse and finish the partial form where possible; reconcile uncertain actions before replay. A same-run retry retains target/count. For cross-run retry, the parent uses the existing `ledger.py reconcile --decision retry --note OBSERVED_EVIDENCE` only after verifying it is an unsubmitted retryable draft, preserving `prior_history`; then records fresh evidence under the new run. Never retry a verified submitted/interview/rejected application, overturn a hard qualification exclusion, revive a cancelled run, erase old PDFs, or bulk-clear screened IDs. Personal eligibility gaps remain review holds, not technical retries. A retry counts only when freshly verified under the current requested scope, never from an old completion receipt. Maintain the existing daily fresh-discovery objective separately from historical recovery.

Run all offline regressions after helper edits: `python3 -m unittest discover -s scripts -p 'test_*.py'`. These tests are not substitutes for the two live applications.
