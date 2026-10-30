"""Single acceptance policy shared by runner, reports, CLI and Web.

Generated checks are auxiliary. VERIFIED requires a caller-supplied behavioral
suite frozen before generation; the suite is never passed to the generator.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def load_acceptance(path: str):
    """Load a small self-contained suite: {command, files: {relative_path: code}}.

    Files live in a reserved directory, outside normal project tests. A caller
    must review the behavioral assertions; a hash records their frozen contents,
    it does not certify their quality or secrecy from hostile repository code.
    """
    if not path:
        return '', {}, ''
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_acceptance(data)


def validate_acceptance(data):
    """Validate an in-memory frozen suite without reading its source file again."""
    from .executor import DEFAULT_ALLOWED_COMMANDS, _is_allowed
    if not isinstance(data, dict) or set(data) != {'command', 'files'}:
        raise ValueError('验收文件必须只包含 command 和 files')
    command, files = data['command'], data['files']
    if not isinstance(command, str) or not _is_allowed(command, DEFAULT_ALLOWED_COMMANDS):
        raise ValueError('验收测试命令不在白名单内')
    if not isinstance(files, dict) or not files:
        raise ValueError('验收 files 必须是非空代码映射')
    if '__replens_acceptance__' not in command or not any(Path(p).name.startswith('test_') for p in files):
        raise ValueError('验收命令必须选择 __replens_acceptance__ 下的测试')
    for rel, code in files.items():
        p = Path(rel)
        if (p.is_absolute() or '..' in p.parts or '\\' in rel
                or len(p.parts) < 2 or p.parts[0] != '__replens_acceptance__'
                or p.suffix != '.py' or not isinstance(code, str)):
            raise ValueError('验收文件必须位于 __replens_acceptance__/ 下且为 Python 源码')
        compile(code, rel, 'exec')
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return command, dict(files), digest


def inventory(results):
    tests = {}
    for r in results or []:
        for test_id, state in (r.tests or {}).items():
            if test_id in tests:
                raise ValueError('重复测试 ID: ' + test_id)
            tests[test_id] = state
    return tests


def assess_primary(before, after):
    reasons = []
    outcome = {'status': 'INVALID', 'reasons': reasons, 'targets': [], 'preserved': []}
    if not before or any(not r.valid or r.timed_out or not r.structured for r in before):
        details = '；'.join(r.invalid_reason for r in (before or []) if r.invalid_reason)
        reasons.append('修复前测试无效或缺少结构化测试 ID' + ('：' + details if details else ''))
        return outcome
    if after is None:
        outcome['status'] = 'NO_PATCH'
        reasons.append('没有进入验收的补丁')
        return outcome
    if not after or any(not r.valid or r.timed_out or not r.structured for r in after):
        details = '；'.join(r.invalid_reason for r in (after or []) if r.invalid_reason)
        reasons.append('修复后测试无效或缺少结构化测试 ID' + ('：' + details if details else ''))
        return outcome
    if [(r.command, r.framework) for r in before] != [(r.command, r.framework) for r in after]:
        reasons.append('修复前后测试命令或框架不一致')
        return outcome
    try:
        original, updated = inventory(before), inventory(after)
    except ValueError as exc:
        reasons.append(str(exc))
        return outcome
    task_ids = {t for t in original if '__replens_task__/' in t and '::test_issue_' in t}
    targets = sorted(t for t, s in original.items() if s in {'FAIL', 'ERROR'}
                     and (not task_ids or t in task_ids))
    preexisting = {t: s for t,s in original.items() if s in {'FAIL', 'ERROR'} and task_ids and t not in task_ids
                   and '__replens_task__/' not in t}
    if task_ids:
        outcome['preexisting_failures'] = sorted(preexisting)
        outcome['scope'] = 'current_issue_and_preserved_tests'
    preserved = sorted(t for t, s in original.items() if s == 'PASS')
    outcome.update(targets=targets, preserved=preserved, status='FAILED')
    if not targets:
        outcome['status'] = 'UNVERIFIED'
        reasons.append('修复前未复现有效的目标失败')
    for t in targets:
        if updated.get(t) != 'PASS':
            reasons.append('目标未明确转为 PASS: ' + t + ' → ' + updated.get(t, 'MISSING'))
    for t in preserved:
        if updated.get(t) != 'PASS':
            reasons.append('原有通过测试未保持: ' + t + ' → ' + updated.get(t, 'MISSING'))
    if task_ids:
        for t,state in updated.items():
            if state != 'PASS' and state not in {'SKIP','XFAIL'} and preexisting.get(t) != state:
                reasons.append('当前问题或回归检查未通过: ' + t + ' → ' + state)
        for t,state in preexisting.items():
            if updated.get(t) not in {state,'PASS'}:
                reasons.append('既有失败发生额外变化: ' + t)
    elif any(not r.passed for r in after):
        reasons.append('修复后主测试未全部通过')
    if not reasons:
        outcome['status'] = 'PRIMARY_PASSED'
    return outcome


def assess_repair(before, after, acceptance_before=None, acceptance_after=None,
                  acceptance_sha256='', regression_after=None, insufficient=False,
                  patch_present=False, patch_note=''):
    outcome = assess_primary(before, after)
    outcome['acceptance_sha256'] = acceptance_sha256
    if patch_present and outcome['status'] == 'NO_PATCH':
        outcome.update(status='FAILED', reasons=[patch_note or '补丁未应用'])
    if insufficient:
        outcome.update(status='UNVERIFIED', reasons=['定位证据不足，不能确认修复'])
        return outcome
    if outcome['status'] != 'PRIMARY_PASSED':
        return outcome
    if regression_after is not None:
        if not regression_after or any(not r.valid for r in regression_after):
            outcome.update(status='INVALID', reasons=['自动生成的辅助回归测试执行无效'])
            return outcome
        if any(not r.passed for r in regression_after):
            outcome.update(status='FAILED', reasons=['自动生成的辅助回归测试失败'])
            return outcome
    if not acceptance_sha256:
        outcome.update(status='UNVERIFIED', reasons=['主测试通过；尚未提供冻结的独立行为验收用例，生成测试不能代替独立验收'])
        return outcome
    independent = assess_primary(acceptance_before, acceptance_after)
    outcome['acceptance_targets'] = independent['targets']
    if independent['status'] != 'PRIMARY_PASSED':
        status = independent['status'] if independent['status'] in {'INVALID', 'FAILED'} else 'UNVERIFIED'
        outcome.update(status=status, reasons=['独立行为验收: ' + r for r in independent['reasons']])
    else:
        outcome.update(status='VERIFIED', reasons=['目标失败明确转为 PASS，原有通过测试保持通过，冻结独立行为验收通过'])
        if outcome.get('preexisting_failures'):
            outcome['reasons'].append('仍有与当前问题分开的既有失败，详见preexisting_failures；不表示全仓库测试全部通过')
    return outcome
