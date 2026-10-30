"""上游来源核验：remote 身份、许可、提交父子关系、目标文件。"""
from __future__ import annotations

from pathlib import Path
import json
import re

from . import common, constants, errors, gitops


def _license_info(repo, parent, logs):
    names = gitops._git(repo, ['ls-tree', '-r', '--name-only', parent], logs, 'license-tree').stdout.splitlines()
    candidates = [name for name in names if '/' not in name and name.upper() in {
        'LICENSE', 'LICENSE.TXT', 'LICENSE.MD', 'LICENCE', 'LICENCE.TXT',
        'LICENCE.MD', 'COPYING', 'COPYING.TXT'}]
    if not candidates:
        raise errors.RestoreError('upstream-verification', 'buggy commit 缺少可核验的根目录许可证文件')
    relative = candidates[0]
    data = gitops._git_bytes(repo, ['show', parent + ':' + relative], logs, 'license-content')
    text = data.decode('utf-8', 'replace')
    if 'MIT License' not in text or 'MPL-2.0' not in text:
        raise errors.RestoreError('upstream-verification', 'tqdm 许可文本未包含预期的 MIT 和 MPL-2.0 声明')
    common._save(logs.parent / 'upstream-license' / relative, data)
    project = gitops._git_bytes(repo, ['show', parent + ':pyproject.toml'], logs, 'license-declaration')
    if b'MPL-2.0 AND MIT' not in project:
        raise errors.RestoreError('upstream-verification', '项目许可声明与预期 MPL-2.0 AND MIT 不符')
    return {'path': relative, 'sha256': common._sha(data), 'detected': 'MPL-2.0 AND MIT',
            'bytes': len(data), 'confidence': True,
            'declared_expression': 'MPL-2.0 AND MIT', 'components': ['MPL-2.0', 'MIT']}


def _source_identity(inspected):
    identities = {row['identity'] for row in inspected}
    if 'github.com/tqdm/tqdm' in identities:
        return 'github.com/tqdm/tqdm', 'canonical-remote'
    if 'gitee.com/mirrors/tqdm' in identities:
        return 'gitee.com/mirrors/tqdm', 'mirror-with-pinned-commits'
    raise errors.RestoreError('upstream-verification', '没有识别到 tqdm 的官方或已列明镜像 remote')


def _verify_upstream(repo, logs):
    top = Path(gitops._git(repo, ['rev-parse', '--show-toplevel'], logs, 'repo-root').stdout.strip()).resolve()
    if top != Path(repo).resolve():
        raise errors.RestoreError('upstream-verification', '传入路径不是 Git 仓库根目录')
    remotes = gitops._git(repo, ['remote'], logs, 'remote-names').stdout.splitlines()
    official_remote = ''
    inspected = []
    for name in remotes:
        host, canonical_path, safe_identity = gitops._safe_remote_identity(
            repo, name, logs, 'remote-' + re.sub(r'[^a-zA-Z0-9_.-]', '_', name))
        inspected.append({'name': name, 'identity': safe_identity})
        if host == 'github.com' and canonical_path == 'tqdm/tqdm':
            official_remote = safe_identity
    common._save(logs / 'upstream-remote.json', json.dumps({'remotes': inspected,
             'official_tqdm_remote_found': bool(official_remote)},
             ensure_ascii=False, indent=2).encode('utf-8'))
    source_remote, source_kind = _source_identity(inspected)
    parent = gitops._git(repo, ['rev-parse', '--verify', constants.BUGGY_PARENT + '^{commit}'],
                  logs, 'resolve-buggy-parent').stdout.strip()
    fix = gitops._git(repo, ['rev-parse', '--verify', constants.FIX_COMMIT + '^{commit}'],
               logs, 'resolve-fix-commit').stdout.strip()
    if parent != constants.BUGGY_PARENT or fix != constants.FIX_COMMIT:
        raise errors.RestoreError('upstream-verification', '提交解析结果不是记录中的完整 commit')
    ancestry = gitops._git(repo, ['rev-list', '--parents', '-n', '1', fix], logs, 'fix-parent').stdout.split()
    if len(ancestry) != 2 or ancestry[1] != parent:
        raise errors.RestoreError('upstream-verification', '修复提交的唯一父提交与记录的 buggy_parent 不符')
    gitops._git(repo, ['fsck', '--full', '--no-reflogs', '--no-dangling', parent, fix],
         logs, 'git-object-integrity')
    source = gitops._git_bytes(repo, ['show', parent + ':' + constants.SOURCE_PATH], logs, 'buggy-source')
    if b'class tqdm' not in source or b'def reset(' not in source:
        raise errors.RestoreError('upstream-verification', '目标源码与 tqdm/std.py:tqdm.reset 不符')
    test_patch = gitops._git_bytes(repo, ['diff', '--no-ext-diff', '--binary', parent, fix,
                                   '--', constants.TEST_PATH], logs, 'test-patch')
    gold_patch = gitops._git_bytes(repo, ['diff', '--no-ext-diff', '--binary', parent, fix,
                                   '--', constants.SOURCE_PATH], logs, 'gold-patch')
    if gitops._parse_diff_paths(test_patch) != {constants.TEST_PATH} or gitops._parse_diff_paths(gold_patch) != {constants.SOURCE_PATH}:
        raise errors.RestoreError('upstream-verification', '上游测试/生产补丁路径超出该样本的冻结范围')
    if b'test_reset_inf' not in test_patch or b'total is None' not in test_patch:
        raise errors.RestoreError('upstream-verification', '上游测试补丁未包含预期的 reset(inf) 行为断言')
    if b'total == float("inf")' not in gold_patch or b'self.total = None' not in gold_patch:
        raise errors.RestoreError('upstream-verification', '上游生产补丁未将无限 total 归一为未知值')
    common._save(logs.parent / 'patches' / 'upstream-test.patch', test_patch)
    common._save(logs.parent / 'patches' / 'upstream-gold.patch', gold_patch)
    metadata = gitops._git(repo, ['show', '-s', '--format=%H%n%P%n%s%n%cI', fix], logs,
                    'fix-metadata').stdout.splitlines()
    return {'repository_root': str(top), 'official_remote': official_remote, 'project': 'tqdm',
            'source_remote': source_remote, 'source_kind': source_kind,
            'canonical_repository': 'https://github.com/tqdm/tqdm',
            'network_origin_verification': 'NOT_PERFORMED',
            'identity_basis': 'pinned full commit IDs, parent relation and local git fsck --full',
            'buggy_parent': parent, 'fix_commit': fix,
            'commit_subject': metadata[2] if len(metadata) > 2 else '',
            'commit_time': metadata[3] if len(metadata) > 3 else '',
            'source_path': constants.SOURCE_PATH, 'source_sha256_at_parent': common._sha(source),
            'test_path': constants.TEST_PATH, 'test_patch_sha256': common._sha(test_patch),
            'gold_patch_sha256': common._sha(gold_patch), 'license': _license_info(repo, parent, logs)}
