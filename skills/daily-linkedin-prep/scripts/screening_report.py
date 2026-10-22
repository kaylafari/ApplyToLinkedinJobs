#!/usr/bin/env python3
"""Offline evidence accounting. No browser, network, scoring or ledger writes.

Cards: JSON list, or {"cards": [...]}; each has job_id/id and observed text.
Assessments: list, or {"assessments": [...]}; each has job_id/id, stage
(screening/full_jd), decision (excluded/hold/qualified), and evidence. Full JDs
also require description_source. Qualified requires explicit score >60 and
eligibility_verified=true. Conflicting same-stage assessments fail closed;
provide a reconciled input instead of relying on file order to choose a winner.

Historical matches are identity evidence, NEVER automatic exclusions. A prior
screening record can match an ID without representing an application.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Kept byte-for-behavior compatible with daily-linkedin-prep/pipeline_queue.py.
# Unknown query parameters (including ashby_jid) remain identity-bearing.
TRACKING = {"trk", "trackingid", "ref", "refid", "source", "sourceid", "gh_src",
            "ashby_jid_tracking", "fbclid", "gclid", "lipi", "lici", "originalsubdomain"}
DECISIONS = {"excluded", "hold", "qualified"}
STAGES = {"screening", "full_jd"}


def normalize_url(value):
    if not isinstance(value, str):
        raise ValueError("URL must be a string")
    p = urlsplit(value)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Application URL must be HTTPS without embedded credentials.")
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k.lower() not in TRACKING and not k.lower().startswith("utm_"))
    path = p.path.rstrip("/") or "/"
    if p.hostname.lower() == "jobs.ashbyhq.com" and path.endswith("/application"):
        path = path[:-12]
    return urlunsplit(("https", p.netloc.lower(), path, urlencode(query), ""))


def job_id(record, fallback=None):
    values = [record[k] for k in ("job_id", "id", "linkedin_job_id") if k in record]
    if not values and fallback is not None:
        values = [fallback]
    if not values or any(not isinstance(v, str) or not re.fullmatch(r"[0-9]+", v) for v in values):
        raise ValueError("Job IDs must be explicit nonempty digit strings")
    if len(set(values)) != 1:
        raise ValueError("Conflicting job ID aliases")
    return values[0]


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def ats_id(value):
    nonempty(value, "ats_identity")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*:[^\s:]+(?::[^\s:]+)*", value):
        raise ValueError("ATS identity must be explicitly provider-qualified")
    return value  # Exact, case-sensitive; no guessed provider or extracted number.


def identity_tokens(record):
    """Only explicit identity fields; never company, title, notes or page text."""
    tokens = set()
    nested = record.get("identity", {})
    if not isinstance(nested, dict):
        raise ValueError("identity must be an object")
    for item in (record, nested):
        if item.get("ats_identity") is not None:
            tokens.add(("ats_identity", ats_id(item["ats_identity"])))
        for key in ("url", "application_url", "resolved_application_url", "normalized_url", "detail_url"):
            if item.get(key) is not None:
                tokens.add(("normalized_url", normalize_url(item[key])))
    aliases = record.get("url_aliases", [])
    if not isinstance(aliases, list):
        raise ValueError("url_aliases must be a list")
    tokens.update(("normalized_url", normalize_url(v)) for v in aliases)
    return tokens


def reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def unique_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"Duplicate JSON key: {key}")
        out[key] = value
    return out


def read_input(path, kind):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    data = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    return data, {"path": str(path), "kind": kind, "sha256": hashlib.sha256(raw).hexdigest(),
                  "bytes": len(raw)}


def record_list(data, field):
    result = data.get(field) if isinstance(data, dict) else data
    if not isinstance(result, list) or any(not isinstance(v, dict) for v in result):
        raise ValueError(f"Expected a list of {field} objects")
    return result


def historical_records(data, prefix="$"):
    """Ledger/import/queue jobs plus preserved snapshots and prior_history only."""
    if not isinstance(data, dict):
        raise ValueError("Historical input must be an object with jobs")
    jobs = data.get("jobs")
    if not isinstance(jobs, (dict, list)):
        raise ValueError("Historical input must contain jobs object/list")
    iterable = jobs.items() if isinstance(jobs, dict) else enumerate(jobs)

    def versions(record, pointer, fallback=None):
        if not isinstance(record, dict):
            raise ValueError(f"Invalid historical job at {pointer}")
        yield record, pointer, fallback
        priors = record.get("prior_history", [])
        if not isinstance(priors, list):
            raise ValueError("prior_history must be a list")
        for n, prior in enumerate(priors):
            yield from versions(prior, f"{pointer}.prior_history[{n}]", fallback)

    for key, record in iterable:
        yield from versions(record, f"{prefix}.jobs[{key}]", str(key) if isinstance(jobs, dict) else None)
    history = data.get("history", [])
    if not isinstance(history, list):
        raise ValueError("history must be a list")
    for n, item in enumerate(history):
        if not isinstance(item, dict):
            raise ValueError("history entries must be objects")
        if "snapshot" in item:
            yield from historical_records(item["snapshot"], f"{prefix}.history[{n}].snapshot")


def build_report(card_paths, assessment_paths, history_paths):
    provenance, warnings, cards, assessments, index = [], [], {}, {}, {}
    seen_paths = set()

    def load(path, kind):
        data, source = read_input(path, kind)
        if source["path"] in seen_paths:
            raise ValueError("Same input path supplied more than once")
        seen_paths.add(source["path"])
        provenance.append(source)
        return data, source

    observations = 0
    for path in card_paths:
        data, source = load(path, "cards")
        for n, card in enumerate(record_list(data, "cards")):
            key = job_id(card)
            nonempty(card.get("text"), "observed card text")
            tokens = identity_tokens(card)
            bucket = cards.setdefault(key, {"observations": [], "tokens": set()})
            bucket["observations"].append({"source": source["path"], "record_index": n})
            bucket["tokens"].update(tokens)
            observations += 1

    for path in assessment_paths:
        data, source = load(path, "assessments")
        for n, record in enumerate(record_list(data, "assessments")):
            key = job_id(record)
            stage, decision = record.get("stage"), record.get("decision")
            if stage not in STAGES or decision not in DECISIONS:
                raise ValueError("Assessment requires explicit supported stage and decision")
            nonempty(record.get("evidence"), "assessment evidence")
            if stage == "full_jd":
                nonempty(record.get("description_source"), "description_source")
            if "score" in record:
                score = record["score"]
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 100:
                    raise ValueError("Score must be finite numeric 0..100")
            if decision == "qualified" and (stage != "full_jd" or record.get("eligibility_verified") is not True
                                              or record.get("score", 0) <= 60):
                raise ValueError("Qualified requires full_jd, explicit score >60 and eligibility_verified=true")
            identity_tokens(record)
            by_stage = assessments.setdefault(key, {})
            prior = by_stage.get(stage)
            if prior and prior["record"] != record:
                raise ValueError(f"Conflicting {stage} assessments for {key}; reconcile explicitly")
            entry = by_stage.setdefault(stage, {"record": record, "sources": []})
            entry["sources"].append({"source": source["path"], "record_index": n})

    for path in history_paths:
        data, source = load(path, "history_read_only")
        for record, pointer, fallback in historical_records(data):
            identity = record.get("identity", {})
            explicit = {k: record[k] for k in ("job_id", "linkedin_job_id") if k in record}
            for k in ("job_id", "linkedin_job_id"):
                if k in identity:
                    if k in explicit and explicit[k] != identity[k]:
                        raise ValueError("Conflicting historical nested job IDs")
                    explicit[k] = identity[k]
            key = job_id(explicit, fallback)
            if fallback and key != fallback:
                raise ValueError("Historical key and explicit LinkedIn ID conflict")
            ref = {"source": source["path"], "pointer": pointer, "job_id": key,
                   "status": record.get("status"), "run_id": record.get("run_id")}
            tokens = {("linkedin_job_id", key)}
            try:
                tokens.update(identity_tokens(record))
            except ValueError as exc:
                # Older evidence can contain malformed URLs. Never silently claim
                # a complete identity check when their comparison was unavailable.
                warnings.append({"source": source["path"], "pointer": pointer, "error": str(exc)})
            for token in tokens:
                index.setdefault(token, []).append(ref)

    jobs = []
    counts = {"card_observations": observations, "unique_observed_jobs": len(cards),
              "fully_assessed_jobs": 0, "screening_only_jobs": 0,
              "observed_without_assessment": 0, "assessed_without_card": 0,
              "explicit_decisions": {k: 0 for k in sorted(DECISIONS)},
              "jobs_with_historical_identity_matches": 0}
    for key in sorted(set(cards) | set(assessments)):
        card = cards.get(key, {"observations": [], "tokens": set()})
        stages = assessments.get(key, {})
        stage = "full_jd" if "full_jd" in stages else "screening" if "screening" in stages else None
        selected = stages.get(stage)
        record = selected["record"] if selected else None
        label = "fully_assessed" if stage == "full_jd" else "screening_only" if stage else "observed_without_assessment"
        counts[{"full_jd": "fully_assessed_jobs", "screening": "screening_only_jobs", None: "observed_without_assessment"}[stage]] += 1
        if record and not card["observations"]:
            counts["assessed_without_card"] += 1
        if record:
            counts["explicit_decisions"][record["decision"]] += 1
        tokens = card["tokens"] | {("linkedin_job_id", key)}
        for value in stages.values():
            tokens.update(identity_tokens(value["record"]))
        matches = [{"kind": kind, "value": value, "historical_records": index[(kind, value)]}
                   for kind, value in sorted(tokens) if (kind, value) in index]
        counts["jobs_with_historical_identity_matches"] += bool(matches)
        jobs.append({"job_id": key, "assessment_label": label,
                     "card_observations": card["observations"], "assessments": stages,
                     "explicit_decision": record["decision"] if record else None,
                     "historical_identity_matches": matches})
    return {"schema_version": 1, "counts": counts, "jobs": jobs,
            "input_provenance": provenance, "historical_identity_warnings": warnings,
            "historical_identity_check_complete": bool(history_paths) and not warnings,
            "limitations": ["Explicit agent decisions only; no NLP, inferred scores or company/title deduplication.",
                            "Identity matches are not application-status decisions or automatic exclusions.",
                            "No prepared count, browser verification, search exhaustion claim or external action."]}


def atomic_write(path, report):
    path = Path(path).resolve()
    if str(path) in {p["path"] for p in report["input_provenance"]}:
        raise ValueError("Output cannot overwrite any input, including a ledger")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cards", action="append", required=True, help="Repeat for each observed card batch")
    parser.add_argument("--assessments", action="append", default=[])
    parser.add_argument("--history", action="append", default=[], help="Read-only ledger/import/queue JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.cards, args.assessments, args.history)
        atomic_write(args.output, report)
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, f"screening_report: {exc}\n")
    print(json.dumps(report["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
