"""命令行入口。"""
from __future__ import annotations

import argparse
import json
import shutil
import sys

from . import errors, pipeline

CLI_DESCRIPTION = (
    'Validate one known tqdm fix before it is used in any model experiment. '
    'The supplied repository must already contain both upstream commits. '
    'Gold patches, independent tests, raw logs and the provenance package '
    'belong to the external control output directory.'
)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=CLI_DESCRIPTION)
    parser.add_argument('--repo', required=True, help='已包含样本两个完整提交的本地 tqdm Git 仓库')
    parser.add_argument('--output', required=True, help='仓库外、尚不存在的控制材料与证据输出目录')
    parser.add_argument('--work-dir', required=True, help='仓库外、已存在的临时 worktree 父目录')
    parser.add_argument('--timeout', type=int, default=180,
                        help='每个隔离测试阶段的超时秒数（默认 180）')
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout 必须为正数')
    if shutil.which('git') is None:
        parser.error('PATH 中找不到 Git')
    try:
        record = pipeline.run(args)
    except (errors.RestoreError, OSError, ValueError) as exc:
        print('真实 Bug 恢复失败：' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': record['status'], 'sample_id': record['sample_id'],
                      'evidence_bundle': record['evidence_bundle'],
                      'materials_status': record['cross_worktree_replay']['materials_status'],
                      'replay_status': record['cross_worktree_replay']['replay_status'],
                      'repair_status': record['cross_worktree_replay']['repair_status'],
                      'model_repair_credit': record['model_repair_credit'],
                      'cleanup_errors': record.get('cleanup_errors', [])},
                     ensure_ascii=False, indent=2))
    return 0 if record['status'] == 'PASS' else 2
