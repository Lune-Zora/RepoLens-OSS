"""环境冻结：Python、平台、已安装发行包与上游环境文件。"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys

from . import common, constants, errors, gitops


def _freeze_environment(output, logs, repo, parent):
    if importlib.util.find_spec('pytest') is None:
        raise errors.RestoreError('environment', '当前解释器没有 pytest；请先准备冻结的离线测试环境')
    command = [sys.executable, '-m', 'pytest', '--version']
    check = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    common._save(logs / 'python-pytest-version.json', json.dumps({
        'argv': command, 'returncode': check.returncode, 'stdout': check.stdout,
        'stderr': check.stderr}, ensure_ascii=False, indent=2).encode('utf-8'))
    if check.returncode:
        raise errors.RestoreError('environment', 'pytest 无法通过当前 Python 解释器启动')
    from replens.provenance import _environment
    environment = _environment()
    freeze_text = ''.join(name + '==' + version + '\n'
                          for name, version in environment['packages'].items())
    freeze_bytes = freeze_text.encode('utf-8')
    common._save(output / 'requirements.freeze.txt', freeze_bytes)
    environment.update({'pytest_version_output': check.stdout.strip(),
                        'installed_distributions_lock_sha256': common._sha(freeze_bytes),
                        'test_profile': {'name': 'synchronous-reset-focused',
                                         'primary_command': constants.MAIN_COMMAND,
                                         'acceptance_command': constants.ACCEPTANCE_COMMAND,
                                         'inherited_addopts': 'cleared explicitly; optional asyncio plugin not used',
                                         'full_upstream_suite_run': False},
                        'policy': '同一解释器、平台与完整发行包名称/版本清单；不执行联网安装；锁文件不含分发文件哈希'})
    tree = gitops._git(repo, ['ls-tree', '-r', '--name-only', parent], logs,
                'environment-input-tree').stdout.splitlines()
    patterns = ('pyproject.toml', 'setup.py', 'setup.cfg', 'tox.ini', 'pytest.ini')
    requirements = [item for item in tree if '/' not in item and
                    (item in patterns or (item.startswith('requirements') and item.endswith('.txt')))]
    environment['upstream_environment_files'] = []
    for relative in requirements:
        content = gitops._git_bytes(repo, ['show', parent + ':' + relative], logs,
                             'environment-file-' + re.sub(r'[^a-zA-Z0-9_.-]', '_', relative))
        common._save(output / 'upstream-environment' / relative, content)
        environment['upstream_environment_files'].append({'path': relative, 'sha256': common._sha(content)})
    common._save(output / 'environment.json', json.dumps(environment, ensure_ascii=False,
                                                   indent=2).encode('utf-8'))
    return environment
