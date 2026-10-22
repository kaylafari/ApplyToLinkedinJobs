#!/usr/bin/env python3
"""Atomic local action checkpoints. Never drives a browser or retries an action."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import tempfile

OPERATIONS = {'navigate', 'fill', 'upload', 'advance', 'notion_write', 'inspect'}
OUTCOMES = {'succeeded', 'failed', 'uncertain', 'denied', 'not_applicable'}


def required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' must be nonempty text')
    return value


def apply(state, command, payload):
    """Pure transition for testing; journal records observations, not their truth."""
    if command == 'intent':
        for key in ('action_id', 'job_id', 'target', 'evidence_ref'):
            required_text(payload.get(key), key)
        if payload.get('operation') not in OPERATIONS:
            raise ValueError('Unsupported operation; submission is never permitted')
        action_id = payload['action_id']
        # Store paths to private field plans, never copy answers or owner capabilities.
        allowed = {'action_id', 'job_id', 'target', 'evidence_ref', 'operation', 'recovery_of', 'changed_evidence_ref'}
        if set(payload) - allowed:
            raise ValueError('Unsupported intent fields; use evidence_ref for private details')
        existing = state['actions'].get(action_id)
        if existing:
            if existing['intent'] != payload:
                raise ValueError('Conflicting action ID; reconcile before reuse')
            return {'replayed': True, 'action': existing}
        unresolved = [k for k, v in state['actions'].items()
                      if v['status'] in ('in_flight', 'uncertain')
                      and (v['intent']['job_id'] == payload['job_id'] or v['intent']['target'].strip() == payload['target'].strip())]
        if unresolved and payload['operation'] != 'inspect':
            raise ValueError('Reconcile this job\'s uncertain/in-flight actions before mutation: ' + ', '.join(unresolved))
        recovery_of = payload.get('recovery_of')
        previous_failures = [k for k, v in state['actions'].items()
                             if v['status'] in ('failed', 'denied')
                             and v['intent']['job_id'] == payload['job_id']
                             and v['intent']['operation'] == payload['operation']]
        if previous_failures and not recovery_of:
            raise ValueError('A repeated failed operation needs recovery_of and changed evidence')
        if recovery_of:
            old = state['actions'].get(recovery_of)
            if not old or old['status'] not in ('failed', 'denied', 'not_applicable'):
                raise ValueError('Recovery must reference a resolved failed action')
            if old['intent']['job_id'] != payload['job_id'] or old['intent']['operation'] != payload['operation']:
                raise ValueError('Recovery must match the same job and operation')
            required_text(payload.get('changed_evidence_ref'), 'changed_evidence_ref')
        action = {'intent': payload, 'status': 'in_flight', 'observations': []}
        state['actions'][action_id] = action
        return {'replayed': False, 'action': action}
    if command == 'result':
        action = state['actions'].get(payload.get('action_id'))
        if not action:
            raise ValueError('No matching intent')
        outcome = payload.get('outcome')
        if outcome not in OUTCOMES:
            raise ValueError('Invalid outcome')
        for key in ('evidence_ref', 'source', 'detail'):
            required_text(payload.get(key), key)
        allowed = {'action_id', 'outcome', 'evidence_ref', 'source', 'detail'}
        if set(payload) - allowed:
            raise ValueError('Unsupported result fields')
        if payload in action['observations']:
            return {'replayed': True, 'action': action}
        if action['status'] not in ('in_flight', 'uncertain'):
            raise ValueError('Final result is immutable; use a new action ID')
        action['observations'].append(payload)
        action['status'] = outcome
        return {'replayed': False, 'action': action}
    if command == 'status':
        return {'run_id': state['run_id'], 'action_count': len(state['actions']),
                'unresolved': [k for k, v in state['actions'].items() if v['status'] in ('in_flight', 'uncertain')],
                'actions': state['actions'],
                'limit': 'Recorded evidence only; does not establish current browser state'}
    raise ValueError('Unknown command')


def transact(path, run_id, command, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {'version': 1, 'run_id': run_id, 'actions': {}}
        if state.get('version') != 1 or state.get('run_id') != run_id:
            raise ValueError('Journal identity mismatch')
        result = apply(state, command, payload)
        if command != 'status' and not result.get('replayed'):
            state['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
            fd, temp = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(state, stream, indent=2, sort_keys=True)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('command', choices=['intent', 'result', 'status'])
    parser.add_argument('--payload', help='JSON file; stdin when omitted for intent/result')
    args = parser.parse_args()
    import sys
    try:
        payload = {} if args.command == 'status' else json.loads(Path(args.payload).read_text() if args.payload else sys.stdin.read())
        if not isinstance(payload, dict):
            raise ValueError('Payload must be an object')
        print(json.dumps(transact(args.state, args.run_id, args.command, payload), indent=2))
    except (ValueError, OSError, TypeError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
