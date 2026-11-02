from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import check
from check import Auditor, PolicyError, audit_git_ref


SAFE = 'on: push\njobs:\n  check:\n    runs-on: [self-hosted, Linux]\n'
HOSTED = SAFE.replace('[self-hosted, Linux]', 'ubuntu-latest')
MAIN = '.github/workflows/main.yml'


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='runner policy tests ')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git('init', '--quiet', '--initial-branch=main')
        for name, value in (('user.name', 'Runner Policy Tests'),
                            ('user.email', 'runner-policy@example.invalid'),
                            ('commit.gpgsign', 'false'), ('tag.gpgsign', 'false'),
                            ('core.autocrlf', 'false'), ('gc.auto', '0'),
                            ('core.hooksPath', str(self.root / 'empty-hooks'))):
            self.git('config', name, value)
        self.safe_commit = self.commit_files({MAIN: SAFE})

    def git(self, *arguments, input=None):
        result = subprocess.run(['git', '-C', str(self.root), *arguments], input=input,
                                text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8', newline='\n')

    def commit_files(self, files):
        for name, content in files.items():
            if content is None:
                (self.root / name).unlink()
            else:
                self.write(name, content)
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', 'Synthetic workflow tree')
        return self.git('rev-parse', 'HEAD')


class CommittedRunnerTreeTests(GitFixture):
    def commit_raw_paths(self, files):
        # Construct Git's case-sensitive tree directly, including names that
        # cannot coexist in an ordinary Windows working directory.
        self.git('config', 'core.ignorecase', 'false')
        self.git('config', 'core.protectNTFS', 'false')
        self.git('read-tree', '--empty')
        for name, content in files.items():
            blob = self.git('hash-object', '-w', '--stdin', input=content)
            self.git('update-index', '--add', '--cacheinfo', f'100644,{blob},{name}')
        tree = self.git('write-tree')
        return self.git('commit-tree', tree, '-m', 'Synthetic raw workflow paths')

    def test_case_colliding_git_paths_cannot_hide_hosted_blob_in_windows_snapshot(self):
        commit = self.commit_raw_paths({'.github/workflows/Build.yml': HOSTED,
                                        '.github/workflows/build.yml': SAFE})
        self.assertEqual(2, len(self.git('ls-tree', '-r', '--name-only', commit).splitlines()))
        with self.assertRaisesRegex(PolicyError, 'snapshot path collision'):
            audit_git_ref(self.root, commit)

    def test_windows_alias_and_device_paths_fail_before_snapshot_writes(self):
        for name in ('build.yml.', 'build.yml ', 'build.yml::$DATA', 'NUL.yml', 'COM1.yml'):
            with self.subTest(name=name):
                commit = self.commit_raw_paths({'.github/workflows/' + name: HOSTED})
                with self.assertRaisesRegex(PolicyError, 'nonportable filesystem path'):
                    audit_git_ref(self.root, commit)

    def test_committed_head_rejects_hosted_workflow_hidden_by_safe_editor_changes(self):
        unsafe = self.commit_files({'.github/workflows/legacy.yml': HOSTED})
        self.write('.github/workflows/legacy.yml', SAFE)

        self.assertEqual([], Auditor(self.root).run())
        result = audit_git_ref(self.root, 'HEAD')

        self.assertEqual(unsafe, result.commit)
        self.assertTrue(any('legacy.yml' in error and 'GitHub-hosted' in error for error in result.errors))
        self.assertEqual(SAFE, (self.root / '.github/workflows/legacy.yml').read_text())

    def test_safe_changed_file_does_not_hide_unedited_hosted_workflow_in_branch(self):
        self.commit_files({'.github/workflows/legacy.yml': HOSTED})
        edited = self.commit_files({MAIN: SAFE.replace('Linux', 'Windows')})
        self.git('branch', 'older-safe-diff', edited)
        self.commit_files({'.github/workflows/legacy.yml': SAFE})

        result = audit_git_ref(self.root, 'refs/heads/older-safe-diff')

        self.assertEqual(edited, result.commit)
        self.assertTrue(any('legacy.yml' in error and 'GitHub-hosted' in error for error in result.errors))
        self.assertEqual((), audit_git_ref(self.root, 'HEAD').errors)

    def test_explicit_dispatch_source_ref_is_audited_instead_of_current_head(self):
        unsafe = self.commit_files({MAIN: HOSTED})
        self.git('branch', 'dispatch-source', unsafe)
        self.commit_files({MAIN: SAFE})

        result = subprocess.run([sys.executable, check.__file__, '--root', str(self.root),
                                 '--git-ref', 'refs/heads/dispatch-source'], capture_output=True, text=True)

        self.assertEqual(1, result.returncode)
        self.assertIn(unsafe, result.stderr)
        self.assertIn('GitHub-hosted', result.stderr)
        self.assertEqual((), audit_git_ref(self.root, 'HEAD').errors)

    def test_safe_branch_and_annotated_tag_resolve_to_same_immutable_tree(self):
        self.git('branch', 'safe-source', self.safe_commit)
        self.git('tag', '-a', 'safe-tag', '-m', 'Synthetic audited tag', self.safe_commit)
        branch = audit_git_ref(self.root, 'refs/heads/safe-source')
        tag = audit_git_ref(self.root, 'refs/tags/safe-tag')

        self.assertEqual(self.safe_commit, branch.commit)
        self.assertEqual(branch, tag)
        self.assertEqual(((), 1, 1), (branch.errors, branch.workflows, branch.jobs))

    def test_missing_reference_fails_without_falling_back_to_working_tree(self):
        with self.assertRaisesRegex(PolicyError, 'rev-parse failed'):
            audit_git_ref(self.root, 'refs/heads/not-fetched')
        self.assertEqual([], Auditor(self.root).run())

    def test_commit_without_workflows_passes_empty_and_ignores_editor_workflows(self):
        empty_tree = self.commit_files({MAIN: None})
        self.write(MAIN, HOSTED)

        result = audit_git_ref(self.root, empty_tree)

        self.assertEqual(empty_tree, result.commit)
        self.assertEqual(((), 0, 0), (result.errors, result.workflows, result.jobs))

    def test_readme_only_workflow_directory_is_valid_zero_workflow_commit(self):
        commit = self.commit_files({MAIN: None, '.github/workflows/README.md': 'No workflows configured.\n'})

        result = audit_git_ref(self.root, commit)

        self.assertEqual(((), 0, 0), (result.errors, result.workflows, result.jobs))
        self.assertEqual(['no workflows found'], Auditor(self.root).run())

    def test_missing_git_repository_fails_even_when_editor_workflow_looks_safe(self):
        with tempfile.TemporaryDirectory(prefix='non-git runner policy ') as directory:
            root = Path(directory)
            workflow = root / MAIN
            workflow.parent.mkdir(parents=True)
            workflow.write_text(SAFE, encoding='utf-8')

            with self.assertRaisesRegex(PolicyError, 'rev-parse failed'):
                audit_git_ref(root, 'HEAD')

    def test_local_git_replace_cannot_hide_raw_committed_hosted_tree(self):
        unsafe = self.commit_files({MAIN: HOSTED})
        safe = self.commit_files({MAIN: SAFE})
        self.git('replace', unsafe, safe)

        result = audit_git_ref(self.root, unsafe)

        self.assertEqual(unsafe, result.commit)
        self.assertTrue(any('GitHub-hosted' in error for error in result.errors))

    def test_git_archive_export_ignore_cannot_hide_committed_workflow(self):
        self.commit_files({'.github/workflows/legacy.yml': HOSTED,
                           '.gitattributes': '.github/workflows/legacy.yml export-ignore\n'})

        result = audit_git_ref(self.root, 'HEAD')

        self.assertTrue(any('legacy.yml' in error and 'GitHub-hosted' in error for error in result.errors))

    def test_missing_committed_blob_is_not_replaced_by_existing_editor_file(self):
        blob = self.git('rev-parse', f'HEAD:{MAIN}')
        loose_object = self.root / '.git' / 'objects' / blob[:2] / blob[2:]
        self.assertTrue(loose_object.is_file())
        loose_object.chmod(0o600)
        loose_object.unlink()

        with self.assertRaisesRegex(PolicyError, 'cat-file failed'):
            audit_git_ref(self.root, 'HEAD')
        self.assertEqual(SAFE, (self.root / MAIN).read_text())

    def test_blob_byte_limit_accepts_boundary_and_rejects_excess(self):
        size = len(SAFE.encode())
        with patch.object(check, 'MAX_WORKFLOW_BYTES', size):
            self.assertEqual((), audit_git_ref(self.root, 'HEAD').errors)
        with patch.object(check, 'MAX_WORKFLOW_BYTES', size - 1):
            with self.assertRaisesRegex(PolicyError, 'bounded byte limit'):
                audit_git_ref(self.root, 'HEAD')

    def test_total_tree_byte_and_file_limits_never_silently_truncate(self):
        self.commit_files({'.github/workflows/extra.yml': SAFE})
        with patch.object(check, 'MAX_WORKFLOW_FILES', 2):
            self.assertEqual(2, audit_git_ref(self.root, 'HEAD').workflows)
        with patch.object(check, 'MAX_WORKFLOW_FILES', 1):
            with self.assertRaisesRegex(PolicyError, 'bounded file limit'):
                audit_git_ref(self.root, 'HEAD')
        size = len(SAFE.encode()) * 2
        with patch.object(check, 'MAX_TREE_BYTES', size):
            self.assertEqual(2, audit_git_ref(self.root, 'HEAD').workflows)
        with patch.object(check, 'MAX_TREE_BYTES', size - 1):
            with self.assertRaisesRegex(PolicyError, 'bounded byte limit'):
                audit_git_ref(self.root, 'HEAD')

    def test_committed_local_reusable_is_not_replaced_by_editor_content(self):
        main = 'on: push\njobs:\n  call:\n    uses: ./.github/workflows/child.yml\n'
        self.commit_files({MAIN: main, '.github/workflows/child.yml': HOSTED})
        self.write('.github/workflows/child.yml', SAFE)

        result = audit_git_ref(self.root, 'HEAD')

        self.assertTrue(any('child.yml' in error and 'GitHub-hosted' in error for error in result.errors))

    def test_missing_committed_local_reusable_is_not_supplied_by_untracked_file(self):
        main = 'on: push\njobs:\n  call:\n    uses: ./.github/workflows/child.yml\n'
        self.commit_files({MAIN: main})
        self.write('.github/workflows/child.yml', SAFE)

        result = audit_git_ref(self.root, 'HEAD')

        self.assertTrue(any('invalid local reusable reference' in error for error in result.errors))

    def test_symlink_workflow_tree_entry_is_rejected_without_following_working_tree(self):
        target_blob = self.git('hash-object', '-w', '--stdin', input='outside.yml\n')
        self.git('update-index', '--add', '--cacheinfo', f'120000,{target_blob},{MAIN}')
        self.git('commit', '--quiet', '-m', 'Synthetic symlink workflow')

        with self.assertRaisesRegex(PolicyError, 'unsupported non-regular file'):
            audit_git_ref(self.root, 'HEAD')
        self.assertEqual(SAFE, (self.root / MAIN).read_text())

    def test_cli_safe_commit_reports_exact_commit_and_counts(self):
        result = subprocess.run([sys.executable, check.__file__, '--root', str(self.root),
                                 '--git-ref', self.safe_commit], capture_output=True, text=True)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(self.safe_commit, result.stdout)
        self.assertIn('1 workflows, 1 jobs', result.stdout)


if __name__ == '__main__':
    unittest.main()
