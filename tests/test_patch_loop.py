"""Patch-loop failure contracts, candidate choice, feedback, and real reporting."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from replens.executor import TestResult as ExecutionResult
from replens.indexer import Symbol
from replens.patchgen import (
    AttemptRecord, LLMPatchGenerator, RulePatchGenerator,
    _pick_best_failed, auto_patch_loop,
)
from replens.planner import RulePlanner
from replens.repo import RepoMeta
from replens.retriever import Evidence
from replens.runner import RepairRequest, run_repair


COMMAND = "python -m unittest discover -s tests"


def _result(failed=0, errors=0, valid=True, label="", passed=None):
    passed = failed + errors == 0 if passed is None else passed
    return ExecutionResult(
        COMMAND, 0 if passed else 1, stderr=label, passed=passed, valid=valid,
        summary={"passed": int(passed), "failed": failed, "errors": errors,
                 "skipped": 0, "collected": 0, "ran": max(1, failed + errors)},
        tests=dict({'suite.Case.test_preserved': 'PASS'}, **{
            'suite.Case.test_target': 'PASS' if passed else 'FAIL'}),
        structured=True, framework='unittest',
    )


def failed_attempt(name, after, before=None, note="补丁应用成功"):
    return AttemptRecord(name, name, note, name,
                         [_result(failed=1)] if before is None else before,
                         after, False, "still_failing")


class FailedCandidateSelectionTests(unittest.TestCase):
    def test_uses_test_failures_and_errors_instead_of_suite_count(self):
        many = failed_attempt("many", [_result(failed=15, errors=5)])
        few = failed_attempt("few", [_result(failed=1)])
        self.assertIs(_pick_best_failed([many, few]), few)

    def test_patch_apply_failure_never_beats_executed_valid_candidate(self):
        failed_apply = failed_attempt("apply_error", None, note="补丁应用失败")
        executed = failed_attempt("executed", [_result(failed=20)])
        self.assertIs(_pick_best_failed([failed_apply, executed]), executed)

    def test_invalid_execution_never_beats_valid_failure(self):
        invalid = failed_attempt("invalid", [_result(valid=False)])
        executed = failed_attempt("executed", [_result(failed=20)])
        self.assertIs(_pick_best_failed([invalid, executed]), executed)

    def test_missing_failure_counts_are_not_treated_as_zero(self):
        unknown = failed_attempt("unknown", [_result(passed=False)])
        known = failed_attempt("known", [_result(failed=1)])
        self.assertIs(_pick_best_failed([unknown, known]), known)

    def test_equal_summaries_do_not_depend_on_python_object_identity(self):
        pass_before, pass_after = _result(), _result()
        first = failed_attempt("first", [pass_after, _result(failed=1)],
                               before=[pass_before, _result(failed=1)])
        shared = _result()
        second = failed_attempt("second", [shared, _result(failed=1)],
                                before=[shared, _result(failed=1)])
        self.assertIs(_pick_best_failed([first, second]), first)

    def test_invalid_baseline_does_not_win_on_lower_after_failure_count(self):
        invalid = failed_attempt("invalid_before", [_result(failed=1)],
                                 before=[_result(valid=False)])
        valid = failed_attempt("valid", [_result(failed=5)])
        self.assertIs(_pick_best_failed([invalid, valid]), valid)


class PatchLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="replens_loop_test_"))
        self.meta = RepoMeta(path=str(self.tmp))
        self.evidence = [Evidence(Symbol("api.py", "function", "read_value", 1, 2,
                                          code="def read_value(value):\n    return int(value)"),
                                  1.0, "test evidence", "keyword_match")]
        self.paths = []
        for name in ("FIRST_PATCH", "SECOND_PATCH", "RETRY_PATCH", "RULE_PATCH"):
            p = self.tmp / (name + ".json")
            p.write_text(json.dumps({"candidate": name}), encoding="utf-8")
            self.paths.append(str(p))
        self.baseline = [_result(failed=1, label="BASELINE_FAILURE")]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def llm(self, *rounds):
        gen = LLMPatchGenerator(None)
        gen.generate_candidates = Mock(side_effect=rounds)
        return gen

    def call_loop(self, gen, suite, rounds=1, strict=True):
        with patch("replens.patchgen.build_patch_generator", return_value=gen), \
             patch("replens.executor.run_test_suite", side_effect=suite) as run, \
             patch.dict(os.environ, {"REPLENS_REQUIRE_LLM": "1" if strict else "0"}):
            ret = auto_patch_loop(self.meta, "read value", self.evidence, COMMAND,
                                  max_repair_rounds=rounds)
        return ret, run

    def suite(self, failures, invalid=()):
        def run(repo, command, **kwargs):
            pf = kwargs.get("patch_file")
            if pf is None:
                return self.baseline, None, ""
            if failures[pf] is None:
                return self.baseline, None, "补丁应用失败：from 串不存在"
            return (self.baseline,
                    [_result(failed=failures[pf], valid=pf not in invalid,
                                 label=Path(pf).stem + "_FAILURE")],
                    "补丁应用成功")
        return run

    def test_no_evidence_returns_baseline_without_building_generator(self):
        with patch("replens.patchgen.build_patch_generator") as build, \
             patch("replens.executor.run_test_suite", return_value=(self.baseline, None, "")) as run:
            ret = auto_patch_loop(self.meta, "unrelated", [], COMMAND)
        pf, note, before, after = ret
        self.assertIsNone(pf)
        self.assertIn("证据", note)
        self.assertIs(before, self.baseline)
        self.assertIsNone(after)
        build.assert_not_called()
        run.assert_called_once()

    def test_no_rule_candidate_returns_baseline_and_preserves_reason(self):
        gen = RulePatchGenerator()
        gen.generate = Mock(return_value=(None, "没有匹配的规则"))
        ret, run = self.call_loop(gen, self.suite({}), strict=False)
        pf, note, before, after = ret
        self.assertIsNone(pf)
        self.assertIn("没有匹配的规则", note)
        self.assertIs(before, self.baseline)
        self.assertIsNone(after)
        gen.generate.assert_called_once()
        run.assert_called_once()

    def test_llm_with_no_candidates_keeps_four_item_contract(self):
        gen = self.llm([], [])
        ret, run = self.call_loop(gen, self.suite({}))
        pf, note, before, after = ret
        self.assertIsNone(pf)
        self.assertIn("0", note)
        self.assertIs(before, self.baseline)
        self.assertIsNone(after)
        run.assert_called_once()

    def test_all_failed_candidates_return_baseline_without_unaccepted_after(self):
        p1, p2 = self.paths[:2]
        gen = self.llm([(p1, "first"), (p2, "second")], [])
        ret, run = self.call_loop(gen, self.suite({p1: 20, p2: 1}))
        pf, note, before, after = ret
        self.assertIsNone(pf)
        self.assertIn("未选出", note)
        self.assertIs(before, self.baseline)
        self.assertIsNone(after)
        self.assertEqual(run.call_count, 2)

    def test_feedback_uses_best_candidates_failure_and_its_patch(self):
        p1, p2, retry = self.paths[:3]
        gen = self.llm([(p1, "first"), (p2, "second")], [(retry, "retry")])
        ret, _ = self.call_loop(gen, self.suite({p1: 20, p2: 1, retry: 0}))
        feedback = gen.generate_candidates.call_args_list[1].kwargs["feedback"]
        self.assertIn("SECOND_PATCH_FAILURE", feedback)
        self.assertIn('"SECOND_PATCH"', feedback)
        self.assertNotIn("FIRST_PATCH", feedback)
        self.assertEqual(ret[0], retry)
        self.assertTrue(ret[3][0].passed)

    def test_failed_rule_fallback_does_not_overwrite_better_llm_feedback(self):
        p1, _, _, rule = self.paths
        gen = self.llm([(p1, "first")], [])
        with patch.object(RulePatchGenerator, "generate", return_value=(rule, "rule")):
            ret, _ = self.call_loop(gen, self.suite({p1: 1, rule: 20}), strict=False)
        feedback = gen.generate_candidates.call_args_list[1].kwargs["feedback"]
        self.assertIn("FIRST_PATCH_FAILURE", feedback)
        self.assertIn('"FIRST_PATCH"', feedback)
        self.assertNotIn("RULE_PATCH", feedback)
        self.assertIsNone(ret[0])

    def test_successful_rule_fallback_is_returned_with_matching_results(self):
        p1, _, _, rule = self.paths
        gen = self.llm([(p1, "first")])
        with patch.object(RulePatchGenerator, "generate", return_value=(rule, "rule")):
            ret, run = self.call_loop(gen, self.suite({p1: 1, rule: 0}), strict=False)
        self.assertEqual(ret[0], rule)
        self.assertTrue(ret[3][0].passed)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(gen.generate_candidates.call_count, 1)

    def test_stops_testing_candidates_as_soon_as_one_passes(self):
        p1, p2 = self.paths[:2]
        gen = self.llm([(p1, "first"), (p2, "second")])
        ret, run = self.call_loop(gen, self.suite({p1: 0, p2: 1}))
        self.assertEqual(ret[0], p1)
        self.assertEqual(run.call_count, 1)

    def test_invalid_after_with_exit_zero_is_not_accepted(self):
        p1 = self.paths[0]
        gen = self.llm([(p1, "first")], [])
        ret, _ = self.call_loop(gen, self.suite({p1: 0}, invalid={p1}))
        self.assertIsNone(ret[0])
        self.assertIsNone(ret[3])

    def test_patch_apply_failure_returns_baseline_and_reaches_retry_feedback(self):
        p1 = self.paths[0]
        gen = self.llm([(p1, "first")], [])
        ret, _ = self.call_loop(gen, self.suite({p1: None}))
        self.assertIsNone(ret[0])
        self.assertIsNone(ret[3])
        self.assertIn("from 串不存在", gen.generate_candidates.call_args_list[1].kwargs["feedback"])

    def test_offline_rule_failure_is_not_generated_or_tested_twice(self):
        p1 = self.paths[0]
        gen = RulePatchGenerator()
        gen.generate = Mock(return_value=(p1, "rule"))
        ret, run = self.call_loop(gen, self.suite({p1: 1}), strict=False)
        self.assertIsNone(ret[0])
        self.assertEqual(run.call_count, 1)
        gen.generate.assert_called_once()

    def test_later_round_can_replace_best_feedback_without_mismatching_patch(self):
        p1, p2, retry = self.paths[:3]
        gen = self.llm([(p1, "first")], [(p2, "second")], [(retry, "retry")])
        ret, _ = self.call_loop(gen, self.suite({p1: 20, p2: 1, retry: 0}), rounds=2)
        feedback = gen.generate_candidates.call_args_list[2].kwargs["feedback"]
        self.assertIn("SECOND_PATCH_FAILURE", feedback)
        self.assertIn('"SECOND_PATCH"', feedback)
        self.assertNotIn("FIRST_PATCH", feedback)
        self.assertEqual(ret[0], retry)


class RepairFailureIntegrationTests(unittest.TestCase):
    """Real indexing, rule patches, unittest subprocesses, and report generation."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="replens_loop_integration_"))
        self.repo = self.tmp / "repo"
        (self.repo / "tests").mkdir(parents=True)
        self.source = "def read_value(value):\n    return int(value)\n"
        (self.repo / "api.py").write_text(self.source, encoding="utf-8")
        (self.repo / "tests" / "test_api.py").write_text(
            "import unittest\nfrom api import read_value\nclass T(unittest.TestCase):\n"
            "    def test_read_value(self):\n        self.assertEqual(read_value(' x '), 2)\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_repair_offline(self, issue):
        with patch("replens.runner.build_planner", return_value=RulePlanner()), \
             patch("replens.patchgen.build_patch_generator", return_value=RulePatchGenerator()), \
             patch.dict(os.environ, {"REPLENS_REQUIRE_LLM": "0"}):
            outcome = run_repair(RepairRequest(str(self.repo), issue, test_cmd=COMMAND, timeout=15))
        self.assertIsNone(outcome.patch_file_used)
        self.assertIsNone(outcome.after)
        self.assertEqual(outcome.report["after"], [])
        self.assertTrue(outcome.markdown.startswith("# RepoLens"))
        self.assertNotIn("独立验收通过", outcome.markdown)
        self.assertEqual((self.repo / "api.py").read_text(encoding="utf-8"), self.source)
        return outcome

    def test_failed_rule_patch_produces_report_instead_of_unpacking_error(self):
        outcome = self.run_repair_offline("read_value whitespace")
        self.assertFalse(outcome.before[0].passed)
        self.assertTrue(outcome.before[0].valid)
        self.assertTrue(list((self.repo / ".replens" / "candidates").glob("*.json")))

    def test_unsupported_issue_produces_baseline_report(self):
        outcome = self.run_repair_offline("read_value numeric conversion")
        self.assertFalse(outcome.before[0].passed)
        self.assertIn("空白处理模式", outcome.patch_note)

    def test_insufficient_evidence_produces_report(self):
        (self.repo / "tests" / "test_api.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertEqual(1, 1)\n", encoding="utf-8")
        outcome = self.run_repair_offline("GPU acceleration quantum blockchain")
        self.assertTrue(outcome.insufficient)
        self.assertTrue(outcome.before[0].passed)


if __name__ == "__main__":
    unittest.main()
