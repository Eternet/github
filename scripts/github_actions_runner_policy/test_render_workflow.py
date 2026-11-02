from fnmatch import fnmatchcase
from pathlib import Path
import tempfile
import unittest

from check import Auditor, load_yaml
from render_workflow import render

PIN = '09abf9b1a413ea8f0e079350a98788c5f103c8cd'


def triggered(workflow, event, changed_path, branch='main'):
    definition = workflow['on'][event]
    if event == 'workflow_dispatch':
        return True
    if event == 'push' and branch not in definition['branches']:
        return False
    return any(fnmatchcase(changed_path, pattern) for pattern in definition['paths'])


class WorkflowTriggerTests(unittest.TestCase):
    def test_normal_code_css_and_documentation_do_not_start_runner_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = load_yaml(render(Path(directory), 'main', PIN))
        for event in ('pull_request', 'push'):
            for path in ('src/Program.cs', 'README.md', 'docs/operations.md', 'wwwroot/site.css'):
                with self.subTest(event=event, path=path):
                    self.assertFalse(triggered(workflow, event, path))

    def test_workflow_and_action_trigger_pr_and_default_push(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = load_yaml(render(root, 'master', PIN))
        self.assertEqual(workflow['on']['pull_request']['paths'], workflow['on']['push']['paths'])
        self.assertEqual(['master'], workflow['on']['push']['branches'])
        for event in ('pull_request', 'push'):
            for path in ('.github/workflows/unrelated.yml', '.github/actions/nested/action.yml'):
                with self.subTest(event=event, path=path):
                    self.assertTrue(triggered(workflow, event, path, 'master'))
        self.assertFalse(triggered(workflow, 'push', '.github/workflows/a.yml', 'feature'))

    def test_unread_configs_do_not_trigger_and_manual_dispatch_remains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github').mkdir()
            (root / '.github/actionlint.yaml').write_text('self-hosted-runner: {}\n')
            workflow = load_yaml(render(root, 'main', PIN))
        self.assertTrue(triggered(workflow, 'workflow_dispatch', 'src/Program.cs'))
        for event in ('pull_request', 'push'):
            for path in ('.github/actionlint.yaml', '.github/generated-matrix.json', 'matrix.json'):
                self.assertFalse(triggered(workflow, event, path), (event, path))

    def test_local_reusable_and_its_inline_matrix_are_audited_and_trigger_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / '.github/workflows'
            workflows.mkdir(parents=True)
            (workflows / 'caller.yml').write_text('on: push\njobs:\n  invoke:\n    uses: ./.github/workflows/reusable.yml\n')
            child = workflows / 'reusable.yml'
            safe = "on: workflow_call\njobs:\n  check:\n    runs-on: ${{ matrix.runner }}\n    strategy:\n      matrix:\n        runner: [self-hosted]\n"
            child.write_text(safe)
            self.assertEqual([], Auditor(root).run())
            child.write_text(safe.replace('[self-hosted]', '[self-hosted, ubuntu-latest]'))
            self.assertTrue(any('GitHub-hosted' in error for error in Auditor(root).run()))
            workflow = load_yaml(render(root, 'main', PIN))
            for event in ('pull_request', 'push'):
                self.assertTrue(triggered(workflow, event, '.github/workflows/reusable.yml'))

    def test_actual_central_workflow_matches_template_and_tests_its_own_changes(self):
        root = Path(__file__).resolve().parents[2]
        actual = (root / '.github/workflows/self-hosted-runner-policy.yml').read_text(encoding='utf-8')
        self.assertEqual(render(root, 'main', None, central=True), actual)
        workflow = load_yaml(actual)
        for event in ('pull_request', 'push'):
            self.assertTrue(triggered(workflow, event, 'scripts/github_actions_runner_policy/check.py'))
            self.assertTrue(triggered(workflow, event, 'scripts/github_actions_runner_policy/approved_reusables.json'))
            self.assertTrue(triggered(workflow, event, '.github/actions/verify-dotnet-readiness/action.yml'))
            self.assertFalse(triggered(workflow, event, 'files/.editorconfig'))
            self.assertFalse(triggered(workflow, event, 'docs/README.md'))

    def test_real_netmap_integration_fixture_has_filtered_triggers_and_shared_immutable_action(self):
        fixture = Path(__file__).with_name('fixtures') / 'netmap-runner-policy.yml'
        content = fixture.read_text(encoding='utf-8')
        workflow = load_yaml(content)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(render(Path(directory), 'main', PIN), content)
        self.assertEqual('Eternet/github/scripts/github_actions_runner_policy@' + PIN,
                         workflow['jobs']['runner-policy']['steps'][1]['uses'])
        self.assertFalse(triggered(workflow, 'pull_request', 'src/Eternet.Netmap/Nodes.cs'))
        self.assertFalse(triggered(workflow, 'push', 'README.md'))
        self.assertTrue(triggered(workflow, 'pull_request', '.github/workflows/test.yml'))
        self.assertTrue(triggered(workflow, 'push', '.github/actions/guard/action.yml'))

    def test_read_only_runs_cancel_superseded_pr_or_ref_and_have_short_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = load_yaml(render(Path(directory), 'main', PIN))
        self.assertEqual({'contents': 'read'}, workflow['permissions'])
        self.assertEqual('runner-policy-${{ github.event.pull_request.number || github.ref }}',
                         workflow['concurrency']['group'])
        self.assertIs(True, workflow['concurrency']['cancel-in-progress'])
        self.assertEqual(5, workflow['jobs']['runner-policy']['timeout-minutes'])
        self.assertEqual({'runner-policy'}, set(workflow['jobs']))

    def test_mutable_pins_and_branch_yaml_injection_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for pin in ('main', 'a' * 39, 'Z' * 40):
                with self.subTest(pin=pin), self.assertRaises(ValueError):
                    render(Path(directory), 'main', pin)
            with self.assertRaises(ValueError):
                render(Path(directory), "main]\nother: unsafe", PIN)


if __name__ == '__main__':
    unittest.main()
