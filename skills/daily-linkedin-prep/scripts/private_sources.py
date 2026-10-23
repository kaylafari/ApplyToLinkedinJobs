#!/usr/bin/env python3
"""Provision private text inputs from environment variables; never print their values."""
import argparse
import base64
import binascii
import fcntl
import gzip
import json
import os
from pathlib import Path
import tempfile

SOURCES = {
    'LINKEDIN_CANDIDATE_CONTEXT': 'candidate-context.md',
    'LINKEDIN_MASTER_RESUME': 'master-resume.txt',
}


def configuration(env, repo_root):
    repo_root = Path(repo_root).resolve()
    default = Path.home() / '.local' / 'share' / 'linkedin-prep'
    private = Path(env.get('LINKEDIN_PRIVATE_DIR') or default).expanduser()
    state = Path(env.get('LINKEDIN_STATE_DIR') or private / 'state').expanduser()
    for directory in (private, state):
        if not directory.is_absolute():
            raise ValueError('Private and state directories must be absolute paths.')
        if directory.resolve().is_relative_to(repo_root):
            raise ValueError('Private and state directories must be outside the Git checkout.')
    return private, state


def existing_text(path):
    if path.is_symlink():
        raise ValueError('Private input files must not be symbolic links.')
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('Private input paths must be regular files.')
    try:
        value = path.read_bytes().decode('utf-8')
    except (UnicodeError, OSError):
        raise ValueError('Private input files must be readable UTF-8 text.') from None
    validate_text(value)
    return value


def validate_text(value):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('Private inputs must contain nonempty text without NUL characters.')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise ValueError('Private inputs must be valid UTF-8 text.') from None


def decode_input(value):
    if not isinstance(value, str):
        raise ValueError('Private inputs must contain UTF-8 text.')
    if value.startswith('gzip-base64:'):
        try:
            value = gzip.decompress(base64.b64decode(value[12:], validate=True)).decode('utf-8')
        except (binascii.Error, UnicodeError, ValueError, OSError, EOFError):
            raise ValueError('Compressed private input must be valid gzip-base64 of UTF-8 text.') from None
    elif value.startswith('base64:'):
        try:
            value = base64.b64decode(value[7:], validate=True).decode('utf-8')
        except (binascii.Error, UnicodeError, ValueError):
            raise ValueError('Encoded private input must be valid base64 of UTF-8 text.') from None
    validate_text(value)
    return value


def validate_history(value):
    def reject_constant(_):
        raise ValueError('Application history must use finite JSON values.')

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Application history cannot contain duplicate JSON keys.')
            result[key] = item
        return result

    try:
        history = json.loads(value, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (ValueError, TypeError):
        raise ValueError('Application history must be a JSON ledger with a jobs object; no values were logged.') from None
    if (not isinstance(history, dict) or type(history.get('version', 1)) is not int or history.get('version', 1) != 1
            or not isinstance(history.get('jobs'), dict)
            or any(not key.strip() or not isinstance(job, dict) for key, job in history['jobs'].items())):
        raise ValueError('Application history must be a supported JSON ledger with a jobs object.')


def history_source(env, repo_root, state):
    """An optional private snapshot or an existing path visible to this machine."""
    explicit_path = env.get('LINKEDIN_APPLICATION_HISTORY_PATH')
    if explicit_path is not None:
        if not explicit_path.strip():
            raise ValueError('Configured application-history path must not be empty.')
        path = Path(explicit_path).expanduser()
    else:
        # A restored live ledger takes precedence over a cached environment snapshot.
        ledger = state / 'ledger.json'
        path = ledger if ledger.exists() or ledger.is_symlink() else state / 'application-history.json'
    if not path.is_absolute() or path.resolve().is_relative_to(Path(repo_root).resolve()):
        raise ValueError('Application-history path must be absolute, outside the checkout and accessible in this environment.')
    saved = existing_text(path)
    if explicit_path is not None and saved is None:
        raise ValueError('Application-history path is not present in this environment; a laptop path is not automatically cloud-accessible.')
    supplied = env.get('LINKEDIN_APPLICATION_HISTORY')
    if supplied is not None:
        supplied = decode_input(supplied)
        validate_history(supplied)
        if saved is not None and saved != supplied:
            raise ValueError('Saved application history differs from supplied history. Preserve and reconcile it; no history was overwritten.')
    value = saved if saved is not None else supplied
    if value is not None:
        validate_history(value)
    return path, value


def prepare(env, repo_root, check_only=False):
    private, state = configuration(env, repo_root)
    # Check all values before creating any files. Keep existing learned answers intact.
    desired = {}
    for key, name in SOURCES.items():
        path = private / name
        saved = existing_text(path)
        supplied = env.get(key)
        if supplied is not None:
            supplied = decode_input(supplied)
            if saved is not None and saved != supplied:
                raise ValueError('Saved private sources differ from supplied inputs. Preserve and reconcile them; no files were overwritten.')
        value = saved if saved is not None else supplied
        if value is None:
            raise ValueError('Missing private sources. Set LINKEDIN_CANDIDATE_CONTEXT and LINKEDIN_MASTER_RESUME, or provision both files privately.')
        if check_only and saved is None:
            raise ValueError('Private files are not prepared. Run the prepare command first.')
        desired[path] = value
    history_path, history = history_source(env, repo_root, state)
    if history is not None:
        if check_only and not history_path.is_file():
            raise ValueError('Application history is not prepared. Run the prepare command first.')
        desired[history_path] = history
    if not check_only:
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(private, 0o700)
        lock_fd = os.open(private / '.sources.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Recheck after locking; another setup must not overwrite a newer context.
            for path, value in desired.items():
                saved = existing_text(path)
                if saved is not None and saved != value:
                    raise ValueError('Private sources changed during setup; no existing files were overwritten.')
            for path, value in desired.items():
                if path.exists():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd, tmp = tempfile.mkstemp(prefix='.source-', dir=path.parent)
                try:
                    with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                        stream.write(value)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Link refuses to replace an existing target, even if a writer races us.
                    os.link(tmp, path)
                finally:
                    Path(tmp).unlink(missing_ok=True)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        'candidate_context_path': str(private / SOURCES['LINKEDIN_CANDIDATE_CONTEXT']),
        'master_resume_path': str(private / SOURCES['LINKEDIN_MASTER_RESUME']),
        'state_dir': str(state),
        'application_history_configured': history is not None,
        'application_history_path': str(history_path) if history is not None else None,
        'notion_tracker_configured': bool(env.get('LINKEDIN_NOTION_TRACKER_URL', '').strip()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'check'])
    args = parser.parse_args(argv)
    # Resolve from this file so installed/symlinked entrypoints cannot change the root.
    repo_root = Path(__file__).resolve().parents[3]
    try:
        result = prepare(os.environ, repo_root, check_only=args.command == 'check')
    except ValueError as error:
        parser.exit(2, str(error) + '\n')
    except OSError:
        parser.exit(2, 'Private source filesystem operation failed; no input values were logged.\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
