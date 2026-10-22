#!/usr/bin/env python3
"""Offline handoff payload builder. Supplied assertions are not live browser checks.

Accepts a parent-issued JSON receipt and QA manifest; never contacts Notion or
modifies the ledger. Free-form candidate answers are deliberately not copied.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

REPORTS = ('keyword-report.md', 'alignment-report.md', 'evidence-ledger.md',
           'resume-diff.md', 'qa-report.md', 'resume.tex', 'feedback.json')
FLAGS = ('identity_verified', 'form_open_verified', 'supported_answers_persisted',
         'tab_preserved', 'submission_protection_verified', 'accessible_fields_complete')
PROTECTED = {'application submitted', 'submitted', 'interview', 'rejected'}


def string(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing string: ' + key)
    return value.strip()


def stable_url(value):
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Expected credential-free HTTPS application URL')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('trackingid', 'refid', 'ebp')]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def build(receipt, manifest):
    """Return deterministic proposed content; never assert external verification."""
    if not isinstance(receipt, dict) or not isinstance(manifest, dict):
        raise ValueError('Receipt and manifest must be objects')
    if receipt.get('issuer') != 'parent':
        raise ValueError('Requires parent-issued receipt')
    kind = receipt.get('handoff_kind')
    if kind not in ('done', 'manual_upload'):
        raise ValueError('Unsupported handoff kind')
    for key in FLAGS:
        if receipt.get(key) is not True:
            raise ValueError('Missing true evidence assertion: ' + key)
    if receipt.get('submission_performed') is not False:
        raise ValueError('Must explicitly assert no submission')
    labels = receipt.get('existing_statuses', [])
    if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
        raise ValueError('existing_statuses must be a string list')
    if any(x.strip().lower() not in {'', 'prepared for review', 'preparation blocked'} for x in labels):
        raise ValueError('Existing outcome status protected; parent reconciliation required')
    job = string(receipt, 'job_id')
    if not job.isdigit():
        raise ValueError('Expected numeric LinkedIn job ID')
    run = string(receipt, 'run_id')
    company = string(receipt, 'company')
    title = string(receipt, 'title')
    date = string(receipt, 'preparation_date')
    dt.date.fromisoformat(date)
    url = stable_url(string(receipt, 'review_url'))
    tab = receipt.get('application_tab')
    if not isinstance(tab, dict) or tab.get('kind') != 'application':
        raise ValueError('Missing distinct application tab')
    tab_id = string(tab, 'id')
    if stable_url(string(tab, 'url')) != url:
        raise ValueError('Review URL and application tab disagree')
    string(receipt, 'form_evidence_reference')
    if manifest.get('job_id') != job or manifest.get('status') != 'qa_passed':
        raise ValueError('Manifest job or QA status mismatch')
    qa = manifest.get('qa')
    if not isinstance(qa, dict) or any(qa.get(k) is not True for k in ('evidence', 'ats_text', 'layout', 'page_count')):
        raise ValueError('QA gates missing or failed')
    pdf = Path(string(manifest, 'artifact_path'))
    if not pdf.is_absolute() or pdf.name != 'Kayla_Farivar_Resume.pdf':
        raise ValueError('Expected absolute job-specific resume PDF path')
    data = pdf.read_bytes()
    if not data.startswith(b'%PDF-'):
        raise ValueError('Invalid PDF signature')
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest.get('sha256') or digest != receipt.get('resume_sha256'):
        raise ValueError('PDF hash mismatch')
    if string(receipt, 'resume_path') != str(pdf):
        raise ValueError('Receipt PDF differs from manifest')
    for name in REPORTS:
        path = pdf.parent / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('Missing QA supporting artifact: ' + name)
    attachment = receipt.get('attachment_verified')
    questions = receipt.get('unanswered_question_labels', [])
    if not isinstance(questions, list) or any(not isinstance(q, str) or not q.strip() for q in questions):
        raise ValueError('Questions must be labels, not answer objects')
    # Older receipts treated all labels as required. Preserve that conservative
    # fallback only when the explicit required subset is absent.
    required = receipt.get('required_unresolved', questions)
    if (not isinstance(required, list) or any(not isinstance(q, str) or not q.strip() for q in required)
            or len(required) != len(set(required)) or len(questions) != len(set(questions))
            or not set(required) <= set(questions)):
        raise ValueError('Required unanswered labels must be a distinct subset of question labels')
    other = [q for q in questions if q not in required]
    # Even apparent labels can contain private answers. Only copy labels after
    # the parent confirms they are sanitized; private answer dictionaries are never copied.
    safe_labels = receipt.get('question_labels_safe_for_tracking') is True
    def question_line(heading, labels):
        detail = '; '.join(labels) if safe_labels else 'review source receipt locally'
        return heading + ': ' + str(len(labels)) + (' (' + detail + ')' if labels else '')
    if kind == 'done':
        if attachment is not True or receipt.get('preparation_complete') is not True:
            raise ValueError('Done requires verified attachment and complete preparation')
        if receipt.get('ledger_status') not in ('prepared_for_review', 'ready_for_review'):
            raise ValueError('Done requires prepared ledger status')
        if receipt.get('validation_errors') != []:
            raise ValueError('Done has missing or nonempty validation result')
        if receipt['ledger_status'] == 'ready_for_review' and (required or receipt.get('final_control_visible') is not True):
            raise ValueError('Fully ready requires final control and no required questions')
        status, heading = 'Prepared for Review', 'Prepared for review — NOT submitted'
    else:
        if attachment is not False or receipt.get('ledger_status') != 'blocked':
            raise ValueError('Manual upload requires missing attachment and blocked status')
        string(receipt, 'missing_upload_reason')
        if receipt.get('upload_recovery_exhausted') is not True:
            raise ValueError('Manual upload requires recovery outcome evidence')
        string(receipt, 'upload_evidence_reference')
        status, heading = 'Preparation Blocked', 'Needs résumé upload — fields filled to best supported extent'
    ats = receipt.get('ats_identity')
    if ats is not None and (not isinstance(ats, str) or not ats.strip()):
        raise ValueError('Invalid ATS identity')
    body = '\n'.join([
        heading, 'Run: ' + run, 'Preparation date (America/Los_Angeles): ' + date,
        'LinkedIn job ID: ' + job, 'ATS identity: ' + (ats or 'Not provided'),
        'Review URL: ' + url, 'Preserved application tab: ' + tab_id,
        'Résumé path (local reference only): ' + str(pdf), 'SHA256: ' + digest,
        'Attachment verified: ' + ('yes' if attachment else 'no; manual upload required'),
        question_line('Required unanswered questions', required),
        question_line('Optional/other unanswered questions', other),
        'Upload errors and inaccessible steps: review parent receipt locally' if kind == 'manual_upload' else 'Technical incompleteness: none asserted in supplied receipt',
        'No application submitted. Application Date remains blank.',
    ])
    return {'version': 1, 'run_id': run, 'job_id': job, 'handoff_kind': kind,
            'properties': {'Company': company, 'Job Title': title, 'Company Website': url,
                           'Application Status': status, 'Application Date': None},
            'body': body, 'resume_path': str(pdf), 'resume_sha256': digest,
            'attachment_verified': attachment, 'external_write_performed': False,
            'notion_saved_verified': False,
            'evidence_limit': 'Validates supplied parent assertions and stored PDF integrity; not live form or visual QA verification'}


def write_once(path, payload):
    """Locked atomic first-write, identical replay no-op, conflicting replay refused."""
    path = Path(path)
    encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    with open(str(path) + '.lock', 'a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if path.exists():
            if json.loads(path.read_text()) == payload:
                return 'unchanged'
            raise ValueError('Existing receipt conflict; refusing overwrite')
        fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(encoded)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return 'created'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt')
    parser.add_argument('manifest')
    parser.add_argument('output')
    args = parser.parse_args()
    try:
        payload = build(json.loads(Path(args.receipt).read_text()), json.loads(Path(args.manifest).read_text()))
        print(write_once(args.output, payload))
    except (ValueError, OSError, TypeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
