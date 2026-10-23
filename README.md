# ApplyToLinkedinJobs

An agent-led skill with Python helpers for evidence-backed job screening, tailored resumes, preserved application drafts, and Notion tracking. **Applications are prepared for human review; this workflow never submits them.**

## Cloud setup

See [cloud/README.md](cloud/README.md) for private environment variables, document dependencies, browser requirements and state migration. Set these two private variables to the full text of your existing documents:

- `LINKEDIN_CANDIDATE_CONTEXT`: consolidated personal context and previous answers.
- `LINKEDIN_MASTER_RESUME`: separate master-resume baseline.

Provide `LINKEDIN_NOTION_TRACKER_URL` privately for live tracking. `.env.example` contains names only. No candidate contents, resumes, contact details, private tracker IDs, browser state or real application history are included.

From the repo root, run `bash cloud/setup.sh`, then use [automation/cloud-task-prompt.md](automation/cloud-task-prompt.md) for an explicitly authorized run. Root [AGENTS.md](AGENTS.md) routes cloud tasks to the repository skill without a global installation. The setup helper writes private source files outside the checkout, refuses to overwrite newer saved answers, and reports paths without values. Actual browser authentication and prior-job history must still be available; cloning this repository does not transfer desktop Edge tabs or start applications.

## Workflow and source layout

- [Hybrid cloud/local workflow](skills/daily-linkedin-prep/references/hybrid-workflow.md): immutable packets, supported local-browser discovery/filling, a bounded local queue, separate execution history and pending Notion updates. Default one open application; final submission is manual. Start/resume locally with [this prompt](automation/local-browser-task-prompt.md). The helper does not configure cross-device transport, browser access or a background service.
- [skills/daily-linkedin-prep/SKILL.md](skills/daily-linkedin-prep/SKILL.md): eligibility rules, preparation sequence and evidence gates.
- `skills/daily-linkedin-prep/scripts/`: hybrid handoff/queue, legacy ledger and pipeline queue, action journal, screening, PDF build/verification, handoff receipts, acceptance audit and private-input setup, with offline tests.
- `skills/daily-linkedin-prep/references/`: operating contracts and a safe [candidate-context example](skills/daily-linkedin-prep/references/candidate-context.example.md).
- [automation/](automation/): recurring prompt and cloud-task prompt. No live automation is created by this checkout.
- [.github/workflows/tests.yml](.github/workflows/tests.yml): offline regression suite using synthetic fixtures.

Keep one private context file for facts, career evidence, preferences and dated answers, plus one unchanged master baseline. Newest explicit answers win only for the same question and scope. Retain replaced values as history. Export updated private context/state before cloud handoff, and update the private value before provisioning a fresh task. Legacy source filenames are redirects, not parallel personal profiles.

Local use follows the same private-input contract; copying the skill alone does not provision private sources. Do not overwrite an existing local installation or its private data. Python 3.10+ and POSIX locking are required; Python packages are standard-library only. Live document builds additionally need TeX/latexmk and Poppler (see cloud setup). Browser operations require the coordinating agent and its supported tools; the Python helpers never browse or submit forms.

## Tests

```sh
cd skills/daily-linkedin-prep
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Tests validate local logic, source provisioning and stored assertions. They do not prove a live form is filled, an upload accepted or a Notion row saved; those require current browser readback.

## Privacy and export scope

This public repository contains workflow code, instructions, prompts and synthetic examples only. Real profiles, resumes, answers, personal archives, browser/session data, credentials and application records stay outside it. `.gitignore` also excludes legacy private filenames, artifacts, backups and dotenv files. The legacy helpers retain their preparation gates and reject mutation while the hybrid queue owns their history interlock. No installed desktop skill, live automation, private cloud values or account permissions are changed by the repository update.
