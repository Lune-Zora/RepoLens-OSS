"""本地 CI 运行器：在本机按 ci.yml 的同样步骤顺序执行并出报告。

存在原因（2026-10-28）：
  远端是 Gitee，`.github/workflows/ci.yml` 在那里**不会被自动执行**，
  等于 CI 从未真正验证过。本脚本让 CI 的每一步都能在本机实跑并留下证据，
  平台侧只须镜像同一批命令。

用法：
    python scripts/run_ci.py                 # 跑全部步骤
    python scripts/run_ci.py --list          # 只列步骤不执行
    python scripts/run_ci.py --only 3,4      # 只跑指定序号
    python scripts/run_ci.py --keep-going    # 失败后继续，最后汇总

退出码：0 全部通过；1 存在失败。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 报告写入 reports/ci/（**入库**），不是 reports/runs/（本地临时输出，已被 .gitignore 排除）。
# 原因：reports/runs/ 被忽略时，CI 通过的声明在仓库里没有任何可查证据。
REPORT_DIR = ROOT / "reports" / "ci"
# 每次覆盖的稳定指针，便于直接查看最近一次结果，避免翻找时间戳文件名。
LATEST = REPORT_DIR / "最新一次.json"

# (序号自增) 名称 / 命令 / 期望退出码，与 .github/workflows/ci.yml 一一对应
STEPS: list[tuple[str, list[str], int]] = [
    (
        "环境自检",
        [sys.executable, "scripts/check_env.py", "--with-web"],
        0,
    ),
    (
        "数据集口径校验",
        [sys.executable, "scripts/validate_datasets.py"],
        0,
    ),
    (
        "单元与集成测试",
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        0,
    ),
    (
        "端到端演示（规则自动补丁，离线）",
        [
            sys.executable, "-m", "replens", "run", "examples/demo_repo",
            "--issue", "date string with surrounding whitespace fails to parse",
            "--test-cmd", "pytest tests/test_parser.py", "--auto-patch",
            "--acceptance-suite", "examples/acceptance/demo.json",
            "--require-verified", "--no-provenance",
            "--output", "reports/runs/ci_demo.md",
        ],
        0,
    ),
    (
        "拒绝未验证修复（报告仍写出）",
        [
            sys.executable, "-m", "replens", "run", "examples/demo_repo",
            "--issue", "date string with surrounding whitespace fails to parse",
            "--test-cmd", "pytest tests/test_parser.py", "--auto-patch",
            "--require-verified", "--no-provenance", "--format", "json",
            "--output", "reports/runs/ci_unverified.json",
        ],
        # replens 用退出码 2 表示「跑通但结论为 UNVERIFIED」，此处属预期行为
        2,
    ),
    (
        "检索评测",
        [
            sys.executable, "-m", "replens", "eval", "examples/demo_repo",
            "--dataset", "eval/datasets/dataset.json",
        ],
        0,
    ),
    (
        "基线对比",
        [
            sys.executable, "eval/baselines.py", "examples/demo_repo",
            "--dataset", "eval/datasets/dataset.json",
        ],
        0,
    ),
    (
        "Web 冒烟",
        [sys.executable, "scripts/check_web.py"],
        0,
    ),
]


def _run_step(index: int, name: str, command: list[str], expect: int) -> dict:
    started = time.time()
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = round(time.time() - started, 2)
    passed = result.returncode == expect
    return {
        "index": index,
        "name": name,
        "command": " ".join(command),
        "expected_exit": expect,
        "actual_exit": result.returncode,
        "passed": passed,
        "duration_seconds": duration,
        "stdout_tail": (result.stdout or "").strip().splitlines()[-12:],
        "stderr_tail": (result.stderr or "").strip().splitlines()[-12:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RepoLens-OSS 本地 CI 运行器")
    parser.add_argument("--list", action="store_true", help="只列步骤不执行")
    parser.add_argument("--only", default="", help="逗号分隔的步骤序号，从 1 开始")
    parser.add_argument("--keep-going", action="store_true", help="失败后继续执行剩余步骤")
    parser.add_argument("--json-only", action="store_true", help="只输出 JSON 报告")
    args = parser.parse_args()

    if args.list:
        for index, (name, command, expect) in enumerate(STEPS, 1):
            print(f"{index}. {name}  (期望退出码 {expect})")
            print("   " + " ".join(command))
        return 0

    selected = list(range(1, len(STEPS) + 1))
    if args.only:
        selected = [int(x) for x in args.only.split(",") if x.strip()]

    results: list[dict] = []
    for index, (name, command, expect) in enumerate(STEPS, 1):
        if index not in selected:
            continue
        if not args.json_only:
            print(f"[{index}/{len(STEPS)}] {name} ... ", end="", flush=True)
        record = _run_step(index, name, command, expect)
        results.append(record)
        if not args.json_only:
            mark = "通过" if record["passed"] else "失败"
            print(f"{mark}（{record['duration_seconds']}s，退出码 {record['actual_exit']}）")
            if not record["passed"]:
                for line in (record["stderr_tail"] or record["stdout_tail"])[-15:]:
                    print(f"      {line}")
        if not record["passed"] and not args.keep_going:
            break

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    report_path = REPORT_DIR / f"ci_local_{stamp}.json"
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "repo_root": str(ROOT),
        "steps": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
            "failed": sum(1 for r in results if not r["passed"]),
        },
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    LATEST.write_text(text, encoding="utf-8")

    summary = payload["summary"]
    if args.json_only:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print()
        print(f"共 {summary['total']} 步：通过 {summary['passed']}，失败 {summary['failed']}")
        print(f"报告：{report_path.relative_to(ROOT).as_posix()}")
        print("提醒：改动本脚本的 STEPS 后，须同步 .github/workflows/ci.yml。")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
