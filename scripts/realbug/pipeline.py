"""主流程编排：核验 → 环境冻结 → 独立验收 → buggy 基线 → Gold → 证据包 → 跨 worktree 重放。

2026-10-28 二次拆分：原 `run()` 单函数 256 行，现按 `record['stage']` 的 8 个阶段边界
拆为 `_stage_*` 函数，共享状态收进 `_Context`。

阶段与 record['stage'] 一一对应：

| 阶段函数 | stage 标记 | 产物 |
|---|---|---|
| `_stage_preflight` | `preflight` | `ctx.source_before` |
| `_stage_upstream_verification` | `upstream-verification` | `ctx.upstream_info` |
| `_stage_environment` | `environment` | `environment.json` |
| `_stage_independent_acceptance` | —（紧随 environment） | `ctx.acceptance_*` |
| `_stage_buggy_worktree` | `buggy-worktree` | `ctx.buggy` / `ctx.replay` / `ctx.execution_repo` |
| `_stage_buggy_baseline` | `buggy-baseline` | `ctx.baseline` / `ctx.target_before` |
| `_stage_gold_validation` | `gold-validation` | `ctx.primary_*` / `ctx.acceptance_*_result` / `ctx.validation` / `ctx.bundle` |
| `_stage_cross_worktree_replay` | `cross-worktree-replay` | `ctx.replay_result` |
| `_stage_complete` | `complete` | `sample.json` + 最终 `run.json` |

`run()` 只负责：建上下文 → 顺序调用阶段 → 异常落盘 → 收尾清理与只读审计。
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import tempfile
import time
import traceback

from . import common, constants, environment, errors, gitops, suites, upstream


class _Context:
    """run() 的共享状态。所有阶段通过它读写，不再穿透十几个局部变量。"""

    def __init__(self, args):
        self.args = args
        self.output = Path(args.output).expanduser().resolve()
        self.repo = Path(args.repo).expanduser().resolve()
        self.logs = self.output / 'logs'
        self.record = None

        # 生命周期资源，收尾阶段使用
        self.managed = None
        self.execution_repo = None
        self.work_parent = None
        self.worktrees = []
        self.source_before = None
        self.previous_tempdir = tempfile.tempdir
        self.previous_temp_env = {key: os.environ.get(key) for key in ('TEMP', 'TMP', 'TMPDIR')}

        # 阶段产物
        self.upstream_info = None
        self.acceptance = None
        self.acceptance_cmd = None
        self.acceptance_files = None
        self.acceptance_sha = None
        self.buggy = None
        self.replay = None
        self.baseline = None
        self.target_before = None
        self.primary_before = None
        self.primary_after = None
        self.gold_note = None
        self.acceptance_before = None
        self.acceptance_after = None
        self.acceptance_target = None
        self.validation = None
        self.frozen = None
        self.bundle = None
        self.replay_result = None


def _save_record(ctx):
    common._save(ctx.output / 'run.json',
                 json.dumps(ctx.record, ensure_ascii=False, indent=2).encode('utf-8'))


def _stage_preflight(ctx):
    ctx.record['stage'] = 'preflight'
    if not ctx.repo.is_dir():
        raise errors.RestoreError('preflight', '本地 tqdm Git 仓库目录不存在；本脚本不会联网克隆')
    ctx.source_before = gitops._repository_fingerprint(ctx.repo)
    ctx.record['source_read_only_audit'] = {'before': ctx.source_before, 'status': 'PENDING'}


def _stage_upstream_verification(ctx):
    ctx.record['stage'] = 'upstream-verification'
    ctx.upstream_info = upstream._verify_upstream(ctx.repo, ctx.logs)
    ctx.record['upstream'] = ctx.upstream_info
    ctx.record['steps'].append({'stage': ctx.record['stage'], 'status': 'PASS'})


def _stage_environment(ctx):
    ctx.record['stage'] = 'environment'
    environment._freeze_environment(ctx.output, ctx.logs, ctx.repo, constants.BUGGY_PARENT)
    ctx.record['steps'].append({'stage': ctx.record['stage'], 'status': 'PASS'})


def _stage_independent_acceptance(ctx):
    ctx.acceptance = {'command': constants.ACCEPTANCE_COMMAND,
                      'files': {constants.ACCEPTANCE_PATH: constants.ACCEPTANCE_SOURCE}}
    from replens.validation import validate_acceptance
    ctx.acceptance_cmd, ctx.acceptance_files, ctx.acceptance_sha = validate_acceptance(ctx.acceptance)
    common._save(ctx.output / 'independent-acceptance.json',
                 json.dumps(ctx.acceptance, ensure_ascii=False, indent=2).encode('utf-8'))
    ctx.record['independent_acceptance_sha256'] = ctx.acceptance_sha


def _stage_buggy_worktree(ctx):
    ctx.record['stage'] = 'buggy-worktree'
    ctx.work_parent = Path(ctx.args.work_dir).expanduser().resolve()
    if not ctx.work_parent.is_dir():
        raise errors.RestoreError(ctx.record['stage'], '临时 worktree 父目录必须预先存在')
    if (common._is_within(ctx.work_parent, ctx.repo)
            or common._is_within(ctx.repo, ctx.work_parent)):
        raise errors.RestoreError(ctx.record['stage'], 'worktree 父目录不能位于上游仓库内或包住上游仓库')
    ctx.managed = Path(tempfile.mkdtemp(prefix='tqdm_reset_inf_', dir=ctx.work_parent)).resolve()
    execution_temp = ctx.managed / 'execution-temp'
    execution_temp.mkdir()
    tempfile.tempdir = str(execution_temp)
    for key in ctx.previous_temp_env:
        os.environ[key] = str(execution_temp)
    ctx.execution_repo = ctx.managed / 'local-git'
    gitops._git(ctx.repo, ['clone', '--no-local', '--no-checkout', '-c', 'core.autocrlf=false',
                           str(ctx.repo), str(ctx.execution_repo)], ctx.logs, 'local-execution-clone')
    ctx.record['source_repository_read_only'] = True
    ctx.record['execution_repository'] = str(ctx.execution_repo)
    ctx.buggy = ctx.managed / 'buggy'
    ctx.replay = ctx.managed / 'replay'
    ctx.worktrees.append(ctx.buggy)
    ctx.record['buggy_worktree_path'] = str(ctx.buggy)
    gitops._add_worktree(ctx.execution_repo, ctx.buggy, constants.BUGGY_PARENT, ctx.logs, 'add-buggy-worktree')
    gitops._apply_test_patch(ctx.repo, ctx.buggy, ctx.output / 'patches/upstream-test.patch',
                             ctx.logs, 'buggy-test')


def _stage_buggy_baseline(ctx):
    ctx.record['stage'] = 'buggy-baseline'
    ctx.baseline, _, baseline_note = suites._capture_suite(
        ctx.buggy, constants.MAIN_COMMAND, ctx.logs, 'main-baseline', ctx.args.timeout)
    ctx.target_before = suites._target_state(ctx.baseline, 'test_reset_inf')
    if ctx.target_before not in {'FAIL', 'ERROR'}:
        raise errors.RestoreError(ctx.record['stage'],
                                  '新增公开回归测试没有在 buggy 版本产生有效失败：' + ctx.target_before)
    if any(not row.valid or not row.structured or row.timed_out for row in ctx.baseline):
        raise errors.RestoreError(ctx.record['stage'], 'buggy 基线包含无效测试、收集错误或超时')
    ctx.record['baseline'] = {'patch_note': baseline_note, 'target_state': ctx.target_before,
                              'results': suites._phase_summary(ctx.baseline)}
    ctx.record['steps'].append({'stage': ctx.record['stage'], 'status': 'PASS'})


def _stage_gold_validation(ctx):
    from replens.provenance import build_provenance, source_snapshot

    ctx.record['stage'] = 'gold-validation'
    source_snapshot_before = source_snapshot(ctx.buggy)
    ctx.primary_before, ctx.primary_after, ctx.gold_note = suites._capture_suite(
        ctx.buggy, constants.MAIN_COMMAND, ctx.logs, 'main-gold', ctx.args.timeout,
        patch_file=ctx.output / 'patches/upstream-gold.patch')
    if not ctx.gold_note.startswith('补丁应用成功'):
        raise errors.RestoreError(ctx.record['stage'], '参考生产补丁没有成功应用：' + ctx.gold_note)
    if not suites._same_test_states(ctx.baseline, ctx.primary_before):
        raise errors.RestoreError(ctx.record['stage'], '同一 buggy 工作树的独立基线与补丁前结果不一致')
    if suites._target_state(ctx.primary_before, 'test_reset_inf') not in {'FAIL', 'ERROR'}:
        raise errors.RestoreError(ctx.record['stage'], '补丁前目标失败没有复现')
    if (suites._target_state(ctx.primary_after, 'test_reset_inf') != 'PASS'
            or not all(r.passed for r in ctx.primary_after)):
        raise errors.RestoreError(ctx.record['stage'], '上游 Gold 没有让目标和整组公开测试通过')
    ctx.record['gold'] = {'patch_note': ctx.gold_note,
                          'before': suites._phase_summary(ctx.primary_before),
                          'after': suites._phase_summary(ctx.primary_after)}

    ctx.acceptance_before, ctx.acceptance_after, acceptance_note = suites._capture_suite(
        ctx.buggy, ctx.acceptance_cmd, ctx.logs, 'independent-acceptance', ctx.args.timeout,
        patch_file=ctx.output / 'patches/upstream-gold.patch', extra_files=ctx.acceptance_files)
    if not acceptance_note.startswith('补丁应用成功'):
        raise errors.RestoreError(ctx.record['stage'], '独立验收补丁应用失败：' + acceptance_note)
    ctx.acceptance_target = 'test_reset_infinity_matches_unknown_total_behavior'
    if suites._target_state(ctx.acceptance_before, ctx.acceptance_target) not in {'FAIL', 'ERROR'}:
        raise errors.RestoreError(ctx.record['stage'], '冻结独立验收没有在 buggy 版本失败')
    if (suites._target_state(ctx.acceptance_after, ctx.acceptance_target) != 'PASS'
            or not all(row.passed for row in ctx.acceptance_after)):
        raise errors.RestoreError(ctx.record['stage'], 'Gold 未通过全部独立行为验收')
    ctx.record['independent_acceptance'] = {
        'before': suites._phase_summary(ctx.acceptance_before),
        'after': suites._phase_summary(ctx.acceptance_after),
        'finite_control_test': 'test_reset_finite_total_keeps_normal_counting'}
    ctx.record['steps'].append({'stage': ctx.record['stage'], 'status': 'PASS'})

    from replens.report import build_report, render_markdown
    from replens.validation import assess_repair
    ctx.validation = assess_repair(ctx.primary_before, ctx.primary_after,
                                  ctx.acceptance_before, ctx.acceptance_after,
                                  ctx.acceptance_sha, insufficient=False,
                                  patch_present=True, patch_note=ctx.gold_note)
    if ctx.validation['status'] != 'VERIFIED':
        raise errors.RestoreError(ctx.record['stage'],
                                  '统一验收状态不是 VERIFIED：' + repr(ctx.validation))
    report = build_report(repo_path=str(ctx.buggy), issue=constants.ISSUE, evidences=[],
                          insufficient=False, plan=None, test_command=constants.MAIN_COMMAND,
                          before_results=ctx.primary_before, after_results=ctx.primary_after,
                          patch_note=ctx.gold_note, generated_test='',
                          patch_diff=(ctx.output / 'patches/upstream-gold.patch').read_text(encoding='utf-8'),
                          validation=ctx.validation, framework='pytest', generated_kind='none',
                          acceptance_before=ctx.acceptance_before,
                          acceptance_after=ctx.acceptance_after,
                          acceptance_test_cmd=ctx.acceptance_cmd)
    report['test_timeout'] = ctx.args.timeout
    report['provenance'] = {'status': 'WRITTEN', 'schema_version': '2.0',
                            'materials_status': 'NOT_CHECKED', 'replay_status': 'NOT_RUN',
                            'reasons': []}
    markdown = render_markdown(report)
    ctx.frozen = source_snapshot_before
    bundle = ctx.output / 'evidence_bundle'
    ctx.bundle = build_provenance(report, markdown, ctx.output / 'patches/upstream-gold.patch',
                                  ctx.buggy, out_dir=bundle,
                                  acceptance_suite={'command': ctx.acceptance_cmd,
                                                    'files': ctx.acceptance_files},
                                  frozen_source=ctx.frozen)
    ctx.record['evidence_bundle'] = str(ctx.bundle)
    ctx.record['recorded_repair_status'] = ctx.validation['status']


def _stage_cross_worktree_replay(ctx):
    from replens.provenance import source_snapshot, verify_provenance

    ctx.record['stage'] = 'cross-worktree-replay'
    ctx.worktrees.append(ctx.replay)
    ctx.record['replay_worktree_path'] = str(ctx.replay)
    gitops._add_worktree(ctx.execution_repo, ctx.replay, constants.BUGGY_PARENT, ctx.logs,
                         'add-replay-worktree')
    gitops._apply_test_patch(ctx.repo, ctx.replay, ctx.output / 'patches/upstream-test.patch',
                             ctx.logs, 'replay-test')
    replay_snapshot = source_snapshot(ctx.replay)
    if (replay_snapshot['files'] != ctx.frozen['files']
            or replay_snapshot['tree_sha256'] != ctx.frozen['tree_sha256']):
        raise errors.RestoreError(ctx.record['stage'], '两个真实工作树的源码哈希不一致')
    ctx.record['cross_worktree_source_hash'] = {
        'first': ctx.frozen['tree_sha256'], 'second': replay_snapshot['tree_sha256'],
        'status': 'PASS'}
    ctx.replay_result = verify_provenance(
        str(ctx.replay), str(ctx.bundle), timeout=ctx.args.timeout,
        result_observer=lambda name, before, after, note: suites._save_suite_logs(
            before, after, note, ctx.logs, 'replay-' + name))
    common._save(ctx.output / 'cross-worktree-replay.json',
                 json.dumps(ctx.replay_result, ensure_ascii=False, indent=2).encode('utf-8'))
    if (ctx.replay_result.get('materials_status') != 'PASS'
            or ctx.replay_result.get('replay_status') != 'PASS'
            or ctx.replay_result.get('repair_status') != 'VERIFIED'):
        raise errors.RestoreError(ctx.record['stage'],
                                  '第二个全新 Git worktree 重放未完全通过：'
                                  + json.dumps(ctx.replay_result, ensure_ascii=False))
    ctx.record['cross_worktree_replay'] = ctx.replay_result
    ctx.record['steps'].append({'stage': ctx.record['stage'], 'status': 'PASS'})


def _stage_complete(ctx):
    ctx.record['status'] = 'PASS'
    ctx.record['stage'] = 'complete'
    ctx.record['environment_sha256'] = common._sha((ctx.output / 'environment.json').read_bytes())
    ctx.record['independent_materials_outside_bundle_input'] = True
    ctx.record['model_repair_credit'] = False
    ctx.record['completed_at_unix'] = time.time()
    common._save(ctx.output / 'sample.json', json.dumps({
        'sample_id': ctx.record['sample_id'], 'repository': 'tqdm',
        'source': 'upstream Git history; exact commits in run.json',
        'license': ctx.upstream_info['license'], 'buggy_commit': constants.BUGGY_PARENT,
        'fix_commit': constants.FIX_COMMIT,
        'issue_source': 'upstream fix commit; wording frozen in run.json',
        'issue': constants.ISSUE, 'environment_sha256': ctx.record['environment_sha256'],
        'regression_test': {'path': constants.TEST_PATH, 'command': constants.MAIN_COMMAND,
                            'target_state_before': ctx.target_before,
                            'target_state_after': 'PASS'},
        'independent_test': {'path': 'independent-acceptance.json',
                             'sha256': ctx.acceptance_sha,
                             'target_state_before': suites._target_state(
                                 ctx.acceptance_before, ctx.acceptance_target),
                             'target_state_after': suites._target_state(
                                 ctx.acceptance_after, ctx.acceptance_target)},
        'evidence_bundle': 'evidence_bundle',
        'classification': 'reference-patch validation; not autonomous model repair',
        'control_materials_must_not_enter_model_input': True,
    }, ensure_ascii=False, indent=2).encode('utf-8'))
    _save_record(ctx)


def _mark_failed(ctx, stage, exc):
    ctx.record['status'] = 'FAILED'
    ctx.record['failed_stage'] = stage
    ctx.record['failure'] = str(exc)
    ctx.record['traceback'] = traceback.format_exc()
    ctx.record['completed_at_unix'] = time.time()
    _save_record(ctx)


def _finalize(ctx):
    """无论成败都要执行的收尾：还原临时环境、清理 worktree、只读审计、日志清单。"""
    tempfile.tempdir = ctx.previous_tempdir
    for key, value in ctx.previous_temp_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    cleanup_errors = []
    if ctx.managed is not None:
        for path in reversed(ctx.worktrees):
            if not common._is_within(path, ctx.managed):
                cleanup_errors.append('拒绝清理受管临时目录以外的路径: ' + str(path))
                continue
            try:
                gitops._git(ctx.execution_repo, ['worktree', 'remove', '--force', str(path)],
                            ctx.logs, 'cleanup-' + path.name, check=True)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if not cleanup_errors:
            try:
                gitops._remove_managed_directory(ctx.managed, ctx.work_parent)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            ctx.record['temporary_worktree_directory_retained'] = str(ctx.managed)
    if cleanup_errors:
        ctx.record['cleanup_errors'] = cleanup_errors
        ctx.record['status'] = 'FAILED'
        ctx.record['failed_stage'] = 'cleanup'
    if ctx.source_before is not None:
        try:
            source_after = gitops._repository_fingerprint(ctx.repo)
            unchanged = source_after == ctx.source_before
            ctx.record['source_read_only_audit'].update(
                after=source_after, status='PASS' if unchanged else 'FAIL')
            if not unchanged:
                ctx.record.update(status='FAILED', failed_stage='source-read-only-audit',
                                  failure='输入仓库的文件或 Git 元数据发生变化')
        except Exception as exc:
            ctx.record.update(status='FAILED', failed_stage='source-read-only-audit', failure=str(exc))
    if ctx.output.exists():
        ctx.record['raw_log_manifest'] = [
            {'path': path.relative_to(ctx.output).as_posix(), 'bytes': path.stat().st_size,
             'sha256': common._sha(path.read_bytes())}
            for path in sorted(ctx.logs.rglob('*')) if path.is_file()]
        ctx.record['completed_at_unix'] = ctx.record.get('completed_at_unix', time.time())
        _save_record(ctx)


STAGES = (
    _stage_preflight,
    _stage_upstream_verification,
    _stage_environment,
    _stage_independent_acceptance,
    _stage_buggy_worktree,
    _stage_buggy_baseline,
    _stage_gold_validation,
    _stage_cross_worktree_replay,
    _stage_complete,
)


def run(args):
    """执行完整控制流程；阶段顺序见 STAGES。异常一律落盘 run.json 后重抛。"""
    ctx = _Context(args)
    if common._is_within(ctx.output, ctx.repo):
        raise errors.RestoreError('preflight', '控制材料和证据包必须放在上游仓库之外')
    ctx.output.mkdir(parents=True, exist_ok=False)
    ctx.logs.mkdir()
    ctx.record = {'schema_version': 1, 'sample_id': 'tqdm-reset-total-infinity',
                  'status': 'RUNNING', 'started_at_unix': time.time(),
                  'repository': 'tqdm', 'issue': constants.ISSUE,
                  'buggy_parent': constants.BUGGY_PARENT, 'fix_commit': constants.FIX_COMMIT,
                  'control_artifact_directory': str(ctx.output), 'steps': []}
    _save_record(ctx)
    try:
        for stage in STAGES:
            stage(ctx)
        return ctx.record
    except errors.RestoreError as exc:
        _mark_failed(ctx, exc.stage, exc)
        raise
    except Exception as exc:
        _mark_failed(ctx, ctx.record.get('stage', 'unexpected-error'), exc)
        raise errors.RestoreError(ctx.record['failed_stage'], str(exc)) from exc
    finally:
        _finalize(ctx)
