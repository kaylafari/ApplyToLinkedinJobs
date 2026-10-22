#!/usr/bin/env python3
"""Audit a user-scoped live test from parent receipts; never inspects the browser."""
import argparse
import json
from pathlib import Path
from handoff_receipt import build
from screening_report import normalize_url


def validate_questions(receipt):
    """Validate explicit parent classification; cannot independently know candidate facts."""
    for key in ('supported_fields_complete', 'answers_evidence_checked'):
        if receipt.get(key) is not True:
            raise ValueError(key + ' must be explicitly true')
    if receipt.get('unsupported_answers_present') is not False:
        raise ValueError('Unsupported or invented answers must be explicitly absent')
    for key in ('validation_errors', 'inaccessible_steps', 'technical_unresolved'):
        if receipt.get(key) != []:
            raise ValueError(key + ' must be explicitly empty for this test')
    required = receipt.get('required_unresolved')
    labels = receipt.get('unanswered_question_labels', [])
    records = receipt.get('unknown_personal_questions', [])
    for values in (required, labels):
        if (not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values)
                or len(values) != len(set(values))):
            raise ValueError('Question labels must be distinct nonempty strings')
    if not isinstance(records, list):
        raise ValueError('unknown_personal_questions must be a list')
    recorded = []
    for question in records:
        if not isinstance(question, dict):
            raise ValueError('Unknown question classification must be an object')
        label = question.get('label')
        if not isinstance(label, str) or not label.strip():
            raise ValueError('Unknown personal question needs a label')
        if (question.get('kind') != 'unknown_personal' or question.get('left_blank') is not True
                or question.get('supported_answer_available') is not False
                or question.get('source_reviewed') is not True):
            raise ValueError('Only genuinely unknown personal questions left blank may remain')
        for key in ('reason', 'evidence_reference'):
            if not isinstance(question.get(key), str) or not question[key].strip():
                raise ValueError('Unknown personal question needs ' + key)
        recorded.append(label)
    if len(recorded) != len(set(recorded)) or set(recorded) != set(labels) or not set(required) <= set(labels):
        raise ValueError('Unresolved labels must match classified unknown personal questions')
    if labels:
        note = receipt.get('review_note')
        if not isinstance(note, str) or not note.strip() or any(label not in note for label in labels):
            raise ValueError('Review note must explicitly label every missing personal question')
        if receipt.get('review_note_preserved') is not True:
            raise ValueError('Review note must be preserved for the user')
    return labels


def audit(items, cycle_id, target, run_id=None):
    if not isinstance(target, int) or isinstance(target, bool) or target < 1:
        raise ValueError('Target must be a positive integer')
    if not isinstance(cycle_id, str) or not cycle_id.strip():
        raise ValueError('A live test cycle ID is required')
    if run_id is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError('Expected ledger run ID must be a nonempty string')
        run_id = run_id.strip()
    accepted, rejected = [], []
    accepted_run = None
    seen_jobs, seen_tabs, seen_urls, seen_ats, seen_pdfs = set(), set(), set(), set(), set()
    for receipt, manifest in items:
        try:
            payload = build(receipt, manifest)
            receipt_run = payload['run_id']
            if run_id is not None and receipt_run != run_id:
                raise ValueError('Receipt belongs to another ledger run')
            if accepted_run is not None and receipt_run != accepted_run:
                raise ValueError('Mixed ledger runs are not allowed in one test')
            if receipt.get('test_cycle_id') != cycle_id:
                raise ValueError('Receipt belongs to another test cycle')
            if receipt.get('final_live_verification') is not True:
                raise ValueError('Fresh final live verification missing')
            # This user requires the complete application, not inaccessible later pages.
            if receipt.get('final_control_visible') is not True:
                raise ValueError('Final review/submit control not reached')
            question_labels = validate_questions(receipt)
            job, tab = receipt['job_id'], receipt['application_tab']['id'].strip()
            url = payload['properties']['Company Website']
            canonical = normalize_url(url)
            ats = (receipt.get('ats_identity') or '').strip()
            pdf = str(Path(payload['resume_path']).resolve())
            if job in seen_jobs or tab in seen_tabs or canonical in seen_urls or (ats and ats in seen_ats) or pdf in seen_pdfs:
                raise ValueError('Duplicate job, ATS identity, mutable application tab, URL, or PDF package')
            seen_jobs.add(job)
            seen_tabs.add(tab)
            seen_urls.add(canonical)
            seen_pdfs.add(pdf)
            if ats:
                seen_ats.add(ats)
            accepted_run = receipt_run
            accepted.append({'job_id': job, 'review_url': url, 'tab_id': tab,
                             'resume_path': payload['resume_path'],
                             'attachment_verified': payload['attachment_verified'],
                             'unknown_personal_question_labels': question_labels,
                             'notion_saved_verified': receipt.get('notion_saved_verified') is True})
        except (ValueError, OSError, TypeError, KeyError) as error:
            rejected.append({'job_id': receipt.get('job_id') if isinstance(receipt, dict) else None,
                             'reason': str(error)})
    return {'cycle_id': cycle_id, 'expected_run_id': run_id, 'accepted_run_id': accepted_run,
            'target': target, 'successful_applications': len(accepted),
            'success': len(accepted) == target and not rejected,
            'attachments_verified': sum(x['attachment_verified'] for x in accepted),
            'notion_logged': sum(x['notion_saved_verified'] for x in accepted),
            'accepted': accepted, 'rejected': rejected,
            'limitation': 'Checks supplied parent assertions and PDF integrity, not browser state or truth of personal-answer classification. Clearly labeled unknown personal questions may pass this test, not fully-ready status. Manual upload remains the separate user-defined exception, never strict prepared status.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cycle-id', required=True)
    parser.add_argument('--run-id', help='Expected ledger run ID; reject other runs')
    parser.add_argument('--target', type=int, default=2)
    parser.add_argument('--receipt', action='append', required=True,
                        help='Parent JSON receipt with resume_manifest absolute path')
    args = parser.parse_args()
    try:
        items = []
        for path in args.receipt:
            receipt = json.loads(Path(path).read_text())
            manifest = json.loads(Path(receipt['resume_manifest']).read_text())
            items.append((receipt, manifest))
        result = audit(items, args.cycle_id, args.target, run_id=args.run_id)
        print(json.dumps(result, indent=2))
        return 0 if result['success'] else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
