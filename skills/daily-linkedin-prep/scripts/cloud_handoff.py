#!/usr/bin/env python3
"""Offline hybrid handoff. Browser work belongs to the supported local agent.

Receipts are agent assertions, not independent live verification. This program
never connects to a browser, authenticates, writes Notion, or submits a form.
Use a private local POSIX filesystem for execution state, not a cloud-synced JSON
file shared by multiple computers. Immutable packet directories may be copied.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import uuid

from pipeline_queue import locked, save, normalize_url, timestamp
from verify_resume import verify as verify_resume

VERSION = 1
GATES = ("identity_verified", "form_revalidated", "supported_answers_persisted",
         "resume_attachment_verified", "tab_preserved", "final_control_visible",
         "submission_protection_verified")
PREFLIGHT = ("retrieval_verified", "dependencies_verified", "browser_supported",
             "authenticated", "lifecycle_supported", "history_reviewed",
             "tracker_reconciled")
STATES = ("needs_input", "ready_for_review", "failed", "expired", "submitted")


class HandoffError(ValueError):
    """A safe, actionable message with no interpolated private values."""


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise HandoffError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def read_json(path):
    # Never include input values in parse errors/logs.
    try:
        return json.loads(Path(path).read_text())
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Invalid UTF-8 JSON file.") from None


def private_path(path):
    path = Path(path).expanduser().absolute()
    repo = Path(__file__).resolve().parents[3]
    require(not path.resolve().is_relative_to(repo), "Live handoff/state must be outside the checkout.")
    require(not path.is_symlink(), "Handoff/state path cannot be a symlink.")
    return path


def asset_path(root, name):
    require(isinstance(name, str), "Asset path must be a portable relative path.")
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and name == relative.as_posix()
            and name not in (".", "manifest.json", "INDEX.md")
            and ".." not in relative.parts and "\\" not in name,
            "Unsafe or reserved asset path.")
    path = Path(root) / name
    require(path.resolve().is_relative_to(Path(root).resolve()), "Asset escapes packet.")
    current = Path(root)
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), "Symlink assets are not supported.")
    require(path.is_file(), "Declared asset is missing.")
    return path


def validate_packet(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "Packet must be a directory.")
    require(not (root / "manifest.json").is_symlink(), "Manifest cannot be a symlink.")
    p = read_json(root / "manifest.json")
    fields = {"schema_version", "applicant_id", "revision", "prepared_at", "profile_version",
              "source_sha256", "job", "answers", "form", "assets"}
    require(isinstance(p, dict) and set(p) == fields, "Packet must match the versioned manifest fields.")
    require(p["schema_version"] == VERSION, "Unsupported packet schema.")
    require(text(p["applicant_id"]) and text(p["profile_version"]), "Opaque applicant/profile IDs required.")
    require(type(p["revision"]) is int and p["revision"] > 0, "Positive integer revision required.")
    timestamp(p["prepared_at"])
    require(isinstance(p["source_sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", p["source_sha256"]), "Source SHA256 required.")
    job = p["job"]
    require(isinstance(job, dict) and set(job) == {"linkedin_job_id", "company", "title", "url", "application_url", "ats_identity"}, "Exact job identity fields required.")
    require(text(job["linkedin_job_id"]) and job["linkedin_job_id"].isdigit()
            and text(job["company"]) and text(job["title"]), "Job ID, company and title required.")
    normalize_url(job["url"])
    normalize_url(job["application_url"])
    require(job["ats_identity"] is None or (text(job["ats_identity"]) and ":" in job["ats_identity"]), "ATS identity must be provider-qualified or null.")
    require(isinstance(p["answers"], list), "Answers must be a list.")
    for a in p["answers"]:
        require(isinstance(a, dict) and set(a) == {"question", "value", "sources", "confidence", "requires_review"}, "Answer fields do not match schema.")
        require(text(a["question"]) and (a["value"] is None or isinstance(a["value"], str))
                and isinstance(a["sources"], list) and all(text(s) for s in a["sources"])
                and a["confidence"] in ("low", "medium", "high")
                and type(a["requires_review"]) is bool, "Invalid answer candidate.")
        require((a["value"] is None and a["requires_review"]) or bool(a["sources"]), "Unknown answers need review; proposed values need evidence.")
    f = p["form"]
    require(isinstance(f, dict) and set(f) == {"coverage", "observed_at", "fingerprint", "questions"}, "Form observation fields required.")
    require(f["coverage"] in ("uninspected", "partial", "complete") and isinstance(f["questions"], list)
            and all(text(q) for q in f["questions"]), "Invalid form coverage/questions.")
    if f["coverage"] == "uninspected":
        require(f["observed_at"] is None and f["fingerprint"] is None and not f["questions"], "Uninspected form cannot claim observations.")
    else:
        timestamp(f["observed_at"])
        require(text(f["fingerprint"]), "Observed form needs a fingerprint.")
    require(isinstance(p["assets"], list), "Assets must be a list.")
    seen = set()
    for a in p["assets"]:
        require(isinstance(a, dict) and set(a) == {"path", "sha256", "kind"}
                and text(a["kind"]), "Asset fields do not match schema.")
        require(a["path"] not in seen, "Duplicate asset path.")
        seen.add(a["path"])
        require(digest(asset_path(root, a["path"]).read_bytes()) == a["sha256"], "Asset hash mismatch.")
    application_id = digest((p["applicant_id"] + "\0" + job["linkedin_job_id"]).encode())
    return p, application_id, digest(encoded(p))


def publish(source, destination):
    """Copy declared files only. Transport/durability must be verified separately."""
    source, destination = private_path(source), private_path(destination)
    p, app_id, sha = validate_packet(source)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(destination / "publication"):
        target = destination / app_id / str(p["revision"])
        if target.exists():
            require(validate_packet(target)[2] == sha, "Published revision is immutable.")
        else:
            target.parent.mkdir(mode=0o700, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=".packet-", dir=target.parent))
            try:
                for a in p["assets"]:
                    out = temporary / a["path"]
                    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(asset_path(source, a["path"]), out)
                    os.chmod(out, 0o600)
                save(temporary / "manifest.json", p)
                index = temporary / "INDEX.md"
                index.write_text("# Private application packet\n\nApplication ID: " + app_id
                                 + "\nRevision: " + str(p["revision"])
                                 + "\n\nPreparation only; not a filled or submitted application.\n")
                os.chmod(index, 0o600)
                require(validate_packet(temporary)[2] == sha, "Source changed during publication.")
                os.rename(temporary, target)
                # Flush directory entries as well as the atomic JSON manifest.
                for directory in (target, target.parent, destination):
                    fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
    return {"application_id": app_id, "revision": p["revision"], "sha256": sha,
            "packet": str(target), "transport_verified": False}


def receipt(value):
    require(isinstance(value, dict) and text(value.get("reference")), "Receipt needs a private evidence reference.")
    timestamp(value.get("checked_at"))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["checked_at"].replace("Z", "+00:00"))).total_seconds()
    require(-300 <= age <= 86400, "Receipt must be current within 24 hours.")
    return value


def snapshot(state, value):
    receipt(value)
    require(value.get("all_runner_resources_observed") is True
            and isinstance(value.get("resources"), dict)
            and value.get("unassigned_resources") == [], "Complete resource inventory required; reconcile unknown popups first.")
    require(set(value["resources"]) == set(state["jobs"]), "Inventory must name every imported application, including empty lists.")
    observed = set()
    for key, resources in value["resources"].items():
        require(isinstance(resources, list) and len(resources) <= state["max_pages_per_application"], "Resource cap exceeded; stop and reconcile.")
        for item in resources:
            require(isinstance(item, dict) and text(item.get("id"))
                    and item.get("kind") in ("tab", "context", "popup"), "Resource ID and kind required.")
            require(item["id"] not in observed, "A browser resource cannot belong to two applications.")
            observed.add(item["id"])
    occupied = sum(bool(r) or state["jobs"][k]["status"] == "in_progress"
                   for k, r in value["resources"].items())
    require(occupied <= state["max_open_applications"], "Application cap exceeded; stop and reconcile.")
    for key, resources in value["resources"].items():
        job = state["jobs"][key]
        handoff = job["status"] == "ready_for_review" or job.get("manual_upload") is True
        if handoff and job["resources"] and not resources:
            job["status"] = "needs_input"
            job["manual_upload"] = False
            job["review_blocker"] = "Review pages closed without confirmed submission; reconcile before reuse."
            enqueue(state, key, job)
        job["resources"] = resources
    state["inventory_at"] = value["checked_at"]


def packet_for(job):
    info = job["revisions"][str(job["active_revision"])]
    p, _, sha = validate_packet(info["path"])
    require(sha == info["sha256"], "Pinned packet changed after import.")
    return p, Path(info["path"])


def destinations(job):
    urls = {normalize_url(job["identity"]["application_url"]), *job.get("url_aliases", [])}
    ats = job.get("destination", {}).get("ats_identity") or job["identity"]["ats_identity"]
    return urls, ats


def enqueue(state, key, job):
    # Payloads deliberately omit candidate answers and document contents.
    item = {"id": uuid.uuid4().hex, "application_id": key, "status": job["status"],
            "job": {**job["identity"], **job.get("destination", {})}, "created_at": now(), "requires_reconciliation": True,
            "allow_create": job["status"] in ("ready_for_review", "submitted") or job.get("manual_upload") is True,
            "manual_upload": job.get("manual_upload", False), "resume": job.get("reviewed_resume"),
            "blocker": job.get("review_blocker")}
    if job["status"] == "submitted":
        item["submission_evidence"] = {k: job["last_receipt"][k]
                                       for k in ("reference", "submitted_at", "confirmation_source")}
    # A newer confirmed outcome supersedes stale pending preparation updates.
    prior = [i for i in state["outbox"] if i["application_id"] == key]
    job.setdefault("superseded_notion_intents", []).extend(prior)
    state["outbox"] = [i for i in state["outbox"] if i["application_id"] != key]
    state["outbox"].append(item)


def reviewed_resume(job, r):
    p, _ = packet_for(job)
    qa = verify_resume(r.get("resume_manifest", ""), job["identity"]["linkedin_job_id"])
    require(any(a["sha256"] == qa["sha256"] and a["kind"] == "resume" for a in p["assets"]), "Reviewed resume must match an immutable packet asset.")
    return {"path": qa["artifact_path"], "sha256": qa["sha256"]}


def sync_history(state, path, key, job):
    """Leave conservative duplicate protection even after the hybrid owner exits."""
    history = Path(state["history_path"])
    with locked(history):
        h = read_json(history)
        require(h.get("hybrid_owner") == {"state_path": str(path.resolve())}, "Hybrid history interlock is missing.")
        job_id = job["identity"]["linkedin_job_id"]
        old = h["jobs"].get(job_id)
        require(not old or (old.get("hybrid_state") == str(path.resolve())
                            and old.get("hybrid_application_id") == key), "History changed; reconcile before browser work.")
        require(not old or old.get("status") != "submitted" or job["status"] == "submitted", "Historical submission cannot regress.")
        h["jobs"][job_id] = {"status": "submitted" if job["status"] == "submitted" else "reconciliation_needed",
                             "run_id": None, "hybrid_state": str(path.resolve()), "hybrid_application_id": key,
                             "hybrid_status": job["status"], "application_url": job.get("destination", job["identity"])["application_url"],
                             "ats_identity": destinations(job)[1], "url_aliases": sorted(destinations(job)[0]),
                             "prior_history": old.get("prior_history", []) if old else [],
                             "note": "Hybrid application history; inspect the private execution ledger before reuse."}
        save(history, h)


def operate(path, command, **a):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(path):
        state = read_json(path) if path.exists() else None
        if command == "init":
            require(state is None, "State already exists; resume it instead of resetting history.")
            for name, default in (("max_open_applications", 1), ("max_pages_per_application", 4), ("max_claims", 15), ("max_minutes", 60), ("max_attempts", 2)):
                a.setdefault(name, default)
                require(type(a[name]) is int and a[name] > 0, "Limits must be positive integers.")
            state = {"version": VERSION, "jobs": {}, "owner": None, "preflight": None,
                     "pilot_passed": False, "outbox": [], "events": [],
                     **{k: a[k] for k in ("max_open_applications", "max_pages_per_application", "max_claims", "max_minutes", "max_attempts")}}
            result = {"initialized": True}
        else:
            require(isinstance(state, dict) and state.get("version") == VERSION, "Initialize supported state first.")
            if command == "status":
                return {"jobs": {k: {"status": j["status"], "active_revision": j["active_revision"],
                                     "available_revisions": sorted(j["revisions"]), "resources": j["resources"],
                                     "attempts": j["attempts"]} for k, j in state["jobs"].items()},
                        "owned": state["owner"] is not None, "preflight_passed": bool(state["preflight"]),
                        "pilot_passed": state["pilot_passed"], "pending_notion": len(state["outbox"]),
                        "max_open_applications": state["max_open_applications"]}
            if command in ("acquire", "recover"):
                if command == "recover":
                    require(state["owner"] and state["owner"]["token"] == a.get("token"), "Exact abandoned owner token required.")
                    r = receipt(a.get("receipt"))
                    require(r.get("owner_stopped") is True, "Verify the previous owner stopped; elapsed time is insufficient.")
                else:
                    require(state["owner"] is None, "Queue consumer already owns state; no automatic takeover.")
                require(text(a.get("owner")), "Consumer name required.")
                token = uuid.uuid4().hex
                state["owner"] = {"name": a["owner"], "token": token, "started_at": now(), "claims": 0}
                state["preflight"] = None
                if command == "recover":
                    for job in state["jobs"].values():
                        if job["status"] == "in_progress":
                            job["status"] = "waiting_for_local_browser"
                result = {"token": token, "preflight_required": True}
            else:
                require(state["owner"] and a.get("token") == state["owner"]["token"], "Exact current consumer token required.")
                if command == "release":
                    require(not any(j["status"] == "in_progress" or j["resources"] for j in state["jobs"].values()), "Checkpoint work and close/reconcile held browser resources before release.")
                    if state.get("history_path"):
                        history = Path(state["history_path"])
                        with locked(history):
                            h = read_json(history)
                            require(h.get("hybrid_owner") in (None, {"state_path": str(path.resolve())}), "History interlock belongs to another queue.")
                            h.pop("hybrid_owner", None)
                            save(history, h)
                    state["owner"] = None
                    state["preflight"] = None
                    result = {"released": True}
                elif command == "import":
                    packet = private_path(a["packet"])
                    p, key, sha = validate_packet(packet)
                    require(state.get("applicant_id", p["applicant_id"]) == p["applicant_id"], "One applicant per execution ledger; do not mix profiles.")
                    state["applicant_id"] = p["applicant_id"]
                    identity = {"applicant_id": p["applicant_id"], **p["job"]}
                    for otherkey, other in state["jobs"].items():
                        old = other["identity"]
                        if otherkey != key and old["applicant_id"] == p["applicant_id"]:
                            urls, ats = destinations(other)
                            require(not (normalize_url(identity["application_url"]) in urls
                                         or (identity["ats_identity"] and ats == identity["ats_identity"])), "Duplicate application destination/ATS identity.")
                    job = state["jobs"].get(key)
                    if job:
                        urls, ats = destinations(job)
                        require(all(job["identity"][k] == identity[k] for k in ("applicant_id", "linkedin_job_id", "company", "title", "url"))
                                and normalize_url(identity["application_url"]) in urls
                                and identity["ats_identity"] in (job["identity"]["ats_identity"], ats), "Identity changed; reconcile explicitly before importing.")
                    else:
                        job = {"identity": identity, "status": "prepared", "active_revision": p["revision"],
                               "revisions": {}, "resources": [], "human_notes": [], "attempts": 0}
                        state["jobs"][key] = job
                    rev = str(p["revision"])
                    require(rev not in job["revisions"] or job["revisions"][rev]["sha256"] == sha, "Imported revision is immutable.")
                    job["revisions"].setdefault(rev, {"path": str(packet.resolve()), "sha256": sha})
                    result = {"application_id": key, "status": job["status"], "active_revision": job["active_revision"]}
                elif command == "preflight":
                    r = receipt(a.get("receipt"))
                    require(all(r.get(k) is True for k in PREFLIGHT) and text(r.get("browser_session")), "All local preflight checks must pass.")
                    history = Path(r.get("history_path", ""))
                    require(history.is_absolute() and history.resolve() != path.resolve(), "Restored history path must be absolute and separate.")
                    require(not state.get("history_path") or state["history_path"] == str(history.resolve()), "Restored history identity cannot change between sessions.")
                    with locked(history):
                        h = read_json(history)
                        require(isinstance(h, dict) and isinstance(h.get("jobs"), dict), "Restored prior-job history required.")
                        require(not h.get("owner") and h.get("hybrid_owner") in (None, {"state_path": str(path.resolve())}), "Another pipeline owns history; stop it before hybrid browser work.")
                        snapshot(state, r)
                        h["hybrid_owner"] = {"state_path": str(path.resolve())}
                        save(history, h)
                    state["history_path"] = str(history.resolve())
                    state["preflight"] = r
                    result = {"preflight_passed": True, "pilot_required": not state["pilot_passed"]}
                elif command == "reconcile":
                    require(state["preflight"], "Run preflight first.")
                    snapshot(state, a.get("receipt"))
                    result = {"inventory_recorded": True}
                elif command == "pause":
                    receipt(a.get("receipt"))
                    for j in state["jobs"].values():
                        if j["status"] == "in_progress":
                            j["status"] = "waiting_for_local_browser"
                    state["preflight"] = None
                    result = {"paused": True}
                elif command == "claim":
                    require(state["preflight"], "Local browser preflight required; unavailable means waiting, not cloud fallback.")
                    receipt(state["preflight"])
                    owner = state["owner"]
                    elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(owner["started_at"])).total_seconds()
                    require(owner["claims"] < state["max_claims"] and elapsed < state["max_minutes"] * 60, "Session budget reached; checkpoint and exit.")
                    require(state["pilot_passed"] or owner["claims"] == 0
                            or a["application_id"] == state.get("pilot_application_id"), "Complete the one-job non-submitting pilot before a long queue.")
                    require(not any(j["status"] == "in_progress" for j in state["jobs"].values()), "Only one application may be prepared at a time.")
                    occupied = sum(bool(j["resources"]) for j in state["jobs"].values())
                    key = a["application_id"]
                    job = state["jobs"][key]
                    require(job["status"] in ("prepared", "waiting_for_local_browser", "waiting_for_preparation", "failed", "needs_input"), "Application is held for review, expired or submitted.")
                    require(bool(job["resources"]) or occupied < state["max_open_applications"], "Close prior application resources before opening another.")
                    require(job.get("technical_failures", 0) < state["max_attempts"], "Per-job technical retry budget reached; record changed recovery evidence before retrying.")
                    packet_for(job)
                    history = Path(state["preflight"]["history_path"])
                    with locked(history, exclusive=False):
                        h = read_json(history)
                    require(not h.get("owner"), "Legacy pipeline owns history; stop it before hybrid browser work.")
                    require(h.get("hybrid_owner") == {"state_path": str(path.resolve())}, "Hybrid history interlock is missing.")
                    for oldkey, old in h["jobs"].items():
                        if old.get("hybrid_state") == str(path.resolve()) and old.get("hybrid_application_id") == key:
                            require(old.get("status") != "submitted", "History confirms this application was submitted.")
                            continue
                        evidence = old.get("evidence", {})
                        oldurl = evidence.get("application_tab", {}).get("url") or old.get("application_url") or old.get("url")
                        urls, ats = destinations(job)
                        oldurls = [oldurl, *old.get("url_aliases", [])]
                        require(str(oldkey) != job["identity"]["linkedin_job_id"]
                                and old.get("ats_identity") != (ats or "__absent__")
                                and not any(u and normalize_url(u) in urls for u in oldurls), "Historical identity match; reconcile existing work instead of opening a new application.")
                    job["status"] = "in_progress"
                    job["attempts"] += 1
                    owner["claims"] += 1
                    state["pilot_application_id"] = key
                    sync_history(state, path, key, job)
                    result = {"application_id": key, "packet": job["revisions"][str(job["active_revision"])]["path"],
                              "reuse_resources": job["resources"], "never_submit": True}
                elif command == "bind":
                    key = a["application_id"]
                    job = state["jobs"][key]
                    r = receipt(a.get("receipt"))
                    require(state["preflight"] and job["status"] == "in_progress" and r.get("identity_verified") is True,
                            "Claim and verify the actual destination before binding it.")
                    url = normalize_url(r.get("application_url", ""))
                    ats = r.get("ats_identity")
                    require(ats is None or (text(ats) and ":" in ats), "ATS identity must be provider-qualified or null.")
                    urls, known_ats = destinations(job)
                    require(not known_ats or ats in (None, known_ats), "Established ATS identity cannot change.")
                    ats = ats or known_ats
                    for otherkey, other in state["jobs"].items():
                        if otherkey != key:
                            other_urls, other_ats = destinations(other)
                            require(url not in other_urls and not (ats and ats == other_ats), "Redirect duplicates another application; stop and reconcile.")
                    history = Path(state["history_path"])
                    with locked(history, exclusive=False):
                        hist = read_json(history)
                    for old in hist["jobs"].values():
                        if old.get("hybrid_state") == str(path.resolve()) and old.get("hybrid_application_id") == key:
                            continue
                        oldurl = old.get("application_url") or old.get("url") or old.get("evidence", {}).get("application_tab", {}).get("url")
                        require(not (ats and ats == old.get("ats_identity"))
                                and not any(u and normalize_url(u) == url for u in [oldurl, *old.get("url_aliases", [])]),
                                "Redirect matches historical work; stop and reconcile before filling.")
                    job["url_aliases"] = sorted(urls | {url})
                    job["destination"] = {"application_url": url, "ats_identity": ats}
                    sync_history(state, path, key, job)
                    result = {"destination_bound": True}
                elif command == "observe-form":
                    job = state["jobs"][a["application_id"]]
                    r = receipt(a.get("receipt"))
                    require(state["preflight"] and job["status"] in ("in_progress", "waiting_for_preparation") and job["resources"], "Claim and inventory the live form before observing it.")
                    require(not any(k != a["application_id"] and j["status"] == "in_progress" for k, j in state["jobs"].items()), "Only one application may be prepared at a time.")
                    f = r.get("form", {})
                    require(isinstance(f, dict) and set(f) == {"coverage", "observed_at", "fingerprint", "questions"}
                            and f["coverage"] in ("partial", "complete") and text(f["fingerprint"])
                            and isinstance(f["questions"], list) and all(text(q) for q in f["questions"]), "Observed form needs coverage, fingerprint and question structure.")
                    timestamp(f["observed_at"])
                    require(r.get("identity_verified") is True and r.get("no_values_or_secrets") is True
                            and r.get("no_human_edits_verified") is True, "Only verified discovery-only forms may wait for cloud preparation.")
                    output = private_path(a["output"])
                    require(not output.exists() and output.resolve() not in (path.resolve(), Path(state["history_path"]).resolve()), "Observation export must be a new private file.")
                    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    observation = {"application_id": a["application_id"], "job": {**job["identity"], **job.get("destination", {})}, "form": f}
                    save(output, observation)
                    job["form_observation"] = f
                    job["status"] = "waiting_for_preparation"
                    result = {"observation": str(output), "status": job["status"], "resources_still_held": True}
                elif command == "accept-preparation":
                    job = state["jobs"][a["application_id"]]
                    r = receipt(a.get("receipt"))
                    rev = str(a["revision"])
                    require(job["status"] == "waiting_for_preparation" and state["preflight"]
                            and r.get("no_human_edits_verified") is True and r.get("form_revalidated") is True,
                            "Only discovery-only work with fresh validation may accept preparation.")
                    require(job["resources"] and not any(k != a["application_id"] and j["status"] == "in_progress" for k, j in state["jobs"].items()), "Existing resource reservation and sequential preparation are required before acceptance.")
                    require(rev in job["revisions"] and a["revision"] > job["active_revision"], "Import a newer preparation revision first.")
                    info = job["revisions"][rev]
                    p, _, sha = validate_packet(info["path"])
                    require(sha == info["sha256"] and p["form"] == job["form_observation"]
                            and r.get("fingerprint") == p["form"]["fingerprint"], "Preparation must bind the exact observed live form.")
                    job["active_revision"] = a["revision"]
                    # Resume the same application, not another claim or browser slot.
                    job["status"] = "in_progress"
                    result = {"active_revision": a["revision"], "reuse_resources": job["resources"]}
                elif command == "retry":
                    job = state["jobs"][a["application_id"]]
                    r = receipt(a.get("receipt"))
                    require(job["status"] == "failed" and state["preflight"]
                            and r.get("recovery_verified") is True and text(r.get("changed_evidence")), "Retry requires a failed job and verified changed recovery evidence.")
                    job.setdefault("recovery_receipts", []).append(r)
                    job["technical_failures"] = 0
                    job["status"] = "waiting_for_local_browser"
                    result = {"retry_checkpointed": True, "total_claims_preserved": job["attempts"]}
                elif command in ("report", "note", "activate-revision"):
                    key = a["application_id"]
                    job = state["jobs"][key]
                    r = receipt(a.get("receipt"))
                    if command == "note":
                        require(text(r.get("note")), "Human correction/review note required.")
                        job["human_notes"].append(r)
                        result = {"note_preserved": True}
                    elif command == "activate-revision":
                        rev = str(a["revision"])
                        require(job["status"] in ("prepared", "failed", "expired") and not job["resources"]
                                and r.get("human_approved") is True and rev in job["revisions"], "Revision activation requires explicit human approval and no held work.")
                        job["active_revision"] = a["revision"]
                        packet_for(job)
                        job["status"] = "prepared"
                        result = {"active_revision": a["revision"], "human_notes_preserved": True}
                    else:
                        status = a["status"]
                        require(status in STATES, "Unsupported result state.")
                        require(job["status"] != "submitted" or status == "submitted", "Confirmed submission cannot regress.")
                        if status == "submitted":
                            require(r.get("confirmation_source") in ("human", "visible_success")
                                    and text(r.get("confirmation")), "Submission requires explicit human confirmation or visible success evidence.")
                            timestamp(r.get("submitted_at"))
                        else:
                            permitted = ("in_progress", "waiting_for_preparation", "waiting_for_local_browser")
                            require(state["preflight"] and job["status"] in permitted, "Claim the application before reporting a result.")
                            if status == "ready_for_review":
                                require(job["status"] == "in_progress", "Resume active preparation before recording readiness.")
                            require(r.get("submission_performed") is False, "Executor never submits applications.")
                            if status == "ready_for_review":
                                require(all(r.get(k) is True for k in GATES) and r.get("required_unresolved") == []
                                        and r.get("validation_errors") == [] and r.get("inaccessible_steps") == []
                                        and bool(job["resources"]) and job.get("destination"), "Ready requires a bound destination, complete live form, attachment and preserved-resource gates.")
                                job["reviewed_resume"] = reviewed_resume(job, r)
                            if r.get("manual_upload") is True:
                                require(status == "needs_input" and r.get("resume_attachment_verified") is False
                                        and r.get("upload_recovery_exhausted") is True and text(r.get("missing_upload_reason"))
                                        and all(r.get(k) is True for k in GATES if k != "resume_attachment_verified")
                                        and r.get("validation_errors") == [] and r.get("inaccessible_steps") == []
                                        and bool(job["resources"]) and job.get("destination"), "Manual upload requires a bound destination, complete reviewable form, exhausted upload recovery and a QA resume.")
                                job["reviewed_resume"] = reviewed_resume(job, r)
                        job["manual_upload"] = r.get("manual_upload") is True
                        previous = job["status"]
                        job["status"] = status
                        if status == "failed":
                            job["technical_failures"] = job.get("technical_failures", 0) + 1
                        job["last_receipt"] = r
                        require(state.get("history_path"), "Reconcile restored history before recording application outcomes.")
                        sync_history(state, path, key, job)
                        if ((status in ("ready_for_review", "submitted") and previous != status)
                                or job["manual_upload"] or (job.get("notion_receipt") and status != "submitted")):
                            enqueue(state, key, job)
                        result = {"status": status, "resources_still_held": bool(job["resources"])}
                elif command == "pilot":
                    r = receipt(a.get("receipt"))
                    job = state["jobs"][a["application_id"]]
                    require(job["status"] == "ready_for_review" and r.get("non_submitting_pilot_passed") is True,
                            "Pilot must finish one verified application without submission.")
                    state["pilot_passed"] = True
                    result = {"pilot_passed": True}
                elif command == "outbox":
                    return {"next": state["outbox"][0] if state["outbox"] else None}
                elif command == "ack-notion":
                    r = receipt(a.get("receipt"))
                    previous_ack = state.get("notion_acknowledgements", {}).get(a.get("outbox_id"))
                    if previous_ack is not None:
                        require(previous_ack == r, "Conflicting Notion acknowledgement; reconcile the recorded receipt.")
                        return {"acknowledged": True, "idempotent": True}
                    require(state["outbox"] and state["outbox"][0]["id"] == a.get("outbox_id"), "Acknowledge only the oldest pending Notion intent.")
                    require(r.get("reopened_verified") is True and r.get("existing_outcomes_preserved") is True,
                            "Notion requires readback and preservation of existing outcomes.")
                    normalize_url(r.get("row_url", ""))
                    item = state["outbox"].pop(0)
                    state["jobs"][item["application_id"]]["notion_receipt"] = r
                    state.setdefault("notion_acknowledgements", {})[item["id"]] = r
                    result = {"acknowledged": True}
                else:
                    raise ValueError("Unknown command.")
        state["events"].append({"at": now(), "command": command,
                                "application_id": a.get("application_id")})
        save(path, state)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", help="Private local execution ledger, outside checkout")
    subs = parser.add_subparsers(dest="command", required=True)
    pub = subs.add_parser("publish")
    pub.add_argument("--source", required=True)
    pub.add_argument("--destination", required=True)
    val = subs.add_parser("validate")
    val.add_argument("--packet", required=True)
    init = subs.add_parser("init")
    for flag, default in (("max-open-applications", 1), ("max-pages-per-application", 4), ("max-claims", 15), ("max-minutes", 60), ("max-attempts", 2)):
        init.add_argument("--" + flag, type=int, default=default)
    subs.add_parser("status")
    for command in ("acquire", "recover", "release", "import", "preflight", "reconcile", "pause", "claim", "bind", "observe-form", "accept-preparation", "retry", "report", "note", "activate-revision", "pilot", "outbox", "ack-notion"):
        sub = subs.add_parser(command)
        if command in ("acquire", "recover"):
            sub.add_argument("--owner", required=True)
        if command != "acquire":
            sub.add_argument("--token", required=True)
        if command in ("recover", "preflight", "reconcile", "pause", "bind", "observe-form", "accept-preparation", "retry", "report", "note", "activate-revision", "pilot", "ack-notion"):
            sub.add_argument("--receipt", required=True, help="Private JSON receipt from observed evidence")
        if command in ("claim", "bind", "observe-form", "accept-preparation", "retry", "report", "note", "activate-revision", "pilot"):
            sub.add_argument("--application-id", required=True)
        if command == "import":
            sub.add_argument("--packet", required=True)
        if command in ("activate-revision", "accept-preparation"):
            sub.add_argument("--revision", type=int, required=True)
        if command == "observe-form":
            sub.add_argument("--output", required=True)
        if command == "report":
            sub.add_argument("--status", choices=STATES, required=True)
        if command == "ack-notion":
            sub.add_argument("--outbox-id", required=True)
    args = vars(parser.parse_args(argv))
    command, state = args.pop("command"), args.pop("state")
    try:
        if command == "publish":
            result = publish(args["source"], args["destination"])
        elif command == "validate":
            p, key, sha = validate_packet(args["packet"])
            result = {"application_id": key, "revision": p["revision"], "sha256": sha, "integrity_verified": True}
        else:
            require(state is not None, "--state required for queue commands.")
            if "receipt" in args:
                args["receipt"] = read_json(args["receipt"])
            result = operate(state, command, **args)
    except HandoffError as error:
        parser.exit(2, str(error) + "\n")
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        # Do not echo exceptions containing potentially private paths/values.
        parser.exit(2, "Handoff command rejected. Check schema, ownership, evidence, resource limits and private files; no live action was performed.\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
