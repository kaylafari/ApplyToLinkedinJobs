# Repository work

For coding, documentation, and offline tests, use synthetic fixtures. Do not require personal environment variables or start job applications for code-only tasks.

For application-preparation tasks, read `skills/daily-linkedin-prep/SKILL.md` and `cloud/README.md`, then use the supplied cloud task prompt in `automation/cloud-task-prompt.md`. The repository instructions work without a globally installed skill.

Candidate context and the master resume come from `LINKEDIN_CANDIDATE_CONTEXT` and `LINKEDIN_MASTER_RESUME` or previously provisioned private files outside the checkout. Run `python3 skills/daily-linkedin-prep/scripts/private_sources.py prepare` and read its two returned paths. Never echo the environment values or commit private files, browser state, generated resumes, personal run records, or learned answers.

Maintain one candidate-context file with dated answers and supersession history plus one unchanged master-resume baseline. Never overwrite newer learned answers with a stale cloud variable. Preserve and privately export updated context/state before changing cloud tasks or containers; files outside the checkout must not be assumed to persist.

Use only supported, authenticated browser tools. A cloud task does not inherit the local desktop's Edge session or saved application tabs. Verify browser access, account state, private sources, and prior-job history before live preparation. Never bypass permissions, clear duplicate history, invent answers, or submit applications. All live browser work stays with one coordinating parent.
