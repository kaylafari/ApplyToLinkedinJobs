# Pipeline queue helper

This helper coordinates local worker claims, not browser actions. It never modifies
`ledger.json`. The parent retains the sole ledger owner capability and verifies
live forms before recording prepared status and acknowledging `complete`.

All commands start with `python3 pipeline_queue.py --state /absolute/run/pipeline.json`.
Commands are documented by `--help` and `<command> --help`.

## Initialization and discovery

`init --ledger /absolute/ledger.json --run-id applylinkedin-YYYY-MM-DD --target 15`

`add --candidate-file /absolute/candidate.json`

Candidate shape:

```json
{
  "company": "Example",
  "title": "Software Engineer",
  "linkedin_job_id": "1234567890",
  "application_url": "https://www.linkedin.com/jobs/view/1234567890/",
  "score": 75,
  "eligibility_verified": true,
  "duplicate_check": {"checked_at": "2026-09-29T12:00:00Z", "result": "clear"},
  "ats_identity": "provider:posting-id",
  "qualification_evidence": "Required-item scoring, exclusion checks, source URLs and observed dates"
}
```

Omit `ats_identity` until actually known. Names/title/LinkedIn ID and initial
normalized URL are immutable. Company/title alone never deduplicates. Add is
idempotent and cannot reset done/blocked/in-progress jobs. Unknown URL query keys
are retained; only known tracking keys are removed. Provider-qualified ATS IDs
and normalized URLs prevent cross-LinkedIn-ID duplicates inside this queue.
Historic ledger/submission/Notion checks remain mandatory outside the helper.

## Application worker

`claim --role application --owner application-agent [--job-id 1234567890]`

Returns random job `token`. One active application claim globally; an employer
blocker clears that claim so another ready job can proceed. No automatic expiry.

`bind --job-id ID --owner application-agent --token JOB_TOKEN --application-url URL [--ats-identity provider:id]`

Bind the freshly observed external destination before entering candidate details
or generating the résumé. A duplicate sets `must_stop_application=true`, preserves
the claim, and prevents completion/adoption. Release the browser lease, then mark
the job `reconciliation_needed` or `skipped` with exact evidence. This intentionally
does not provide an automatic duplicate-conflict override.

After live verification, parent records the prepared ledger entry, then parent:

`complete --job-id ID --owner application-agent --token JOB_TOKEN --ledger-owner PARENT_TOKEN`

The parent can migrate an already prepared same-run ledger job directly (no fake
qualification clearance or `add` needed) with:

`adopt --job-id ID --ledger-owner PARENT_TOKEN`

Done requires every preparation gate, same run, actual prepared ledger status,
existing SHA256-matching résumé, and distinct preserved application tab. These are recorded assertions;
the parent must still inspect live browser evidence. Target capacity comes from
actual ledger prepared count, including pre-migration work, not worker claims.

`block --role application --job-id ID --owner application-agent --token JOB_TOKEN --status blocked --reason 'Exact employer-local blocker'`

Optional exception statuses: `blocked`, `needs_user_input`, `skipped`,
`reconciliation_needed`. None count as done. Workers must not label unknown
required answers fully ready or submit anything.

## Notion worker

`claim --role notion --owner notion-agent`

Only `done` jobs with `notion_status=pending` can be claimed, and the current ledger
is rechecked. One active Notion claim globally. After deduplication and saved-value
verification:

`note --job-id ID --owner notion-agent --token JOB_TOKEN --receipt-file /absolute/receipt.json`

Receipt requires `row_url` and `verified: true`; include saved field values, blank
Application Date, review status, verification timestamp, and page-body evidence.
Status becomes `notion_logged`. Identical receipt retries are idempotent, while
conflicting retries with the same claim token fail. Notion writes are still external
actions performed by the worker; the helper does not verify them independently.

For uncertain writes, `block --role notion ... --reason 'Exact uncertainty'` retains
done status but sets Notion blocked. Reconcile the actual row before parent retry.

## Shared external-browser mutation lease

Each worker must acquire the common lease before browser mutations and release
it immediately after its small browser batch. PDF work and detached job-description
scoring can continue without it. The lease applies across all browser worker roles.

`claim-browser --role discovery --owner discovery-agent`

`claim-browser --role application --owner application-agent --job-id ID --token JOB_TOKEN`

`claim-browser --role notion --owner notion-agent --job-id ID --token JOB_TOKEN`

Returns distinct `browser_token`; release with:

`release-browser --owner AGENT --token BROWSER_TOKEN`

No application completion/block/ownership transition while that job's browser
lease remains held. A helper cannot prevent an agent bypassing the protocol; all
workers must follow the skill. Discovery has no job token until it has candidates.

## Recovery and visibility

`status` or `list [--status ready_for_application]` hides claim capabilities.

Parent-only retry after fresh evidence resolves a recorded blocker:

`retry --job-id ID --ledger-owner PARENT_TOKEN --reason 'New observed evidence'`

If a previously done job no longer passes current ledger verification (for
example its preserved tab was closed and the parent updated that evidence), stop
its Notion handoff and explicitly invalidate it:

`invalidate --job-id ID --ledger-owner PARENT_TOKEN --reason 'Parent verified lost tab and updated ledger evidence'`

This requires no active job claim or browser lease; recover a stopped owner first.
It refuses invalidation while the ledger still verifies the job as prepared.
The queue becomes `reconciliation_needed` with Notion `suspended`, retaining prior
completion evidence, receipts, and event history. Other done jobs can then proceed
to Notion. Parent may later `retry` with new evidence to resume application work,
or `adopt` after repairing and verifying the prepared ledger record. A stale oldest
Notion candidate deliberately raises an error until the parent reconciles it;
do not repeatedly retry the unchanged claim or falsify success.

For abandoned active claims, parent first confirms the exact owner has stopped:

`recover --role ROLE --job-id ID --owner OLD_AGENT --token OLD_JOB_TOKEN --ledger-owner PARENT_TOKEN --owner-check 'Stopped agent and re-observed affected form'`

For abandoned browser lease, release/recover it first:

`recover-browser --owner OLD_AGENT --token OLD_BROWSER_TOKEN --ledger-owner PARENT_TOKEN --owner-check 'Stopped owner and re-observed browser state'`

Never infer abandonment from elapsed time. Never discard history, replace the
ledger, retry uncertain external writes blindly, or count a résumé-only job as done.
The helper is a coordination safeguard, not a security boundary against a worker
with direct filesystem access. Parent exception tracking remains required for
blocked/partial jobs under the skill even though the Notion worker takes only done.
