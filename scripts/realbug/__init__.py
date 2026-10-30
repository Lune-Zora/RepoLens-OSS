"""tqdm reset(inf) 单样本恢复控制端。

拆分自原先 695 行的单文件脚本（2026-10-28）。对外契约不变：
模块级符号名、函数签名、CLI 参数与退出码全部保持原样。

分工：

| 模块 | 职责 |
|---|---|
| `constants` | 提交哈希、路径、测试命令等固定常量 |
| `errors` | `RestoreError` |
| `common` | 哈希、路径包含判断、写入 |
| `gitops` | Git 命令执行、日志落盘、worktree、补丁应用、只读审计 |
| `suites` | 测试套件执行与前后状态比对 |
| `upstream` | remote 身份、许可、提交父子关系、目标文件核验 |
| `environment` | Python、平台、发行包与上游环境文件冻结 |
| `pipeline` | 主流程编排 |
| `cli` | 命令行入口 |

调用方应通过本包或 `scripts/restore_tqdm_reset_inf.py` 使用，
不要直接依赖子模块内部结构。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许子模块在调用期 from replens.* 导入（核心包位于仓库根目录）
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from . import common, constants, environment, errors, gitops, pipeline, suites, upstream  # noqa: E402
from .cli import main  # noqa: E402
from .common import _is_within, _save, _sha  # noqa: E402
from .constants import (  # noqa: E402
    ACCEPTANCE_COMMAND,
    ACCEPTANCE_PATH,
    ACCEPTANCE_SOURCE,
    BUGGY_PARENT,
    FIX_COMMIT,
    ISSUE,
    MAIN_COMMAND,
    SOURCE_PATH,
    TEST_PATH,
)
from .environment import _freeze_environment  # noqa: E402
from .errors import RestoreError  # noqa: E402
from .gitops import (  # noqa: E402
    _add_worktree,
    _apply_test_patch,
    _git,
    _git_bytes,
    _git_command,
    _parse_diff_paths,
    _remove_managed_directory,
    _repository_fingerprint,
    _safe_remote_identity,
)
from .pipeline import run  # noqa: E402
from .suites import (  # noqa: E402
    _capture_suite,
    _phase_summary,
    _same_test_states,
    _save_suite_logs,
    _target_state,
)
from .upstream import _license_info, _source_identity, _verify_upstream  # noqa: E402

__all__ = [
    "RestoreError", "main", "run",
    "_sha", "_is_within", "_save",
    "_repository_fingerprint", "_git_command", "_git", "_git_bytes",
    "_parse_diff_paths", "_safe_remote_identity",
    "_remove_managed_directory", "_add_worktree", "_apply_test_patch",
    "_save_suite_logs", "_capture_suite", "_target_state",
    "_same_test_states", "_phase_summary",
    "_license_info", "_source_identity", "_verify_upstream",
    "_freeze_environment",
    "FIX_COMMIT", "BUGGY_PARENT", "SOURCE_PATH", "TEST_PATH", "ISSUE",
    "MAIN_COMMAND", "ACCEPTANCE_PATH", "ACCEPTANCE_COMMAND", "ACCEPTANCE_SOURCE",
    "common", "constants", "environment", "errors", "gitops", "pipeline", "suites", "upstream",
]
