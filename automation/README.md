# Prompts and automation

- `cloud-task-prompt.md`: one explicitly authorized cloud application-preparation run.
- `prepare-applications-prompt.md`: portable recurring-workflow prompt template.
- `local-browser-task-prompt.md`: start/resume the local coordinating agent using
  the computer's supported browser connection and the private hybrid queue.
- `three-job-cloud-pilot.md`: explicitly scoped three-packet ZIP test using the
  user's selected GPT-6.1 Sol / Medium session settings.

New cloud sessions load workflow code from the selected repository branch. ZIP
handoffs contain private application packets, not skill installations. Ensure the
selected branch includes the hybrid helper and prompts before starting a pilot.

Cloud/local runs now default to the skill's hybrid workflow: immutable packets,
finite budgets and one held application by default. The Python helper performs
offline coordination; actual browser connection, private transport and background
execution require the supported integrations described in the workflow reference.

Read `cloud/README.md` for private environment variables and supported browser setup. Never paste personal candidate data into either public prompt. A global skill installation or machine-specific path is not required; the prompts reference the repository skill and resolve private files through private_sources.py.

The workflow defaults remain 15 applications at 02:00 America/Los_Angeles, with `gpt-6-astra` and High reasoning where configured/available. A prompt does not select model settings, create a recurring job, or override a current user stop/scope restriction. Configure any live schedule separately only when requested. This update does not change the existing paused local automation or create a cloud schedule.
