# ApplyToLinkedinJobs

Codex skill and local Python helpers for preparing LinkedIn job applications with evidence-backed screening, tailored resumes, preserved application drafts, and Notion tracking. **Applications are prepared for human review; this workflow never submits them.**

This repository exports the `daily-linkedin-prep` skill as of October 21, 2026. It contains the agent instructions, workflow references, all eight current helper scripts, twelve regression-test modules, a synthetic historical-screening fixture, and the automation prompt template. It is not a standalone browser bot: live actions require an authenticated, supported browser session operated by the coordinating agent.

## Contents

- [`skills/daily-linkedin-prep/SKILL.md`](skills/daily-linkedin-prep/SKILL.md): workflow, eligibility rules, evidence gates and handoff requirements.
- `skills/daily-linkedin-prep/scripts/`: locked ledger, pipeline queue, action journal, screening report, resume build/verification, handoff receipt and acceptance audit helpers, with tests.
- `skills/daily-linkedin-prep/references/`: operational documentation, including private-source provisioning instructions.
- `skills/daily-linkedin-prep/agents/openai.yaml`: skill display metadata.
- [`automation/`](automation/): sanitized automation prompt and setup notes.

## Install and configure

1. Copy `skills/daily-linkedin-prep` into your local Codex skills directory. If it is already installed, compare changes and preserve its private sources rather than overwriting the live installation.
2. Provision the applicant-approved private files described in [`candidate-sources.md`](skills/daily-linkedin-prep/references/candidate-sources.md). Personal profile, resume, career inventory, saved answers and historical records are intentionally excluded and ignored by Git. Missing inputs must not be replaced by invented answers.
3. In your local skill/reference/prompt copies, replace `/absolute/path/linkedin-daily-state` with an absolute private state directory, `/absolute/path/daily-linkedin-prep` with the installed skill directory, and `NOTION_TRACKER_URL` with the authorized tracker URL. Use a dedicated state directory outside this checkout. Preserve and reconcile existing duplicate-protection history before migration.
4. Use the installed supported browser tools and the workflow's single parent browser owner. No browser sessions, permissions, live automations or application data are configured by cloning this repository.

## Requirements and tests

Use Python 3.10+ on macOS or Linux; helpers use standard-library modules, including POSIX `fcntl` locking. There are no Python package dependencies. Resume generation also requires `latexmk` and Poppler (`pdftotext`, `pdfinfo`, `pdftoppm`) on PATH. Unit tests mock external resume tools and do not require those executables or network access.

```sh
cd skills/daily-linkedin-prep
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/ledger.py --help
python3 scripts/pipeline_queue.py --help
```

Tests validate local logic and stored assertions. They do not prove that a live form remains filled, an upload was accepted, or a Notion row was saved; those require current browser readback.

## Export scope

All production Python helper files are copied unchanged from the installed skill. Two test files were made portable: the screening parity test imports the neighboring queue helper, and the historical-screening regression uses a synthetic checked-in fixture plus temporary evidence instead of private files on the original machine. Workflow documentation replaces private filesystem paths and tracker identifiers with setup placeholders. Candidate policy retains the general rules but excludes saved personal answers and applicant-specific location/project facts. `candidate-sources.md` is a provisioning guide, not the private embedded source material.

Credentials, browser/session state, personal candidate sources, real application ledgers/checkpoints/history, tailored resumes, run reports, local handoff files, generated caches, and obsolete backup copies are excluded. The export does not modify the installed skill or the running application workflow.
