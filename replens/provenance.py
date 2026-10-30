"""JSON evidence bundles with separate material, replay and repair conclusions.

Source files are supplied separately and must match the frozen content inventory.
Local hashes check consistency; they are not signatures or proof of test quality.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .executor import DEFAULT_ALLOWED_COMMANDS, TestResult, _is_allowed, run_test_suite
from .validation import assess_repair, validate_acceptance

SCHEMA_VERSION = '2.0'
TEST_STATES = {'PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL', 'XPASS'}
REPAIR_STATES = {'VERIFIED', 'UNVERIFIED', 'FAILED', 'INVALID', 'NO_PATCH'}
RESULT_FIELDS = {'command', 'returncode', 'stdout', 'stderr', 'timed_out', 'passed',
                 'valid', 'summary', 'invalid_reason', 'tests', 'structured', 'framework'}


def _json_text(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + '\n'


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _git_field(repo_path, args):
    try:
        proc = subprocess.run(['git', '-C', str(repo_path)] + args,
                              capture_output=True, text=True, timeout=15)
        return proc.stdout.strip() if proc.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or '\x00' in value or value.startswith('/')
            or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise ValueError('文件路径必须是规范的项目内相对路径')
    return PurePosixPath(value)


def _inside(root, relative):
    _relative_path(relative)
    root = Path(root).resolve()
    path = root.joinpath(*PurePosixPath(relative).parts)
    if any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents):
        raise ValueError('证据材料不支持符号链接')
    if root not in path.resolve().parents:
        raise ValueError('证据材料路径不在根目录内')
    return path


def _tree_hash(files):
    h = hashlib.sha256()
    for relative, digest in sorted(files.items()):
        h.update(relative.encode('utf-8') + b'\0' + digest.encode('ascii') + b'\0')
    return h.hexdigest()


def source_snapshot(repo_path):
    """Freeze content identities before indexing or candidate generation."""
    from .repo import EXCLUDE_DIRS
    root = Path(repo_path).resolve()
    files = {}
    for parent, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        # Git worktrees use a .git file containing a checkout-specific path.
        names[:] = [name for name in names if name != '.git']
        for name in sorted(names):
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            path = _inside(root, relative)
            if path.is_file():
                files[relative] = _sha256_of_file(path)
    return {'root': str(root), 'files': files, 'tree_sha256': _tree_hash(files),
            'git_commit': _git_field(root, ['rev-parse', 'HEAD'])}


def _source_tree_digest(repo_path):
    return source_snapshot(repo_path)['tree_sha256']


def _environment():
    return {'python_version': platform.python_version(),
            'implementation': platform.python_implementation(),
            'platform': platform.platform(), 'executable': sys.executable,
            'packages': dict(sorted((dist.metadata['Name'], dist.version)
                                   for dist in importlib.metadata.distributions()
                                   if dist.metadata['Name']))}


def _file_map(files):
    if not isinstance(files, dict):
        raise ValueError('附加材料必须是路径到 UTF-8 文本的映射')
    for relative, content in files.items():
        _relative_path(relative)
        if not isinstance(content, str):
            raise ValueError('附加材料内容必须是文本')
        content.encode('utf-8')
    return dict(files)


def _records(rows):
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise ValueError('测试结果必须是列表或 null')
    for row in rows:
        if not isinstance(row, dict) or set(row) != RESULT_FIELDS:
            raise ValueError('测试结果字段不符合 Schema 2.0')
        if type(row['returncode']) is not int:
            raise ValueError('测试退出码必须是整数')
        if any(type(row[k]) is not bool for k in ('timed_out', 'passed', 'valid', 'structured')):
            raise ValueError('测试状态标记必须是布尔值')
        if any(not isinstance(row[k], str) for k in ('command', 'stdout', 'stderr', 'invalid_reason', 'framework')):
            raise ValueError('测试命令和日志字段必须是文本')
        tests = row['tests']
        if not isinstance(tests, dict) or any(not isinstance(k, str) or not k or v not in TEST_STATES
                                              for k, v in tests.items()):
            raise ValueError('测试 ID 与状态映射格式错误')
        if row['summary'] is not None and (not isinstance(row['summary'], dict)
                or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in row['summary'].items())):
            raise ValueError('测试汇总格式错误')
    return rows


def _results(rows):
    return None if rows is None else [TestResult(**row) for row in _records(rows)]


def _suite(command, files, before, after, enabled):
    return {'enabled': enabled, 'command': command,
            'files': _file_map(files), 'before': _records(before), 'after': _records(after)}


def build_provenance(report, rendered_report, patch_file, repo_path, out_dir=None,
                     extra_files=None, acceptance_suite=None, frozen_source=None):
    """Save frozen suites after candidate selection and final acceptance.

    acceptance_suite is the in-memory {command, files} loaded before generation.
    It is never re-read from its original path or supplied to a generator here.
    """
    current = source_snapshot(repo_path)
    source = frozen_source if frozen_source is not None else current
    if source['files'] != current['files'] or source['tree_sha256'] != current['tree_sha256']:
        raise ValueError('运行期间源码内容发生变化，不能建立一致的证据包')
    primary_files = _file_map(extra_files or {})
    if any(relative.split('/')[0] == '__replens_acceptance__' for relative in primary_files):
        raise ValueError('独立验收材料不能混入主测试附加文件')
    acceptance_cmd, acceptance_files, acceptance_sha = '', {}, ''
    if acceptance_suite is not None:
        acceptance_cmd, acceptance_files, acceptance_sha = validate_acceptance(acceptance_suite)
    if (acceptance_cmd != report.get('acceptance_test_cmd', '')
            or acceptance_sha != report.get('validation', {}).get('acceptance_sha256', '')):
        raise ValueError('打包的独立材料与本次冻结验收记录不一致')
    primary = _suite(report['test_command'], primary_files, report['before'],
                     report.get('after') or None, True)
    aux_enabled = bool(report.get('regression_before') or report.get('regression_after'))
    aux_files = dict(primary_files)
    if aux_enabled:
        aux_files['tests/test_replens_regression.py'] = report.get('generated_test', '')
    auxiliary = _suite(report.get('regression_test_cmd', ''), aux_files if aux_enabled else {},
                       report.get('regression_before') or None,
                       report.get('regression_after') or None, aux_enabled)
    auxiliary['kind'] = report.get('generated_test_kind', 'none')
    acceptance = _suite(acceptance_cmd, acceptance_files, report.get('acceptance_before') or None,
                        report.get('acceptance_after') or None, bool(acceptance_cmd))
    acceptance['sha256'] = acceptance_sha
    validation = assess_repair(_results(primary['before']), _results(primary['after']),
                               _results(acceptance['before']), _results(acceptance['after']),
                               acceptance_sha, _results(auxiliary['after']),
                               report.get('insufficient_evidence', False),
                               patch_present=bool(patch_file), patch_note=report.get('patch_note', ''))
    if report['status'] != validation['status']:
        raise ValueError('报告结论与结构化验收结果不一致')

    root = Path(repo_path).resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    bundle = Path(out_dir).resolve() if out_dir else root / '.replens' / 'provenance' / (stamp + '-' + uuid4().hex[:8])
    if bundle == root or (root in bundle.parents and '.replens' not in bundle.relative_to(root).parts):
        raise ValueError('仓库内的证据包必须保存在排除索引的 .replens 目录')
    payload = {'report.json': _json_text(report).encode('utf-8'),
               'report.md': rendered_report.encode('utf-8')}
    patch = None
    if patch_file:
        content = Path(patch_file).read_bytes()
        relative = 'patch/patch' + ('.json' if str(patch_file).lower().endswith('.json') else '.diff')
        payload[relative] = content
        patch = {'path': relative, 'sha256': _sha256(content), 'content': content.decode('utf-8')}
    suites = {}
    for name, suite in [('primary', primary), ('auxiliary', auxiliary), ('acceptance', acceptance)]:
        suite['timeout'] = report.get('test_timeout', 120)
        if type(suite['timeout']) is not int or suite['timeout'] <= 0:
            raise ValueError('测试超时必须是正整数')
        relative = 'suites/' + name + '.json'
        payload[relative] = _json_text(suite).encode('utf-8')
        suites[name] = relative
    prov = {'schema_version': SCHEMA_VERSION, 'created_at': stamp,
            'source': source, 'environment': _environment(), 'issue': report.get('issue', ''),
            'insufficient_evidence': report.get('insufficient_evidence', False),
            'repair': {'status': report['status'], 'reasons': report.get('reasons', []),
                       'validation': report.get('validation', {}), 'patch_note': report.get('patch_note', '')},
            'patch': patch, 'suites': suites,
            'reports': {'json': 'report.json', 'markdown': 'report.md'}}
    payload['provenance.json'] = _json_text(prov).encode('utf-8')
    manifest = {'schema_version': SCHEMA_VERSION,
                'files': {rel: _sha256(data) for rel, data in sorted(payload.items())}}
    bundle.mkdir(parents=True, exist_ok=False)
    for relative, data in payload.items():
        target = _inside(bundle, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (bundle / 'manifest.json').write_text(_json_text(manifest), encoding='utf-8')
    return str(bundle)


def _read_json(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('JSON 包含重复字段: ' + key)
            obj[key] = value
        return obj
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
    if not isinstance(data, dict):
        raise ValueError('JSON 根节点必须是对象')
    return data


def _load_bundle(bundle):
    prov = _read_json(_inside(bundle, 'provenance.json'))
    if prov.get('schema_version') in {'1.0', '1.1', '1.2', '1.3'}:
        return prov, None
    if prov.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支持的证据包 schema_version')
    required = {'schema_version', 'created_at', 'source', 'environment', 'issue',
                'insufficient_evidence', 'repair', 'patch', 'suites', 'reports'}
    if set(prov) != required or prov['reports'] != {'json': 'report.json', 'markdown': 'report.md'}:
        raise ValueError('provenance.json 字段不符合 Schema 2.0')
    if not isinstance(prov['created_at'], str) or not isinstance(prov['issue'], str):
        raise ValueError('证据包时间和问题字段必须为文本')
    environment = prov['environment']
    if (not isinstance(environment, dict)
            or set(environment) != {'python_version', 'implementation', 'platform', 'executable', 'packages'}
            or any(not isinstance(environment[k], str)
                   for k in ('python_version', 'implementation', 'platform', 'executable'))
            or not isinstance(environment['packages'], dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment['packages'].items())):
        raise ValueError('Python 环境字段不符合 Schema 2.0')
    repair = prov['repair']
    if (not isinstance(repair, dict) or set(repair) != {'status', 'reasons', 'validation', 'patch_note'}
            or repair['status'] not in REPAIR_STATES or not isinstance(repair['validation'], dict)
            or not isinstance(repair['patch_note'], str) or not isinstance(repair['reasons'], list)
            or any(not isinstance(reason, str) for reason in repair['reasons'])):
        raise ValueError('修复结论字段不符合 Schema 2.0')
    suite_paths = {name: 'suites/' + name + '.json' for name in ('primary', 'auxiliary', 'acceptance')}
    if prov['suites'] != suite_paths or type(prov['insufficient_evidence']) is not bool:
        raise ValueError('证据包套件声明无效')
    expected = {'provenance.json', 'report.json', 'report.md', *suite_paths.values()}
    patch = prov['patch']
    if patch is not None:
        if (not isinstance(patch, dict) or set(patch) != {'path', 'sha256', 'content'}
                or patch['path'] not in {'patch/patch.json', 'patch/patch.diff'}):
            raise ValueError('补丁声明格式错误')
        expected.add(patch['path'])
    manifest = _read_json(_inside(bundle, 'manifest.json'))
    if (set(manifest) != {'schema_version', 'files'} or manifest['schema_version'] != SCHEMA_VERSION
            or not isinstance(manifest['files'], dict) or set(manifest['files']) != expected):
        raise ValueError('证据包文件清单不完整或不符合 Schema 2.0')
    for relative in sorted(expected):
        path = _inside(bundle, relative)
        if not path.is_file() or _sha256_of_file(path) != manifest['files'][relative]:
            raise ValueError('必需材料缺失或哈希不一致: ' + relative)
    if patch and (patch['content'].encode('utf-8') != _inside(bundle, patch['path']).read_bytes()
                  or patch['sha256'] != manifest['files'][patch['path']]):
        raise ValueError('补丁内容与副本不一致')
    suites = {name: _read_json(_inside(bundle, relative)) for name, relative in suite_paths.items()}
    for name, suite in suites.items():
        fields = {'enabled', 'command', 'files', 'before', 'after', 'timeout'}
        fields |= {'sha256'} if name == 'acceptance' else ({'kind'} if name == 'auxiliary' else set())
        if set(suite) != fields or type(suite['enabled']) is not bool:
            raise ValueError('测试套件字段格式错误: ' + name)
        if type(suite['timeout']) is not int or suite['timeout'] <= 0:
            raise ValueError('测试套件超时格式错误: ' + name)
        if name == 'auxiliary' and suite['kind'] not in {'none', 'generated_auxiliary', 'invalid_generated', 'model_behavioral_cases', 'user_behavioral_examples'}:
            raise ValueError('辅助检查类型无效')
        _file_map(suite['files'])
        _records(suite['before'])
        _records(suite['after'])
        if not isinstance(suite['command'], str) or (suite['enabled'] and not _is_allowed(suite['command'], DEFAULT_ALLOWED_COMMANDS)):
            raise ValueError('套件测试命令无效: ' + name)
        if not suite['enabled'] and (suite['files'] or suite['before'] is not None or suite['after'] is not None):
            raise ValueError('未启用的套件不能包含执行结果或材料')
        if name != 'acceptance' and any(p.split('/')[0] == '__replens_acceptance__' for p in suite['files']):
            raise ValueError('独立材料不允许混入主测试或辅助检查')
    if not suites['primary']['enabled']:
        raise ValueError('证据包缺少主测试')
    acceptance = suites['acceptance']
    if acceptance['enabled']:
        _, _, digest = validate_acceptance({'command': acceptance['command'], 'files': acceptance['files']})
        if digest != acceptance['sha256']:
            raise ValueError('冻结独立材料哈希不一致')
    elif acceptance['sha256'] or acceptance['command']:
        raise ValueError('未启用的独立套件声明不一致')
    report = _read_json(_inside(bundle, 'report.json'))
    if (prov['repair']['status'] not in REPAIR_STATES or report.get('status') != prov['repair']['status']
            or report.get('reasons') != prov['repair']['reasons']
            or report.get('validation') != prov['repair']['validation']
            or report.get('insufficient_evidence') != prov['insufficient_evidence']):
        raise ValueError('报告与证据包修复结论不一致')
    for name, prefix in [('primary', ''), ('auxiliary', 'regression_'), ('acceptance', 'acceptance_')]:
        suite = suites[name]
        for phase in ('before', 'after'):
            if (report.get(prefix + phase) or None) != (suite[phase] or None):
                raise ValueError('报告与测试结果不一致: ' + name + '/' + phase)
        if suite['timeout'] != report.get('test_timeout', 120):
            raise ValueError('报告与套件超时不一致: ' + name)
    if (report['test_command'] != suites['primary']['command']
            or report.get('regression_test_cmd', '') != suites['auxiliary']['command']
            or report.get('acceptance_test_cmd', '') != acceptance['command']
            or report.get('validation', {}).get('acceptance_sha256', '') != acceptance['sha256']):
        raise ValueError('报告与套件命令或冻结哈希不一致')
    if (report.get('generated_test_kind', 'none') != suites['auxiliary']['kind']
            or (suites['auxiliary']['enabled'] and report.get('generated_test', '')
                != suites['auxiliary']['files'].get('tests/test_replens_regression.py'))):
        raise ValueError('报告与辅助检查材料不一致')
    recorded = assess_repair(_results(suites['primary']['before']), _results(suites['primary']['after']),
                             _results(acceptance['before']), _results(acceptance['after']),
                             acceptance['sha256'], _results(suites['auxiliary']['after']),
                             prov['insufficient_evidence'], patch_present=bool(patch),
                             patch_note=prov['repair']['patch_note'])
    identities = ('status', 'targets', 'preserved', 'acceptance_targets', 'acceptance_sha256')
    if any(recorded.get(key) != prov['repair']['validation'].get(key) for key in identities):
        raise ValueError('记录的修复状态与统一验收逻辑不一致')
    return prov, suites


def _same_results(recorded, actual):
    if recorded is None or actual is None:
        return recorded is None and actual is None
    if len(recorded) != len(actual):
        return False
    # Logs contain temporary paths and timings; test identities and states must match.
    keys = ('command', 'returncode', 'framework', 'valid', 'passed', 'structured',
            'timed_out', 'tests', 'summary')
    return all(all(row[key] == result.to_dict()[key] for key in keys)
               for row, result in zip(recorded, actual))


def _verification(status='FAIL', reasons=None, materials='NOT_CHECKED', replay='NOT_RUN',
                  repair='NOT_EVALUATED', **extra):
    return dict(status=status, reasons=reasons or [], materials_status=materials,
                replay_status=replay, repair_status=repair, **extra)


def verify_provenance(repo_path, prov_dir, test_command='', timeout=None, result_observer=None):
    """Return separate material, replay and repair conclusions.

    An optional control-side observer receives (suite, before, after, patch_note)
    with unabridged TestResult objects before the replay results are compared.
    Observer write failures fail replay rather than silently dropping raw logs.
    """
    bundle = Path(prov_dir).resolve()
    try:
        prov, suites = _load_bundle(bundle)
        if suites is None:
            return _verification(status='LEGACY', materials='LEGACY',
                                 reasons=['旧版证据包缺少完整套件，保留历史；请重新运行生成 2.0 包'],
                                 schema_version=prov.get('schema_version'))
        source = prov['source']
        if (not isinstance(source, dict) or set(source) != {'root', 'files', 'tree_sha256', 'git_commit'}
                or not isinstance(source['files'], dict) or not source['files']
                or _tree_hash(source['files']) != source['tree_sha256']):
            raise ValueError('源码清单或 SHA-256 格式错误')
        current = source_snapshot(repo_path)
        if current['files'] != source['files'] or current['tree_sha256'] != source['tree_sha256']:
            raise ValueError('当前源码内容与冻结源码清单不一致')
        environment = _environment()
        if any(prov['environment'][key] != environment[key]
               for key in ('python_version', 'implementation', 'platform', 'packages')):
            raise ValueError('Python 版本、实现、平台或已安装依赖版本与记录不同')
        if test_command and test_command.strip() != suites['primary']['command'].strip():
            raise ValueError('传入的主测试命令与冻结记录不同')
        if timeout is not None and timeout <= 0:
            raise ValueError('重放超时必须大于零')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, SyntaxError) as exc:
        return _verification(materials='FAIL', reasons=[str(exc)])

    actual = {}
    phase_checks = {}
    patch_file = str(_inside(bundle, prov['patch']['path'])) if prov['patch'] else None
    try:
        for name in ('primary', 'auxiliary', 'acceptance'):
            suite = suites[name]
            note = ''
            if not suite['enabled'] or (name != 'primary' and not patch_file):
                before, after = None, None
            elif name == 'auxiliary' and suite['kind'] == 'invalid_generated':
                try:
                    ast.parse(suite['files']['tests/test_replens_regression.py'])
                except SyntaxError as exc:
                    before, after = None, [TestResult(suite['command'], -1, valid=False,
                                                      invalid_reason='生成回归测试无法执行: ' + str(exc))]
                else:
                    raise ValueError('辅助检查记录的语法无效状态与材料不符')
            else:
                files = dict(suites['primary']['files'])
                files.update(suite['files'])
                before, after, note = run_test_suite(repo_path, suite['command'],
                                                    timeout=timeout if timeout is not None else suite['timeout'],
                                                    patch_file=patch_file, extra_files=files)
                if name != 'primary' and patch_file and after is None:
                    after = [TestResult(suite['command'], -1, valid=False, invalid_reason=note)]
            if result_observer is not None:
                result_observer(name, before, after, note)
            actual[name] = (before, after)
            phase_checks[name] = {phase: _same_results(suite[phase], rows)
                                  for phase, rows in [('before', before), ('after', after)]}
        if source_snapshot(repo_path)['files'] != source['files']:
            raise ValueError('重放期间原始源码内容发生变化')
        validation = assess_repair(*actual['primary'], *actual['acceptance'],
                                   suites['acceptance']['sha256'], actual['auxiliary'][1],
                                   prov['insufficient_evidence'], patch_present=bool(patch_file),
                                   patch_note=prov['repair']['patch_note'])
        identities = ('status', 'targets', 'preserved', 'acceptance_targets', 'acceptance_sha256')
        validation_matches = all(validation.get(k) == prov['repair']['validation'].get(k) for k in identities)
        matches = all(all(check.values()) for check in phase_checks.values()) and validation_matches
        return _verification(status='PASS' if matches else 'FAIL', materials='PASS',
                             replay='PASS' if matches else 'FAIL', repair=validation['status'],
                             reasons=['材料、逐测试身份/状态及统一验收结论重放一致' if matches else '重放结果与记录不一致'],
                             schema_version=SCHEMA_VERSION, recorded_repair_status=prov['repair']['status'],
                             validation=validation, phase_checks=phase_checks,
                             validation_matches=validation_matches)
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, RuntimeError) as exc:
        return _verification(materials='PASS', replay='FAIL', reasons=['重放未完成: ' + str(exc)],
                             schema_version=SCHEMA_VERSION, phase_checks=phase_checks)
