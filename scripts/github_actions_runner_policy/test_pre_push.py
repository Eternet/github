import subprocess
from pathlib import Path
import shlex
import sys
import unittest
from unittest.mock import patch

from check import PolicyError
import pre_push
from pre_push import audit_updates, parse_updates
from test_git_refs import GitFixture, HOSTED, MAIN, SAFE


ZERO = '0' * 40


def update(local_ref, local_object, remote_ref='refs/heads/published', remote_object=ZERO):
    return f'{local_ref} {local_object} {remote_ref} {remote_object}\n'


class PushGuardTests(GitFixture):
    def unsafe_branch_with_safe_head(self):
        unsafe = self.commit_files({'.github/workflows/legacy.yml': HOSTED})
        self.git('branch', 'stale-source', unsafe)
        safe = self.commit_files({'.github/workflows/legacy.yml': SAFE})
        return unsafe, safe

    def test_all_pushed_branches_are_checked_even_when_checked_out_head_is_safe(self):
        unsafe, safe = self.unsafe_branch_with_safe_head()
        safe_row = update('refs/heads/main', safe, 'refs/heads/main')
        unsafe_row = update('refs/heads/stale-source', unsafe, 'refs/heads/other')

        for content in (safe_row + unsafe_row, unsafe_row + safe_row):
            with self.subTest(content=content):
                errors, updates, commits = audit_updates(self.root, content)
                self.assertEqual((2, 2), (updates, commits))
                self.assertTrue(any('stale-source' in error and 'GitHub-hosted' in error for error in errors))

    def test_safe_editor_change_cannot_hide_committed_head_being_pushed(self):
        unsafe = self.commit_files({MAIN: HOSTED})
        self.write(MAIN, SAFE)

        errors, updates, commits = audit_updates(self.root, update('HEAD', unsafe))

        self.assertEqual((1, 1), (updates, commits))
        self.assertTrue(any('GitHub-hosted' in error for error in errors))

    def test_lightweight_and_annotated_commit_tags_are_audited(self):
        self.git('tag', 'lightweight', self.safe_commit)
        self.git('tag', '-a', 'annotated', '-m', 'Synthetic tag', self.safe_commit)
        annotated_object = self.git('rev-parse', 'refs/tags/annotated')
        self.assertNotEqual(self.safe_commit, annotated_object)
        content = update('refs/tags/lightweight', self.safe_commit, 'refs/tags/lightweight')
        content += update('refs/tags/annotated', annotated_object, 'refs/tags/annotated')

        errors, updates, commits = audit_updates(self.root, content)

        self.assertEqual([], errors)
        self.assertEqual((2, 1), (updates, commits))

    def test_tag_with_hosted_tree_is_rejected_even_when_current_branch_is_safe(self):
        unsafe, _ = self.unsafe_branch_with_safe_head()
        self.git('tag', '-a', 'stale-tag', '-m', 'Synthetic unsafe tag', unsafe)
        tag_object = self.git('rev-parse', 'refs/tags/stale-tag')

        errors, _, _ = audit_updates(self.root, update('refs/tags/stale-tag', tag_object, 'refs/tags/stale-tag'))

        self.assertTrue(any('stale-tag' in error and 'GitHub-hosted' in error for error in errors))

    def test_noncommit_tag_is_rejected_without_working_tree_fallback(self):
        blob = self.git('hash-object', '-w', '--stdin', input='Synthetic noncommit tag\n')
        self.git('tag', 'blob-tag', blob)

        errors, updates, commits = audit_updates(self.root, update('refs/tags/blob-tag', blob, 'refs/tags/blob-tag'))

        self.assertEqual((1, 0), (updates, commits))
        self.assertTrue(any('rev-parse failed' in error for error in errors))

    def test_deletions_are_skipped_and_unchanged_push_has_no_updates(self):
        deleted = update('(delete)', ZERO, 'refs/heads/deleted', self.safe_commit)

        self.assertEqual(([], 0, 0), audit_updates(self.root, deleted))
        self.assertEqual(([], 0, 0), audit_updates(self.root, ''))

    def test_valid_code_repository_without_workflows_can_be_pushed(self):
        commit = self.commit_files({MAIN: None, 'README.md': 'Synthetic repository without Actions workflows.\n'})

        self.assertEqual(([], 1, 1), audit_updates(self.root, update('refs/heads/main', commit)))

    def test_announced_object_must_match_the_actual_local_source_ref(self):
        current = self.commit_files({MAIN: HOSTED})
        self.assertNotEqual(self.safe_commit, current)

        errors, updates, commits = audit_updates(self.root, update('refs/heads/main', self.safe_commit))

        self.assertEqual((1, 0), (updates, commits))
        self.assertTrue(any('does not match the object' in error for error in errors))

    def test_missing_local_ref_fails_closed(self):
        errors, _, commits = audit_updates(self.root, update('refs/heads/not-fetched', self.safe_commit))

        self.assertEqual(0, commits)
        self.assertTrue(any('rev-parse failed' in error for error in errors))

    def test_unknown_push_sources_and_destinations_are_rejected(self):
        for source in ('(unknown)', self.safe_commit, 'HEAD~1', 'refs/notes/example'):
            with self.subTest(source=source):
                with self.assertRaisesRegex(PolicyError, 'unsupported source ref'):
                    parse_updates(update(source, self.safe_commit))
        with self.assertRaisesRegex(PolicyError, 'unsupported destination ref'):
            parse_updates(update('HEAD', self.safe_commit, 'refs/notes/example'))

    def test_malformed_or_mixed_format_hook_records_are_rejected(self):
        for content in ('HEAD not-a-hash refs/heads/main ' + ZERO,
                        'HEAD ' + self.safe_commit + ' refs/heads/main',
                        update('HEAD', self.safe_commit).strip() + ' extra',
                        update('HEAD', self.safe_commit, remote_object='0' * 64)):
            with self.subTest(content=content):
                with self.assertRaises(PolicyError):
                    parse_updates(content)

    def test_hook_input_limits_reject_excess_instead_of_truncating_refs(self):
        row = update('refs/heads/main', self.safe_commit)
        size = len(row.encode())
        with patch.object(pre_push, 'MAX_STDIN_BYTES', size):
            self.assertEqual(1, len(parse_updates(row)))
        with patch.object(pre_push, 'MAX_STDIN_BYTES', size - 1):
            with self.assertRaisesRegex(PolicyError, 'bounded byte limit'):
                parse_updates(row)
        with patch.object(pre_push, 'MAX_PUSH_UPDATES', 1):
            with self.assertRaisesRegex(PolicyError, 'bounded update limit'):
                parse_updates(row + row)

    def test_cli_reads_every_ref_from_hook_stdin_and_accepts_git_remote_arguments(self):
        unsafe, safe = self.unsafe_branch_with_safe_head()
        content = update('HEAD', safe) + update('refs/heads/stale-source', unsafe, 'refs/heads/stale')

        result = subprocess.run([sys.executable, pre_push.__file__, '--root', str(self.root),
                                 'origin', 'https://example.invalid/repository'], input=content,
                                capture_output=True, text=True)

        self.assertEqual(1, result.returncode)
        self.assertIn('stale-source', result.stderr)
        self.assertIn('Push rejected', result.stderr)
        self.assertNotIn('example.invalid', result.stderr)

    def test_cli_safe_source_reports_exact_update_and_commit_counts(self):
        self.git('tag', 'safe-tag', self.safe_commit)
        content = update('refs/heads/main', self.safe_commit)
        content += update('refs/tags/safe-tag', self.safe_commit, 'refs/tags/safe-tag')

        result = subprocess.run([sys.executable, pre_push.__file__, '--root', str(self.root)],
                                input=content, capture_output=True, text=True)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('2 updates, 1 committed trees', result.stdout)

    def test_real_local_push_rejects_entire_mixed_batch_then_accepts_safe_branch(self):
        unsafe = self.commit_files({MAIN: HOSTED})
        self.git('branch', 'safe-publish', self.safe_commit)
        self.write(MAIN, SAFE)
        remote = self.root / 'local-remote.git'
        self.git('init', '--quiet', '--bare', str(remote))
        self.git('remote', 'add', 'synthetic', str(remote))
        hooks = self.root / 'owned-hooks'
        hooks.mkdir()
        hook = hooks / 'pre-push'
        command = [Path(sys.executable).as_posix(), Path(pre_push.__file__).resolve().as_posix(),
                   '--root', self.root.as_posix()]
        hook.write_text('#!/bin/sh\nexec ' + ' '.join(shlex.quote(part) for part in command) + ' "$@"\n',
                        encoding='utf-8', newline='\n')
        hook.chmod(0o755)
        self.git('config', 'core.hooksPath', str(hooks))

        rejected = subprocess.run(['git', '-C', str(self.root), 'push', 'synthetic',
                                   'refs/heads/safe-publish:refs/heads/safe',
                                   'refs/heads/main:refs/heads/unsafe'], capture_output=True, text=True)
        published = subprocess.run(['git', '--git-dir=' + str(remote), 'show-ref'], capture_output=True, text=True)

        self.assertNotEqual(0, rejected.returncode)
        self.assertIn('GitHub-hosted', rejected.stderr)
        self.assertEqual('', published.stdout)
        self.assertEqual(unsafe, self.git('rev-parse', 'HEAD'))

        self.git('push', 'synthetic', 'refs/heads/safe-publish:refs/heads/safe')
        accepted = subprocess.run(['git', '--git-dir=' + str(remote), 'rev-parse', 'refs/heads/safe'],
                                  capture_output=True, text=True, check=True)
        self.assertEqual(self.safe_commit, accepted.stdout.strip())


if __name__ == '__main__':
    unittest.main()
