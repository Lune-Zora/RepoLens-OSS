"""统一修复编排：CLI 与 Web 共用的入口。

设计动机：
- 此前 CLI 先复现失败 → 提取执行轨迹 → 再做检索与计划（cli.py:117-128）；
  Web 端先检索 → 再生成补丁 → 最后才解析失败日志（web/app.py:386-391）。
  顺序不一致，导致同一 Issue 的两入口产生不同的首轮 evidence，Web 端
  也没有把失败帧用于辅助定位。

- 本模块提供单一入口 `run_repair(RepairRequest) -> RepairResult`，
  把顺序固定为：
      1) 建索引
      2) 复现失败 → 提取执行轨迹
      3) 用轨迹辅助做首轮检索与计划
      4) 生成回归测试
      5) 应用补丁（auto_patch 或指定 patch）
      6) 独立回归验证
      7) 构建报告与可选证据包

  CLI 与 Web 都只做"参数解析 + 渲染"，业务全在此模块内。
"""
from __future__ import annotations

import os
import ast
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from .executor import run_test_suite, render_patch_as_diff, test_framework, TestResult
from .validation import load_acceptance, assess_repair, inventory
from .execution_evidence import parse_traceback_frames
from .flip_predictor import predict_flips, seed_names_from_patch
from .indexer import IndexDB, build_index
from .patchgen import auto_patch_loop
from .planner import build_planner
from .report import build_report, render_markdown
from .provenance import SCHEMA_VERSION, source_snapshot
from .repo import RepoMeta, import_repo
from .retriever import Evidence, _is_test_path
from .testgen import generate_regression_test


@dataclass
class RepairRequest:
    """修复请求（CLI / Web 入口共用）。"""
    repo: str
    issue: str
    top_k: int = 5
    hybrid: bool = False
    test_cmd: str = ""                # 空时由框架推断
    auto_patch: bool = True
    patch: str = ""                    # 指定候选补丁（auto_patch=False 时使用）
    timeout: int = 120
    force_reindex: bool = False
    extra_files: dict = field(default_factory=dict)
    acceptance_suite: str = ""       # 冻结行为测试 JSON，必须在被修仓库之外
    model: str = ""
    behavior_examples: list = field(default_factory=list)


@dataclass
class RepairResult:
    """修复结果（CLI / Web 入口共用）。"""
    repo_path: str
    issue: str
    insufficient: bool
    evidences: List[Evidence]
    plan: object
    test_command: str
    before: list
    after: Optional[list]
    patch_note: str
    patch_file_used: Optional[str]
    generated_test: str
    patch_diff: str
    execution_hints: list
    flip_prediction: Optional[dict]
    regression_before: Optional[list]
    regression_after: Optional[list]
    regression_test_cmd: str
    provenance_dir: Optional[str] = None
    report: dict = field(default_factory=dict)
    markdown: str = ""
    status: str = "UNVERIFIED"
    reasons: list = field(default_factory=list)
    acceptance_before: Optional[list] = None
    acceptance_after: Optional[list] = None
    provenance_status: dict = field(default_factory=dict)


def _default_test_command(meta: RepoMeta) -> str:
    if meta.test_framework == "pytest":
        return "pytest"
    if meta.test_framework == "unittest":
        return "python -m unittest discover -s tests"
    return "pytest"


def _regression_test_command(framework: str) -> str:
    if framework == "unittest":
        return "python -m unittest discover -s tests -p test_replens_regression.py"
    return "pytest tests/test_replens_regression.py"


def _do_retrieve(issue, db, top_k, hybrid=False, execution_hints=None):
    from .retriever import retrieve
    if hybrid:
        from .retriever_hybrid import build_embedder, retrieve_hybrid
        emb = build_embedder()
        if emb is None:
            return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)
        return retrieve_hybrid(issue, db, top_k=top_k, embedder=emb,
                               execution_hints=execution_hints)
    return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)


def run_repair(req: RepairRequest, build_provenance=None) -> RepairResult:
    """执行完整修复闭环。

    Args:
        req: 修复请求。
        build_provenance: 可选打包器，接收 extra_files、acceptance_suite 和
            frozen_source 关键字参数；传 None 时不生成证据包。
    """
    # 在任何索引、计划或候选生成之前冻结独立材料，且禁止位于检索根内。
    if req.acceptance_suite:
        root = Path(req.repo).resolve()
        suite_path = Path(req.acceptance_suite).resolve()
        if suite_path == root or root in suite_path.parents:
            raise ValueError('独立验收材料必须放在被修复仓库之外，避免进入检索和提示词')
        if (root / '__replens_acceptance__').exists():
            raise ValueError('仓库已有保留的 __replens_acceptance__ 目录')
    acceptance_cmd, acceptance_files, acceptance_sha = load_acceptance(req.acceptance_suite)
    frozen_source = source_snapshot(req.repo) if build_provenance is not None else None
    meta = import_repo(req.repo)
    db = build_index(meta, force=req.force_reindex)
    try:
        from .model_settings import using_model
        with using_model(req.model):
            return _run_repair(req, meta, db, acceptance_cmd, acceptance_files,
                               acceptance_sha, build_provenance, frozen_source)
    finally:
        db.close()


def _run_repair(req, meta, db, acceptance_cmd, acceptance_files, acceptance_sha,
                build_provenance, frozen_source):

    test_cmd = req.test_cmd.strip() or _default_test_command(meta)
    framework = test_framework(test_cmd)

    # === 步骤 1：先复现失败（无补丁），提取执行轨迹 ===
    before_repro, _, _ = run_test_suite(
        req.repo, test_cmd, timeout=req.timeout,
        patch_file=None, extra_files=req.extra_files)
    before_output = "".join(
        (r.stderr or "") + "\n" + (r.stdout or "") + "\n"
        for r in before_repro)
    hints = parse_traceback_frames(before_output, meta.path)

    # Understand the current issue first; unrelated failing tests must not determine the task.
    from .issue_understanding import model_client, understand_and_retrieve
    from .task_tests import generate_task_tests, task_command, TASK_FILE, scope_violation, compile_cases
    from .validation import inventory
    model_mode = model_client('probe') is not None
    understanding = None
    task_info = {}
    blocked_reason = ''
    if model_mode:
        evidences, insufficient, understanding = understand_and_retrieve(
            req.issue, db, req.top_k, hybrid=req.hybrid, execution_hints=hints, repo_root=req.repo)
    else:
        evidences, insufficient = _do_retrieve(
            req.issue, db, req.top_k, hybrid=req.hybrid, execution_hints=hints)
    planner = build_planner()
    try:
        plan = planner.generate(req.issue, evidences)
    except Exception as exc:
        from .planner import Plan
        plan = Plan(root_cause='模型计划生成失败', risks=[str(exc)], generated_by='error')
        blocked_reason = '模型计划生成失败：'+str(exc)
    extra_files = dict(req.extra_files or {})
    requested_test_command = test_cmd
    generated_test = ''
    repair_issue = req.issue
    if model_mode and not insufficient and not blocked_reason:
        try:
            attempts=[]
            retained=[]
            feedback=None
            for design_round in range(2):
                if req.behavior_examples:
                    targets=[e.symbol for e in evidences if e.source=='intent_target']
                    cases=[]
                    for item in req.behavior_examples:
                        if not isinstance(item,dict):raise ValueError('行为样例必须是JSON对象')
                        case=dict(item)
                        if len(targets)==1:
                            case.setdefault('path',targets[0].path);case.setdefault('symbol',targets[0].name)
                        case.setdefault('args',[]);case.setdefault('kwargs',{})
                        case.setdefault('intent','issue');case.setdefault('normalize','identity')
                        cases.append(case)
                    generated_test,cases=compile_cases(req.repo,cases,evidences)
                    task_info={'kind':'user_behavioral_examples','cases':cases,'independent':False,
                               'frozen_before_patch':True,'sha256':hashlib.sha256(generated_test.encode('utf8')).hexdigest()}
                else:
                    generated_test, task_info = generate_task_tests(req.repo, req.issue, evidences, understanding, feedback=feedback)
                if retained:
                    generated_test,merged=compile_cases(req.repo, retained+task_info['cases'], evidences)
                    task_info['cases']=merged
                    task_info['sha256']=hashlib.sha256(generated_test.encode('utf8')).hexdigest()
                    task_info['retained_passing_cases']=len(retained)
                extra_files[TASK_FILE] = generated_test
                test_cmd = task_command(meta)
                framework = 'pytest'
                task_baseline, _, _ = run_test_suite(req.repo, test_cmd, timeout=req.timeout, extra_files=extra_files)
                states = inventory(task_baseline)
                task_info['baseline_tests'] = states
                failing_issue = [t for t,state in states.items() if '__replens_task__/' in t
                                 and '::test_issue_' in t and state in {'FAIL','ERROR'}]
                bad_preservation = [t for t,state in states.items() if '__replens_task__/' in t
                                    and '::test_preserve_' in t and state != 'PASS']
                valid=bool(task_baseline) and all(r.valid for r in task_baseline)
                attempts.append({'round':design_round+1,'sha256':task_info['sha256'],
                                 'cases':task_info['cases'],'baseline_tests':states,'execution_valid':valid})
                task_info['reproduced_issue_tests']=failing_issue
                task_info['design_attempts']=list(attempts)
                if valid and failing_issue and not bad_preservation:
                    break
                if valid and not failing_issue and not bad_preservation and design_round==0 and not req.behavior_examples:
                    retained=list(task_info['cases'])
                    feedback={'cases':retained,'results':states,'reason':'当前问题用例全部通过，未覆盖报告的异常条件；保留原用例，再增加边界输入。'}
                    continue
                blocked_reason = ('当前问题的测试尚未形成可信失败复现：' +
                    ('测试执行无效；' if not valid else '') +
                    ('没有issue用例失败；' if not failing_issue else '') +
                    ('保留行为用例在原代码上不成立：'+', '.join(bad_preservation) if bad_preservation else ''))
            repair_issue += '\nExpected behavior: ' + understanding.expected_behavior
            repair_issue += '\nFrozen model-proposed test cases: ' + json.dumps(task_info['cases'],ensure_ascii=False)
        except Exception as exc:
            blocked_reason = '当前问题行为测试生成失败：' + str(exc)
            task_info['error'] = str(exc)
    elif not model_mode:
        if req.behavior_examples:
            blocked_reason='行为样例模式需要已配置的模型确认目标函数；未忽略用户样例继续修复。'
        generated_test = generate_regression_test(req.repo, evidences, req.issue, framework=framework)

    patch_file_used = None
    before, after, patch_note = None, None, ''
    if blocked_reason or insufficient:
        before, _, _ = run_test_suite(req.repo, test_cmd, timeout=req.timeout, extra_files=extra_files)
        patch_note = blocked_reason or (understanding.explanation if understanding else '定位证据不足')
    elif req.auto_patch:
        patch_file_used, patch_note, before, after = auto_patch_loop(
            meta, repair_issue, evidences, test_cmd, timeout=req.timeout, extra_files=extra_files)
    else:
        patch_file_used = req.patch or None
        violation = scope_violation(req.repo, patch_file_used, evidences) if patch_file_used and model_mode else ''
        if violation:
            patch_file_used = None; blocked_reason = violation
        before, after, patch_note = run_test_suite(req.repo, test_cmd, timeout=req.timeout,
                                                  patch_file=patch_file_used, extra_files=extra_files)
        patch_note = violation or patch_note

    # === 步骤 5：测试翻转预测 ===
    flip_prediction = None
    if patch_file_used and after:
        try:
            before_ids = inventory(before)
            before_failed = {t.replace('::', '.').split('.')[-1]
                             for t, s in before_ids.items() if s in {'FAIL', 'ERROR'}}
            focus_paths = set()
            for name in before_failed:
                for row in db.conn.execute(
                        "SELECT DISTINCT path FROM symbols WHERE name=? "
                        "AND type IN ('function','method')", (name,)):
                    if _is_test_path(row["path"]):
                        focus_paths.add(row["path"])
            seeds = seed_names_from_patch(patch_file_used, db)
            predicted = predict_flips(db, seeds, focus_paths=focus_paths or None)
            after_ids = inventory(after)
            actual = sorted(t for t, s in before_ids.items()
                            if s in {'FAIL', 'ERROR'} and after_ids.get(t) == 'PASS')
            hit = [t for t in actual if t.replace('::', '.').split('.')[-1] in predicted]
            flip_prediction = dict(predicted=sorted(predicted), actual_flips=actual, hit=hit,
                                   precision=round(len({t.replace('::', '.').split('.')[-1] for t in hit}) / len(predicted), 4) if predicted else None,
                                   recall=round(len(hit) / len(actual), 4) if actual else None,
                                   identity='full_test_id; prediction matching uses method names')
        except Exception:
            flip_prediction = None

    # === 步骤 6：diff 渲染 + 独立回归验证 ===
    patch_diff = ""
    if patch_file_used and os.path.isfile(patch_file_used):
        try:
            patch_diff = render_patch_as_diff(meta.path, patch_file_used)
        except Exception:
            patch_diff = ""

    regression_before = regression_after = None
    regression_test_cmd = '' if model_mode else _regression_test_command(framework)
    generated_kind = task_info.get('kind','none') if task_info.get('cases') else 'none'
    if generated_test and not insufficient and patch_file_used and not model_mode:
        try:
            tree = ast.parse(generated_test)
            has_tests = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and n.name.startswith('test_') for n in ast.walk(tree))
            if has_tests:
                generated_kind = 'generated_auxiliary'
                regression_before, regression_after, regression_note = run_test_suite(
                    req.repo, regression_test_cmd, timeout=req.timeout,
                    patch_file=patch_file_used,
                    extra_files=dict(extra_files, **{'tests/test_replens_regression.py': generated_test}))
                if regression_after is None:
                    regression_after = [TestResult(regression_test_cmd, -1, valid=False,
                                                   invalid_reason=regression_note)]
        except Exception as exc:
            generated_kind = 'invalid_generated'
            regression_after = [TestResult(regression_test_cmd, -1, valid=False,
                                           invalid_reason='生成回归测试无法执行: ' + str(exc))]

    acceptance_before = acceptance_after = None
    if acceptance_cmd and patch_file_used:
        # 永不把这些用例、结果或失败日志回喂候选生成器。
        acceptance_before, acceptance_after, acceptance_note = run_test_suite(
            req.repo, acceptance_cmd, timeout=req.timeout, patch_file=patch_file_used,
            extra_files=dict(extra_files, **acceptance_files))
        if acceptance_after is None:
            acceptance_after = [TestResult(acceptance_cmd, -1, valid=False,
                                           invalid_reason=acceptance_note)]
    validation = assess_repair(before, after, acceptance_before, acceptance_after,
                               acceptance_sha, regression_after, insufficient,
                               patch_present=bool(patch_file_used), patch_note=patch_note)

    # === 步骤 7：构建报告 + 可选证据包 ===
    rep = build_report(
        repo_path=meta.path, issue=req.issue, evidences=evidences,
        insufficient=insufficient, plan=plan, test_command=test_cmd,
        before_results=before, after_results=after, patch_note=patch_note,
        generated_test=generated_test, patch_diff=patch_diff,
        execution_hints=hints, flip_prediction=flip_prediction,
        regression_before=regression_before, regression_after=regression_after,
        regression_test_cmd=regression_test_cmd,
        validation=validation, framework=framework, generated_kind=generated_kind,
        acceptance_before=acceptance_before, acceptance_after=acceptance_after,
        acceptance_test_cmd=acceptance_cmd,
    )
    rep['test_timeout'] = req.timeout
    rep['requested_test_command'] = requested_test_command
    rep['understanding'] = understanding.to_dict() if understanding else None
    rep['task_tests'] = task_info
    rep['blocked_reason'] = blocked_reason
    rep['verification_scope'] = '当前问题的冻结用例、原有通过测试保持及独立验收；不保证未覆盖行为'
    rep['model'] = (understanding.model if understanding else '')
    rep['provenance'] = {
        'status': 'WRITTEN' if build_provenance is not None else 'SKIPPED',
        'schema_version': SCHEMA_VERSION,
        'materials_status': 'NOT_CHECKED', 'replay_status': 'NOT_RUN', 'reasons': [],
    }
    markdown = render_markdown(rep)
    provenance_dir = None
    if build_provenance is not None:
        try:
            provenance_dir = build_provenance(
                rep, markdown, patch_file_used, meta.path,
                extra_files=extra_files,
                acceptance_suite={'command': acceptance_cmd, 'files': dict(acceptance_files)}
                if acceptance_cmd else None,
                frozen_source=frozen_source)
            if not provenance_dir:
                rep['provenance']['status'] = 'SKIPPED'
                markdown = render_markdown(rep)
        except Exception as exc:
            rep['provenance'].update(status='FAILED', reasons=[str(exc)])
            markdown = render_markdown(rep)

    return RepairResult(
        repo_path=meta.path, issue=req.issue, insufficient=insufficient,
        evidences=evidences, plan=plan, test_command=test_cmd,
        before=before, after=after, patch_note=patch_note,
        patch_file_used=patch_file_used,
        generated_test=generated_test, patch_diff=patch_diff,
        execution_hints=hints, flip_prediction=flip_prediction,
        regression_before=regression_before, regression_after=regression_after,
        regression_test_cmd=regression_test_cmd,
        provenance_dir=provenance_dir, report=rep, markdown=markdown,
        status=validation['status'], reasons=validation['reasons'],
        acceptance_before=acceptance_before, acceptance_after=acceptance_after,
        provenance_status=rep['provenance'],
    )
