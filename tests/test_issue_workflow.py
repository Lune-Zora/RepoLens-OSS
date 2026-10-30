"""Isolated synthetic unit/integration fixtures; not model performance evidence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from replens.executor import TestResult as ExecutionResult
from replens.indexer import build_index
from replens.issue_understanding import understand_and_retrieve
from replens.model_settings import selected_model,using_model
from replens.repo import import_repo
from replens.task_tests import compile_cases,scope_violation
from replens.validation import assess_primary


class IssueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='replens_task_unit_')
        self.root=Path(self.temp.name);self.repo=self.root/'repo';self.repo.mkdir()
        self.source=('def choose(text):\n    return text.lower() == "yes"\n\n'
                     'def count(items):\n    return len(items)\n')
        (self.repo/'api.py').write_text(self.source,encoding='utf8')
        (self.repo/'tests').mkdir()
        (self.repo/'tests/test_public.py').write_text('import unittest\nimport api\n'
            'class Public(unittest.TestCase):\n def test_existing(self): self.assertFalse(api.choose("no"))\n',encoding='utf8')
        self.db=build_index(import_repo(self.repo))
        self.client=Mock(model='isolated-unit-fixture')
        self.client.chat_json.return_value={'summary':'带空格的输入应保持原有语义','expected_behavior':'ignore surrounding whitespace',
            'scope':'matched','same_feature_refs':['api.py:1'],'explanation':'函数choose处理该输入','target_refs':['api.py:1'],'search_query':'choose text whitespace'}

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def evidence(self):
        with patch('replens.issue_understanding.model_client',return_value=self.client):
            return understand_and_retrieve('带空格也应该识别',self.db)

    def test_grounded_target_and_explanation_retain_original_chinese(self):
        evidence,insufficient,info=self.evidence()
        self.assertFalse(insufficient)
        self.assertEqual(evidence[0].symbol.name,'choose')
        self.assertEqual(info.original_issue,'带空格也应该识别')
        self.assertEqual(info.targets[0]['path'],'api.py')
        self.assertEqual(info.generated_by,'llm')

    def test_unknown_model_reference_is_reported_instead_of_becoming_evidence(self):
        self.client.chat_json.return_value['target_refs']=['missing.py:1']
        ev,insufficient,info=self.evidence()
        self.assertTrue(insufficient);self.assertEqual(ev,[]);self.assertIn('不存在',info.error)

    def test_absent_feature_returns_explanation_and_actual_catalog(self):
        self.client.chat_json.return_value.update(scope='not_found',target_refs=[],explanation='仓库没有日期功能')
        ev,insufficient,info=self.evidence()
        self.assertTrue(insufficient);self.assertIn('没有日期',info.explanation)
        self.assertIn('api.py:choose',info.repository_functions)

    def test_domain_mismatch_is_rejected_even_when_a_real_function_was_selected(self):
        self.client.chat_json.return_value['same_feature_refs']=[]
        ev,insufficient,info=self.evidence()
        self.assertTrue(insufficient)
        self.assertEqual(ev,[])
        self.assertIn('不匹配',info.explanation)

    def test_return_contract_mismatch_cannot_be_overridden_by_model_routing(self):
        (self.repo/'api.py').write_text('def choose(text) -> str:\n    return text\n',encoding='utf8')
        (self.repo/'tests/test_public.py').write_text('import unittest\nimport api\nclass T(unittest.TestCase):\n def test_value(self): self.assertEqual(api.choose("no"),"no")\n',encoding='utf8')
        refreshed=build_index(import_repo(self.repo),force=True);refreshed.close()
        self.client.chat_json.return_value['return_kind']='boolean'
        ev,insufficient,info=self.evidence()
        self.assertTrue(insufficient)
        self.assertEqual(ev,[])
        self.assertEqual(info.return_contracts['api.py:1'],['string'])
        self.assertIn('公开测试',info.explanation)

    def test_public_constructor_expectation_provides_an_object_contract(self):
        from replens.contracts import output_kinds
        from replens.indexer import Symbol
        (self.repo/'api.py').write_text('def parse(value):\n    return None\n',encoding='utf8')
        (self.repo/'tests/test_public.py').write_text('import unittest,datetime\nfrom api import parse\nclass T(unittest.TestCase):\n def test_date(self): self.assertEqual(parse("2024-01-01"),datetime.date(2024,1,1))\n',encoding='utf8')
        self.assertEqual(output_kinds(self.repo,Symbol('api.py','function','parse',1,2)),{'object'})

    def test_scope_guard_rejects_collateral_change_in_the_same_file(self):
        ev,_,_=self.evidence();pf=self.root/'candidate.json'
        pf.write_text(json.dumps({'api.py':{'from':self.source,'to':self.source.replace('text.lower()','text.strip().lower()').replace('len(items)','0')}}),encoding='utf8')
        self.assertIn('其他函数',scope_violation(self.repo,pf,ev))
        pf.write_text(json.dumps({'api.py':{'from':'text.lower()','to':'text.strip().lower()'}}),encoding='utf8')
        self.assertEqual(scope_violation(self.repo,pf,ev),'')

    def test_generated_reports_cannot_change_index_inputs(self):
        report=self.repo/'reports/compliance';report.mkdir(parents=True)
        (report/'text.md').write_text('choose yes text '*100,encoding='utf8')
        self.assertFalse(any(p.startswith('reports/') for p in import_repo(self.repo).document_files))

    def test_index_content_change_detected_even_with_same_size_and_mtime(self):
        src=self.repo/'api.py';stat=src.stat();src.write_text(self.source.replace('== "yes"','!= "yes"'),encoding='utf8')
        os.utime(src,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        fresh=build_index(import_repo(self.repo))
        self.assertIn('!= "yes"',fresh.conn.execute("SELECT code FROM symbols WHERE name='choose'").fetchone()[0]);fresh.close()

    def test_per_request_model_is_restored(self):
        before=selected_model()
        with using_model('one'):
            self.assertEqual(selected_model(),'one')
            with using_model('two'):self.assertEqual(selected_model(),'two')
            self.assertEqual(selected_model(),'one')
        self.assertEqual(selected_model(),before)

    def test_task_cases_reproduce_then_replay_without_mutating_repo(self):
        from replens.runner import RepairRequest,run_repair
        from replens.planner import RulePlanner
        from replens.patchgen import LLMPatchGenerator
        from replens.provenance import build_provenance,verify_provenance
        task_client=Mock(model='isolated-unit-fixture')
        target_cases=[
            {'path':'api.py','symbol':'choose','args':[' yes '],'expected':True,'intent':'issue'},
            {'path':'api.py','symbol':'choose','args':['YES'],'expected':True,'intent':'preserve'},
            {'path':'api.py','symbol':'choose','args':['no'],'expected':False,'intent':'preserve'}]
        state={'round':0,'cases':target_cases}
        task_client.purpose='task_tests'
        def answer(*args,**kwargs):
            if task_client.purpose=='test_input_refinement':
                return {'inputs':[{'path':'api.py','symbol':'choose','args':[' yes '],'kwargs':{}}]*3}
            if task_client.purpose=='task_tests':
                state['round']+=1
                state['cases'] = ([dict(target_cases[0],args=[value],expected=expected)
                                   for value,expected in [('yes',True),('no',False),('YES',True)]]
                                  if getattr(self,'refine',False) and state['round']==1 else target_cases)
                return {'cases':state['cases']}
            return {'approved':True,'cases':state['cases'],'changes':[]}
        task_client.chat_json.side_effect=answer
        patch_client=Mock();patch_client.chat_json.return_value={'api.py':{'from':'text.lower()','to':'text.strip().lower()'}}
        gen=LLMPatchGenerator(patch_client,n_candidates=1)
        independent=self.root/'acceptance.json'
        independent.write_text(json.dumps({'command':'python -m unittest discover -s __replens_acceptance__',
            'files':{'__replens_acceptance__/test_behavior.py':'import unittest\nimport api\nclass Hidden(unittest.TestCase):\n def test_tabs(self): self.assertTrue(api.choose("\\tyes\\n"))\n def test_no(self): self.assertFalse(api.choose("no"))\n'}}),encoding='utf8')
        with patch('replens.issue_understanding.model_client',return_value=self.client), \
             patch('replens.task_tests.model_client',return_value=task_client), \
             patch('replens.runner.build_planner',return_value=RulePlanner()), \
             patch('replens.patchgen.build_patch_generator',return_value=gen), \
             patch.dict(os.environ,{'REPLENS_REQUIRE_LLM':'1'}):
            result=run_repair(RepairRequest(str(self.repo),'允许首尾空白',test_cmd='python -m unittest discover -s tests',
                             acceptance_suite=str(independent),
                             behavior_examples=[{"args":[" yes "],"expected":True}] if getattr(self,"user_examples",False) else []),build_provenance=build_provenance)
        self.assertEqual(result.status,'VERIFIED',result.report)
        self.assertTrue(result.report['task_tests']['frozen_before_patch'])
        self.assertEqual((self.repo/'api.py').read_text(encoding='utf8'),self.source)
        replay=verify_provenance(str(self.repo),result.provenance_dir)
        self.assertEqual(replay['replay_status'],'PASS',replay)
        self.assertEqual(replay['repair_status'],'VERIFIED')
        self.assertNotIn('Hidden',str(patch_client.chat_json.call_args))
        if getattr(self,'user_examples',False):
            task_client.chat_json.assert_not_called()
            self.assertEqual(result.report['task_tests']['kind'],'user_behavioral_examples')
            self.assertEqual(result.report['task_tests']['cases'][0]['expected'],True)
        if getattr(self,'refine',False):
            self.assertEqual(len(result.report['task_tests']['design_attempts']),2)
            self.assertEqual(result.report['task_tests']['retained_passing_cases'],3)
            self.assertEqual(len(result.report['task_tests']['cases']),6)

    def test_explicit_behavior_example_is_not_rewritten_by_the_model(self):
        self.user_examples=True
        self.test_task_cases_reproduce_then_replay_without_mutating_repo()

    def test_unreproduced_issue_refines_inputs_and_keeps_passing_checks(self):
        self.refine=True
        self.test_task_cases_reproduce_then_replay_without_mutating_repo()


class CurrentIssueAcceptanceTests(unittest.TestCase):
    def result(self,tests):
        return [ExecutionResult('python -m pytest tests __replens_task__/test_current_issue.py',
            int(any(s!='PASS' for s in tests.values())),tests=tests,structured=True,valid=True,
            framework='pytest',passed=all(s=='PASS' for s in tests.values()))]

    def test_fixing_unrelated_failure_cannot_satisfy_current_issue(self):
        task='__replens_task__/test_current_issue.py::TaskTests::test_issue_00'
        before=self.result({task:'PASS','tests/test_public.py::test_other':'FAIL'})
        after=self.result({task:'PASS','tests/test_public.py::test_other':'PASS'})
        self.assertEqual(assess_primary(before,after)['status'],'UNVERIFIED')

    def test_collateral_regression_blocks_acceptance(self):
        task='__replens_task__/test_current_issue.py::TaskTests::test_issue_00'
        before=self.result({task:'FAIL','tests/test_public.py::test_other':'PASS'})
        after=self.result({task:'PASS','tests/test_public.py::test_other':'ERROR'})
        self.assertEqual(assess_primary(before,after)['status'],'FAILED')

    def test_existing_unrelated_failure_is_disclosed_and_must_not_worsen(self):
        task='__replens_task__/test_current_issue.py::TaskTests::test_issue_00';other='tests/test_public.py::test_other'
        before=self.result({task:'FAIL',other:'FAIL'})
        accepted=assess_primary(before,self.result({task:'PASS',other:'FAIL'}))
        self.assertEqual(accepted['status'],'PRIMARY_PASSED')
        self.assertEqual(accepted['preexisting_failures'],[other])
        self.assertEqual(assess_primary(before,self.result({task:'PASS',other:'ERROR'}))['status'],'FAILED')
