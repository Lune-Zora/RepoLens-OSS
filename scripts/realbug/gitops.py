"""Git 操作薄封装：命令执行、日志落盘、worktree、补丁应用、只读审计。"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from urllib.parse import urlsplit

from . import common, constants, errors


def _repository_fingerprint(repo):
    """Read-only audit including .git; distinct from the portable source hash."""
    root = Path(repo).resolve()
    files = {}
    for parent, dirs, names in os.walk(root):
        for name in list(dirs):
            path = Path(parent) / name
            if path.is_symlink():
                dirs.remove(name)
                files[path.relative_to(root).as_posix()] = common._sha(os.readlink(path).encode('utf-8'))
        for name in names:
            path = Path(parent) / name
            digest = hashlib.sha256()
            if path.is_symlink():
                digest.update(os.readlink(path).encode('utf-8'))
            else:
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(65536), b''):
                        digest.update(block)
            files[path.relative_to(root).as_posix()] = digest.hexdigest()
    from replens.provenance import _tree_hash
    return {'files': len(files), 'tree_sha256_including_git': _tree_hash(files)}


def _git_command(repo, args):
    # Trust only this explicitly supplied/local managed path for this process.
    # The desktop user's checkout may be owned by a different Windows SID.
    trusted = Path(repo).resolve().as_posix()
    command = ['git', '--no-optional-locks', '-c', 'safe.directory=' + trusted]
    if (Path(repo) / '.git').is_dir():
        command += ['-c', 'safe.directory=' + (Path(repo) / '.git').resolve().as_posix()]
    return [*command, '-C', str(repo), *args]


def _git(repo, args, logs, label, check=True):
    command = _git_command(repo, args)
    proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    log = logs / 'git' / (label + '.json')
    common._save(log, json.dumps({'argv': command, 'returncode': proc.returncode,
                           'stdout': proc.stdout, 'stderr': proc.stderr},
                          ensure_ascii=False, indent=2).encode('utf-8'))
    if check and proc.returncode != 0:
        raise errors.RestoreError(label, 'Git 命令失败：' + ' '.join(args) + '\n' + proc.stderr[-2000:])
    return proc


def _parse_diff_paths(data):
    paths = set()
    for line in data.decode('utf-8', 'replace').splitlines():
        if line.startswith(('--- ', '+++ ')):
            value = line[4:].split('\t', 1)[0]
            if value != '/dev/null':
                if value.startswith(('a/', 'b/')):
                    value = value[2:]
                paths.add(value)
    return paths


def _git_bytes(repo, args, logs, label):
    command = _git_command(repo, args)
    proc = subprocess.run(command, capture_output=True)
    common._save(logs / 'git' / (label + '.stdout.bin'), proc.stdout)
    common._save(logs / 'git' / (label + '.stderr.bin'), proc.stderr)
    common._save(logs / 'git' / (label + '.json'), json.dumps({
        'argv': command, 'returncode': proc.returncode,
        'stdout_sha256': common._sha(proc.stdout), 'stderr_sha256': common._sha(proc.stderr),
    }, ensure_ascii=False, indent=2).encode('utf-8'))
    if proc.returncode:
        raise errors.RestoreError(label, 'Git 命令失败：' + ' '.join(args) + '\n'
                           + proc.stderr.decode('utf-8', 'replace')[-2000:])
    return proc.stdout


def _safe_remote_identity(repo, name, logs, label):
    command = _git_command(repo, ['remote', 'get-url', name])
    proc = subprocess.run(command, capture_output=True, text=True,
                          encoding='utf-8', errors='replace')
    raw = proc.stdout.strip()
    if '://' in raw:
        parsed = urlsplit(raw)
        host, path = (parsed.hostname or '').lower(), parsed.path.strip('/')
    else:
        match = re.fullmatch(r'(?:[^@/:]+@)?([^:]+):(.+)', raw)
        host, path = (match.group(1).lower(), match.group(2).strip('/')) if match else ('', '')
    canonical_path = path.removesuffix('.git').lower()
    identity = host + '/' + canonical_path if host and canonical_path else 'unrecognized'
    common._save(logs / 'git' / (label + '.json'), json.dumps({
        'argv': command, 'returncode': proc.returncode, 'safe_remote_identity': identity,
        'stderr': proc.stderr[-1000:]}, ensure_ascii=False, indent=2).encode('utf-8'))
    if proc.returncode:
        raise errors.RestoreError('upstream-verification', '无法读取本地 Git remote：' + name)
    return host, canonical_path, identity


def _remove_managed_directory(managed, work_parent):
    managed, work_parent = Path(managed).resolve(), Path(work_parent).resolve()
    if managed == work_parent or not common._is_within(managed, work_parent):
        raise ValueError('拒绝清理受管临时父目录以外的路径')
    def writable_retry(function, path, exc_info):
        if not common._is_within(path, managed):
            raise ValueError('拒绝改变受管目录以外的文件权限')
        os.chmod(path, stat.S_IWRITE)
        function(path)
    shutil.rmtree(managed, onerror=writable_retry)


def _add_worktree(repo, path, commit, logs, label):
    _git(repo, ['worktree', 'add', '--quiet', '--detach', str(path), commit], logs, label)
    marker = Path(path) / '.git'
    if not marker.is_file():
        raise errors.RestoreError(label, '新建的 Git worktree 缺少预期的 .git 文件')


def _apply_test_patch(repo, path, patch, logs, label):
    _git(path, ['apply', '--check', str(patch)], logs, label + '-check')
    _git(path, ['apply', str(patch)], logs, label + '-apply')
    changed = set()
    for row in _git(path, ['status', '--porcelain', '--untracked-files=all'], logs,
                    label + '-status').stdout.splitlines():
        if len(row) > 3:
            changed.add(row[3:].replace('\\', '/'))
    if changed != {constants.TEST_PATH}:
        raise errors.RestoreError(label, '测试补丁修改路径超出预期：' + repr(sorted(changed)))
