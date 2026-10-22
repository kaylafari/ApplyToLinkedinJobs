#!/usr/bin/env python3
"""Verify stored resume integrity. QA flags must come from actual prior checks."""
import argparse
import hashlib
import json
from pathlib import Path

REPORTS = ('keyword-report.md', 'alignment-report.md', 'evidence-ledger.md',
           'resume-diff.md', 'qa-report.md', 'resume.tex', 'feedback.json')


def verify(manifest_path, job_id):
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get('job_id') != str(job_id) or manifest.get('status') != 'qa_passed':
        raise ValueError('Manifest is not QA-passed for this job')
    if any(manifest.get('qa', {}).get(key) is not True
           for key in ('evidence', 'ats_text', 'layout', 'page_count')):
        raise ValueError('Missing or failed QA gates')
    pdf = Path(manifest.get('artifact_path', ''))
    if not pdf.is_absolute() or pdf.name != 'Kayla_Farivar_Resume.pdf':
        raise ValueError('Expected absolute path ending in Kayla_Farivar_Resume.pdf')
    data = pdf.read_bytes()
    if not data.startswith(b'%PDF-'):
        raise ValueError('Artifact does not have a PDF signature')
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest.get('sha256'):
        raise ValueError('PDF bytes do not match the approved hash')
    for name in REPORTS:
        if not (pdf.parent / name).is_file() or not (pdf.parent / name).stat().st_size:
            raise ValueError('Missing supporting artifact: ' + name)
    return {'job_id': str(job_id), 'artifact_path': str(pdf), 'sha256': digest,
            'bytes': len(data), 'integrity_verified': True,
            'limit': 'Stored integrity only; does not reperform visual or factual QA'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('job_id')
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.manifest, args.job_id)))
    except (ValueError, OSError, TypeError) as error:
        raise SystemExit(str(error))
