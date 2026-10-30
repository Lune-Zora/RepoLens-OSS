"""Real material packaging and failures that field-name blacklists cannot detect."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from replens.eval_inputs import evaluation_inputs, pack_inputs, validate_manifest
from replens.eval_runner import run_eval

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT = object()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InputPackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='replens_input_policy_'))
        self.visible = self.tmp/'visible'
        self.visible.mkdir()
        (self.visible/'api.py').write_text('def read_value(value):\n    """Read numeric value."""\n    return int(value)\n', encoding='utf-8')
        (self.visible/'README.md').write_text('numeric value usage', encoding='utf-8')
        (self.visible/'failure.log').write_text('ValueError: numeric value\n', encoding='utf-8')
        (self.tmp/'control').mkdir()
        self.annotation = self.tmp/'control/labels.json'
        self.issue = 'read numeric value'
        self.annotation.write_text(json.dumps([dict(id='bug-1', issue=self.issue, expected=['api.py:read_value:1'])]), encoding='utf-8')
        self.gold = self.tmp/'control/gold.patch'
        self.gold.write_text('CONTROL_ONLY_SECRET_GOLD_DIFF\n', encoding='utf-8')
        self.path = self.tmp/'manifest.json'
        self.sample = dict(
            sample_id='bug-1', repository='https://example.test/project', source='upstream git',
            license='MIT', buggy_commit='a'*40, fix_commit='b'*40, issue_source='manual fixture', issue=self.issue,
            environment=dict(os='portable', python='3.9+', dependencies=[], install_command='none'),
            regression_test={'status': 'pending'}, before_gold_validation={'status': 'pending'},
            independent_test_result={'status': 'pending'},
            model_input_manifest=dict(schema_version=1, root='visible', files=[
                dict(path='api.py', kind='source', sha256=digest(self.visible/'api.py')),
                dict(path='README.md', kind='document', sha256=digest(self.visible/'README.md')),
                dict(path='failure.log', kind='failure_log', sha256=digest(self.visible/'failure.log'))]),
            reference_materials=[dict(path='control/gold.patch', role='reference_patch', sha256=digest(self.gold)),
                                 dict(path='control/labels.json', role='annotation', sha256=digest(self.annotation))])
        self.save()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def save(self, data=_DEFAULT):
        self.path.write_text(json.dumps([self.sample] if data is _DEFAULT else data, ensure_ascii=False), encoding='utf-8')

    def invalid(self):
        receipts, errors = validate_manifest(self.path)
        self.assertTrue(errors, receipts)
        return '\n'.join(errors)

    def add_input(self, rel, code, kind='document'):
        p = self.visible/rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding='utf-8')
        self.sample['model_input_manifest']['files'].append(dict(path=rel, kind=kind, sha256=digest(p)))
        self.save()

    def test_valid_top_list_and_object_are_audited(self):
        for data in [[self.sample], {'samples': [self.sample]}]:
            self.save(data)
            receipts, errors = validate_manifest(self.path)
            self.assertEqual(errors, [])
            self.assertEqual(len(receipts[0]['files']), 3)
            self.assertEqual(receipts[0]['issue_sha256'], hashlib.sha256(self.issue.encode()).hexdigest())
            self.assertIn('不证明', receipts[0]['scope'])

    def test_malformed_samples_are_errors_not_attribute_errors(self):
        for data in [[], {}, None, 42, ['bad'], [False], {'samples': ['bad']}, {'samples': 'bad'},
                     {'samples': [self.sample], 'extra': True}]:
            with self.subTest(data=data):
                self.save(data)
                self.invalid()

    def test_missing_and_wrong_typed_required_metadata_are_rejected(self):
        original = copy.deepcopy(self.sample)
        mutations = [('environment', []), ('regression_test', []), ('independent_test_result', ''),
                     ('before_gold_validation', {}), ('buggy_commit', 'short'),
                     ('reference_materials', {}), ('issue', 1)]
        for key, value in mutations:
            with self.subTest(key=key):
                self.sample = dict(original, **{key: value})
                self.save()
                self.invalid()

    def test_unknown_model_input_fields_cannot_carry_nested_or_bare_answers(self):
        original = copy.deepcopy(self.sample)
        for key, value in [('expected', ['api.py']), ('gold_patch', 'fix'),
                           ('nested', {'expected_solution': 'fix'}), ('files', ['control/gold.patch'])]:
            with self.subTest(key=key):
                self.sample = copy.deepcopy(original)
                self.sample['model_input_manifest'][key] = value
                self.save()
                self.invalid()

    def test_undeclared_files_including_hidden_files_cannot_enter_inputs(self):
        for rel in ['answers.txt', '.secret', 'nested/answer.py']:
            with self.subTest(rel=rel):
                p = self.visible/rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('secret', encoding='utf-8')
                error = self.invalid()
                self.assertIn('未声明', error)
                p.unlink()

    def test_hash_drift_and_missing_file_are_rejected(self):
        (self.visible/'api.py').write_text('changed', encoding='utf-8')
        self.assertIn('哈希', self.invalid())
        (self.visible/'api.py').unlink()
        self.invalid()

    def test_absolute_traversal_and_windows_separator_paths_are_rejected(self):
        original = copy.deepcopy(self.sample)
        for rel in ['../control/gold.patch', '/tmp/input.py', 'C:/input.py', 'a\\b.py', './api.py']:
            with self.subTest(rel=rel):
                self.sample = copy.deepcopy(original)
                self.sample['model_input_manifest']['files'][0]['path'] = rel
                self.save()
                self.invalid()

    def test_unrecognized_kinds_and_unknown_entry_fields_are_rejected(self):
        original = copy.deepcopy(self.sample)
        for changes in [{'kind': 'answer'}, {'kind': 'document'}, {'expected': 'fix'}, {'sha256': '0'*64}]:
            with self.subTest(changes=changes):
                self.sample = copy.deepcopy(original)
                self.sample['model_input_manifest']['files'][0].update(changes)
                self.save()
                self.invalid()

    def test_duplicate_casefold_paths_are_rejected(self):
        self.sample['model_input_manifest']['files'].append(dict(self.sample['model_input_manifest']['files'][0]))
        self.save()
        self.assertIn('重复', self.invalid())

    def test_hardlink_to_material_is_rejected(self):
        link = self.tmp/'linked.py'
        os.link(self.visible/'api.py', link)
        self.assertIn('硬链接', self.invalid())

    def test_references_must_exist_be_hashed_and_be_outside_input_root(self):
        original = copy.deepcopy(self.sample)
        for entry in [dict(path='not_there.patch', role='reference_patch', sha256='0'*64),
                      dict(path='visible/api.py', role='reference_patch', sha256=digest(self.visible/'api.py')),
                      dict(path='control/gold.patch', role='reference_patch', sha256='0'*64)]:
            with self.subTest(entry=entry):
                self.sample = copy.deepcopy(original)
                self.sample['reference_materials'][0] = entry
                self.save()
                self.invalid()

    def test_reference_renamed_as_allowed_text_is_still_rejected_by_bytes(self):
        self.add_input('harmless.md', self.gold.read_text(encoding='utf-8'))
        self.assertIn('字节', self.invalid())

    def test_manifest_cannot_live_under_input_root(self):
        path = self.visible/'manifest.json'
        self.sample['model_input_manifest']['root'] = str(self.visible)
        path.write_text(json.dumps([self.sample]), encoding='utf-8')
        receipts, errors = validate_manifest(path)
        self.assertTrue(errors)
        self.assertIn('之外', errors[0])

    def test_duplicate_sample_and_same_fix_are_not_counted_as_independent_bugs(self):
        for second in [self.sample, dict(self.sample, sample_id='bug-2')]:
            self.save([self.sample, second])
            self.assertIn('重复', self.invalid())

    def test_pack_copies_exact_allowed_bytes_and_keeps_controller_outside_repo(self):
        receipt = pack_inputs(self.path, 'bug-1', self.tmp/'packed')
        package = Path(receipt['package_root'])
        actual = {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()}
        self.assertEqual(actual, {'api.py', 'README.md', 'failure.log'})
        self.assertTrue((package.parent/'issue.txt').is_file())
        self.assertTrue((package.parent/'input_receipt.json').is_file())
        for entry in receipt['packed_files']:
            self.assertEqual(digest(package/entry['path']), entry['sha256'])
        self.assertNotIn('CONTROL_ONLY_SECRET', ''.join(p.read_text(encoding='utf-8') for p in package.rglob('*') if p.is_file()))

    def test_pack_refuses_existing_output_and_does_not_modify_input(self):
        before = digest(self.visible/'api.py')
        with self.assertRaises(ValueError):
            pack_inputs(self.path, 'bug-1', self.visible)
        self.assertEqual(digest(self.visible/'api.py'), before)
        with self.assertRaises(ValueError):
            pack_inputs(self.path, 'bug-1', self.visible/'new_package')

    def test_isolated_index_does_not_include_control_material_or_failure_log(self):
        from replens.indexer import build_index
        with evaluation_inputs(self.visible, self.annotation, str(self.path), 'bug-1') as (meta, receipt):
            package = Path(meta.path)
            self.assertEqual(meta.python_files, ['api.py'])
            self.assertEqual(meta.document_files, ['README.md'])
            self.assertFalse(receipt['failure_logs_used'])
            db = build_index(meta, force=True)
            try:
                paths = {row['path'] for row in db.conn.execute('SELECT DISTINCT path FROM symbols')}
                self.assertEqual(paths, {'api.py', 'README.md'})
                codes = '\n'.join(row['code'] for row in db.conn.execute('SELECT code FROM symbols'))
                self.assertNotIn('CONTROL_ONLY_SECRET', codes)
            finally:
                db.close()
        self.assertFalse(package.exists())
        self.assertFalse((self.visible/'.replens').exists())

    def test_eval_and_all_baselines_use_isolated_package_with_audit(self):
        from test_eval import baselines, ablation
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_eval(str(self.visible), str(self.annotation), top_k=2,
                              input_manifest=str(self.path), sample_id='bug-1')
            base = baselines.run_baselines(str(self.visible), str(self.annotation), top_k=2,
                                           input_manifest=str(self.path), sample_id='bug-1')
            ab = ablation.run_ablation(str(self.visible), str(self.annotation), top_k=2,
                                        input_manifest=str(self.path), sample_id='bug-1')
        self.assertEqual(result['input_audit']['mode'], 'isolated_allowlist')
        self.assertEqual(result['input_audit']['query_sha256'], result['input_audit']['issue_sha256'])
        self.assertEqual(result['input_audit']['manifest_sha256'], base['input_audit']['manifest_sha256'])
        self.assertEqual(result['input_audit']['manifest_sha256'], ab['input_audit']['manifest_sha256'])
        self.assertEqual(result['mrr@2'], dict(base['rows'])['RepoLens BM25']['mrr@2'])
        self.assertEqual(result['mrr@2'], dict(ab['rows'])['bm25_callgraph']['mrr@2'])

    def test_sample_id_issue_and_repo_must_match_frozen_material(self):
        with self.assertRaises(ValueError):
            with evaluation_inputs(self.tmp, self.annotation, str(self.path), 'bug-1'):
                pass
        with self.assertRaises(ValueError):
            with evaluation_inputs(self.visible, self.annotation, str(self.path), 'wrong-id'):
                pass
        self.issue = 'a different query'
        self.sample['issue'] = self.issue
        self.save()
        with self.assertRaises(ValueError):
            with evaluation_inputs(self.visible, self.annotation, str(self.path), 'bug-1'):
                pass

    def test_missing_manifest_or_corrupt_json_are_normal_validation_errors(self):
        _, errors = validate_manifest(self.tmp/'missing.json')
        self.assertTrue(errors)
        self.path.write_text('{broken', encoding='utf-8')
        self.invalid()

    def test_cli_validation_and_packaging_have_honest_success_text(self):
        spec = importlib.util.spec_from_file_location('manifest_cli', ROOT/'scripts/validate_sample_manifest.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main([str(self.path), '--pack-inputs', str(self.tmp/'cli_package'),
                             '--sample-id', 'bug-1', '--audit-output', str(self.tmp/'audit.json')])
        self.assertEqual(code, 0)
        self.assertIn('不代表行为验收通过', out.getvalue())
        self.assertTrue((self.tmp/'audit.json').is_file())
        for protected in [self.path, self.gold, self.annotation]:
            with self.subTest(protected=protected):
                before = protected.read_bytes()
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    cli.main([str(self.path), '--audit-output', str(protected)])
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(protected.read_bytes(), before)
        self.save(['bad_sample'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([str(self.path)]), 1)


if __name__ == '__main__':
    unittest.main()
