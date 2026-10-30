"""把挖掘并验证通过的候选提升为正式修复评测集。

存在原因（2026-10-29）：
  `eval/mined/` 里的候选长期停在「定位级」，无法进入正式分母。两个原因：
  1. 旧版 miner 丢弃 commit/parent，候选无法还原 buggy 基线（已修）；
  2. 验证用「整个测试文件」，文件里无关用例失败就会把有效修复误判为未通过（已修）。
  本脚本负责最后一步：去重、校验字段、写成符合 schema 的修复集。

用法：
    python scripts/promote_mined.py mined/*.json --output eval/datasets/real_dataset_mined.json
    python scripts/promote_mined.py mined/*.json --output ... --dry-run

退出码：0 成功；1 输入不合法。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replens.eval_metrics import parse_expected  # noqa: E402

DATA_DIR = ROOT / "eval" / "datasets"
REPAIR_FIELDS = ("project", "buggy_parent", "fix_commit", "test_cmd")
# 已有的正式修复集，用于去重；提升不得与它们重复。
EXISTING_REPAIR = ("real_dataset_loguru.json", "real_dataset_extended.json")
# 人工通读判定：verdict=exclude 的提交不进入正式分母。
VERDICTS = DATA_DIR / "人工复核判定.json"


def _load_verdicts():
    """读取人工判定，返回 {fix_commit: (verdict, reason)}。"""
    if not VERDICTS.exists():
        return {}
    data = json.loads(VERDICTS.read_text(encoding="utf-8"))
    return {v["fix_commit"]: (v["verdict"], v.get("reason", ""))
            for v in data.get("verdicts", [])}


def _normalize_issue(text: str) -> str:
    return " ".join(str(text).lower().replace("`", "").split())


def _test_cmd(targets) -> str:
    """与验证时实际执行的命令保持一致，便于复现。"""
    return "pytest " + " ".join(targets)


def _same_commit(candidate: str, known: set) -> bool:
    """按前缀比对提交哈希。

    已有数据集里用的是 12 位短哈希，而 miner 输出 40 位完整哈希；
    直接等值比较会漏判重复（2026-10-29 实测：loguru 有 2 条漏判）。
    """
    if not candidate:
        return False
    probe = candidate.lower()
    for item in known:
        other = item.lower()
        shorter = min(len(probe), len(other))
        if shorter >= 7 and probe[:shorter] == other[:shorter]:
            return True
    return False


def _load_existing() -> tuple:
    commits = set()
    issues = set()
    for name in EXISTING_REPAIR:
        path = DATA_DIR / name
        if not path.exists():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            if item.get("category") != "bug":
                continue
            if item.get("fix_commit"):
                commits.add(item["fix_commit"])
            if item.get("issue"):
                issues.add((item.get("project"), _normalize_issue(item["issue"])))
    return commits, issues


def promote(paths, dry_run=False):
    seen_commits, seen_issues = _load_existing()
    verdicts = _load_verdicts()
    excluded = []
    promoted = []
    skipped = []

    for raw_path in paths:
        path = Path(raw_path)
        items = json.loads(path.read_text(encoding="utf-8"))
        for item in items:
            validation = item.get("validation") or {}
            if not validation.get("ok"):
                skipped.append((path.name, item.get("issue", "")[:48], "未通过验证"))
                continue
            missing = [f for f in ("buggy_parent", "fix_commit", "project") if not item.get(f)]
            if missing:
                skipped.append((path.name, item.get("issue", "")[:48], "缺字段 " + ",".join(missing)))
                continue
            targets = validation.get("targets") or []
            if not targets:
                skipped.append((path.name, item.get("issue", "")[:48], "缺验证目标，无法复现"))
                continue
            expected = item.get("expected") or []
            if not expected:
                skipped.append((path.name, item.get("issue", "")[:48], "缺 expected"))
                continue
            try:
                for entry in expected:
                    parse_expected(entry)
            except ValueError as exc:
                skipped.append((path.name, item.get("issue", "")[:48], "标注无效: " + str(exc)))
                continue

            commit = item["fix_commit"]
            # 人工判定为 exclude 的直接跳过（按提交前缀匹配，兼容短哈希）。
            hit = next((k for k in verdicts if _same_commit(commit, {k})), None)
            if hit and verdicts[hit][0] == "exclude":
                excluded.append((item.get("project"), commit[:10], verdicts[hit][1][:60]))
                continue
            issue_key = (item.get("project"), _normalize_issue(item.get("issue", "")))
            if _same_commit(commit, seen_commits):
                skipped.append((path.name, item.get("issue", "")[:48], "提交已存在"))
                continue
            if issue_key in seen_issues:
                skipped.append((path.name, item.get("issue", "")[:48], "同一问题已存在"))
                continue
            seen_commits.add(commit)
            seen_issues.add(issue_key)

            promoted.append({
                "id": len(promoted) + 1,
                "project": item["project"],
                "category": "bug",
                "fix_commit": commit,
                "buggy_parent": item["buggy_parent"],
                "test_cmd": _test_cmd(targets),
                "issue": item["issue"],
                # 出处随样本一起提升：丢了它，后续复核 C6 只能重新猜这段文字从哪来。
                "issue_source": item.get("issue_source", "commit_subject"),
                "expected": expected,
                "validation_scope": validation.get("scope", "unknown"),
            })

    return promoted, skipped, excluded


def main():
    parser = argparse.ArgumentParser(description="把已验证的挖掘候选提升为正式修复集")
    parser.add_argument("mined", nargs="+", help="miner 输出的 JSON（可多个）")
    parser.add_argument("--output", required=True, help="输出数据集路径")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    promoted, skipped, excluded = promote(args.mined)

    print("提升: %d 条" % len(promoted))
    for item in promoted:
        print("  + %-8s %-10s %s" % (item["project"], item["fix_commit"][:10], item["issue"][:56]))
    print("跳过: %d 条" % len(skipped))
    for name, issue, reason in skipped:
        print("  - [%s] %s | %s" % (reason, issue, name))
    if excluded:
        print("人工判定剔除: %d 条" % len(excluded))
        for project, commit, reason in excluded:
            print("  x [%s] %s %s" % (project, commit, reason))

    if not promoted:
        print("没有可提升的样本，未写文件")
        return 1

    if args.dry_run:
        print("dry-run：未写入")
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已写入: " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
