"""测试套件执行与前后状态比对。"""
from __future__ import annotations

import json

from . import common


def _save_suite_logs(before, after, note, logs, label):
    phases = {'before': before, 'after': after}
    for phase, results in phases.items():
        if results is None:
            continue
        for index, result in enumerate(results, 1):
            prefix = logs / label / phase / ('%02d' % index)
            common._save(prefix.with_suffix('.stdout.txt'), result.stdout.encode('utf-8'))
            common._save(prefix.with_suffix('.stderr.txt'), result.stderr.encode('utf-8'))
            record = {'command': result.command, 'returncode': result.returncode,
                      'timed_out': result.timed_out, 'passed': result.passed,
                      'valid': result.valid, 'structured': result.structured,
                      'framework': result.framework, 'summary': result.summary,
                      'invalid_reason': result.invalid_reason, 'tests': result.tests,
                      'stdout_sha256': common._sha(result.stdout.encode('utf-8')),
                      'stderr_sha256': common._sha(result.stderr.encode('utf-8'))}
            common._save(prefix.with_suffix('.json'), json.dumps(record, ensure_ascii=False,
                                                            indent=2).encode('utf-8'))
    common._save(logs / label / 'patch_note.txt', note.encode('utf-8'))


def _capture_suite(root, command, logs, label, timeout, patch_file=None, extra_files=None):
    from replens.executor import run_test_suite

    before, after, note = run_test_suite(str(root), command, timeout=timeout,
                                         patch_file=str(patch_file) if patch_file else None,
                                         extra_files=extra_files or {})
    _save_suite_logs(before, after, note, logs, label)
    return before, after, note


def _target_state(results, suffix):
    if not results:
        raise ValueError('测试没有产生结果')
    if any(not row.valid or not row.structured or row.timed_out for row in results):
        raise ValueError('测试结果无效或超时')
    matches = [state for row in results for test_id, state in row.tests.items()
               if test_id.replace('::', '.').split('.')[-1].split('[')[0].endswith(suffix)]
    if len(matches) != 1:
        raise ValueError('未得到唯一目标测试状态：' + suffix + '；实际测试=' + repr([
            test_id for row in results for test_id in row.tests]))
    return matches[0]


def _same_test_states(left, right):
    return [row.tests for row in left] == [row.tests for row in right]


def _phase_summary(results):
    return [{'command': r.command, 'returncode': r.returncode, 'valid': r.valid,
             'passed': r.passed, 'summary': r.summary, 'tests': r.tests,
             'timed_out': r.timed_out} for r in results]
