from fnmatch import fnmatchcase
from pathlib import Path
import unittest

from check import load_yaml


def triggered(workflow, event, path, branch='main'):
    definition = workflow['on'][event]
    if event == 'workflow_dispatch':
        return True
    return (event != 'push' or branch in definition['branches']) and any(
        fnmatchcase(path, pattern) for pattern in definition['paths'])


class CentralWorkflowTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.workflow = load_yaml((root / '.github/workflows/self-hosted-runner-policy.yml').read_text())

    def test_normal_code_css_docs_and_unread_configs_do_not_start_jobs(self):
        for event in ('pull_request', 'push'):
            for path in ('src/Program.cs', 'README.md', 'docs/operations.md', 'wwwroot/site.css',
                         '.github/actionlint.yaml', 'matrix.json'):
                with self.subTest(event=event, path=path):
                    self.assertFalse(triggered(self.workflow, event, path))

    def test_workflows_actions_checker_and_actual_manifest_trigger(self):
        for event in ('pull_request', 'push'):
            for path in ('.github/workflows/reusable.yml', '.github/actions/verify-dotnet-readiness/action.yml',
                         'scripts/github_actions_runner_policy/check.py',
                         'scripts/github_actions_runner_policy/approved_reusables.json'):
                with self.subTest(event=event, path=path):
                    self.assertTrue(triggered(self.workflow, event, path))

    def test_default_push_and_pr_share_paths_and_manual_dispatch_is_available(self):
        events = self.workflow['on']
        self.assertEqual(events['pull_request']['paths'], events['push']['paths'])
        self.assertEqual(['main'], events['push']['branches'])
        self.assertFalse(triggered(self.workflow, 'push', '.github/workflows/test.yml', 'feature'))
        self.assertTrue(triggered(self.workflow, 'workflow_dispatch', 'README.md'))

    def test_only_one_read_only_job_uses_own_runner_and_cancels_its_superseded_ref(self):
        self.assertEqual({'contents': 'read'}, self.workflow['permissions'])
        self.assertEqual({'runner-policy'}, set(self.workflow['jobs']))
        self.assertEqual(['self-hosted', 'Linux'], self.workflow['jobs']['runner-policy']['runs-on'])
        self.assertEqual(5, self.workflow['jobs']['runner-policy']['timeout-minutes'])
        self.assertEqual('runner-policy-${{ github.event.pull_request.number || github.ref }}',
                         self.workflow['concurrency']['group'])
        self.assertIs(True, self.workflow['concurrency']['cancel-in-progress'])


if __name__ == '__main__':
    unittest.main()
