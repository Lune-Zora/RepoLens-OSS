"""Audited allowlist packages for localization experiments (stdlib only).

Controller annotations and reference materials must stay outside the input root.
This verifies bytes and separation, not the semantic absence of solutions in prose.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .eval_metrics import relative_path

REQUIRED_FIELDS = ('sample_id', 'repository', 'source', 'license', 'buggy_commit',
                   'fix_commit', 'issue_source', 'issue', 'environment',
                   'regression_test', 'before_gold_validation',
                   'model_input_manifest', 'reference_materials', 'independent_test_result')
ALLOWED_FIELDS = set(REQUIRED_FIELDS) | {'candidate_trace', 'replay_artifacts'}
KINDS = {'source': {'.py'}, 'document': {'.md', '.rst', '.txt'}, 'failure_log': {'.log', '.txt'}}
REFERENCE_ROLES = {'reference_patch', 'annotation', 'independent_test', 'validation_log', 'training_material'}
RESERVED = {'.git', '.replens', '__pycache__', '.pytest_cache', '.venv', 'venv',
            'node_modules', '__replens_acceptance__', 'reports', 'eval', 'training'}


class InputManifestError(ValueError):
    pass


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def _external(path, root, label):
    if path == root or root in path.parents:
        raise InputManifestError(label + ' 必须放在模型输入根目录之外: ' + str(path))


def _resolve(base, value):
    if not isinstance(value, str) or not value.strip():
        raise InputManifestError('路径必须是非空字符串')
    raw = base / value
    if raw.is_symlink() or any(p.is_symlink() for p in raw.parents):
        raise InputManifestError('材料路径不允许符号链接: ' + str(raw))
    return raw.resolve(strict=True)


def _plain_file(path):
    if not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise InputManifestError('材料必须是普通文件，不能是符号链接: ' + str(path))
    if path.stat().st_nlink != 1:
        raise InputManifestError('材料不允许硬链接: ' + str(path))


def _entry_bytes(root, entry):
    if not isinstance(entry, dict) or set(entry) != {'path', 'kind', 'sha256'}:
        raise InputManifestError('输入文件条目必须且只能含 path/kind/sha256')
    rel, kind, digest = entry['path'], entry['kind'], entry['sha256']
    relative_path(rel)
    parts = Path(rel).parts
    if (kind not in KINDS or Path(rel).suffix.lower() not in KINDS[kind]
            or any(p.lower() in RESERVED for p in parts)):
        raise InputManifestError('不允许的输入材料类型或保留路径: ' + rel)
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise InputManifestError('输入 sha256 必须是 64 位小写十六进制: ' + rel)
    candidate = root / rel
    _plain_file(candidate)
    candidate.resolve(strict=True).relative_to(root)
    content = candidate.read_bytes()
    if sha256(content) != digest:
        raise InputManifestError('输入文件哈希不匹配: ' + rel)
    # A binary payload renamed to .txt is not a text document.
    content.decode('utf-8-sig')
    return content


def _listed_files(root):
    paths = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            p = Path(parent) / name
            if p.is_symlink():
                raise InputManifestError('输入根不允许链接目录: ' + str(p))
        for name in files:
            p = Path(parent) / name
            _plain_file(p)
            paths.add(p.relative_to(root).as_posix())
    return paths


def audit_sample(sample, base, controller_path):
    if not isinstance(sample, dict):
        raise InputManifestError('样本必须是对象')
    missing = set(REQUIRED_FIELDS) - set(sample)
    unknown = set(sample) - ALLOWED_FIELDS
    if missing or unknown:
        raise InputManifestError('缺少字段: ' + ', '.join(sorted(missing)) + '；未声明字段: ' + ', '.join(sorted(unknown)))
    for key in ('sample_id', 'repository', 'source', 'license', 'buggy_commit', 'fix_commit', 'issue_source', 'issue'):
        if not isinstance(sample[key], str) or not sample[key].strip():
            raise InputManifestError(key + ' 必须是非空字符串')
    for key in ('buggy_commit', 'fix_commit'):
        if not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', sample[key]):
            raise InputManifestError(key + ' 必须是完整提交哈希')
    env = sample['environment']
    if not isinstance(env, dict) or set(env) != {'os', 'python', 'dependencies', 'install_command'}:
        raise InputManifestError('environment 必须且只能含 os/python/dependencies/install_command')
    for key in ('os', 'python', 'install_command'):
        if not isinstance(env[key], str) or not env[key].strip():
            raise InputManifestError('environment.' + key + ' 必须是非空字符串')
    if not isinstance(env['dependencies'], list) or any(not isinstance(x, str) or not x.strip() for x in env['dependencies']):
        raise InputManifestError('environment.dependencies 必须是字符串数组（无依赖用 []）')
    for key in ('regression_test', 'before_gold_validation', 'independent_test_result'):
        if not isinstance(sample[key], dict) or not sample[key]:
            raise InputManifestError(key + ' 必须是非空对象；未执行时显式记录 pending')
    spec = sample['model_input_manifest']
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'root', 'files'}
            or type(spec['schema_version']) is not int or spec['schema_version'] != 1):
        raise InputManifestError('model_input_manifest v1 必须且只能含 schema_version/root/files')
    root = _resolve(base, spec['root'])
    if not root.is_dir() or root.is_symlink():
        raise InputManifestError('模型输入 root 必须是普通目录')
    _external(controller_path.resolve(), root, '样本 manifest')
    entries = spec['files']
    if not isinstance(entries, list) or not entries:
        raise InputManifestError('model_input_manifest.files 必须是非空数组')
    seen, sources, contents = set(), 0, {}
    for entry in entries:
        content = _entry_bytes(root, entry)
        rel = entry['path']
        if rel.casefold() in seen:
            raise InputManifestError('输入路径重复（含大小写冲突）: ' + rel)
        seen.add(rel.casefold())
        contents[rel] = content
        sources += entry['kind'] == 'source'
    if not sources:
        raise InputManifestError('输入至少需要一个 Python 源文件')
    actual = _listed_files(root)
    if actual != set(contents):
        raise InputManifestError('实际输入与允许清单不一致；未声明=' + repr(sorted(actual-set(contents)))
                                 + '；缺失=' + repr(sorted(set(contents)-actual)))
    references = sample['reference_materials']
    if not isinstance(references, list) or not references:
        raise InputManifestError('reference_materials 必须显式列出仓库外的答案、标注或独立测试材料')
    reference_receipts, digests = [], set()
    for entry in references:
        if not isinstance(entry, dict) or set(entry) != {'path', 'role', 'sha256'} or entry['role'] not in REFERENCE_ROLES:
            raise InputManifestError('参考材料必须且只能含 path/role/sha256，role 必须为受支持的控制端材料类型')
        raw_path = base / entry['path']
        _plain_file(raw_path)
        reference = _resolve(base, entry['path'])
        _external(reference, root, '参考/答案材料')
        digest = sha256(reference.read_bytes())
        if entry['sha256'] != digest:
            raise InputManifestError('参考材料哈希不匹配: ' + entry['path'])
        if reference in digests:
            raise InputManifestError('参考材料路径重复: ' + entry['path'])
        digests.add(reference)
        if digest in {sha256(v) for v in contents.values()}:
            raise InputManifestError('参考材料字节出现在模型输入中（可能被改名）: ' + entry['path'])
        reference_receipts.append(dict(path=str(reference), role=entry['role'], sha256=digest))
    return dict(schema_version=1, sample_id=sample['sample_id'], root=str(root),
                issue_sha256=sha256(sample['issue'].encode('utf-8')),
                files=[dict(e) for e in entries], reference_materials=reference_receipts,
                manifest_sha256=sha256(json.dumps(sample, sort_keys=True, ensure_ascii=False).encode('utf-8')),
                scope='路径/字节/实际清单校验；元数据声明不证明真实复现或语义无答案')


def read_samples(path):
    controller = Path(path).resolve(strict=True)
    data = json.loads(controller.read_text(encoding='utf-8-sig'))
    if isinstance(data, dict) and set(data) == {'samples'}:
        data = data['samples']
    if not isinstance(data, list) or not data:
        raise InputManifestError('manifest 顶层必须为非空数组或仅含 samples 数组的对象')
    return controller, data


def validate_manifest(path):
    receipts, errors = [], []
    try:
        controller, samples = read_samples(path)
    except (OSError, ValueError, TypeError) as exc:
        return [], [str(exc)]
    seen, fixes = set(), set()
    for index, sample in enumerate(samples, 1):
        try:
            receipt = audit_sample(sample, controller.parent, controller)
            key = (sample['repository'].rstrip('/'), sample['fix_commit'].lower())
            if receipt['sample_id'] in seen or key in fixes:
                raise InputManifestError('sample_id 或同仓库 fix_commit 重复；不能重复计为独立 Bug')
            seen.add(receipt['sample_id'])
            fixes.add(key)
            receipts.append(receipt)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            errors.append(f'样本 {index}: {exc}')
    return receipts, errors


def pack_inputs(manifest_path, sample_id, destination):
    """Create a fresh small package with exactly the audited bytes, plus an external receipt."""
    receipts, errors = validate_manifest(manifest_path)
    if errors:
        raise InputManifestError('；'.join(errors))
    controller, samples = read_samples(manifest_path)
    matches = [(s, r) for s, r in zip(samples, receipts) if s['sample_id'] == sample_id]
    if len(matches) != 1:
        raise InputManifestError('找不到唯一 sample_id: ' + str(sample_id))
    sample, receipt = matches[0]
    root = Path(receipt['root'])
    dest = Path(destination).resolve()
    if dest.exists():
        raise InputManifestError('打包目标已存在，拒绝覆盖: ' + str(dest))
    _external(dest, root, '打包输出')
    if dest in controller.parents or any(dest == Path(r['path']) or dest in Path(r['path']).parents for r in receipt['reference_materials']):
        raise InputManifestError('打包目标不能包含控制端材料')
    # Reaudit after selection; changed manifests/files must not reuse an old receipt.
    current = audit_sample(sample, controller.parent, controller)
    if current != receipt:
        raise InputManifestError('材料在打包期间发生改变')
    dest.mkdir(parents=True)
    try:
        package = dest / 'repo'
        package.mkdir()
        packed = []
        for entry in receipt['files']:
            content = _entry_bytes(root, entry)
            p = package / entry['path']
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            packed.append(dict(entry, sha256=sha256(p.read_bytes())))
        if _listed_files(package) != {e['path'] for e in packed}:
            raise InputManifestError('实际打包内容与清单不符')
        if audit_sample(sample, controller.parent, controller) != current:
            raise InputManifestError('输入材料在复制期间发生改变')
        (dest / 'issue.txt').write_text(sample['issue'], encoding='utf-8')
        receipt = dict(receipt, packed_files=packed, package_root=str(package))
        (dest / 'input_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    except BaseException:
        shutil.rmtree(dest)
        raise
    return receipt


@contextmanager
def evaluation_inputs(repo_path, dataset_file, input_manifest='', sample_id=''):
    """Yield a restricted RepoMeta and audit, or explicitly label legacy unrestricted mode."""
    from .repo import import_repo
    original = Path(repo_path).resolve()
    dataset = Path(dataset_file).resolve()
    _external(dataset, original, '评测标签文件')
    if not input_manifest:
        if sample_id:
            raise InputManifestError('sample_id 需要同时指定 input_manifest')
        meta = import_repo(original)
        files = [{'path': p, 'sha256': sha256((original / p).read_bytes())}
                 for p in meta.python_files + meta.document_files]
        yield meta, dict(mode='repository_audit_only', indexed_files=files,
                         warning='未提供冻结输入清单；本次仅记录索引材料，不认证答案隔离')
        return
    _, errors = validate_manifest(input_manifest)
    if errors:
        raise InputManifestError('；'.join(errors))
    controller, samples = read_samples(input_manifest)
    selected = [s for s in samples if isinstance(s, dict) and s.get('sample_id') == sample_id]
    if len(selected) != 1:
        raise InputManifestError('隔离评测必须指定唯一 sample_id')
    selected_root = _resolve(controller.parent, selected[0].get('model_input_manifest', {}).get('root'))
    if selected_root != original:
        raise InputManifestError('--repo 必须与所选样本的模型输入 root 一致')
    _external(dataset, selected_root, '评测标签文件')
    from .eval_metrics import load_dataset
    items = load_dataset(dataset)
    if len(items) != 1 or str(items[0]['id']) != sample_id or items[0]['issue'] != selected[0]['issue']:
        raise InputManifestError('隔离评测一次只接收与 sample_id/issue 一致的冻结样本')
    workspace = Path(tempfile.mkdtemp(prefix='replens_eval_inputs_'))
    try:
        receipt = pack_inputs(input_manifest, sample_id, workspace / 'package')
        dataset_digest = sha256(dataset.read_bytes())
        if dataset_digest in {e['sha256'] for e in receipt['packed_files']}:
            raise InputManifestError('评测标签字节出现在输入包内')
        meta = import_repo(receipt['package_root'])
        meta.python_files = [e['path'] for e in receipt['packed_files'] if e['kind'] == 'source']
        meta.document_files = [e['path'] for e in receipt['packed_files'] if e['kind'] == 'document']
        receipt.update(mode='isolated_allowlist', indexed_files=[e for e in receipt['packed_files']
                                                               if e['kind'] in {'source', 'document'}],
                       query_sha256=sha256(items[0]['issue'].encode('utf-8')),
                       dataset_sha256=dataset_digest,
                       failure_logs_used=False)
        yield meta, receipt
    finally:
        shutil.rmtree(workspace)
