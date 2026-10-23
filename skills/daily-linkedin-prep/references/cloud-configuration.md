# Private sources and cloud configuration

The cloud-ready setup lives in the repository's `cloud/README.md`. This skill needs exactly two maintained personal files: candidate-context.md and master-resume.txt, both kept outside the Git checkout.

Supply `LINKEDIN_CANDIDATE_CONTEXT` as the full UTF-8 consolidated-context text, and `LINKEDIN_MASTER_RESUME` as the full UTF-8 master-resume text. These are text values, not PDF bytes, local desktop paths, network-secret placeholders, or examples. Single-line editors may use `base64:` followed by base64-encoded UTF-8 bytes; the helper decodes the prefix losslessly before writing files. Base64 is encoding, not encryption; the encoded value is equally private. Use your private cloud environment/personal vault; never paste the real values into a Git-tracked `.env` file, issue, commit, task prompt, terminal command, or public setup log.

Run `python3 scripts/private_sources.py prepare` from this skill directory, or `python3 skills/daily-linkedin-prep/scripts/private_sources.py prepare` from the repository root. It writes 0600 files in a private directory (default `~/.local/share/linkedin-prep`, mode 0700), preserves exact UTF-8 bytes, and prints only paths plus tracker-configuration status. `check` verifies existing prepared files without creating them. It checks presence/integrity of inputs, not truth, approval, current browser state, or form readiness.

Optional `LINKEDIN_PRIVATE_DIR` and `LINKEDIN_STATE_DIR` must be absolute paths outside the checkout. Provide `LINKEDIN_NOTION_TRACKER_URL` separately for the authorized live tracker. Read its value privately; do not hardcode personal Notion IDs in the repo.

For operational history, set `LINKEDIN_APPLICATION_HISTORY` to the full real ledger
JSON (raw text or base64:/gzip-base64: encoded), or set
`LINKEDIN_APPLICATION_HISTORY_PATH` to a privately restored existing file visible
inside this environment. Use the helper's returned `application_history_path`.
The contents variable can create `state/application-history.json`; an existing
`state/ledger.json` takes precedence, and conflicting input is rejected to protect
newer submissions. The path alternative references a file, not a remote sync
service: a laptop-only path cannot be opened by cloud. No history is fabricated
when neither source is present. Candidate answers and application history are
different data; the two maintained personal source files remain unchanged.

If private files already exist, keep them. Setup accepts identical values or files provisioned without content variables. Conflicting environment text causes an error instead of overwriting learned answers or changing the master baseline. Reconcile changed answers privately; do not delete a newer context merely to make setup pass. After the agent updates answers, update the private environment value or provide the newer file for future fresh tasks. Files outside the checkout must be privately exported/restored; do not assume they survive cloud snapshots or a new task.

Before cloud packet preparation, confirm real prior-job history is restored and accessible job evidence is available. Supported authenticated browser access is required only for browser actions; the cloud does not inherit desktop Edge tabs. A missing Notion URL is a tracking-configuration issue, not proof of failed authentication and not a ZIP-preparation blocker. Do not create new applicant accounts, bypass authentication, invent history, or replace existing submitted-job records with empty state. Code-only work uses synthetic test inputs and needs no private values or live browser.

For size-limited single-line editors, `gzip-base64:` followed by base64 of gzip-compressed UTF-8 bytes is also supported losslessly. Keep compressed values private.
