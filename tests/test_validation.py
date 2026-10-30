"""Acceptance must be based on stable test identities, never on exit code alone."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from replens.cli import main as cli_main
from replens.executor import TestResult as ExecutionResult, _run_one, run_test_suite
from replens.patchgen import RulePatchGenerator
from replens.planner import RulePlanner
from replens.report import build_report, render_markdown
from replens.runner import RepairRequest, run_repair
from replens.validation import assess_primary, assess_repair, load_acceptance

ROOT = Path(__file__).resolve().parents[1]
COMMAND = 'python -m unittest discover -s tests'


def result(tests, command=COMMAND, valid=True, structured=True):
    passed = all(s in {'PASS', 'SKIP', 'XFAIL'} for s in tests.values())
    return ExecutionResult(command, 0 if passed else 1, passed=passed, valid=valid,
                           tests=tests, structured=structured, framework='unittest')


class AcceptancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.before = [result({'case.A.target': 'FAIL', 'case.B.target': 'PASS'})]
        self.after = [result({'case.A.target': 'PASS', 'case.B.target': 'PASS'})]

    def assess(self, **kwargs):
        return assess_repair(self.before, self.after, self.before, self.after, 'frozen', **kwargs)

    def test_only_full_contract_is_verified(self):
        out = self.assess()
        self.assertEqual(out['status'], 'VERIFIED')
        self.assertEqual(out['targets'], ['case.A.target'])
        self.assertEqual(out['preserved'], ['case.B.target'])

    def test_primary_pass_without_independent_suite_is_unverified(self):
        self.assertEqual(assess_repair(self.before, self.after)['status'], 'UNVERIFIED')

    def test_target_skip_xfail_xpass_and_disappearance_are_not_pass(self):
        for state in ['SKIP', 'XFAIL', 'XPASS', None]:
            with self.subTest(state=state):
                tests = {'case.B.target': 'PASS'}
                if state:
                    tests['case.A.target'] = state
                self.assertEqual(assess_primary(self.before, [result(tests)])['status'], 'FAILED')

    def test_original_passing_test_must_still_explicitly_pass(self):
        for state in ['FAIL', 'ERROR', 'SKIP', 'XFAIL', None]:
            with self.subTest(state=state):
                tests = {'case.A.target': 'PASS'}
                if state:
                    tests['case.B.target'] = state
                self.assertEqual(assess_primary(self.before, [result(tests)])['status'], 'FAILED')

    def test_invalid_or_unstructured_baseline_cannot_verify(self):
        for baseline in [[], [result({'x': 'FAIL'}, valid=False)],
                         [result({'x': 'FAIL'}, structured=False)]]:
            self.assertEqual(assess_primary(baseline, self.after)['status'], 'INVALID')

    def test_baseline_must_reproduce_failure(self):
        self.assertEqual(assess_primary(self.after, self.after)['status'], 'UNVERIFIED')

    def test_invalid_after_and_mismatched_command_are_rejected(self):
        self.assertEqual(assess_primary(self.before, [result({'x': 'PASS'}, valid=False)])['status'], 'INVALID')
        self.assertEqual(assess_primary(self.before, [result({'x': 'PASS'}, command='pytest')])['status'], 'INVALID')

    def test_independent_failure_overrides_primary_pass(self):
        out = assess_repair(self.before, self.after, self.before, self.before, 'frozen')
        self.assertEqual(out['status'], 'FAILED')

    def test_independent_missing_results_and_invalid_execution_cannot_verify(self):
        for before, after in [(None, None), ([], []),
                              (self.before, [result({'x': 'PASS'}, valid=False)])]:
            self.assertNotEqual(assess_repair(self.before, self.after, before, after, 'frozen')['status'], 'VERIFIED')

    def test_generated_checks_are_auxiliary_and_can_block_false_success(self):
        self.assertEqual(self.assess(regression_after=self.before)['status'], 'FAILED')
        self.assertEqual(self.assess(regression_after=[result({'x': 'PASS'}, valid=False)])['status'], 'INVALID')
        self.assertEqual(assess_repair(self.before, self.after, regression_after=self.after)['status'], 'UNVERIFIED')

    def test_duplicate_ids_cannot_hide_failures(self):
        self.assertEqual(assess_primary(self.before * 2, self.after)['status'], 'INVALID')

    def test_json_and_markdown_cannot_claim_verified_from_main_results(self):
        rep = build_report('fixture', 'issue', [], False, None, COMMAND,
                           self.before, self.after, '', '', regression_after=self.before)
        self.assertEqual(rep['status'], 'FAILED')
        self.assertIn('FAILED', render_markdown(rep))
        self.assertNotIn('独立验收通过', render_markdown(rep))


class TemporaryRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='replens_validation_'))
        self.repo = self.tmp / 'repo'
        (self.repo / 'tests').mkdir(parents=True)
        (self.repo / 'api.py').write_text('VALUE = 0\n', encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_tests(self, code):
        (self.repo / 'tests/test_api.py').write_text(code, encoding='utf-8')

    def patch_file(self, rel='api.py', old='VALUE = 0', new='VALUE = 1'):
        path = self.tmp / 'fix.json'
        path.write_text(json.dumps({rel: {'from': old, 'to': new}}), encoding='utf-8')
        return str(path)


class UnittestAdapterTests(TemporaryRepo):
    def test_full_ids_distinguish_same_method_name_in_different_classes(self):
        self.write_tests('import unittest\nclass A(unittest.TestCase):\n def test_same(self): self.fail()\n'
                   'class B(unittest.TestCase):\n def test_same(self): pass\n')
        r = _run_one(COMMAND, str(self.repo), 10)
        self.assertTrue(r.valid, r.to_dict())
        self.assertEqual(r.tests, {'test_api.A.test_same': 'FAIL', 'test_api.B.test_same': 'PASS'})

    def test_no_tests_and_all_skipped_are_invalid(self):
        for code in ['', 'import unittest\n@unittest.skip("unavailable")\nclass A(unittest.TestCase):\n def test_a(self): pass\n']:
            self.write_tests(code)
            r = _run_one(COMMAND, str(self.repo), 10)
            self.assertFalse(r.valid, r.to_dict())
            self.assertFalse(r.passed)

    def test_expected_failure_is_never_an_ordinary_pass(self):
        self.write_tests('import unittest\nclass A(unittest.TestCase):\n'
                   ' @unittest.expectedFailure\n def test_a(self): self.fail()\n'
                   ' def test_b(self): pass\n')
        r = _run_one(COMMAND, str(self.repo), 10)
        self.assertTrue(r.valid)
        self.assertEqual(r.tests['test_api.A.test_a'], 'XFAIL')
        self.assertEqual(r.summary['failed'], 0)
        self.assertEqual(r.summary['passed'], 1)

    def test_subtest_failure_cannot_be_recorded_as_parent_pass(self):
        self.write_tests('import unittest\nclass A(unittest.TestCase):\n def test_a(self):\n'
                   '  for x in range(2):\n   with self.subTest(x=x): self.assertEqual(x, 0)\n')
        r = _run_one(COMMAND, str(self.repo), 10)
        self.assertEqual(r.tests['test_api.A.test_a'], 'FAIL')
        self.assertFalse(r.passed)

    def test_missing_dependency_and_import_error_are_invalid(self):
        for code in ['import __replens_missing_dependency__\n', 'raise RuntimeError("cannot import test")\n']:
            self.write_tests(code)
            self.assertFalse(_run_one(COMMAND, str(self.repo), 10).valid)

    def test_timeout_is_invalid(self):
        self.write_tests('import unittest,time\nclass A(unittest.TestCase):\n def test_a(self): time.sleep(2)\n')
        r = _run_one(COMMAND, str(self.repo), 0.1)
        self.assertFalse(r.valid)
        self.assertTrue(r.timed_out)

    def test_before_test_side_effects_do_not_contaminate_after_and_temps_are_cleaned(self):
        self.write_tests('import unittest\nfrom pathlib import Path\nfrom api import VALUE\n'
                   'class A(unittest.TestCase):\n def test_a(self):\n'
                   '  self.assertFalse(Path("stamp").exists())\n  Path("stamp").write_text("created")\n'
                   '  self.assertEqual(VALUE, 1)\n')
        from replens.executor import _prepare_tmp
        created = []
        def tracked_prepare(*args, **kwargs):
            root = _prepare_tmp(*args, **kwargs)
            created.append(Path(root))
            return root
        with patch('replens.executor._prepare_tmp', side_effect=tracked_prepare):
            before, after, note = run_test_suite(str(self.repo), COMMAND, patch_file=self.patch_file())
        self.assertFalse(before[0].passed)
        self.assertTrue(after[0].passed, note + str(after[0].to_dict()))
        self.assertEqual(len(created), 2)
        self.assertTrue(all(not path.exists() for path in created))
        self.assertFalse((self.repo / 'stamp').exists())
        self.assertEqual((self.repo / 'api.py').read_text(encoding='utf-8'), 'VALUE = 0\n')

    def test_manual_test_patch_and_config_patch_are_blocked(self):
        self.write_tests('import unittest\nclass A(unittest.TestCase):\n def test_a(self): self.fail()\n')
        for rel, old, new in [('tests/test_api.py', 'self.fail()', 'pass'),
                              ('pyproject.toml', 'x = 0', 'x = 1')]:
            with self.subTest(rel=rel):
                if rel.endswith('toml'):
                    (self.repo / rel).write_text('x = 0\n', encoding='utf-8')
                _, after, note = run_test_suite(str(self.repo), COMMAND,
                                                patch_file=self.patch_file(rel, old, new))
                self.assertIsNone(after)
                self.assertIn('受保护', note)

    def test_extra_file_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            run_test_suite(str(self.repo), COMMAND, extra_files={'../escape.py': 'pass'})


@unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest optional dependency unavailable')
class PytestAdapterTests(TemporaryRepo):
    def test_parametrized_nodes_and_teardown_error_are_preserved(self):
        self.write_tests('import pytest\n@pytest.mark.parametrize("n", [0,1])\n'
                   'def test_value(n): assert n == 1\n'
                   '@pytest.fixture\ndef broken_teardown():\n yield\n raise RuntimeError("teardown")\n'
                   'def test_teardown(broken_teardown): pass\n')
        r = _run_one('pytest tests -q', str(self.repo), 30)
        self.assertTrue(r.valid, r.to_dict())
        self.assertEqual(r.tests['tests/test_api.py::test_value[0]'], 'FAIL')
        self.assertEqual(r.tests['tests/test_api.py::test_value[1]'], 'PASS')
        self.assertEqual(r.tests['tests/test_api.py::test_teardown'], 'ERROR')

    def test_collect_only_zero_skipped_and_xfail_only_are_invalid(self):
        for code, cmd in [('', 'pytest tests -q'),
                          ('def test_a(): pass\n', 'pytest tests --collect-only -q'),
                          ('import pytest\n@pytest.mark.skip\ndef test_a(): pass\n', 'pytest tests -q'),
                          ('import pytest\n@pytest.mark.xfail\ndef test_a(): assert False\n', 'pytest tests -q')]:
            with self.subTest(cmd=cmd, code=code):
                self.write_tests(code)
                r = _run_one(cmd, str(self.repo), 30)
                self.assertFalse(r.valid, r.to_dict())
                self.assertFalse(r.passed)

    def test_xpass_and_collection_error_cannot_be_a_pass(self):
        self.write_tests('import pytest\n@pytest.mark.xfail\ndef test_a(): pass\n')
        r = _run_one('python -m pytest tests -q', str(self.repo), 30)
        self.assertEqual(r.tests['tests/test_api.py::test_a'], 'XPASS')
        self.assertFalse(r.passed)
        self.write_tests('raise RuntimeError("import failure")\n')
        self.assertFalse(_run_one('pytest tests -q', str(self.repo), 30).valid)


class RepairAcceptanceIntegrationTests(TemporaryRepo):
    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / 'examples/demo_repo', self.repo, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.replens', '__pycache__'))
        self.acceptance = self.tmp / 'acceptance.json'
        shutil.copyfile(ROOT / 'examples/acceptance/demo.json', self.acceptance)
        self.issue = 'parse_date date string whitespace fails to parse'
        self.output = self.tmp / 'report.json'

    @contextlib.contextmanager
    def offline(self):
        with patch('replens.runner.build_planner', return_value=RulePlanner()), \
             patch('replens.patchgen.build_patch_generator', return_value=RulePatchGenerator()), \
             patch.dict(os.environ, {'REPLENS_REQUIRE_LLM': '0'}):
            yield

    def repair(self, acceptance=True, **kwargs):
        with self.offline():
            return run_repair(RepairRequest(str(self.repo), self.issue, test_cmd=COMMAND,
                              acceptance_suite=str(self.acceptance) if acceptance else '', **kwargs))

    def test_real_unittest_demo_is_verified_and_honors_explicit_framework(self):
        out = self.repair()
        self.assertEqual(out.status, 'VERIFIED', out.reasons)
        self.assertEqual(out.report['test_framework'], 'unittest')
        self.assertIn('unittest.TestCase', out.generated_test)
        self.assertEqual(len(out.before[0].tests), 3)
        self.assertEqual(out.status, out.report['status'])
        self.assertIn('VERIFIED — 独立验收通过', out.markdown)
        self.assertTrue(out.acceptance_after[0].passed)
        self.assertEqual(out.flip_prediction['actual_flips'], ['test_parser.ParserTests.test_parse_date_with_whitespace'])

    def test_primary_pass_independent_failure_is_failed_in_all_reports(self):
        data = json.loads(self.acceptance.read_text(encoding='utf-8'))
        key = next(iter(data['files']))
        data['files'][key] += '\nclass UnmetRequirement(unittest.TestCase):\n def test_still_broken(self): self.fail("independent assertion")\n'
        self.acceptance.write_text(json.dumps(data), encoding='utf-8')
        out = self.repair()
        self.assertTrue(out.after[0].passed)
        self.assertFalse(out.acceptance_after[0].passed)
        self.assertEqual(out.status, 'FAILED')
        self.assertEqual(out.report['status'], 'FAILED')
        self.assertNotIn('独立验收通过', out.markdown)

    def test_generated_smoke_or_behavior_cannot_replace_independent_acceptance(self):
        out = self.repair(acceptance=False)
        self.assertTrue(out.after[0].passed)
        self.assertTrue(out.regression_after[0].passed)
        self.assertEqual(out.status, 'UNVERIFIED')

    def test_generated_failure_and_syntax_error_are_visible(self):
        for generated in ['import unittest\nclass T(unittest.TestCase):\n def test_fail(self): self.fail()\n',
                          'def test_invalid(\n']:
            with self.subTest(generated=generated), patch('replens.runner.generate_regression_test', return_value=generated):
                out = self.repair()
                self.assertIn(out.status, {'FAILED', 'INVALID'})
                self.assertTrue(out.report['regression_after'])

    def test_multiline_issue_generates_valid_code_for_both_frameworks(self):
        from replens.indexer import Symbol
        from replens.retriever import Evidence
        from replens.testgen import generate_regression_test
        symbol = Symbol('src/dateparse/parser.py', 'function', 'parse_date', 9, 25,
                        doc='Parse a date, e.g. "2024-01-01".',
                        code='def parse_date(s):\n    return s\n')
        for framework in ['pytest', 'unittest']:
            code = generate_regression_test(str(self.repo), [Evidence(symbol, 1, 'fixture', 'keyword_match')],
                                            'date whitespace\nextra detail\r\nanother detail', framework)
            compile(code, 'generated_' + framework, 'exec')

    def test_acceptance_not_given_to_generator_and_frozen_before_generation(self):
        initial = self.acceptance.read_text(encoding='utf-8')
        from replens.patchgen import auto_patch_loop
        def generate(*args, **kwargs):
            self.assertNotIn('__replens_acceptance__', repr(kwargs.get('extra_files')))
            self.acceptance.write_text('{"tampered": true}', encoding='utf-8')
            return auto_patch_loop(*args, **kwargs)
        with patch('replens.runner.auto_patch_loop', side_effect=generate):
            out = self.repair()
        self.assertEqual(out.status, 'VERIFIED', out.reasons)
        self.acceptance.write_text(initial, encoding='utf-8')
        self.assertEqual(out.report['validation']['acceptance_sha256'], load_acceptance(str(self.acceptance))[2])

    def test_acceptance_inside_repository_is_rejected_before_index(self):
        path = self.repo / 'acceptance.json'
        shutil.copyfile(self.acceptance, path)
        with patch('replens.runner.build_index') as index, self.assertRaises(ValueError):
            run_repair(RepairRequest(str(self.repo), self.issue, acceptance_suite=str(path)))
        index.assert_not_called()

    def test_bad_acceptance_paths_and_commands_are_rejected(self):
        for data in [{'command': 'python -c x', 'files': {}},
                     {'command': COMMAND, 'files': {'../bad.py': 'pass'}},
                     {'command': 'pytest __replens_acceptance__', 'files': {'__replens_acceptance__/test_a.py': 'def test_bad('}}]:
            self.acceptance.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises((ValueError, SyntaxError)):
                load_acceptance(str(self.acceptance))

    def test_cli_strict_writes_report_then_returns_nonzero_without_independent_suite(self):
        args = ['run', str(self.repo), '--issue', self.issue, '--test-cmd', COMMAND,
                '--auto-patch', '--require-verified', '--format', 'json', '--no-provenance',
                '--output', str(self.output)]
        with self.offline(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as code:
                cli_main(args)
        self.assertEqual(code.exception.code, 2)
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['status'], 'UNVERIFIED')
        with self.offline(), contextlib.redirect_stdout(io.StringIO()):
            cli_main(args + ['--acceptance-suite', str(self.acceptance)])
        self.assertEqual(json.loads(self.output.read_text(encoding='utf-8'))['status'], 'VERIFIED')

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest optional dependency unavailable')
    def test_real_pytest_main_and_unittest_independent_suite_are_verified(self):
        with self.offline():
            out = run_repair(RepairRequest(str(self.repo), self.issue,
                             test_cmd='pytest tests/test_parser.py -q', acceptance_suite=str(self.acceptance)))
        self.assertEqual(out.status, 'VERIFIED', out.reasons)
        self.assertEqual(out.report['test_framework'], 'pytest')
        self.assertIn('tests/test_parser.py::ParserTests::test_parse_date_with_whitespace', out.report['validation']['targets'])

    @unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web test dependencies unavailable')
    def test_real_http_api_returns_same_verified_and_failed_status(self):
        from fastapi.testclient import TestClient
        from web.app import app
        client = TestClient(app)
        request = dict(repo=str(self.repo), issue=self.issue, test_cmd=COMMAND,
                       auto_patch=True, no_provenance=True, acceptance_suite=str(self.acceptance))
        with self.offline():
            response = client.post('/api/run', json=request)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['status'], 'VERIFIED')
        self.assertEqual(body['status'], body['report']['status'])
        self.assertIn('VERIFIED', body['markdown'])
        data = json.loads(self.acceptance.read_text(encoding='utf-8'))
        key = next(iter(data['files']))
        data['files'][key] += '\nclass Fail(unittest.TestCase):\n def test_fail(self): self.fail()\n'
        self.acceptance.write_text(json.dumps(data), encoding='utf-8')
        with self.offline():
            body = client.post('/api/run', json=request).json()
        self.assertEqual(body['status'], 'FAILED')
        self.assertEqual(body['status'], body['report']['status'])
        self.assertNotIn('独立验收通过', body['markdown'])


if __name__ == '__main__':
    unittest.main()
