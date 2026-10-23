# Cloud preparation task

Use skills/daily-linkedin-prep/SKILL.md in **hybrid mode**, following AGENTS.md,
cloud/README.md and references/hybrid-workflow.md. This template is for an explicitly
authorized preparation run; coding/setup requests do not start applications.

Resolve/read the two private applicant sources with private_sources.py prepare,
preserving newer approved answers and the master baseline. Restore real duplicate
history using LINKEDIN_APPLICATION_HISTORY or an environment-visible
LINKEDIN_APPLICATION_HISTORY_PATH and the helper's returned history path; never
replace it with empty state. Retain the skill's eligibility rules.
The objective remains 15 distinct eligible applications unless the user specifies
another target, but packet counts are not live prepared-application counts.

Prepare immutable versioned packets and QA document assets with cloud_handoff.py.
Use one shared executor, not executable scripts per job. Ground answers in approved
evidence; mark unknowns and inaccessible forms explicitly. Use the computer's live
browser only if its supported integration is actually exposed, awake, online,
authenticated and authorized. Otherwise queue browser work for the local parent
using automation/local-browser-task-prompt.md. Never copy cookies or substitute a
cloud browser. Locally observed question structure may return through the private
handoff for a newer preparation revision; preserve local human edits and outcomes.

Use the selected manual-download handoff in references/manual-transfer.md: publish
packets, create a uniquely named private ZIP and SHA256 checksum, and offer actual
supported download artifacts. No shared-drive destination is required. Local receipt
and verification happen after the user transfers the batch; do not claim them from
archive creation or a cloud-only path. If download is unavailable, preserve files
and report that exact limitation. Confirm actual cloud background-execution support
and platform limits before promising laptop-independent continuation. If transport,
browser or persistence is unavailable, complete permitted offline work, checkpoint
and report the exact blocker. A directory or prompt alone configures none of these.

Use finite resumable batches: default at most 15 new preparations or 60 minutes per
invocation, two attempts per operation, and a supported explicit token/cost budget
where measurable. Reuse unchanged approved assets/answers by source/profile version;
use deterministic helpers and concise summaries instead of repeated full history,
polling or per-job agents. Retain the objective across batches without claiming an
ended session continues running.

Local completion must pass preflight and a one-job non-submitting pilot, keep one
application open by default (including held review pages/popups), and release its
slot only after actual resource closure. Never submit; the human reviews and does
final submission. Never replace a packet under review or regress a confirmed outcome.

The local parent owns Notion writes for its queue, preferring authorized connector/
API upserts with deduplication, uncertain-write reconciliation and readback. Cloud
workers do not independently write the same records. Preserve existing outcomes/
notes, blank unsubmitted dates, and the standing
manual-upload exception. Do not claim logging from a prepared payload.

Report packets, current verified drafts, missing answers, manual uploads, confirmed
human submissions, Notion pending, handoff location, stop reason and recovery steps
separately. Privately export newer context/history before ending. Never commit or
publicly publish personal files. Do not change schedules or saved model settings.
