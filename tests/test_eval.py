"""Scoring counterexamples, common cutoffs, and actual localization integration."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from replens.eval_metrics import (file_scores, hit_symbols, load_dataset,
                                  parse_expected, score_predictions, validate_dataset)
from replens.eval_runner import run_eval
from replens.indexer import Symbol
from replens.retriever import Evidence

ROOT = Path(__file__).resolve().parents[1]


def module(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT/rel)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


baselines = module('test_baselines_module', 'eval/baselines.py')
ablation = module('test_ablation_module', 'eval/ablation.py')


def symbol(path, name='read', line=10, kind='function'):
    return Symbol(path, kind, name, line, line+3)


def item(expected, key=1):
    return dict(id=key, issue='read value', expected=expected)


class MetricCounterexamples(unittest.TestCase):
    def test_file_duplicates_do_not_occupy_ranks(self):
        scores = file_scores(['a.py', 'a.py', 'a.py', 'b.py', 'c.py'], {'b.py'}, 2)
        self.assertEqual(scores, dict(hit1=0, hitk=1, rr=0.5, ranked=['a.py', 'b.py']))

    def test_all_mrr_is_truncated_at_same_k(self):
        self.assertEqual(file_scores(['a.py', 'b.py', 'c.py'], {'c.py'}, 2)['rr'], 0)
        samples = [item(['c.py'])]
        m = baselines._aggregate(samples, [[['a.py', 'b.py', 'c.py']]], 2)
        r = score_predictions(samples, [([symbol(p) for p in ['a.py', 'b.py', 'c.py']], False)], 2)
        self.assertEqual(m['mrr@2'], 0)
        self.assertEqual(m['mrr@2'], r['mrr@2'])

    def test_multiple_targets_cannot_form_a_cartesian_match(self):
        targets = [parse_expected('a.py:read:10'), parse_expected('b.py:write:30')]
        self.assertFalse(hit_symbols([symbol('a.py', 'write', 30)], targets, 'func'))
        self.assertFalse(hit_symbols([symbol('a.py', 'write', 30)], targets, 'line'))
        self.assertTrue(hit_symbols([symbol('b.py', 'write', 32)], targets, 'line'))

    def test_line_match_requires_same_function_not_just_nearby_line(self):
        self.assertFalse(hit_symbols([symbol('a.py', 'wrong', 10)], [('a.py', 'right', 10)], 'line'))
        self.assertFalse(hit_symbols([symbol('a.py', 'right', 10, 'class')], [('a.py', 'right', 10)], 'func'))

    def test_denominators_depend_on_available_supported_annotations(self):
        samples = [item(['a.py'], 1), item(['b.py:read'], 2), item(['c.py:read:10'], 3),
                   item(['d.py:Parser.read:10'], 4), item([], 5)]
        predictions = [([symbol(p)], False) for p in ['a.py', 'b.py', 'c.py', 'd.py']] + [([], True)]
        r = score_predictions(samples, predictions, 3)
        self.assertEqual((r['total_positive'], r['total_func_eval'], r['total_line_eval']), (4, 2, 1))
        self.assertEqual(r['total_qualified_unsupported'], 1)
        self.assertEqual(r['func_name@3'], 1)
        self.assertEqual(r['symbol_start@3'], 1)
        self.assertEqual(r['details'][3]['unsupported_qualified_targets'], ['d.py:Parser.read:10'])

    def test_qualified_method_is_not_silently_matched_by_bare_name(self):
        r = score_predictions([item(['a.py:Parser.read:10'])], [([symbol('a.py')], False)])
        self.assertEqual(r['hit@5'], 1)
        self.assertIsNone(r['func_name@5'])
        self.assertIsNone(r['symbol_start@5'])

    def test_dynamic_keys_do_not_mislabel_top_two_as_top_five(self):
        r = score_predictions([item(['a.py:read:10'])], [([symbol('a.py')], False)], 2)
        self.assertEqual(r['hit@2'], 1)
        self.assertIn('func_name@2', r)
        self.assertIn('symbol_start@2', r)
        self.assertIn('mrr@2', r)
        for key in ['hit@5', 'func@5', 'line_acc@5']:
            self.assertNotIn(key, r)

    def test_refusal_does_not_also_get_localization_credit(self):
        r = score_predictions([item(['a.py'])], [([symbol('a.py')], True)], 2)
        self.assertEqual(r['hit@1'], 0)
        self.assertEqual(r['mrr@2'], 0)
        self.assertEqual(r['positive_refusal_rate'], 1)

    def test_noise_only_and_zero_granularity_return_none(self):
        r = score_predictions([item([])], [([], True)], 1)
        self.assertIsNone(r['hit@1'])
        self.assertIsNone(r['mrr@1'])
        self.assertIsNone(r['func_name@1'])
        self.assertEqual(r['refusal_rate'], 1)

    def test_random_and_grep_handle_noise_only_without_division_by_zero(self):
        meta = Mock(python_files=[], document_files=[])
        for result in [baselines.random_baseline(meta, [item([])], top_k=2),
                       baselines.grep_baseline(meta, [item([])], top_k=2)]:
            self.assertIsNone(result['hit@2'])
            self.assertIsNone(result['mrr@2'])

    def test_malformed_datasets_and_annotations_fail_with_value_error(self):
        bad = [None, {}, [], ['wrong'], [{}], [item('a.py')], [item([None])],
               [item(['a.py:read:x'])], [item(['a.py:read:0'])],
               [item(['../a.py'])], [item(['C:/a.py'])], [item(['a\\b.py'])],
               [item(['a.py::1'])], [item(['a.py', 'a.py'])],
               [item(['a.py']), item(['b.py'])],
               [dict(id=True, issue='x', expected=[])], [dict(id=1, issue=' ', expected=[])]]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_dataset(data)

    def test_invalid_k_and_pool_budget_are_rejected(self):
        for k, budget in [(0, 100), (-1, 100), (101, 100), (True, 100), (5, 4), (5, 1001)]:
            with self.subTest(k=k, budget=budget), self.assertRaises(ValueError):
                score_predictions([item([])], [([], True)], k, budget)


class EvaluationIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='replens_metrics_'))
        self.repo = self.tmp/'repo'
        self.repo.mkdir()
        for path in ['a.py', 'b.py', 'c.py']:
            (self.repo/path).write_text('def read():\n    return 1\n', encoding='utf-8')
        (self.repo/'README.md').write_text('read value documentation', encoding='utf-8')
        self.dataset = self.tmp/'labels.json'
        self.dataset.write_text(json.dumps([item(['b.py:read:10'])]), encoding='utf-8')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_eval_baseline_and_ablation_share_file_dedup_and_budget(self):
        from replens.repo import import_repo
        symbols = [symbol('a.py', 'first'), symbol('a.py', 'second'), symbol('b.py')]
        evidence = [Evidence(s, 1, 'fixture', 'keyword_match') for s in symbols]
        samples = load_dataset(self.dataset)
        with patch('replens.eval_runner.retrieve', return_value=(evidence, False)) as retrieve, contextlib.redirect_stdout(io.StringIO()):
            main = run_eval(str(self.repo), str(self.dataset), top_k=2, candidate_limit=100)
            base = baselines.replens_baseline(import_repo(self.repo), samples, top_k=2)
            ab = ablation._metrics(samples, Mock(), 2, 'bm25_callgraph')
        self.assertTrue(all(c.kwargs['top_k'] == 100 for c in retrieve.call_args_list))
        self.assertEqual(main['hit@2'], 1)
        self.assertEqual(main['mrr@2'], 0.5)
        self.assertEqual(main['mrr@2'], base['mrr@2'])
        self.assertEqual(main['mrr@2'], ab['mrr@2'])
        self.assertEqual(main['func_name@2'], 0)  # file rank and symbol rank have different units

    def test_baselines_use_same_python_and_document_universe(self):
        from replens.repo import import_repo
        meta = import_repo(self.repo)
        self.assertEqual(baselines.file_universe(meta), ['README.md', 'a.py', 'b.py', 'c.py'])
        samples = [item(['README.md'])]
        m = baselines.grep_baseline(meta, samples, top_k=1)
        self.assertEqual(m['hit@1'], 1)
        self.assertEqual(baselines.random_baseline(meta, samples), baselines.random_baseline(meta, samples))

    def test_bad_input_fails_before_building_index(self):
        self.dataset.write_text('["invalid"]', encoding='utf-8')
        with patch('replens.eval_runner.build_index') as build, self.assertRaises(ValueError):
            run_eval(str(self.repo), str(self.dataset))
        build.assert_not_called()
        with patch.object(ablation, 'retrieve_predictions') as retrieve, self.assertRaises(ValueError):
            ablation._metrics(['invalid'], Mock(), 2, 'bm25_callgraph')
        retrieve.assert_not_called()

    def test_annotation_file_cannot_be_placed_inside_retrieval_root(self):
        inside = self.repo/'labels.json'
        shutil.copyfile(self.dataset, inside)
        with patch('replens.eval_runner.build_index') as build, self.assertRaises(ValueError):
            run_eval(str(self.repo), str(inside))
        build.assert_not_called()

    def test_index_is_closed_on_retrieval_failure(self):
        db = Mock()
        with patch('replens.eval_runner.build_index', return_value=db), \
             patch('replens.eval_runner.retrieve', side_effect=RuntimeError('retrieval failed')):
            with self.assertRaises(RuntimeError):
                run_eval(str(self.repo), str(self.dataset))
        db.close.assert_called_once()

    def test_real_demo_runs_at_multiple_k_and_matches_baseline(self):
        repo = self.tmp/'demo'
        shutil.copytree(ROOT/'examples/demo_repo', repo, ignore=shutil.ignore_patterns('.replens', '__pycache__'))
        for k in [1, 2, 5]:
            with self.subTest(k=k), contextlib.redirect_stdout(io.StringIO()):
                main = run_eval(str(repo), str(ROOT/'eval/datasets/dataset.json'), top_k=k)
                base = baselines.run_baselines(str(repo), str(ROOT/'eval/datasets/dataset.json'), top_k=k)
            bm25 = dict(base['rows'])['RepoLens BM25']
            self.assertEqual(main[f'hit@{k}'], bm25[f'hit@{k}'])
            self.assertEqual(main[f'mrr@{k}'], bm25[f'mrr@{k}'])
            self.assertEqual(main['input_audit']['mode'], 'repository_audit_only')
            self.assertTrue(main['input_audit']['indexed_files'])

    def test_cli_bad_input_has_clear_exit_code_without_traceback(self):
        from replens.cli import main
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit) as code:
            main(['eval', str(self.repo), '--dataset', str(self.dataset), '--top-k', '0'])
        self.assertEqual(code.exception.code, 2)
        self.assertIn('评测输入错误', out.getvalue())
        self.assertNotIn('Traceback', out.getvalue())

    @unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'), 'Web dependencies unavailable')
    def test_real_web_eval_dynamic_metrics_and_invalid_input(self):
        from fastapi.testclient import TestClient
        from web.app import app
        client = TestClient(app)
        with contextlib.redirect_stdout(io.StringIO()):
            response = client.post('/api/eval', json=dict(repo=str(self.repo), dataset=str(self.dataset), top_k=2))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('mrr@2', response.json())
        self.assertNotIn('hit@5', response.json())
        response = client.post('/api/eval', json=dict(repo=str(self.repo), dataset=str(self.dataset), top_k=0))
        self.assertEqual(response.status_code, 400)
        self.assertIn('top_k', response.json()['error'])


if __name__ == '__main__':
    unittest.main()
