# Automation setup

The prompt in `prepare-applications-prompt.md` is a sanitized snapshot of the local automation. Creating or changing a live automation is a separate action and was not performed by this repository export.

The workflow defaults are 15 applications, daily at 02:00 America/Los_Angeles, using `gpt-6-astra` with High reasoning where available. Configure model and reasoning in the automation settings; a prompt alone does not select them. The inspected local automation was paused on October 21, 2026; this repository does not enable it or publish its private target chat ID. Current explicit user instructions override the saved workflow.

For a new installation, configure the skill's absolute installation path, an absolute private state directory, and the authorized Notion tracker URL. Replace the placeholder paths and `NOTION_TRACKER_URL` in your local copy of the skill, references and prompt. Provision the candidate sources privately as described in the skill's candidate-sources reference. Do not start a second owner while an existing run is active.
