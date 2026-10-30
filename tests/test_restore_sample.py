"""Control-entry regression checks; fixtures are not real upstream bug evidence."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from replens.executor import TestResult as ExecutionResult, _run_one


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/restore_tqdm_reset_inf.py'
SPEC = importlib.util.spec_from_file_location('restore_sample_control', SCRIPT)
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


class RestoreControlTests(unittest.TestCase):
    def test_read_only_audit_includes_git_metadata_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'module.py').write_text('VALUE = 1\n')
            (root / '.git').mkdir()
            head = root / '.git/HEAD'
            head.write_text('ref: refs/heads/main\n')
            before = control._repository_fingerprint(root)
            self.assertEqual(before['files'], 2)
            self.assertEqual(before, control._repository_fingerprint(root))
            head.write_text('ref: refs/heads/other\n')
            self.assertNotEqual(before, control._repository_fingerprint(root))

    def test_british_license_filename_keeps_both_declared_components(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(control.gitops, '_git', return_value=SimpleNamespace(stdout='LICENCE\n')), \
                 patch.object(control.gitops, '_git_bytes', side_effect=[b'MIT License\nMPL-2.0\n',
                                                                 b'license = "MPL-2.0 AND MIT"']):
                license_info = control._license_info(Path(directory), 'fixture', Path(directory) / 'logs')
            self.assertEqual(license_info['path'], 'LICENCE')
            self.assertEqual(license_info['declared_expression'], 'MPL-2.0 AND MIT')
            self.assertEqual(license_info['components'], ['MPL-2.0', 'MIT'])

    def test_mirror_is_recorded_as_mirror_not_canonical_upstream(self):
        identity, kind = control._source_identity([{'identity': 'gitee.com/mirrors/tqdm'}])
        self.assertEqual(identity, 'gitee.com/mirrors/tqdm')
        self.assertEqual(kind, 'mirror-with-pinned-commits')
        with self.assertRaises(control.RestoreError):
            control._source_identity([{'identity': 'gitee.com/someone/tqdm'}])

    def test_cleanup_cannot_remove_parent_or_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            managed = parent / 'managed'
            managed.mkdir()
            (managed / 'fixture.txt').write_text('isolated fixture')
            with self.assertRaises(ValueError):
                control._remove_managed_directory(parent, parent)
            control._remove_managed_directory(managed, parent)
            self.assertTrue(parent.exists())
            self.assertFalse(managed.exists())

    def test_complete_before_and_after_logs_are_both_preserved(self):
        before = ExecutionResult('python -m pytest tests', 1, stdout='before-' * 1200,
                                 stderr='before stderr', valid=True)
        after = ExecutionResult('python -m pytest tests', 0, stdout='after-' * 1200,
                                stderr='after stderr', valid=True)
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory)
            control._save_suite_logs([before], [after], 'fixture patch', logs, 'gold')
            for phase, result in [('before', before), ('after', after)]:
                folder = logs / 'gold' / phase
                stdout = (folder / '01.stdout.txt').read_bytes()
                self.assertEqual(stdout, result.stdout.encode('utf-8'))
                self.assertEqual((folder / '01.stderr.txt').read_text(), result.stderr)
                record = json.loads((folder / '01.json').read_text())
                self.assertEqual(record['stdout_sha256'], hashlib.sha256(stdout).hexdigest())

    def test_missing_clone_records_failure_without_starting_upstream_or_worktree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = SimpleNamespace(repo=str(root / 'missing-clone'), output=str(root / 'output'),
                                   work_dir=str(root), timeout=10)
            with patch.object(control.upstream, '_verify_upstream') as upstream, \
                 patch.object(control.gitops, '_add_worktree') as add_worktree:
                with self.assertRaises(control.RestoreError):
                    control.run(args)
            upstream.assert_not_called()
            add_worktree.assert_not_called()
            result = json.loads((root / 'output/run.json').read_text(encoding='utf-8'))
            self.assertEqual((result['status'], result['failed_stage']), ('FAILED', 'preflight'))
            self.assertFalse((root / 'output/evidence_bundle').exists())

    def test_timeout_keeps_partial_process_output(self):
        partial_stdout = ('partial-output-' * 500).encode('utf-8')
        partial_stderr = b'partial-stderr'
        expired = subprocess.TimeoutExpired(['fixture'], 1, output=partial_stdout,
                                             stderr=partial_stderr)
        with patch('replens.executor.subprocess.run', side_effect=expired):
            result = _run_one('python -m pytest tests', '.', 1)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.valid)
        self.assertEqual(result.stdout.encode('utf-8'), partial_stdout)
        self.assertEqual(result.stderr.encode('utf-8'), partial_stderr)
        self.assertEqual(result.framework, 'pytest')


class CompatibilityLayerTests(unittest.TestCase):
    """2026-10-28 拆分为 scripts/realbug/ 后，入口必须保持原模块级契约。"""

    EXPECTED_SYMBOLS = (
        'RestoreError', 'run', 'main',
        '_sha', '_is_within', '_save', '_repository_fingerprint',
        '_git_command', '_git', '_git_bytes', '_parse_diff_paths',
        '_safe_remote_identity', '_save_suite_logs', '_capture_suite',
        '_target_state', '_same_test_states', '_license_info', '_source_identity',
        '_remove_managed_directory', '_verify_upstream', '_freeze_environment',
        '_add_worktree', '_apply_test_patch', '_phase_summary',
        'FIX_COMMIT', 'BUGGY_PARENT', 'SOURCE_PATH', 'TEST_PATH', 'ISSUE',
        'MAIN_COMMAND', 'ACCEPTANCE_PATH', 'ACCEPTANCE_COMMAND', 'ACCEPTANCE_SOURCE',
    )

    def test_all_original_symbols_still_exposed(self):
        """拆分不得丢失任何模块级符号。"""
        missing = [name for name in self.EXPECTED_SYMBOLS if not hasattr(control, name)]
        self.assertEqual(missing, [], '入口缺少原符号: %s' % missing)

    def test_patchable_submodules_exposed(self):
        """子模块须可从入口取到，供 patch.object 精确替换。"""
        for name in ('gitops', 'upstream', 'pipeline', 'suites', 'environment', 'common', 'constants', 'errors'):
            self.assertTrue(hasattr(control, name), '入口缺少子模块: ' + name)

    def test_patching_gitops_intercepts_license_lookup(self):
        """patch control.gitops._git 必须真正拦到调用，否则测试是假通过。"""
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(control.gitops, '_git') as git_mock:
                git_mock.return_value = SimpleNamespace(stdout='LICENCE\n')
                with patch.object(control.gitops, '_git_bytes', return_value=b'MIT License\nMPL-2.0 AND MIT\n'):
                    control._license_info(Path(directory), 'fixture', Path(directory) / 'logs')
            self.assertTrue(git_mock.called, '补丁未拦截到 _git 调用，测试已失效')

    def test_constants_unchanged(self):
        """样本身份常量不得被改动。"""
        self.assertEqual(control.BUGGY_PARENT, '5bf58c2d61410e5a17da3440df921fe34c6c38bf')
        self.assertEqual(control.FIX_COMMIT, '426a0988a1e9970041f5a649ae8a6376c5293fbe')
        self.assertEqual(control.SOURCE_PATH, 'tqdm/std.py')
        self.assertEqual(control.TEST_PATH, 'tests/tests_tqdm.py')


if __name__ == '__main__':
    unittest.main()
