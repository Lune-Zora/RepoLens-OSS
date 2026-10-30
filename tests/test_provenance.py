"""Normal Demo evidence lifecycle; no external model or external material needed."""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from replens.cli import main as cli_main
from replens.patchgen import RulePatchGenerator
from replens.planner import RulePlanner
from replens.provenance import build_provenance, source_snapshot, verify_provenance
from replens.report import render_verification_markdown
from replens.runner import RepairRequest, run_repair
from replens.validation import load_acceptance

ROOT = Path(__file__).resolve().parents[1]
COMMAND = 'python -m unittest discover -s tests'
ISSUE = 'date string with surrounding whitespace fails to parse'
ACCEPTANCE = ROOT / 'examples/acceptance/demo.json'


@contextlib.contextmanager
def offline():
    with patch('replens.runner.build_planner', return_value=RulePlanner()), \
         patch('replens.patchgen.build_patch_generator', return_value=RulePatchGenerator()), \
         patch.dict(os.environ, {'REPLENS_REQUIRE_LLM': '0'}):
        yield


class ProvenanceDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='replens_provenance_')
        cls.root = Path(cls.temp.name)
        cls.repo = cls.root / 'demo'
        shutil.copytree(ROOT / 'examples/demo_repo', cls.repo,
                        ignore=shutil.ignore_patterns('.replens', '__pycache__'))
        cls.original = source_snapshot(cls.repo)
        cls.verified = cls.repair()
        cls.unverified = cls.repair(acceptance=False)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def repair(cls, acceptance=True, builder=build_provenance, **kwargs):
        with offline():
            return run_repair(RepairRequest(str(cls.repo), ISSUE, test_cmd=COMMAND, timeout=20,
                              acceptance_suite=str(ACCEPTANCE) if acceptance else '', **kwargs),
                              build_provenance=builder)

    def test_json_bundle_contains_full_frozen_material_and_source_hashes(self):
        out = self.verified
        self.assertEqual(out.status, 'VERIFIED', out.reasons)
        self.assertEqual(out.provenance_status['status'], 'WRITTEN', out.provenance_status)
        bundle = Path(out.provenance_dir)
        prov = json.loads((bundle / 'provenance.json').read_text(encoding='utf-8'))
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        independent = json.loads((bundle / 'suites/acceptance.json').read_text(encoding='utf-8'))
        cmd, files, sha = load_acceptance(str(ACCEPTANCE))
        self.assertEqual(prov['schema_version'], '2.0')
        self.assertEqual(prov['source']['files'], self.original['files'])
        self.assertEqual(prov['source']['tree_sha256'], self.original['tree_sha256'])
        self.assertIn('packages', prov['environment'])
        self.assertEqual(independent['command'], cmd)
        self.assertEqual(independent['files'], files)
        self.assertEqual(independent['sha256'], sha)
        self.assertEqual(independent['timeout'], 20)
        self.assertEqual(len(independent['before'][0]['tests']), 3)
        self.assertEqual(len(independent['after'][0]['tests']), 3)
        self.assertTrue(independent['after'][0]['passed'])
        for relative, digest in manifest['files'].items():
            self.assertEqual(hashlib.sha256((bundle / relative).read_bytes()).hexdigest(), digest)
        self.assertEqual(prov['patch']['content'].encode('utf-8'), (bundle / prov['patch']['path']).read_bytes())
        self.assertEqual(json.loads((bundle / 'report.json').read_text(encoding='utf-8')), out.report)
        self.assertEqual((bundle / 'report.md').read_text(encoding='utf-8'), out.markdown)

    def test_replay_all_suites_without_loading_original_acceptance(self):
        from replens.provenance import run_test_suite
        observed = []
        with patch('replens.validation.load_acceptance', side_effect=AssertionError('use frozen bundle')), \
             patch('replens.provenance.run_test_suite', wraps=run_test_suite) as execute:
            result = verify_provenance(self.repo, self.verified.provenance_dir,
                                       result_observer=lambda *args: observed.append(args))
        self.assertEqual(result['materials_status'], 'PASS', result)
        self.assertEqual(result['replay_status'], 'PASS', result)
        self.assertEqual(result['repair_status'], 'VERIFIED', result)
        self.assertEqual(set(result['phase_checks']), {'primary', 'auxiliary', 'acceptance'})
        self.assertTrue(all(all(phases.values()) for phases in result['phase_checks'].values()))
        self.assertEqual(execute.call_count, 3)
        self.assertEqual([row[0] for row in observed], ['primary', 'auxiliary', 'acceptance'])
        self.assertTrue(all(row[1] and row[2] for row in observed))
        self.assertTrue(all(row[3].startswith('补丁应用成功') for row in observed))
        self.assertEqual([call.kwargs['timeout'] for call in execute.call_args_list], [20, 20, 20])
        self.assertEqual(source_snapshot(self.repo)['files'], self.original['files'])

    def test_unverified_record_can_replay_pass_without_becoming_verified(self):
        self.assertEqual(self.unverified.status, 'UNVERIFIED')
        result = verify_provenance(self.repo, self.unverified.provenance_dir)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['repair_status'], 'UNVERIFIED')
        self.assertIn('UNVERIFIED', render_verification_markdown(result))

    def test_replay_log_write_failure_is_visible(self):
        def unavailable(*args):
            raise OSError('raw replay log storage unavailable')
        result = verify_provenance(self.repo, self.verified.provenance_dir,
                                   result_observer=unavailable)
        self.assertEqual(result['materials_status'], 'PASS', result)
        self.assertEqual(result['replay_status'], 'FAIL', result)
        self.assertIn('raw replay log storage unavailable', ' '.join(result['reasons']))

    def test_no_patch_is_a_replayable_record(self):
        out = self.repair(auto_patch=False)
        self.assertEqual(out.status, 'NO_PATCH', out.reasons)
        result = verify_provenance(self.repo, out.provenance_dir)
        self.assertEqual(result['replay_status'], 'PASS', result)
        self.assertEqual(result['repair_status'], 'NO_PATCH')

    def test_unmatched_production_patch_preserves_failed_conclusion(self):
        candidate = self.root / 'unmatched.json'
        candidate.write_text(json.dumps({'src/dateparse/parser.py': {
            'from': '# ordinary outdated source anchor', 'to': '# updated anchor'}}), encoding='utf-8')
        out = self.repair(auto_patch=False, patch=str(candidate))
        self.assertEqual(out.status, 'FAILED', out.reasons)
        self.assertEqual(out.provenance_status['status'], 'WRITTEN', out.provenance_status)
        result = verify_provenance(self.repo, out.provenance_dir)
        self.assertEqual(result['replay_status'], 'PASS', result)
        self.assertEqual(result['repair_status'], 'FAILED')

    def test_frozen_material_is_only_sent_to_final_validator_and_packer(self):
        from replens import runner
        events = []
        frozen = load_acceptance(str(ACCEPTANCE))
        def generate(*args, **kwargs):
            events.append('generate')
            self.assertNotIn('__replens_acceptance__', repr(kwargs))
            self.assertNotIn('test_tab_and_newline_padding', repr(args))
            return runner_auto(*args, **kwargs)
        def pack(*args, **kwargs):
            events.append('pack')
            self.assertEqual(kwargs['acceptance_suite'], {'command': frozen[0], 'files': frozen[1]})
            self.assertEqual(kwargs['frozen_source']['files'], self.original['files'])
            return build_provenance(*args, **kwargs)
        runner_auto = runner.auto_patch_loop
        with patch('replens.runner.auto_patch_loop', side_effect=generate):
            out = self.repair(builder=pack)
        self.assertEqual(events, ['generate', 'pack'])
        self.assertEqual(out.status, 'VERIFIED')
        self.assertNotIn('__replens_acceptance__', repr(out.evidences))
        self.assertNotIn('test_tab_and_newline_padding', repr(out.plan.to_dict()))
        from replens.indexer import IndexDB
        db = IndexDB(str(self.repo / '.replens/index.db'))
        try:
            paths = [row[0] for row in db.conn.execute('SELECT DISTINCT path FROM symbols')]
            self.assertFalse(any('__replens_acceptance__' in p or '.replens' in p for p in paths))
        finally:
            db.close()

    def test_source_can_be_relocated_with_same_content(self):
        relocated = self.root / 'relocated'
        shutil.copytree(self.repo, relocated, ignore=shutil.ignore_patterns('.replens', '__pycache__'))
        result = verify_provenance(relocated, self.verified.provenance_dir)
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['repair_status'], 'VERIFIED')

    @unittest.skipUnless(shutil.which('git'), 'Git is needed for the worktree regression check')
    def test_git_worktree_recreation_has_stable_source_hash(self):
        origin = self.root / 'git-origin'
        first, second = self.root / 'git-worktree-a', self.root / 'git-worktree-b'
        moved = self.root / 'git-worktree-moved'
        origin.mkdir()
        subprocess.run(['git', 'init', '--quiet', str(origin)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(origin), 'config', 'user.name', 'RepoLens Test'], check=True)
        subprocess.run(['git', '-C', str(origin), 'config', 'user.email', 'replens-test@example.invalid'], check=True)
        (origin / 'module.py').write_text('VALUE = 1\n', encoding='utf-8')
        subprocess.run(['git', '-C', str(origin), 'add', 'module.py'], check=True)
        subprocess.run(['git', '-C', str(origin), 'commit', '--quiet', '-m', 'fixture'], check=True)
        commit = subprocess.run(['git', '-C', str(origin), 'rev-parse', 'HEAD'], check=True,
                                capture_output=True, text=True).stdout.strip()
        for worktree in (first, second):
            subprocess.run(['git', '-C', str(origin), 'worktree', 'add', '--quiet', '--detach',
                            str(worktree), commit], check=True, capture_output=True)
        self.assertTrue((first / '.git').is_file())
        self.assertTrue((second / '.git').is_file())
        left, right = source_snapshot(first), source_snapshot(second)
        self.assertNotIn('.git', left['files'])
        self.assertEqual(left['files'], right['files'])
        self.assertEqual(left['tree_sha256'], right['tree_sha256'])
        subprocess.run(['git', '-C', str(origin), 'worktree', 'move', str(second), str(moved)],
                       check=True, capture_output=True)
        moved_snapshot = source_snapshot(moved)
        self.assertEqual(moved_snapshot['files'], left['files'])
        self.assertEqual(moved_snapshot['tree_sha256'], left['tree_sha256'])
        self.assertEqual(moved_snapshot['git_commit'], commit)
        subprocess.run(['git', '-C', str(origin), 'worktree', 'remove', '--force', str(moved)],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', str(origin), 'worktree', 'add', '--quiet', '--detach',
                        str(second), commit], check=True, capture_output=True)
        recreated = source_snapshot(second)
        self.assertEqual(recreated['files'], left['files'])
        self.assertEqual(recreated['tree_sha256'], left['tree_sha256'])
        self.assertEqual(recreated['git_commit'], commit)
        subprocess.run(['git', '-C', str(origin), 'worktree', 'remove', '--force', str(second)],
                       check=True, capture_output=True)
        subprocess.run(['git', '-C', str(origin), 'worktree', 'remove', '--force', str(first)],
                       check=True, capture_output=True)

    def test_repeated_packaging_creates_distinct_bundles(self):
        out = self.verified
        suite = json.loads(ACCEPTANCE.read_text(encoding='utf-8'))
        paths = [build_provenance(out.report, out.markdown, out.patch_file_used, self.repo,
                                 acceptance_suite=suite, frozen_source=self.original) for _ in range(2)]
        self.assertNotEqual(paths[0], paths[1])

    def test_packaging_io_failure_is_visible_and_keeps_repair_report(self):
        def unavailable(*args, **kwargs):
            raise OSError('Demo output directory unavailable')
        out = self.repair(builder=unavailable)
        self.assertEqual(out.status, 'VERIFIED')
        self.assertIsNone(out.provenance_dir)
        self.assertEqual(out.provenance_status['status'], 'FAILED')
        self.assertIn('Demo output directory unavailable', out.markdown)

    def test_no_packaging_reports_skipped(self):
        out = self.repair(builder=None, auto_patch=False)
        self.assertEqual(out.provenance_status['status'], 'SKIPPED')
        self.assertEqual(out.provenance_status['replay_status'], 'NOT_RUN')

    def test_cli_json_and_strict_replay_exit_codes(self):
        argv = ['verify', str(self.repo), '--format', 'json']
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli_main(argv + ['--provenance', self.verified.provenance_dir, '--require-verified'])
        self.assertEqual(json.loads(stdout.getvalue())['repair_status'], 'VERIFIED')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as code:
            cli_main(argv + ['--provenance', self.unverified.provenance_dir, '--require-verified'])
        self.assertEqual(code.exception.code, 2)
        output = self.root / 'verification.md'
        cli_main(['verify', str(self.repo), '--provenance', self.verified.provenance_dir, '--output', str(output)])
        self.assertIn('材料核验 | PASS', output.read_text(encoding='utf-8'))

    def test_cli_run_json_is_parseable_and_packing_failure_exits_two(self):
        argv = ['run', str(self.repo), '--issue', ISSUE, '--auto-patch', '--format', 'json',
                '--test-cmd', COMMAND, '--acceptance-suite', str(ACCEPTANCE)]
        stdout = io.StringIO()
        with offline(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            cli_main(argv)
        self.assertEqual(json.loads(stdout.getvalue())['provenance']['status'], 'WRITTEN')
        stdout = io.StringIO()
        with offline(), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()), \
             patch('replens.cli.build_provenance', side_effect=OSError('Demo output unavailable')), \
             self.assertRaises(SystemExit) as code:
            cli_main(argv)
        self.assertEqual(code.exception.code, 2)
        self.assertEqual(json.loads(stdout.getvalue())['provenance']['status'], 'FAILED')

    def test_historical_schema_remains_legacy_without_execution(self):
        legacy = self.root / 'legacy'
        legacy.mkdir()
        (legacy / 'provenance.json').write_text('{"schema_version":"1.3"}', encoding='utf-8')
        with patch('replens.provenance.run_test_suite') as execute:
            result = verify_provenance(self.repo, legacy)
        self.assertEqual(result['status'], 'LEGACY')
        self.assertEqual(result['replay_status'], 'NOT_RUN')
        execute.assert_not_called()

    def test_actual_web_handlers_without_optional_http_dependencies(self):
        # Exercise actual handler bodies; this is not an HTTP integration test.
        module = ast.parse((ROOT / 'web/app.py').read_text(encoding='utf-8'))
        functions = [node for node in module.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'api_run', 'api_verify'}]
        for node in functions:
            node.decorator_list = []
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                  *functions], type_ignores=[])
        from threading import Lock
        scope = {'_repair_lock': Lock(), 'JSONResponse': lambda content: content, 'build_provenance': build_provenance,
                 'verify_provenance': verify_provenance,
                 'render_verification_markdown': render_verification_markdown}
        exec(compile(ast.fix_missing_locations(module), str(ROOT / 'web/app.py'), 'exec'), scope)
        request = SimpleNamespace(repo=str(self.repo), issue=ISSUE, top_k=5, hybrid=False,
                                  test_cmd=COMMAND, auto_patch=True, patch='', timeout=20,
                                  force_reindex=False, no_provenance=False, acceptance_suite=str(ACCEPTANCE))
        with patch('replens.runner.run_repair', return_value=self.verified) as run:
            body = scope['api_run'](request)
        self.assertIs(run.call_args.kwargs['build_provenance'], build_provenance)
        self.assertEqual(body['provenance_status']['status'], 'WRITTEN')
        result = scope['api_verify'](SimpleNamespace(repo=str(self.repo),
                                   provenance=body['provenance'], test_cmd='', timeout=None))
        self.assertEqual(result['replay_status'], 'PASS', result)
        self.assertIn('修复结论 | VERIFIED', result['markdown'])

    @unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'),
                         'FastAPI/HTTPX are optional and unavailable')
    def test_real_http_verification(self):
        from fastapi.testclient import TestClient
        from web.app import app
        with TestClient(app) as client:
            response = client.post('/api/verify', json={'repo': str(self.repo),
                                   'provenance': self.verified.provenance_dir})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['repair_status'], 'VERIFIED')


if __name__ == '__main__':
    unittest.main()
