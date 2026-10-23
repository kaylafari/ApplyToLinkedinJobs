# Prompts and automation

- `cloud-task-prompt.md`: one explicitly authorized cloud application-preparation run.
- `prepare-applications-prompt.md`: portable recurring-workflow prompt template.

Read `cloud/README.md` for private environment variables and supported browser setup. Never paste personal candidate data into either public prompt. A global skill installation or machine-specific path is not required; the prompts reference the repository skill and resolve private files through private_sources.py.

The workflow defaults remain 15 applications at 02:00 America/Los_Angeles, with `gpt-6-astra` and High reasoning where configured/available. A prompt does not select model settings, create a recurring job, or override a current user stop/scope restriction. Configure any live schedule separately only when requested. This update does not change the existing paused local automation or create a cloud schedule.
