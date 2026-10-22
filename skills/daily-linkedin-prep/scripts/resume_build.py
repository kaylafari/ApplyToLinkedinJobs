#!/usr/bin/env python3
"""Offline build only. TRUSTED agent-authored TeX, never downloaded/untrusted TeX.

This is not a TeX sandbox or live form verifier. Caller must independently pass
ledger check-next, inspect every page and claims, and approve a separate QA
manifest. Existing output directories are never reused, including failed builds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

PDF = "Kayla_Farivar_Resume.pdf"
REQUIRED = ("identity_verified", "form_open_verified",
            "supported_answers_persisted", "tab_preserved")


def validate_evidence(evidence, job_id):
    if not isinstance(evidence, dict) or evidence.get("job_id") != job_id:
        raise ValueError("Form evidence job_id must exactly match --job-id")
    for key in REQUIRED:
        if evidence.get(key) is not True:
            raise ValueError(f"Form evidence requires Boolean true: {key}")
    tab = evidence.get("application_tab")
    if not isinstance(tab, dict) or tab.get("kind") != "application":
        raise ValueError("A distinct application_tab of kind application is required")
    if not isinstance(tab.get("id"), str) or not tab["id"].strip():
        raise ValueError("Application tab id is required")
    url = urlparse(tab.get("url", ""))
    if url.scheme != "https" or not url.netloc:
        raise ValueError("Application tab requires an absolute HTTPS URL")


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def build(source, output_dir, job_id, form_evidence, runner=subprocess.run):
    source = Path(source).resolve(strict=True)
    evidence_path = Path(form_evidence).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".tex":
        raise ValueError("Source must be a trusted agent-created .tex file")
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(evidence_bytes)
    validate_evidence(evidence, job_id)
    output = Path(output_dir).absolute()
    # Atomic reservation; do not follow or reuse existing directories/symlinks.
    output.mkdir(parents=False, exist_ok=False)
    receipt = {"job_id": job_id, "status": "building", "qa_approved": False,
               "artifact_path": str(output / PDF),
               "source_path": str(source),
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "form_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
               "application_tab": evidence["application_tab"], "commands": [],
               "required_checks": ["Parent ledger check-next gate", "Candidate factual evidence review",
                                   "Extracted text reading order and completeness", "Visual inspection of every page",
                                   "Intended page count and layout density", "Keyword/gap and report review",
                                   "Separate QA manifest approval", "Live attachment and form verification"],
               "limitations": "Recorded form assertions only; no live browser verification. Trusted TeX only; not a sandbox."}
    receipt_path = output / "build-receipt.json"
    write_json(receipt_path, receipt)
    try:
        shutil.copyfile(source, output / "resume.tex")
        (output / "form-evidence.json").write_bytes(evidence_bytes)
        env = os.environ.copy()
        # Defense in depth only. Trusted TeX may still read local files.
        env.update({"openout_any": "p", "shell_escape": "f"})
        commands = [
            ["latexmk", "-norc", "-pdf", "-no-shell-escape", "-halt-on-error",
             "-interaction=nonstopmode", "-jobname=Kayla_Farivar_Resume", "resume.tex"],
            ["pdftotext", "-layout", PDF, "resume-text.txt"],
            ["pdfinfo", PDF],
            ["pdftoppm", "-r", "120", "-png", PDF, "resume-page"],
        ]
        for number, args in enumerate(commands, 1):
            executable = shutil.which(args[0])
            if not executable:
                raise RuntimeError(f"Required tool unavailable: {args[0]}")
            args = [executable, *args[1:]]
            log = output / f"command-{number}-{Path(executable).name}.log"
            record = {"argv": args, "log": str(log), "returncode": None}
            receipt["commands"].append(record)
            write_json(receipt_path, receipt)
            with log.open("wb") as stream:
                result = runner(args, cwd=str(output), env=env, stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=subprocess.STDOUT, shell=False, timeout=180)
            record["returncode"] = result.returncode
            write_json(receipt_path, receipt)
            if result.returncode != 0:
                raise RuntimeError(f"{Path(executable).name} failed ({result.returncode}); see {log}")
        pdf = output / PDF
        if not pdf.is_file() or pdf.is_symlink() or not pdf.read_bytes().startswith(b"%PDF-"):
            raise RuntimeError("Missing or invalid compiled PDF")
        text = output / "resume-text.txt"
        if not text.is_file() or not text.read_text(errors="replace").strip():
            raise RuntimeError("Text extraction is empty or missing")
        info = (output / "command-3-pdfinfo.log").read_text(errors="replace")
        match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not match or int(match[1]) < 1:
            raise RuntimeError("pdfinfo did not return a positive page count")
        page_count = int(match[1])
        pages = sorted(output.glob("resume-page-*.png"))
        numbers = {int(p.stem.removeprefix("resume-page-")) for p in pages}
        if numbers != set(range(1, page_count + 1)) or len(pages) != page_count:
            raise RuntimeError("Rendered page count does not match PDF")
        if any(p.is_symlink() or not p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for p in pages):
            raise RuntimeError("Invalid rendered page image")
        receipt.update(status="built_pending_review", page_count=page_count,
                       sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(), bytes=pdf.stat().st_size,
                       rendered_pages=[str(p) for p in pages])
    except Exception as exc:
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(receipt_path, receipt)
        raise
    write_json(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("source", "output-dir", "job-id", "form-evidence"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    try:
        result = build(args.source, args.output_dir, args.job_id, args.form_evidence)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
