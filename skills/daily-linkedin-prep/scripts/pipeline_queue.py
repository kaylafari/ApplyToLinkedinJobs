#!/usr/bin/env python3
"""Local worker coordination. Never opens a browser, submits, or edits the ledger.

Queue assertions are not live browser verification. Only the parent that holds the
ledger owner capability may acknowledge completion or recover abandoned claims.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import uuid

PREPARED = {"prepared_for_review", "ready_for_review"}
GATES = ("identity_verified", "form_open_verified", "supported_answers_persisted",
         "resume_qa_passed", "resume_attachment_verified", "tab_preserved",
         "preparation_complete", "submission_protection_verified")
TRACKING = {"trk", "trackingid", "ref", "refid", "source", "sourceid", "gh_src",
            "ashby_jid_tracking", "fbclid", "gclid", "lipi", "lici", "originalsubdomain"}


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize_url(value):
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Application URL must be HTTPS without embedded credentials.")
    # Preserve unknown query keys, including ATS job identifiers; drop only tracking.
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k.lower() not in TRACKING and not k.lower().startswith("utm_"))
    path = p.path.rstrip("/") or "/"
    # Ashby overview and application views share a posting identity.
    if p.hostname.lower() == "jobs.ashbyhq.com" and path.endswith("/application"):
        path = path[:-12]
    return urlunsplit(("https", p.netloc.lower(), path, urlencode(query), ""))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("checked_at must be an ISO timestamp with timezone.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("checked_at must include timezone.")
    return value


@contextmanager
def locked(path, exclusive=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield


def save(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, sort_keys=True, allow_nan=False)
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


def event(state, kind, job_id=None, **details):
    state["events"].append({"at": now(), "kind": kind, "job_id": job_id, **details})


def verified_job(ledger, run_id, job_id):
    job = ledger.get("jobs", {}).get(job_id, {})
    e = job.get("evidence", {})
    tab = e.get("application_tab", {})
    valid = (job.get("run_id") == run_id and job.get("status") in PREPARED
             and all(e.get(k) is True for k in GATES)
             and isinstance(tab, dict) and bool(tab.get("id"))
             and tab.get("kind") == "application"
             and isinstance(tab.get("url"), str) and tab["url"].startswith("https://")
             and isinstance(job.get("resume_path"), str)
             and Path(job["resume_path"]).is_file())
    if job.get("status") == "ready_for_review":
        valid = valid and e.get("final_control_visible") is True and e.get("required_unresolved") == [] and e.get("validation_errors") == []
    if not valid:
        raise ValueError("Parent ledger does not verify a prepared application, all gates, preserved tab, and existing resume.")
    expected = e.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64 or hashlib.sha256(Path(job["resume_path"]).read_bytes()).hexdigest() != expected.lower():
        raise ValueError("Parent ledger does not verify the current resume SHA256; hash missing or changed.")
    if any(k != job_id and other.get("run_id") == run_id and other.get("status") in PREPARED
           and str(other.get("evidence", {}).get("application_tab", {}).get("id")) == str(tab["id"])
           for k, other in ledger.get("jobs", {}).items()):
        raise ValueError("Prepared application tab is shared with another same-run ledger job.")
    return job


def ledger_counts(ledger, run_id):
    # Count recorded prepared jobs conservatively for capacity even if stale gates
    # would fail completion; expose separately the freshly checked valid total.
    recorded = [k for k, j in ledger.get("jobs", {}).items()
                if j.get("run_id") == run_id and j.get("status") in PREPARED]
    valid = []
    for key in recorded:
        try:
            verified_job(ledger, run_id, key)
            valid.append(key)
        except ValueError:
            pass
    return {"ledger_prepared": len(recorded), "ledger_verified_prepared": len(valid)}


def parent_owned(state, ledger, owner):
    current = ledger.get("owner") or {}
    if not owner or current.get("token") != owner or current.get("run_id") != state["run_id"]:
        raise ValueError("Parent ledger owner capability does not match.")


def require_claim(job, role, owner, token):
    claim = job.get("claims", {}).get(role)
    if not claim or claim["owner"] != owner or claim["token"] != token:
        raise ValueError("Exact job, role, owner, and claim token required; stale owner rejected.")
    return claim


def public(state):
    copy = json.loads(json.dumps(state))
    if copy.get("browser_lease"):
        copy["browser_lease"].pop("token", None)
    for job in copy["jobs"].values():
        for claim in job.get("claims", {}).values():
            claim.pop("token", None)
        for receipt in job.get("receipts", []):
            receipt.pop("claim_token", None)
    for item in copy["events"]:
        item.pop("claim_token", None)
    return copy


def operate(path, command, **a):
    path = Path(path)
    with locked(path):
        state = json.loads(path.read_text()) if path.exists() else None
        if state and state.get("version") != 1:
            raise ValueError("Unsupported queue version.")
        ledger_path = Path(state["ledger_path"] if state else a.get("ledger", ""))
        if not ledger_path.is_file():
            raise ValueError("Existing parent ledger is required.")
        if path.resolve() == ledger_path.resolve():
            raise ValueError("Queue must be separate from the ledger.")
        # Consistent ledger snapshot. Queue lock always precedes ledger shared lock;
        # ledger.py never acquires the queue lock, avoiding lock-order cycles.
        with locked(ledger_path, exclusive=False):
            ledger = json.loads(ledger_path.read_text())
            if command == "init":
                run = ledger.get("runs", {}).get(a["run_id"], {})
                if type(a["target"]) is not int or a["target"] < 1 or run.get("target") != a["target"]:
                    raise ValueError("Target must match the existing parent ledger run.")
                if state:
                    if (state["run_id"], state["target"], state["ledger_path"]) != (a["run_id"], a["target"], str(ledger_path.resolve())):
                        raise ValueError("Existing queue identity and target are immutable.")
                    return public(state)
                state = {"version": 1, "run_id": a["run_id"], "target": a["target"],
                         "ledger_path": str(ledger_path.resolve()), "jobs": {}, "events": [], "browser_lease": None}
                event(state, "initialized")
                result = public(state)
            elif not state:
                raise ValueError("Initialize the queue first.")
            elif command in {"status", "list"}:
                result = public(state)
                result["counts"] = {**ledger_counts(ledger, state["run_id"]),
                                    "done": sum(j["status"] == "done" for j in state["jobs"].values()),
                                    "notion_verified": sum(j.get("notion_status") == "notion_logged" for j in state["jobs"].values())}
                if a.get("status"):
                    result["jobs"] = {k: j for k, j in result["jobs"].items() if j["status"] == a["status"]}
                return result
            elif command in {"claim-browser", "release-browser", "recover-browser"}:
                lease = state.get("browser_lease")
                if command == "claim-browser":
                    if lease:
                        raise ValueError("Browser mutation lease already held; no automatic expiry or takeover.")
                    if not a.get("owner", "").strip():
                        raise ValueError("Browser owner required.")
                    if a["role"] in {"application", "notion"}:
                        require_claim(state["jobs"].get(a.get("job_id"), {}), a["role"], a["owner"], a.get("token"))
                    elif a["role"] != "discovery":
                        raise ValueError("Browser role must be discovery, application, or notion.")
                    token = uuid.uuid4().hex
                    state["browser_lease"] = {"role": a["role"], "owner": a["owner"], "token": token,
                                              "job_id": a.get("job_id"), "claimed_at": now()}
                    result = {"claimed": True, "browser_token": token, "owner": a["owner"], "role": a["role"]}
                else:
                    if not lease or lease["owner"] != a["owner"] or lease["token"] != a["token"]:
                        raise ValueError("Exact browser lease owner and token required.")
                    if command == "recover-browser":
                        parent_owned(state, ledger, a.get("ledger_owner"))
                        if not a.get("owner_check", "").strip():
                            raise ValueError("Browser recovery requires stopped-owner verification evidence.")
                    state["browser_lease"] = None
                    result = {"released": True}
                event(state, command, a.get("job_id"), owner=a["owner"], owner_check=a.get("owner_check"))
            elif command == "add":
                if ledger.get("runs", {}).get(state["run_id"], {}).get("status") != "active":
                    raise ValueError("Discovery requires an active ledger run.")
                data = a["candidate"]
                for key in ("company", "title", "linkedin_job_id", "application_url"):
                    if not isinstance(data.get(key), str) or not data[key].strip():
                        raise ValueError("Candidate requires nonempty string " + key)
                key = data["linkedin_job_id"]
                if not key.isdigit():
                    raise ValueError("LinkedIn job ID must contain digits only.")
                score = data.get("score")
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 60 < score <= 100:
                    raise ValueError("Qualification score must be finite and strictly >60, at most100.")
                check = data.get("duplicate_check", {})
                if data.get("eligibility_verified") is not True or not isinstance(check, dict) or check.get("result") != "clear":
                    raise ValueError("Verified eligibility and clear duplicate check required.")
                timestamp(check.get("checked_at"))
                url = normalize_url(data["application_url"])
                ats = data.get("ats_identity")
                if ats is not None and (not isinstance(ats, str) or not ats.strip()):
                    raise ValueError("Optional ats_identity must be a provider-qualified nonempty string.")
                identity = {k: data[k] for k in ("company", "title", "linkedin_job_id")}
                identity.update(normalized_url=url, ats_identity=ats)
                if key in state["jobs"]:
                    job = state["jobs"][key]
                    fixed = ("company", "title", "linkedin_job_id", "normalized_url")
                    if any(job["identity"][k] != identity[k] for k in fixed) or (ats and ats != job["identity"].get("ats_identity")):
                        raise ValueError("Job identity is immutable; reconcile changed identity explicitly outside queue.")
                    # Idempotent add never erases progress, claims, or previous evidence.
                    return {"job_id": key, "status": job["status"], "duplicate": True}
                for oldkey, job in state["jobs"].items():
                    old = job["identity"]
                    if url in [old["normalized_url"], *job.get("url_aliases", [])] or (ats and old.get("ats_identity") == ats):
                        return {"job_id": oldkey, "status": job["status"], "duplicate": True}
                state["jobs"][key] = {"identity": identity, "candidate": data,
                    "status": "ready_for_application", "created_at": now(), "claims": {}, "receipts": [], "url_aliases": [url]}
                event(state, "candidate_added", key)
                result = {"job_id": key, "status": "ready_for_application"}
            elif command == "claim":
                role, owner = a["role"], a["owner"]
                if role not in {"application", "notion"} or not owner.strip():
                    raise ValueError("Role application/notion and nonempty owner required.")
                if any(role in j["claims"] for j in state["jobs"].values()):
                    raise ValueError("Role already has an active claim; no automatic expiry or takeover.")
                if role == "application":
                    if ledger.get("runs", {}).get(state["run_id"], {}).get("status") != "active":
                        raise ValueError("Application claim requires an active ledger run.")
                    if ledger_counts(ledger, state["run_id"])["ledger_prepared"] >= state["target"]:
                        return {"claimed": False, "reason": "target_reached"}
                eligible = [(key, j) for key, j in state["jobs"].items()
                            if (j["status"] == "ready_for_application" if role == "application"
                                else j["status"] == "done" and j.get("notion_status") == "pending")]
                if a.get("job_id"):
                    eligible = [(k, j) for k, j in eligible if k == a["job_id"]]
                if not eligible:
                    return {"claimed": False, "reason": "no_eligible_jobs"}
                key, job = min(eligible, key=lambda x: (x[1]["created_at"], x[0]))
                if role == "notion":
                    verified_job(ledger, state["run_id"], key)
                token = uuid.uuid4().hex
                job["claims"][role] = {"owner": owner, "token": token, "claimed_at": now()}
                if role == "application":
                    job["status"] = "in_progress"
                event(state, "claimed", key, role=role, owner=owner)
                result = {"claimed": True, "job_id": key, "role": role, "token": token,
                          "candidate": job["candidate"]}
            else:
                key = a["job_id"]
                if command == "adopt" and key not in state["jobs"]:
                    parent_owned(state, ledger, a.get("ledger_owner"))
                    verified = verified_job(ledger, state["run_id"], key)
                    evidence = verified["evidence"]
                    company, title = evidence.get("company"), evidence.get("title")
                    if not isinstance(company, str) or not company.strip() or not isinstance(title, str) or not title.strip():
                        raise ValueError("Adoption requires company and exact title in verified ledger evidence.")
                    application_url = evidence["application_tab"]["url"]
                    normalized = normalize_url(application_url)
                    if any(normalized in [j["identity"]["normalized_url"], *j.get("url_aliases", [])] for j in state["jobs"].values()):
                        raise ValueError("Adopted ledger job conflicts with an existing queue destination.")
                    # This is migration of verified finished work, not a new candidate
                    # clearance. Do not invent qualification/duplicate-check assertions.
                    identity = {"company": company, "title": title, "linkedin_job_id": key,
                                "normalized_url": normalized, "ats_identity": None}
                    state["jobs"][key] = {"identity": identity, "candidate": {
                        "company": company, "title": title, "linkedin_job_id": key,
                        "application_url": application_url, "provenance": "adopted_same_run_verified_parent_ledger"},
                        "status": "migration_pending", "created_at": now(), "claims": {}, "receipts": [],
                        "url_aliases": [normalized]}
                if key not in state["jobs"]:
                    raise ValueError("Unknown queue job.")
                job = state["jobs"][key]
                if command == "bind":
                    require_claim(job, "application", a["owner"], a["token"])
                    url = normalize_url(a["application_url"])
                    ats = a.get("ats_identity")
                    if ats is not None and (not isinstance(ats, str) or not ats.strip()):
                        raise ValueError("ATS identity must be a provider-qualified string.")
                    old_ats = job["identity"].get("ats_identity")
                    if old_ats and ats and old_ats != ats:
                        raise ValueError("Cannot change an established ATS identity; reconcile before continuing.")
                    for otherkey, other in state["jobs"].items():
                        if otherkey == key:
                            continue
                        other_id = other["identity"]
                        if url in [other_id["normalized_url"], *other.get("url_aliases", [])] or (ats and ats == other_id.get("ats_identity")):
                            # Preserve current claim: worker must stop editing and parent
                            # inspect duplicate history before block/recovery/reconciliation.
                            job["duplicate_conflict"] = {"at": now(), "other_job_id": otherkey, "url": url, "ats_identity": ats}
                            event(state, "duplicate_conflict", key, other_job_id=otherkey)
                            save(path, state)
                            return {"bound": False, "duplicate": True, "job_id": key, "other_job_id": otherkey, "must_stop_application": True}
                    aliases = job.setdefault("url_aliases", [])
                    if url not in aliases:
                        aliases.append(url)
                    job["resolved_application_url"] = a["application_url"]
                    if ats:
                        job["identity"]["ats_identity"] = ats
                    event(state, "destination_bound", key, url=url, ats_identity=ats)
                    save(path, state)
                    return {"bound": True, "job_id": key, "application_url": a["application_url"]}
                lease = state.get("browser_lease")
                if lease and lease.get("job_id") == key:
                    raise ValueError("Release the browser mutation lease before completing or changing job ownership.")
                if command in {"adopt", "retry", "invalidate"}:
                    parent_owned(state, ledger, a.get("ledger_owner"))
                    if job["claims"]:
                        raise ValueError("Cannot adopt or retry a claimed job; reconcile the owner first.")
                    if command == "invalidate":
                        if state.get("browser_lease"):
                            raise ValueError("Release or recover the active browser lease before invalidation.")
                        if not a.get("reason", "").strip():
                            raise ValueError("Invalidation requires an exact evidence-backed reason.")
                        if job["status"] != "done":
                            raise ValueError("Invalidation only applies to a previously done queue job.")
                        try:
                            verified_job(ledger, state["run_id"], key)
                        except ValueError:
                            pass
                        else:
                            raise ValueError("Ledger still verifies prepared; reconcile ledger evidence before invalidation.")
                        job.update(status="reconciliation_needed", notion_status="suspended")
                        job.setdefault("invalidations", []).append({"at": now(), "reason": a["reason"]})
                    elif command == "adopt":
                        if job.get("duplicate_conflict"):
                            raise ValueError("Unreconciled destination duplicate conflict prevents adoption.")
                        verified = verified_job(ledger, state["run_id"], key)
                        if job["status"] == "done":
                            return {"job_id": key, "status": "done", "idempotent": True}
                        job.update(status="done", notion_status="pending", completed_at=now(),
                                   ledger_receipt={"status": verified["status"], "resume_path": verified["resume_path"],
                                                   "evidence": verified["evidence"], "verified_at": now()})
                    else:
                        if not a.get("reason", "").strip():
                            raise ValueError("Retry requires new evidence that resolves the specific blocker.")
                        if job.get("duplicate_conflict"):
                            raise ValueError("Duplicate destination conflict cannot be retried automatically; preserve and reconcile with parent.")
                        if job["status"] in {"blocked", "needs_user_input", "reconciliation_needed"}:
                            job["status"] = "ready_for_application"
                        elif job["status"] == "done" and job.get("notion_status") == "blocked":
                            job["notion_status"] = "pending"
                        else:
                            raise ValueError("Retry only applies to blocked application or Notion work.")
                    event(state, command, key, reason=a.get("reason"))
                    save(path, state)
                    return {"job_id": key, "status": job["status"], "notion_status": job.get("notion_status")}
                role = "application" if command == "complete" else ("notion" if command == "note" else a["role"])
                if command == "note":
                    receipt = a["receipt"]
                    if receipt.get("verified") is not True:
                        raise ValueError("Notion receipt requires verified=true after saved-value verification.")
                    normalize_url(receipt.get("row_url", ""))
                    if not (urlsplit(receipt["row_url"]).hostname or "").lower().endswith((".notion.so", ".notion.com")) and urlsplit(receipt["row_url"]).hostname not in {"notion.so", "notion.com"}:
                        raise ValueError("Receipt must identify a Notion row URL.")
                    for previous in job["receipts"]:
                        if previous.get("claim_token") == a["token"] and previous.get("owner") == a["owner"]:
                            if previous["receipt"] != receipt:
                                raise ValueError("Conflicting receipt for completed claim.")
                            return {"job_id": key, "notion_status": "notion_logged", "idempotent": True}
                require_claim(job, role, a["owner"], a["token"])
                if command == "complete":
                    parent_owned(state, ledger, a.get("ledger_owner"))
                    if job.get("duplicate_conflict"):
                        raise ValueError("Unreconciled destination duplicate conflict prevents done.")
                    verified = verified_job(ledger, state["run_id"], key)
                    if ledger_counts(ledger, state["run_id"])["ledger_prepared"] > state["target"]:
                        raise ValueError("Parent ledger exceeds target; reconcile before acknowledgment.")
                    job.update(status="done", notion_status="pending", completed_at=now(),
                               ledger_receipt={"status": verified["status"], "resume_path": verified["resume_path"],
                                               "evidence": verified["evidence"], "verified_at": now()})
                elif command == "block":
                    reason = a.get("reason", "").strip()
                    if not reason:
                        raise ValueError("Exact blocker reason required.")
                    job.setdefault("blockers", []).append({"at": now(), "role": role, "reason": reason})
                    if role == "application":
                        status = a.get("status", "blocked")
                        if status not in {"blocked", "needs_user_input", "skipped", "reconciliation_needed"}:
                            raise ValueError("Invalid application exception status.")
                        job["status"] = status
                    else:
                        job["notion_status"] = "blocked"
                elif command == "recover":
                    parent_owned(state, ledger, a.get("ledger_owner"))
                    if not a.get("owner_check", "").strip():
                        raise ValueError("Recovery requires evidence parent checked owner is no longer working.")
                    event(state, "recovery_owner_check", key, evidence=a["owner_check"])
                    if role == "application":
                        job["status"] = "ready_for_application"
                    else:
                        job["notion_status"] = "pending"
                elif command == "note":
                    verified_job(ledger, state["run_id"], key)
                    job["receipts"].append({"at": now(), "owner": a["owner"], "claim_token": a["token"], "receipt": receipt})
                    job["notion_status"] = "notion_logged"
                else:
                    raise ValueError("Unknown command.")
                del job["claims"][role]
                event(state, command, key, role=role, owner=a["owner"])
                result = {"job_id": key, "status": job["status"], "notion_status": job.get("notion_status")}
            save(path, state)
            return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", required=True, help="Separate per-run pipeline queue JSON")
    s = p.add_subparsers(dest="command", required=True)
    c = s.add_parser("init")
    c.add_argument("--run-id", required=True)
    c.add_argument("--target", required=True, type=int)
    c.add_argument("--ledger", required=True)
    c = s.add_parser("add", help="Idempotently add a qualified candidate; never resets progress")
    c.add_argument("--candidate-file", required=True)
    for name in ("list", "status"):
        c = s.add_parser(name)
        c.add_argument("--status")
    c = s.add_parser("claim")
    c.add_argument("--role", choices=["application", "notion"], required=True)
    c.add_argument("--owner", required=True, help="Agent identity, not ledger capability")
    c.add_argument("--job-id")
    c = s.add_parser("bind", help="Bind freshly observed ATS destination; stops on duplicates")
    for field in ("job-id", "owner", "token", "application-url"):
        c.add_argument("--" + field, required=True)
    c.add_argument("--ats-identity")
    c = s.add_parser("claim-browser")
    c.add_argument("--role", required=True, choices=["discovery", "application", "notion"])
    c.add_argument("--owner", required=True)
    c.add_argument("--job-id")
    c.add_argument("--token", help="Application/Notion job claim token, not browser token")
    for name in ("release-browser", "recover-browser"):
        c = s.add_parser(name)
        c.add_argument("--owner", required=True)
        c.add_argument("--token", required=True, help="Browser lease token")
        if name == "recover-browser":
            c.add_argument("--ledger-owner", required=True)
            c.add_argument("--owner-check", required=True)
    for name in ("adopt", "retry", "invalidate"):
        c = s.add_parser(name)
        c.add_argument("--job-id", required=True)
        c.add_argument("--ledger-owner", required=True)
        if name in {"retry", "invalidate"}:
            c.add_argument("--reason", required=True)
    for name in ("complete", "block", "note", "recover"):
        c = s.add_parser(name)
        for field in ("job-id", "owner", "token"):
            c.add_argument("--" + field, required=True)
        if name in {"block", "recover"}:
            c.add_argument("--role", choices=["application", "notion"], required=True)
        if name in {"complete", "recover"}:
            c.add_argument("--ledger-owner", required=True, help="Parent only; do not give this capability to workers")
        if name == "block":
            c.add_argument("--reason", required=True)
            c.add_argument("--status", default="blocked", choices=["blocked", "needs_user_input", "skipped", "reconciliation_needed"])
        if name == "recover":
            c.add_argument("--owner-check", required=True)
        if name == "note":
            c.add_argument("--receipt-file", required=True)
    args = vars(p.parse_args())
    path, command = args.pop("state"), args.pop("command")
    for field in ("candidate", "receipt"):
        source = args.pop(field + "_file", None)
        if source:
            args[field] = json.loads(Path(source).read_text())
    try:
        print(json.dumps(operate(path, command, **args), indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
