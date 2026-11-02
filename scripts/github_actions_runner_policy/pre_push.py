#!/usr/bin/env python3
"""Validate every outgoing Git pre-push ref against its committed workflow tree."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import NamedTuple

from check import OBJECT_ID, PolicyError, approved_reusables, audit_git_ref, resolve_object


MAX_STDIN_BYTES = 1024 * 1024
MAX_PUSH_UPDATES = 512
NAMED_REF = re.compile(r'^refs/(?:heads|tags)/.+$')


class PushUpdate(NamedTuple):
    local_ref: str
    local_object: str
    remote_ref: str
    remote_object: str


def parse_updates(content):
    if len(content.encode('utf-8')) > MAX_STDIN_BYTES:
        raise PolicyError('pre-push stdin exceeds bounded byte limit')
    lines = [line for line in content.splitlines() if line.strip()]
    if len(lines) > MAX_PUSH_UPDATES:
        raise PolicyError('pre-push stdin exceeds bounded update limit')
    updates = []
    for number, line in enumerate(lines, 1):
        fields = line.split()
        if len(fields) != 4:
            raise PolicyError(f'pre-push line {number} must contain exactly four fields')
        update = PushUpdate(*fields)
        if not OBJECT_ID.fullmatch(update.local_object) or not OBJECT_ID.fullmatch(update.remote_object):
            raise PolicyError(f'pre-push line {number} has an invalid object id')
        if len(update.local_object) != len(update.remote_object):
            raise PolicyError(f'pre-push line {number} mixes Git object formats')
        if not NAMED_REF.fullmatch(update.remote_ref):
            raise PolicyError(f'pre-push line {number} has an unsupported destination ref')
        if set(update.local_object) == {'0'}:
            continue
        if update.local_ref != 'HEAD' and not NAMED_REF.fullmatch(update.local_ref):
            raise PolicyError(f'pre-push line {number} has an unsupported source ref; use a branch, tag, or HEAD')
        updates.append(update)
    return updates


def audit_updates(root, content, reusables=None):
    updates = parse_updates(content)
    errors = []
    results = {}
    for update in updates:
        try:
            actual = resolve_object(root, update.local_ref)
            if actual != update.local_object.lower():
                raise PolicyError('source ref does not match the object Git is about to push')
            commit = resolve_object(root, update.local_object, 'commit')
            if commit not in results:
                results[commit] = audit_git_ref(root, commit, reusables)
            errors.extend(f'{update.local_ref} -> {update.remote_ref}: {error}' for error in results[commit].errors)
        except (PolicyError, OSError, UnicodeError, ValueError) as exc:
            errors.append(f'{update.local_ref} -> {update.remote_ref}: {exc}')
    return errors, len(updates), len(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    # Git passes these two arguments to hooks. The URL is intentionally neither
    # used nor printed; selection policy is identical for every destination.
    parser.add_argument('remote', nargs='?')
    parser.add_argument('url', nargs='?')
    args = parser.parse_args()
    try:
        content = sys.stdin.read(MAX_STDIN_BYTES + 1)
        errors, updates, commits = audit_updates(args.root, content, approved_reusables())
    except (PolicyError, OSError, UnicodeError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    for error in errors:
        print(f'ERROR: {error}', file=sys.stderr)
    if errors:
        print('Push rejected by Eternet runner policy; no working-tree fallback was used.', file=sys.stderr)
        return 1
    print(f'Pre-push runner policy passed: {updates} updates, {commits} committed trees; deletions skipped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
