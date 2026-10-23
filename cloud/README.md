# Use this repository in a cloud task

Select the ApplyToLinkedinJobs repository for the cloud environment. The root AGENTS.md points to the packaged skill; a global/local skill installation is not required. Prompts are in `automation/cloud-task-prompt.md` (one run) and `automation/prepare-applications-prompt.md` (recurring workflow template). Cloning the repo does not configure or start a live application run.

## Private environment variables

In the cloud environment or your **Personal vault**, request/supply these direct environment variables. Their actual values never belong in GitHub:

| Key | Private value |
| --- | --- |
| `LINKEDIN_CANDIDATE_CONTEXT` | Full UTF-8 contents of your consolidated candidate-context.md, including current answers and history. |
| `LINKEDIN_MASTER_RESUME` | Full UTF-8 contents of your separate master-resume.txt baseline. |
| `LINKEDIN_APPLICATION_HISTORY` | Real application-history ledger JSON contents, optionally base64:/gzip-base64: encoded. Operational state, separate from candidate answers. |
| `LINKEDIN_APPLICATION_HISTORY_PATH` | Alternative: an existing absolute history-file path visible inside this cloud environment. A Mac-only path is not a cloud-accessible reference. |
| `LINKEDIN_NOTION_TRACKER_URL` | Authorized Notion tracker URL for live tracking. |

The first two values are **document contents, not file paths**. Use raw multiline UTF-8 where supported. For single-line editors, use `base64:` followed by base64-encoded UTF-8 file bytes (one uninterrupted line); the helper decodes them losslessly. Base64 is transport encoding, not encryption: keep the value private just like the original text. Paste the text into the private variable UI, not the public task prompt or setup script. Do not echo, dump, or trace your environment. `.env.example` documents key names with empty values only; this repo never loads a `.env` file automatically. Keep real `.env` files private and untracked.

Optional `LINKEDIN_PRIVATE_DIR` selects an absolute private directory (default `~/.local/share/linkedin-prep`). Optional `LINKEDIN_STATE_DIR` selects the actual privately restored state directory (default `state/` under the private directory). Both must be outside the checkout. For migration, restore the real existing ledger/history and artifacts privately before live work; candidate text alone does not provide duplicate protection.

The history contents variable restores a private `state/application-history.json`
when no history exists. An actual restored `state/ledger.json` takes precedence;
conflicting/stale environment text is rejected rather than overwriting it. The
helper returns `application_history_configured` and `application_history_path`
without printing records. Set the contents variable from the real exported history
or use the path alternative after the file has been transferred to cloud. A path
does not transfer data or create an automatic connection to the laptop. Empty sample
keys in `.env.example` are documentation, not configured private values.

Current Codex Cloud documentation distinguishes direct environment variables from proxy-substituted network secrets. Use direct variables for these two text documents because the program must read the actual contents. A personal value must be requested by the environment and scoped to it to reach a task. See [official environment configuration](https://learn.chatgpt.com/docs/environments/cloud-environments#configure-environment-variables-and-network-secrets). On the legacy setup-only Secrets interface, a setup script can materialize inputs before those secrets are removed from the agent phase; do not assume an ordinary shell export in setup persists into task execution. See [legacy environment behavior](https://learn.chatgpt.com/docs/environments/cloud-environment#environment-variables-and-secrets).

## Prepare the environment

Run from the checkout root:

```sh
bash cloud/setup.sh
python3 skills/daily-linkedin-prep/scripts/private_sources.py check
```

The helper creates candidate-context.md and master-resume.txt in the private directory with mode 0600 and reports their paths without their contents. Already provisioned files work without content variables. Identical repeated setup is safe; differing input is rejected to protect newer learned answers. No overwrite flag is provided: reconcile the sources privately instead of deleting the newer copy.

Helpers need Python 3.10+ on Linux/macOS. For real resume builds, install `latexmk`, TeX packages and Poppler. On Debian/Ubuntu, an opt-in installer is provided:

```sh
LINKEDIN_INSTALL_RESUME_TOOLS=1 bash cloud/setup.sh
```

Other platforms may provision equivalent packages themselves. Unit tests use synthetic data and mock document commands; they do not need personal values, a browser, or TeX installation.

## Live browser and handoff requirements

Cloud/local runs use [hybrid-workflow.md](../skills/daily-linkedin-prep/references/hybrid-workflow.md).
Cloud creates immutable packets; the local coordinating agent uses the user's
actual computer browser while awake, online and connected through supported tools.
Use [the local start/resume prompt](../automation/local-browser-task-prompt.md).
The shared `cloud_handoff.py` helper validates/packages files and checkpoints work;
it does not supply browser control, file transport or background execution.

The selected handoff is [manual download and transfer](../skills/daily-linkedin-prep/references/manual-transfer.md):
prepare a private ZIP plus SHA256, download it through an actual supported host
interface, then verify/extract/import on the computer. No shared drive is needed.
Cloud files and archives do not prove completed download or durable preservation.
Keep mutable execution state on one private local POSIX
filesystem, not a sync drive. Default to one held application, finite claim/time/
attempt budgets and an explicit supported token budget. A non-submitting local
pilot must pass before long local work. Cloud background execution and its limits
also require an actual supported host mechanism; this setup script does not start
a daemon, resume a paused schedule, or keep an ended agent session running.

Before live browser actions, inspect actual supported browser capabilities and authentication. Desktop Edge sessions, open drafts, and login cookies are not automatically transferred. A powered-on laptop or Internet access does not expose its browser to a cloud chat. Packet-only preparation can research accessible public job sources and produce ZIPs without that connection. Missing browser controls or `LINKEDIN_NOTION_TRACKER_URL` must not block that stage; real history and sufficient job evidence are still required. The tracker boolean only reports whether a URL was provided, not Notion authentication or database permissions. Verify those separately before tracker writes. The helper scripts are offline and do not add a browser driver.

Use one parent browser executor. Preserve the skill's no-submission boundary and distinct application/PDF pairing. Privately export updated context and application state before ending a task or replacing a container. A new cloud task may lack files outside the repo, and a stale environment value must never replace newer learned answers. Update the private value from the current context before provisioning a fresh task. The master resume remains a separate baseline. Never commit or publish these exports to preserve them.

No live cloud environment, schedule, browser login, or private variable value is configured by this commit. Run tests from the skill directory:

```sh
cd skills/daily-linkedin-prep
python3 -m unittest discover -s scripts -p 'test_*.py'
```

For size-limited single-line editors, `gzip-base64:` followed by base64 of gzip-compressed UTF-8 bytes is also supported losslessly. Keep compressed values private.
