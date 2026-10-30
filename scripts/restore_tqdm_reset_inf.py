"""Validate one known tqdm fix before it is used in any model experiment.

The supplied repository must already contain both upstream commits. Gold patches,
independent tests, raw logs and the resulting provenance package belong to the
external control output directory, never to the repository supplied to a model.

2026-10-28：原 695 行单文件已按职责拆分到 `scripts/realbug/`。
本文件保留为**入口与兼容层**，对外契约不变：

- 模块级符号名与函数签名全部保留（`control._git`、`control.run`、`control.RestoreError` 等）；
- CLI 参数 `--repo` / `--output` / `--work-dir` / `--timeout` 与退出码 0 / 2 不变；
- 子模块作为属性暴露（`control.gitops._git`），便于按模块打补丁。

调用方式不变：

    python scripts/restore_tqdm_reset_inf.py --repo <tqdm-clone> \
        --work-dir <临时父目录> --output <尚不存在的控制输出目录>
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许同目录的 realbug 包被导入（脚本可能以任意 cwd 启动）
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from realbug import (  # noqa: E402
    ACCEPTANCE_COMMAND,
    ACCEPTANCE_PATH,
    ACCEPTANCE_SOURCE,
    BUGGY_PARENT,
    FIX_COMMIT,
    ISSUE,
    MAIN_COMMAND,
    SOURCE_PATH,
    TEST_PATH,
    RestoreError,
    _add_worktree,
    _apply_test_patch,
    _capture_suite,
    _freeze_environment,
    _git,
    _git_bytes,
    _git_command,
    _is_within,
    _license_info,
    _parse_diff_paths,
    _phase_summary,
    _remove_managed_directory,
    _repository_fingerprint,
    _safe_remote_identity,
    _same_test_states,
    _save,
    _save_suite_logs,
    _sha,
    _source_identity,
    _target_state,
    _verify_upstream,
    common,
    constants,
    environment,
    errors,
    gitops,
    main,
    pipeline,
    run,
    suites,
    upstream,
)

__all__ = [
    "main", "run", "RestoreError",
    "_sha", "_is_within", "_save", "_repository_fingerprint",
    "_git_command", "_git", "_git_bytes", "_parse_diff_paths",
    "_safe_remote_identity", "_save_suite_logs", "_capture_suite",
    "_target_state", "_same_test_states", "_license_info", "_source_identity",
    "_remove_managed_directory", "_verify_upstream", "_freeze_environment",
    "_add_worktree", "_apply_test_patch", "_phase_summary",
    "FIX_COMMIT", "BUGGY_PARENT", "SOURCE_PATH", "TEST_PATH", "ISSUE",
    "MAIN_COMMAND", "ACCEPTANCE_PATH", "ACCEPTANCE_COMMAND", "ACCEPTANCE_SOURCE",
    "common", "constants", "environment", "errors", "gitops", "pipeline", "suites", "upstream",
]


if __name__ == "__main__":
    raise SystemExit(main())
