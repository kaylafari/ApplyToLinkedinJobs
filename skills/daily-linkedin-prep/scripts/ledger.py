#!/usr/bin/env python3
"""Durable local state only. This program never operates a browser or submits forms."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import uuid
from zoneinfo import ZoneInfo

PREPARED = {"prepared_for_review", "ready_for_review"}
STATUSES = ("discovered", "in_progress", "prepared_for_review", "ready_for_review",
            "skipped", "blocked", "reconciliation_needed", "needs_user_input", "failed",
            "qualified", "resume_ready", "automatic_disqualification", "qualification_disqualification")
DISQUALIFIED = {"automatic_disqualification", "qualification_disqualification"}
PREPARATION_GATES = ("identity_verified", "supported_answers_persisted", "resume_qa_passed",
                     "resume_attachment_verified", "tab_preserved", "preparation_complete",
                     "submission_protection_verified")
PENDING = {"qualified", "in_progress", "resume_ready"}


def requalification_receipt(job, job_id, evidence_file, imported_history=()):
    """Opt-in correction of screening-only exclusions; assertions are not live proof."""
    path = Path(evidence_file)
    raw = path.read_bytes()
    evidence = json.loads(raw)
    if not isinstance(evidence, dict) or evidence.get("job_id") != job_id:
        raise ValueError("Requalification evidence must identify the exact job ID.")
    flags = ("prior_reason_superseded", "current_eligibility_verified",
             "current_description_verified", "duplicate_check_clear",
             "no_application_effects", "no_human_outcomes")
    if any(evidence.get(key) is not True for key in flags):
        raise ValueError("Every requalification verification must be exactly true.")
    if not job.get("note") or evidence.get("prior_reason") != job["note"]:
        raise ValueError("Requalification must bind the exact prior exclusion reason.")
    checked = dt.datetime.fromisoformat(evidence.get("checked_at", ""))
    if checked.tzinfo is None:
        raise ValueError("Requalification checked_at requires a timezone.")
    age = (dt.datetime.now(dt.timezone.utc) - checked).total_seconds()
    if not -300 <= age <= 86400:
        raise ValueError("Requalification evidence must be current within 24 hours.")
    # Inspect history recursively, not just the latest screening status.
    forbidden_statuses = PREPARED | {"in_progress", "resume_ready", "submitted",
        "cancelled", "interview", "rejected", "application submitted", "applied"}
    effect_keys = {"resume_path", "application_tab", "form_open_verified",
        "supported_answers_persisted", "resume_attachment_verified", "resume_qa_passed",
        "submission_performed", "submitted", "submitted_at", "application_date",
        "human_outcome", "historical_outcome_exclusion", "verified_form", "verified_resume"}
    def check_history(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"status", "old_status", "new_status", "observed_status"} and isinstance(item, str) and item.lower() in forbidden_statuses:
                    raise ValueError("Application effects or human outcomes prevent requalification.")
                if key in effect_keys and item not in (None, False, "", [], {}):
                    raise ValueError("Application effects or human outcomes prevent requalification.")
                if key == "browser_actions" and (type(item) is not int or item != 0):
                    raise ValueError("Nonzero or ambiguous historical browser actions prevent requalification.")
                check_history(item)
        elif isinstance(value, list):
            for item in value:
                check_history(item)
    check_history(job)
    def check_imports(value):
        if isinstance(value, dict):
            if str(value.get("job_id", "")) == job_id:
                check_history(value)
            for child in value.values():
                check_imports(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                check_imports(child)
    check_imports(imported_history)
    refs = evidence.get("references")
    if not isinstance(refs, dict):
        raise ValueError("Requalification requires description, eligibility and duplicate references.")
    receipts = {}
    for key in ("current_description", "eligibility", "duplicate_check"):
        ref = refs.get(key)
        if not isinstance(ref, str) or not Path(ref).is_absolute() or not Path(ref).is_file():
            raise ValueError("Each requalification reference must be an existing absolute file.")
        data = Path(ref).read_bytes()
        if not data.strip():
            raise ValueError("Requalification references cannot be empty.")
        receipts[key] = {"path": str(Path(ref).resolve()), "sha256": hashlib.sha256(data).hexdigest()}
    return {"evidence": evidence, "evidence_file": str(path.resolve()),
            "sha256": hashlib.sha256(raw).hexdigest(), "references": receipts,
            "limitation": "Agent assertions plus stored history checks; no live verification performed."}


def paired(job):
    e = job.get("evidence", {})
    tab = e.get("application_tab", {})
    return (isinstance(tab, dict) and bool(tab.get("id"))
            and isinstance(tab.get("url"), str) and tab["url"].startswith("https://")
            and tab.get("kind") == "application"
            and all(e.get(k) is True for k in
                    ("form_open_verified", "supported_answers_persisted", "tab_preserved")))


def pending_jobs(state, run_id, except_id=None):
    return [key for key, job in state["jobs"].items()
            if key != except_id and job.get("run_id") == run_id
            and (job["status"] in PENDING or (job.get("resume_path") and
                 (not paired(job) or not Path(job["resume_path"]).is_file())
                 and not historical_outcome_excluded(job)
                 and not closed_posting_excluded(job, key)))]


def closed_posting_excluded(job, job_id):
    """A verified closed posting cannot be repaired; retain its audit orphan.

    This only releases serial work, never validates a form or counts preparation.
    A generic skipped/blocked record or an unreadable listing is insufficient.
    """
    receipt = job.get("evidence", {}).get("closed_posting_exclusion", {})
    if not (job.get("status") == "skipped" and isinstance(receipt, dict)
            and receipt.get("verified") is True
            and receipt.get("job_id") == job_id and str(job_id).isdigit()
            and receipt.get("observed_status") == "No longer accepting applications"
            and receipt.get("apply_control_absent") is True
            and receipt.get("listing_url") == f"https://www.linkedin.com/jobs/view/{job_id}/"
            and bool(receipt.get("identity_evidence"))):
        return False
    try:
        checked = dt.datetime.fromisoformat(receipt["checked_at"].replace("Z", "+00:00"))
        reference = Path(receipt["evidence_reference"])
        return (checked.tzinfo is not None and reference.is_absolute()
                and reference.is_file()
                and hashlib.sha256(reference.read_bytes()).hexdigest() == receipt.get("evidence_sha256"))
    except (KeyError, TypeError, ValueError, OSError):
        return False


def historical_outcome_excluded(job):
    """A verified historical outcome is not an unfinished application to repair.

    Retain its orphan in the pairing audit; do not fabricate a live tab merely
    to free the serial queue. Ordinary skipped/blocked orphans still block.
    """
    e = job.get("evidence", {})
    receipt = e.get("historical_outcome_exclusion", {})
    return (job.get("status") == "skipped" and isinstance(receipt, dict)
            and receipt.get("verified") is True
            and receipt.get("observed_status") in
                {"Application Submitted", "Interview", "Rejected"}
            and isinstance(receipt.get("row_url"), str)
            and receipt["row_url"].startswith("https://app.notion.com/p/")
            and bool(receipt.get("checked_at"))
            and bool(receipt.get("identity_evidence")))


def blocking_jobs(state, run_id, job_id):
    queue = pending_jobs(state, run_id)
    # Drain a legacy backlog deterministically; active work/orphans precede queued qualifications.
    queue.sort(key=lambda k: state["jobs"][k]["status"] == "qualified")
    if queue and queue[0] == job_id and all(state["jobs"][k]["status"] == "qualified" for k in queue[1:]):
        return []
    return [k for k in queue if k != job_id]


def pair_audit(state, run_id, artifacts_dir=None):
    jobs = {k: j for k, j in state["jobs"].items() if j.get("run_id") == run_id}
    pdf_jobs = {k: j for k, j in jobs.items() if j.get("resume_path")}
    orphans = [k for k, j in pdf_jobs.items() if not paired(j) or not Path(j["resume_path"]).is_file()]
    tabs = [str(j["evidence"]["application_tab"]["id"]) for j in pdf_jobs.values() if paired(j)]
    duplicate_tabs = sorted({t for t in tabs if tabs.count(t) > 1})
    untracked = []
    if artifacts_dir:
        directory = Path(artifacts_dir)
        if not directory.is_dir():
            raise ValueError("Artifact directory does not exist.")
        # Older revisions are retained, but only the current final revision is paired.
        known = {Path(j["resume_path"]).resolve() for j in pdf_jobs.values()}
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            relative = path.relative_to(directory)
            retained_revision = (len(relative.parts) == 3 and relative.parts[0] in pdf_jobs
                                 and relative.parts[1].startswith("r")
                                 and relative.parts[2] == "Kayla_Farivar_Resume.pdf")
            if path.resolve() not in known and not retained_revision:
                untracked.append(str(path))
        for k, j in pdf_jobs.items():
            if not Path(j["resume_path"]).resolve().is_relative_to(directory.resolve()):
                orphans.append(k)
    return {"run_id": run_id, "resume_jobs": len(pdf_jobs),
            "paired_form_jobs": sum(paired(j) for j in pdf_jobs.values()),
            "orphan_job_ids": sorted(set(orphans)), "duplicate_tab_ids": duplicate_tabs,
            "untracked_pdfs": sorted(untracked),
            "ok": not (orphans or duplicate_tabs or untracked),
            "limitation": "Recorded assertions only; live tab inspection is mandatory."}


def today():
    return dt.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def transition(job, status, note):
    event = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
             "old_status": job.get("status"), "new_status": status, "note": note}
    if "evidence" in job:
        event["evidence"] = json.loads(json.dumps(job["evidence"]))
    job.setdefault("transitions", []).append(event)
    job.update(status=status, note=note)


def save(path, state):
    """Caller owns the lock across the complete read/modify/write transaction."""
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def counts(state, run_id):
    jobs = [j for j in state["jobs"].values() if j.get("run_id") == run_id]
    return {"prepared_total": sum(j["status"] in PREPARED for j in jobs),
            "ready_for_review": sum(j["status"] == "ready_for_review" for j in jobs),
            "prepared_for_review": sum(j["status"] == "prepared_for_review" for j in jobs)}


def owned(state, args):
    owner = state.get("owner")
    if not owner or owner["token"] != args.owner:
        raise ValueError("Browser/state ownership token does not match. Do not touch browser tabs.")
    run = state["runs"][owner["run_id"]]
    if run["status"] != "active":
        raise ValueError("Run is not active.")
    return owner["run_id"], run


def transact(path, args):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {
            "version": 1, "runs": {}, "jobs": {}, "history": [], "owner": None}
        if state.get("version") != 1:
            raise ValueError("Unsupported ledger version.")
        command = args.command
        if command == "status":
            if getattr(args, "summary", False):
                owner = state.get("owner")
                return {
                    "version": state["version"],
                    "owner": {"run_id": owner["run_id"]} if owner else None,
                    "runs": {key: {**{field: run[field] for field in
                                      ("day", "target", "status", "workflow", "kind", "cycle_id", "completion_reason")
                                      if field in run}, "counts": counts(state, key)}
                             for key, run in state["runs"].items()},
                    "job_count": len(state["jobs"]), "history_count": len(state["history"]),
                }
            # Do not reveal the browser ownership capability in routine status output.
            public = json.loads(json.dumps(state))
            if public["owner"]:
                public["owner"].pop("token")
            for run_id, run in public["runs"].items():
                run["counts"] = counts(state, run_id)
            return public
        if command == "audit-pairs":
            if args.run_id not in state["runs"]:
                raise ValueError("Unknown run.")
            return pair_audit(state, args.run_id, args.artifacts_dir)
        if command in ("begin", "begin-test"):
            day = args.day or today()
            dt.date.fromisoformat(day)
            is_test = command == "begin-test"
            if is_test:
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", args.cycle_id):
                    raise ValueError("Test cycle ID must be 1-64 lowercase letters/digits/hyphens, starting and ending alphanumeric.")
                if args.target is None or args.target < 1:
                    raise ValueError("Test target must be explicitly supplied and positive.")
            run_id = "applylinkedin-test-" + args.cycle_id if is_test else "applylinkedin-" + day
            owner = state["owner"]
            if owner and (owner["token"] != args.owner or owner["run_id"] != run_id):
                raise ValueError("An owner already holds the browser. No automatic expiry or takeover.")
            run = state["runs"].get(run_id)
            if is_test and run and (run.get("kind") != "test" or run.get("cycle_id") != args.cycle_id):
                raise ValueError("Existing run is not this named test cycle.")
            if args.target is not None and args.target < 1:
                raise ValueError('Target must be positive.')
            if run and args.target is not None and args.target != run['target']:
                raise ValueError('Same-date retry must retain its existing target.')
            if run and run["status"] == "cancelled":
                raise ValueError("This daily run was cancelled and cannot automatically resume.")
            if run and run["status"] == "complete":
                return {"run_id": run_id, "status": "complete", **counts(state, run_id)}
            if not run:
                state["runs"][run_id] = {"day": day, "target": args.target or 15, "status": "active", "workflow": "serial-paired-v1"}
                if is_test:
                    state["runs"][run_id].update(kind="test", cycle_id=args.cycle_id)
            token = args.owner or uuid.uuid4().hex
            state["owner"] = {"token": token, "run_id": run_id}
            result = {"run_id": run_id, "owner": token, "target": state['runs'][run_id]['target'], **counts(state, run_id)}
        else:
            run_id, run = owned(state, args)
            if command == "check-next":
                pending = blocking_jobs(state, run_id, args.job_id)
                if pending:
                    raise ValueError("Process existing application before another job: " + ", ".join(pending))
                job = state["jobs"].get(args.job_id, {})
                if job.get("run_id") != run_id or job.get("status") not in PENDING:
                    raise ValueError("Current job must be qualified/in_progress/resume_ready in this run.")
                if args.for_resume and not paired(job):
                    raise ValueError("Resume generation requires a verified filled preserved application tab.")
                if args.for_resume:
                    tab_id = str(job['evidence']['application_tab']['id'])
                    if any(k != args.job_id and j.get('run_id') == run_id and paired(j)
                           and str(j['evidence']['application_tab']['id']) == tab_id
                           for k, j in state['jobs'].items()):
                        raise ValueError('Application tab is already paired with another job.')
                return {"job_id": args.job_id, "ok": True, "for_resume": args.for_resume}
            elif command == "record":
                old = state["jobs"].get(args.job_id)
                if old and old.get("run_id") != run_id and old["status"] != "eligible_after_reconciliation":
                    raise ValueError("Duplicate job from another run/history; inspect it and reconcile explicitly.")
                job = json.loads(json.dumps(old or {}))
                for field in ("url", "note", "resume_path", "verified_form", "verified_resume", "required_complete"):
                    value = getattr(args, field)
                    if value is not None:
                        job[field] = value
                if args.evidence_file:
                    evidence = json.loads(Path(args.evidence_file).read_text())
                    if not isinstance(evidence, dict):
                        raise ValueError("Evidence file must contain a JSON object.")
                    job["evidence"] = evidence
                    job["evidence_file"] = str(Path(args.evidence_file).resolve())
                if run.get("workflow") == "serial-paired-v1":
                    # Refresh an already-counted application without opening a second
                    # work item. Every pairing, evidence, ownership and ready gate below
                    # still applies; pending/new applications retain serial exclusion.
                    prepared_refresh = (old and old.get("run_id") == run_id
                                        and old.get("status") in PREPARED
                                        and args.status in PREPARED)
                    if args.status in PENDING | PREPARED and not prepared_refresh:
                        pending = blocking_jobs(state, run_id, args.job_id)
                        if pending:
                            raise ValueError("Process existing application before another job: " + ", ".join(pending))
                    if args.status == "resume_ready" or args.status in PREPARED:
                        if not args.evidence_file or not paired(job):
                            raise ValueError("Fresh evidence of a filled preserved application tab is required.")
                        if not job.get("resume_path") or not Path(job["resume_path"]).is_file():
                            raise ValueError("Resume file does not exist.")
                        if job.get("evidence", {}).get("resume_qa_passed") is not True:
                            raise ValueError("Resume QA must pass.")
                    if paired(job):
                        tab_id = str(job["evidence"]["application_tab"]["id"])
                        if any(k != args.job_id and j.get("run_id") == run_id and paired(j)
                               and str(j["evidence"]["application_tab"]["id"]) == tab_id
                               for k, j in state["jobs"].items()):
                            raise ValueError("Application tab is already paired with another job.")
                if args.status in PREPARED:
                    evidence = job.get("evidence", {})
                    if any(evidence.get(gate) is not True for gate in PREPARATION_GATES):
                        raise ValueError("Prepared requires evidence JSON with every preparation gate exactly true: " + ", ".join(PREPARATION_GATES))
                    if not job.get("resume_path") or not Path(job["resume_path"]).is_file():
                        raise ValueError("Resume file does not exist.")
                    if args.status == "ready_for_review" and (evidence.get("final_control_visible") is not True
                            or evidence.get("required_unresolved") != [] or evidence.get("validation_errors") != []):
                        raise ValueError("Ready requires final_control_visible=true, required_unresolved=[], validation_errors=[] in evidence JSON.")
                    already_counted = old and old.get("run_id") == run_id and old["status"] in PREPARED
                    if not already_counted and counts(state, run_id)["prepared_total"] >= run["target"]:
                        raise ValueError("Daily preparation target is already reached.")
                transition(job, args.status, job.get("note", ""))
                job["run_id"] = run_id
                state["jobs"][args.job_id] = job
                result = {"job_id": args.job_id, "status": args.status, **counts(state, run_id)}
            elif command == "import-history":
                # Normalization is intentional: source-specific formats must be interpreted before import.
                source = Path(args.source)
                data = json.loads(source.read_text())
                if not isinstance(data, dict) or not isinstance(data.get("jobs", []), list) or not isinstance(data.get("runs", []), list):
                    raise ValueError("Import requires an object with jobs and runs arrays.")
                for job in data.get("jobs", []):
                    if not isinstance(job, dict) or not job.get("job_id"):
                        raise ValueError("Every imported job needs a stable job_id.")
                state["history"].append({"source": str(source.resolve()), "snapshot": data})
                added = 0
                for job in data.get("jobs", []):
                    key = str(job["job_id"])
                    if key in state["jobs"]:
                        continue
                    status = job.get("status", "unknown")
                    if status not in PREPARED | DISQUALIFIED | {"skipped", "submitted", "cancelled", "discovered", "qualified", "resume_ready"}:
                        status = "reconciliation_needed"
                    state["jobs"][key] = {**job, "status": status, "run_id": None, "prior_history": [job]}
                    added += 1
                result = {"imported_jobs": added, **counts(state, run_id)}
            elif command == "reconcile":
                job = state["jobs"].get(args.job_id)
                if not job or job.get("run_id") == run_id:
                    raise ValueError("Reconcile is for a known historical/previous-run job.")
                if not args.note:
                    raise ValueError("Reconciliation requires evidence in --note.")
                requalification = None
                evidence_file = getattr(args, "requalification_evidence_file", None)
                if evidence_file and (args.decision != "retry" or job["status"] not in DISQUALIFIED):
                    raise ValueError("Requalification evidence applies only to retry of a historical disqualification.")
                if args.decision == "retry" and job["status"] in DISQUALIFIED:
                    if not evidence_file:
                        raise ValueError("Disqualified historical jobs remain excluded without verified requalification evidence.")
                    requalification = requalification_receipt(job, args.job_id, evidence_file, state.get("history", []))
                prior = json.loads(json.dumps(job))
                prior.pop("prior_history", None)
                prior.pop("transitions", None)
                job.setdefault("prior_history", []).append(prior)
                next_status = "eligible_after_reconciliation" if args.decision == "retry" else (
                    job["status"] if job["status"] in DISQUALIFIED else "skipped")
                transition(job, next_status, args.note)
                job["run_id"] = None
                if requalification:
                    job.setdefault("requalifications", []).append(requalification)
                result = {"job_id": args.job_id, "status": job["status"]}
            elif command in ("release", "cancel", "finish"):
                if command == "finish":
                    if run.get("workflow") == "serial-paired-v1" and (pending_jobs(state, run_id)
                            or not pair_audit(state, run_id)["ok"]):
                        raise ValueError("Finish requires processed backlog and paired resume/form evidence.")
                    if counts(state, run_id)["prepared_total"] < run["target"] and not args.exhausted:
                        raise ValueError("Finish requires the daily target or --exhausted with verified search exhaustion.")
                    run["status"] = "complete"
                    run["completion_reason"] = "search_exhausted" if args.exhausted else "target_reached"
                elif command == "cancel":
                    run["status"] = "cancelled"
                state["owner"] = None
                result = {"run_id": run_id, "status": run["status"], "ownership_released": True,
                          **counts(state, run_id)}
            else:
                raise ValueError("Unknown command.")
        save(path, state)
        return result


def parser():
    p = argparse.ArgumentParser(description=__doc__, epilog="Only the owner may operate application browser tabs. Save the token privately in the run checkpoint. Ownership has no timeout. After a crash, recover the existing token from the ledger only after verifying the prior agent is stopped; do not start a concurrent owner. A new day does not clear unfinished ownership. Release pauses an active run; cancel permanently cancels that day. Resume that same day with begin. Prepared total includes prepared_for_review plus ready_for_review.")
    p.add_argument("--state", required=True, help="Persistent ledger JSON path in a dedicated private directory outside OpenClaw; parent mode is set to 0700")
    sub = p.add_subparsers(dest="command", required=True)
    begin = sub.add_parser("begin", help="Acquire exclusive browser ownership or resume same daily run; default target 15")
    begin.add_argument('--target', type=int, help='Explicit user-requested target for a new run only; defaults to 15')
    begin.add_argument("--owner", help="Existing ownership token for resuming after interruption")
    begin.add_argument("--day", help="ISO date override for explicit historical recovery/tests; default Los Angeles today")
    test = sub.add_parser("begin-test", help="Acquire a separately named user-requested test cycle; never changes daily runs or historical jobs")
    test.add_argument("--cycle-id", required=True, help="Unique explicit lowercase slug for this test; reuse it on retries")
    test.add_argument("--target", required=True, type=int, help="Explicit positive test target; no default")
    test.add_argument("--owner", help="Existing token only when resuming this owned test")
    test.add_argument("--day", help="ISO provenance date, default Los Angeles today; cycle ID remains identity")
    status = sub.add_parser("status", help="Read runs, dedup jobs, and counts; hides owner token")
    status.add_argument("--summary", action="store_true", help="Only safe owner/run metadata and counts, no job evidence or history payloads")
    audit = sub.add_parser("audit-pairs", help="Audit recorded PDF/form pairing; does not inspect browser")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--artifacts-dir")
    for name in ("record", "release", "cancel", "finish", "import-history", "reconcile", "check-next"):
        s = sub.add_parser(name)
        s.add_argument("--owner", required=True, help="Token returned by begin")
        if name == "check-next":
            s.add_argument("--job-id", required=True)
            s.add_argument("--for-resume", action="store_true")
        elif name == "record":
            s.add_argument("--job-id", required=True, help="Stable LinkedIn job ID; do not use a title as identity")
            s.add_argument("--status", choices=STATUSES, required=True)
            s.add_argument("--url")
            s.add_argument("--note", help="Evidence, missing fields, browser tab identifiers, or reason")
            s.add_argument("--resume-path", help="Existing final PDF; persist durable artifact path")
            s.add_argument("--evidence-file", help="Local JSON object of agent assertions, not external verification. Prepared requires these keys exactly true: " + ", ".join(PREPARATION_GATES) + ". Ready also requires final_control_visible=true, required_unresolved=[], validation_errors=[]. Prepared may contain unresolved personal questions before the final page. Other metadata (tabs, questions, observations) is retained. Omission preserves prior evidence; supplying a file replaces the evidence object. Transitions preserve evidence snapshots.")
            s.add_argument("--verified-form", action=argparse.BooleanOptionalAction, default=None, help="Optional supplementary assertion; structured evidence gates govern counting")
            s.add_argument("--verified-resume", action=argparse.BooleanOptionalAction, default=None, help="Optional supplementary assertion; structured evidence gates govern counting")
            s.add_argument("--required-complete", action=argparse.BooleanOptionalAction, default=None, help="Optional supplementary assertion; structured evidence gates govern counting")
        elif name == "finish":
            s.add_argument("--exhausted", action="store_true", help="Assert search exhaustion was verified; allows fewer than 15")
        elif name == "import-history":
            s.add_argument("--source", required=True, help='Normalized JSON: {"runs": [{...}], "jobs": [{"job_id": "123", "status": "filling", "url": "..."}]}. Snapshots, including cancelled runs, are preserved separately; imported outcomes never count today. Unknown/in-progress outcomes require reconciliation.')
        elif name == "reconcile":
            s.add_argument("--requalification-evidence-file", help="Opt-in JSON receipt for a corrected screening-only historical exclusion; exact reason, current evidence references, and no application effects/human outcomes required.")
            s.add_argument("--job-id", required=True)
            s.add_argument("--decision", choices=("retry", "keep"), required=True, help="retry permits explicit reuse after browser/history reconciliation; keep skips")
            s.add_argument("--note", required=True, help="Observed evidence justifying the decision")
    return p


def main():
    args = parser().parse_args()
    try:
        print(json.dumps(transact(args.state, args), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
