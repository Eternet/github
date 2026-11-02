import hashlib
from pathlib import Path
import tempfile
import unittest

from check import Auditor, Expression, PolicyError, check_selector, load_yaml, matrix_rows, resolve


class RunnerPolicyTests(unittest.TestCase):
    def audit(self, content, files=None, reusables=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / '.github' / 'workflows'
            workflows.mkdir(parents=True)
            (workflows / 'main.yml').write_text(content, encoding='utf-8')
            for name, text in (files or {}).items():
                (workflows / name).write_text(text, encoding='utf-8')
            auditor = Auditor(root, reusables)
            return auditor.run()

    def workflow(self, selector, extra=''):
        return f'on: push\njobs:\n  check:\n    runs-on: {selector}\n{extra}'

    def test_verified_literal_selectors_including_no_default_label_infrastructure(self):
        for selector in ('[self-hosted, Linux]', '[self-hosted, Windows, Runner1]', 'copilot-arc',
                         '[mini-pc-perf, minipc-perf-4]', '{group: MiniPC-Perf, labels: minipc-perf-1}',
                         '{labels: [self-hosted, Linux]}'):
            with self.subTest(selector=selector):
                self.assertEqual([], self.audit(self.workflow(selector)))

    def test_hosted_labels_are_rejected_even_with_own_anchor_and_if_false(self):
        for label in ('ubuntu-latest', 'windows-2022', 'macos-15', 'ubuntu-24.04-32core', 'github-hosted'):
            with self.subTest(label=label):
                errors = self.audit(self.workflow(f'[self-hosted, {label}]', '    if: false\n'))
                self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_unknown_labels_and_groups_do_not_become_trusted(self):
        for selector in ('unverified-large-runner', '[Linux, X64]', '{group: unknown, labels: Linux}'):
            with self.subTest(selector=selector):
                self.assertTrue(self.audit(self.workflow(selector)))

    def test_duplicate_yaml_job_and_nested_keys_fail_instead_of_hiding_hosted_job(self):
        duplicate_job = 'on: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n  check:\n    runs-on: self-hosted\n'
        duplicate_selector = 'on: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n    runs-on: self-hosted\n'
        for content in (duplicate_job, duplicate_selector):
            errors = self.audit(content)
            self.assertTrue(any('duplicate YAML key' in error for error in errors), errors)

    def test_on_event_name_remains_string_and_true_boolean_remains_boolean(self):
        loaded = load_yaml('on: push\njobs: {check: {if: false, runs-on: self-hosted}}')
        self.assertIn('on', loaded)
        self.assertIs(False, loaded['jobs']['check']['if'])

    def test_static_matrix_all_combinations_include_are_inspected(self):
        extra = '    strategy:\n      matrix:\n        runner: [self-hosted]\n        include:\n          - runner: ubuntu-latest\n'
        errors = self.audit(self.workflow("'${{ matrix.runner }}'", extra))
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_include_only_rows_cannot_overwrite_prior_rows(self):
        extra = '    strategy:\n      matrix:\n        include:\n          - runner: ubuntu-latest\n          - runner: self-hosted\n'
        errors = self.audit(self.workflow("'${{ matrix.runner }}'", extra))
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_include_cannot_overwrite_a_previous_standalone_combination(self):
        rows = matrix_rows({'strategy': {'matrix': {'os': ['own'], 'include': [
            {'os': 'other', 'runner': 'ubuntu-latest'}, {'os': 'other', 'runner': 'self-hosted'}]}}})
        self.assertEqual([{'os': 'own'}, {'os': 'other', 'runner': 'ubuntu-latest'},
                          {'os': 'other', 'runner': 'self-hosted'}], rows)

    def test_matrix_exclude_removes_only_matching_combinations(self):
        extra = '    strategy:\n      matrix:\n        runner: [self-hosted, ubuntu-latest]\n        exclude:\n          - runner: ubuntu-latest\n'
        self.assertEqual([], self.audit(self.workflow("'${{ matrix.runner }}'", extra)))

    def test_dynamic_matrix_with_static_selector_cannot_change_runner_provenance(self):
        extra = '    strategy:\n      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}\n'
        self.assertEqual([], self.audit(self.workflow('[self-hosted, Linux]', extra)))
        self.assertTrue(any('GitHub-hosted' in error for error in self.audit(self.workflow('ubuntu-latest', extra))))

    def test_dynamic_matrix_with_runner_variable_has_unverified_provenance(self):
        extra = '    strategy:\n      matrix: ${{ fromJSON(needs.plan.outputs.matrix) }}\n'
        self.assertTrue(self.audit(self.workflow("'${{ matrix.runner }}'", extra)))

    def test_reserved_self_hosted_label_proves_ownership_inside_literal_group(self):
        self.assertEqual([], self.audit(self.workflow('{group: default, labels: [self-hosted, Windows]}')))
        self.assertTrue(self.audit(self.workflow('{group: unknown, labels: [Linux]}')))
        errors = self.audit(self.workflow('{group: default, labels: [self-hosted, ubuntu-latest]}'))
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_choice_inputs_every_branch_is_checked(self):
        content = '''on:
  workflow_dispatch:
    inputs:
      runner:
        type: choice
        options: [self-hosted, ubuntu-latest]
jobs:
  check:
    runs-on: ${{ inputs.runner }}
'''
        errors = self.audit(content)
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_boolean_and_fromjson_branch_cannot_hide_hosted(self):
        content = '''on:
  workflow_dispatch:
    inputs:
      hosted: {type: boolean, default: false}
jobs:
  check:
    runs-on: ${{ inputs.hosted && fromJSON('["ubuntu-latest"]') || fromJSON('["self-hosted","Linux"]') }}
'''
        errors = self.audit(content)
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_unbounded_string_allowed_only_for_complete_literal_equality_partition(self):
        content = '''on:
  workflow_dispatch:
    inputs:
      target: {type: string}
jobs:
  check:
    runs-on: [self-hosted, "${{ inputs.target == 'one' && 'Runner1' || 'Runner2' }}"]
'''
        self.assertEqual([], self.audit(content))
        errors = self.audit(content.replace("[self-hosted, \"${{ inputs.target == 'one' && 'Runner1' || 'Runner2' }}\"]",
                                             "${{ inputs.target == 'one' && 'self-hosted' || 'ubuntu-latest' }}"))
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_direct_unbounded_input_rejected_even_inside_self_hosted_array(self):
        content = '''on:
  workflow_dispatch:
    inputs:
      runner: {type: string}
jobs:
  check:
    runs-on: [self-hosted, "${{ inputs.runner }}"]
'''
        self.assertTrue(any('escapes literal equality' in error for error in self.audit(content)))

    def test_workflow_call_string_is_not_bounded_by_dispatch_choice(self):
        content = '''on:
  workflow_dispatch:
    inputs:
      runner: {type: choice, options: [self-hosted]}
  workflow_call:
    inputs:
      runner: {type: string}
jobs:
  check:
    runs-on: ${{ inputs.runner }}
'''
        self.assertTrue(self.audit(content))

    def test_unknown_context_expression_and_function_fail(self):
        for expression in ('needs.build.outputs.runner', 'vars.RUNNER', "join(fromJSON('[\"self-hosted\"]'))"):
            with self.subTest(expression=expression):
                self.assertTrue(self.audit(self.workflow('${{ ' + expression + ' }}')))

    def test_format_json_braces_inside_strings_are_not_expression_delimiters(self):
        expression = "${{ fromJSON(format('{{\"group\":\"MiniPC-Perf\",\"labels\":[\"{0}\"]}}', inputs.runner)) }}"
        selector = resolve(expression, {'inputs.runner': 'minipc-perf-1'})
        self.assertEqual({'group': 'MiniPC-Perf', 'labels': ['minipc-perf-1']}, selector)
        check_selector(selector)

    def test_github_truthiness_and_case_insensitive_loose_equality(self):
        self.assertEqual('ubuntu-latest', Expression("fromJSON('[]') && 'ubuntu-latest' || 'self-hosted'").evaluate({}))
        self.assertEqual('ubuntu-latest', Expression("'0' == 0 && 'ubuntu-latest' || 'self-hosted'").evaluate({}))
        self.assertTrue(Expression("'SELF-HOSTED' == 'self-hosted'").evaluate({}))

    def test_local_reusable_is_checked_and_cycles_fail(self):
        main = 'on: push\njobs:\n  call:\n    uses: ./.github/workflows/child.yml\n'
        errors = self.audit(main, {'child.yml': self.workflow('ubuntu-latest')})
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)
        cycle = 'on: workflow_call\njobs:\n  call:\n    uses: ./.github/workflows/main.yml\n'
        self.assertTrue(any('cycle' in error for error in self.audit(main, {'child.yml': cycle})))

    def test_remote_mutable_unknown_and_tampered_snapshots_fail(self):
        reference = 'Eternet/github/.github/workflows/test.yml@' + 'a' * 40
        main = 'on: push\njobs:\n  call:\n    uses: ' + reference + '\n'
        snapshot = {'content': self.workflow('[self-hosted, Linux]'), 'sha256': 'invalid'}
        self.assertTrue(self.audit(main))
        self.assertTrue(self.audit(main, reusables={reference: snapshot}))
        snapshot['sha256'] = hashlib.sha256(snapshot['content'].encode()).hexdigest()
        self.assertEqual([], self.audit(main, reusables={reference: snapshot}))
        self.assertTrue(self.audit(main.replace('a' * 40, 'main'), reusables={reference: snapshot}))
        snapshot['content'] = self.workflow('ubuntu-latest')
        snapshot['sha256'] = hashlib.sha256(snapshot['content'].encode()).hexdigest()
        self.assertTrue(any('GitHub-hosted' in error for error in self.audit(main, reusables={reference: snapshot})))

    def test_remote_local_reusable_is_resolved_in_remote_revision(self):
        revision = 'a' * 40
        reference = 'Eternet/github/.github/workflows/test.yml@' + revision
        nested = 'Eternet/github/.github/workflows/child.yml@' + revision
        content = 'on: workflow_call\njobs:\n  call:\n    uses: ./.github/workflows/child.yml\n'
        def snapshot(text):
            return {'content': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
        main = 'on: push\njobs:\n  call:\n    uses: ' + reference + '\n'
        snapshots = {reference: snapshot(content), nested: snapshot(self.workflow('ubuntu-latest'))}
        errors = self.audit(main, {'child.yml': self.workflow('self-hosted')}, snapshots)
        self.assertTrue(any('GitHub-hosted' in error for error in errors), errors)

    def test_malformed_expression_and_oversized_matrix_fail_closed(self):
        self.assertTrue(self.audit(self.workflow("'${{ inputs.runner'")))
        with self.assertRaises(PolicyError):
            matrix_rows({'strategy': {'matrix': {'a': list(range(100)), 'b': list(range(100))}}})


if __name__ == '__main__':
    unittest.main()
