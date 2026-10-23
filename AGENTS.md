# Repository work

For coding, documentation, and offline tests, use synthetic fixtures. Do not require personal environment variables or start job applications for code-only tasks.

For application-preparation tasks, read `skills/daily-linkedin-prep/SKILL.md` and `cloud/README.md`, then use the supplied cloud task prompt in `automation/cloud-task-prompt.md`. The repository instructions work without a globally installed skill.

Cloud/local preparation now defaults to hybrid mode in `skills/daily-linkedin-prep/references/hybrid-workflow.md`. Use immutable packets plus the supported local-browser coordinating agent, finite budgets, a single open application by default, and the hybrid ledger/history interlock. Do not start the legacy pipeline concurrently or mistake packet production for a live application. Setup does not configure a browser connection, private cross-device transport, background worker or schedule.

Candidate context and the master resume come from `LINKEDIN_CANDIDATE_CONTEXT` and `LINKEDIN_MASTER_RESUME` or previously provisioned private files outside the checkout. Run `python3 skills/daily-linkedin-prep/scripts/private_sources.py prepare` and read its two returned paths. Never echo the environment values or commit private files, browser state, generated resumes, personal run records, or learned answers.

Application history may be provisioned privately as `LINKEDIN_APPLICATION_HISTORY` (ledger JSON contents, optionally encoded), or referenced by `LINKEDIN_APPLICATION_HISTORY_PATH` (an existing file visible in this environment). Use the helper's returned history path; a path on the laptop does not make that file visible to cloud. Never replace newer history with a stale snapshot. For a requested three-job pilot, missing pilot/helper files mean the wrong branch/commit; stop with that diagnosis rather than falling back to the legacy workflow.

Maintain one candidate-context file with dated answers and supersession history plus one unchanged master-resume baseline. Never overwrite newer learned answers with a stale cloud variable. Preserve and privately export updated context/state before changing cloud tasks or containers; files outside the checkout must not be assumed to persist.

Use only supported, authenticated browser tools. A cloud task does not inherit the local desktop's Edge session or saved application tabs. Verify browser access/account state before browser work. Cloud packet-only preparation needs private applicant sources, real history and accessible job evidence; a connected desktop browser and Notion tracker are not prerequisites for that stage. Missing job-source access is a separate research blocker. Never bypass permissions, clear duplicate history, invent answers, or submit applications. All live browser work stays with one coordinating parent.
