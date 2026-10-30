"""重算已有样本的标注与描述，并重验 fail→pass。

为什么需要（2026-10-29）：

  1. 挖掘器后来改了两处归属规则（纯插入 hunk 的位置、新增符号归到外层类），
     已入库样本的 expected 仍是旧规则生成的；
  2. 8 条弱描述里有 5 条是被「剥 fix 前缀」削成名词短语的，另有 5 条能从上游原文
     （issue 标题 / PR 正文首句）拿到更准确的描述——这些文本入库在 issue_upgrade.json，
     属于「换一处上游原文」，不是改写；
  3. **上一版只改标注、不重验**：改完没有任何证据说明这些样本仍然 fail→pass。
     本版默认重验（加 --no-verify 才跳过），验证不通过就不写数据集、直接非零退出。

用法：

    python scripts/repair_annotations.py eval/datasets/real_dataset_mined.json \
        --repos tqdm=<path>,pypdf=<path>,loguru=<path>,click=<path>,requests=<path> --dry-run

    python scripts/repair_annotations.py ... --output <新路径>     # 写入新文件
    python scripts/repair_annotations.py ... --no-verify --jobs 1  # 只重算标注

退出码：0 成功；1 存在重验失败或标注无法重算。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replens import era_env  # noqa: E402
from replens.miner import (_first_changed_new_line,  # noqa: E402
                           _first_changed_old_line, attribute_symbol,
                           issue_from_subject, verify_item)

# 人工选定的「弱描述 → 上游原文」升级表。放在 eval/curation/ 而不是 eval/datasets/：
# 后者被「每个 *.json 都必须是已登记数据集」的契约测试覆盖，运维记录不该混进去。
UPGRADES = ROOT / "eval" / "curation" / "issue_upgrade.json"
# 人工通读判定：verdict=weak 才允许把标题换成提交正文（换出处由人决定，不由启发式决定）。
VERDICTS = ROOT / "eval" / "datasets" / "人工复核判定.json"


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def load_upgrades() -> dict:
    """读取「弱描述 → 上游原文」的人工选定升级（按 fix_commit 索引）。"""
    if not UPGRADES.exists():
        return {}
    data = json.loads(UPGRADES.read_text(encoding="utf-8"))
    return {entry["fix_commit"]: entry for entry in data.get("entries", [])}


def load_verdicts() -> dict:
    """读取人工通读判定：{fix_commit: verdict}。"""
    if not VERDICTS.exists():
        return {}
    data = json.loads(VERDICTS.read_text(encoding="utf-8"))
    return {entry["fix_commit"]: entry.get("verdict") for entry in data.get("verdicts", [])}


def _commit_text(repo, commit):
    subject = _git(repo, "log", "-1", "--format=%s", commit).stdout.strip()
    body = _git(repo, "log", "-1", "--format=%b", commit).stdout
    return subject, body


def _issue_for(repo, commit, upgrades, verdicts):
    """返回 (描述, 来源, 质量)。

    顺序：人工选定的上游原文 → 判定为 weak 时取提交正文 → 提交标题。
    正文只在人工判定为 weak 时采用：标题短不等于标题坏，
    让启发式直接替换会把合格标题换成正文里的实现细节（2026-10-29 实测）。
    """
    upgrade = upgrades.get(commit)
    if upgrade:
        return upgrade["issue"], upgrade["source"], "clear"
    subject, body = _commit_text(repo, commit)
    weak = verdicts.get(commit) == "weak"
    issue, source = issue_from_subject(subject, body, allow_body=weak)
    # 只有「人工判为 weak 且没能换到更好的上游原文」才继续标 weak。
    quality = "weak" if weak and source == "commit_subject" else "clear"
    return issue, source, quality


def _expected_for(repo, item, notes):
    parent = item.get("buggy_parent")
    commit = item.get("fix_commit")
    expected = item.get("expected") or []
    if not expected or not parent or not commit:
        return expected
    path = expected[0].split(":")[0]
    diff = _git(repo, "diff", parent, commit, "--", path).stdout
    if not diff:
        notes.append("提交未改动标注路径: " + path)
        return expected
    old_pos = _first_changed_old_line(diff)
    if not old_pos:
        return expected
    old_src = _git(repo, "show", parent + ":" + path).stdout
    new_src = _git(repo, "show", commit + ":" + path).stdout
    name, line = attribute_symbol(old_src, new_src, old_pos, _first_changed_new_line(diff))
    return [path + ":" + name + ":" + str(line) if name else path]


def repair(item, repo, upgrades, verdicts):
    """重算一条样本的 expected / issue / issue_source / issue_quality。"""
    notes = []
    updated = dict(item)

    expected = _expected_for(repo, item, notes)
    if expected != (item.get("expected") or []):
        notes.append("expected: %r -> %r" % (item.get("expected"), expected))
        updated["expected"] = expected

    issue, source, quality = _issue_for(repo, item["fix_commit"], upgrades, verdicts)
    if issue != item.get("issue"):
        notes.append("issue[%s]: %r -> %r" % (source, item.get("issue"), issue))
        updated["issue"] = issue
    if source != item.get("issue_source"):
        updated["issue_source"] = source
    if quality != item.get("issue_quality"):
        updated["issue_quality"] = quality
        # 只在「变弱 / 从弱转好」时报告：初次填补字段不算修正，不该刷屏。
        if "weak" in (quality, item.get("issue_quality")):
            notes.append("issue_quality: %r -> %r" % (item.get("issue_quality"), quality))
    return updated, notes


def _verification_python(repo, item, mode):
    if mode != "auto":
        return sys.executable, era_env.MODERN
    profile, _ = era_env.select_profile(repo, item["buggy_parent"])
    setup_py = _git(repo, "show", item["buggy_parent"] + ":setup.py").stdout
    environment = era_env.ensure(profile, runtime=era_env.runtime_specifiers(setup_py))
    return environment["python"], environment["profile"]


def verify(item, repo, timeout, era_mode, attempts=2):
    """重跑一遍 fail→pass，返回结果记录。

    超时重试一次：2026-10-29 实测，同一批 pypdf/click 用例在 4 并发下偶发卡死
    （进程 CPU 为 0、日志停在测试内部），单跑同样命令 5s 内完成。这类卡死是环境抖动，
    不是样本问题；重试一次能区分「真超时」与「抖动」，又不至于把抖动当成失败证据。
    """
    try:
        python, profile = _verification_python(repo, item, era_mode)
    except era_env.EraEnvError as exc:
        return {"id": item.get("id"), "ok": False, "reason": "时代环境不可用: " + str(exc)[:160]}
    record = {}
    for attempt in range(1, attempts + 1):
        record = verify_item(repo, item["buggy_parent"], item["fix_commit"],
                             item.get("test_files"), item.get("src_files"),
                             item_id=item.get("id", 0), timeout=timeout, python=python)
        if record.get("reason") != "测试超时" or attempt == attempts:
            break
        print("  #%s 验证超时，重试（%d/%d）" % (item.get("id"), attempt + 1, attempts))
    record["env_profile"] = profile
    return record


def _apply_verification(updated, record):
    updated["fail_pass"] = bool(record.get("ok"))
    updated["verified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    updated["env_profile"] = record.get("env_profile", era_env.MODERN)
    if record.get("scope"):
        updated["validation_scope"] = record["scope"]
    targets = record.get("targets") or []
    if record.get("ok") and targets:
        # test_cmd 必须与真正跑通的命令一致，否则复现时又要重新猜目标。
        updated["test_cmd"] = "pytest " + " ".join(targets)
    return updated


def main():
    parser = argparse.ArgumentParser(description="重算标注与描述，并重验 fail→pass")
    parser.add_argument("dataset", help="数据集 JSON")
    parser.add_argument("--repos", required=True, help="project=path，逗号分隔")
    parser.add_argument("--output", help="输出路径；缺省则原地覆盖")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action=argparse.BooleanOptionalAction, default=True,
                        help="重跑 fail→pass（默认开启；--no-verify 跳过）")
    parser.add_argument("--jobs", type=int, default=4, help="并发验证数，默认 4")
    parser.add_argument("--timeout", type=int, default=240, help="单条验证超时秒数")
    parser.add_argument("--era-env", choices=["off", "auto"], default="off",
                        help="auto：按父提交依赖选时代环境（需要时建 venv）")
    parser.add_argument("--limit", type=int, default=0,
                        help="只处理前 N 条（冒烟用；0 表示全部）")
    parser.add_argument("--verify-only", action="store_true",
                        help="只重验、不改标注与描述：用于人工策展的样本"
                             "（描述是手写的，重算会把它覆盖成提交标题）")
    args = parser.parse_args()

    repos = {}
    for chunk in args.repos.split(","):
        if chunk.strip():
            name, _, path = chunk.partition("=")
            repos[name.strip()] = str(Path(path.strip()).expanduser().resolve())
    upgrades = load_upgrades()
    verdicts = load_verdicts()

    items = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.limit:
        items = items[:args.limit]
    repaired = []
    changed = 0
    issue_changed = 0
    for item in items:
        repo = repos.get(item.get("project"))
        if not repo:
            print("#%s 跳过：缺少仓库路径 %s" % (item.get("id"), item.get("project")))
            repaired.append(item)
            continue
        if args.verify_only:
            # 人工策展的样本：描述是人写的，重算会被提交标题覆盖，故只做重验。
            repaired.append(dict(item))
            continue
        updated, notes = repair(item, repo, upgrades, verdicts)
        if notes:
            changed += 1
            if any(note.startswith("issue[") for note in notes):
                issue_changed += 1
            print("#%s %s" % (item.get("id"), str(item.get("fix_commit"))[:10]))
            for note in notes:
                print("    " + note)
        repaired.append(updated)

    print()
    if args.verify_only:
        print("共 %d 条：仅重验（未改标注与描述）" % len(repaired))
    else:
        print("共 %d 条：标注或描述被修正 %d 条（其中描述 %d 条）"
              % (len(repaired), changed, issue_changed))

    failed = []
    if args.verify:
        started = time.time()
        print("开始重验 fail→pass（并发 %d，单条超时 %ds）..." % (args.jobs, args.timeout))

        def job(item):
            if not (item.get("buggy_parent") and item.get("fix_commit")):
                # 干扰样本 / 只有定位标注的样本：没有修复提交，不参与 fail→pass，
                # 但也不该算失败（否则 --verify-only 跑策展修复杂集会被噪声样本卡住）。
                return {"id": item.get("id"), "ok": None, "reason": "非修复样本，跳过"}
            repo = repos.get(item.get("project"))
            if not repo:
                return {"id": item.get("id"), "ok": False, "reason": "缺少仓库路径"}
            return verify(item, repo, args.timeout, args.era_env)

        if max(args.jobs, 1) == 1:
            # 串行时逐条打印：100 条要跑十几分钟，不打印就等于「卡住了还是没卡」无法判断。
            records = []
            for index, item in enumerate(repaired, 1):
                record = job(item)
                state = "PASS" if record.get("ok") else (
                    "跳过" if record.get("ok") is None else record.get("reason"))
                print("  [%d/%d] #%s %s %s" % (index, len(repaired), item.get("id"),
                                               item.get("project"), state), flush=True)
                records.append(record)
        else:
            with ThreadPoolExecutor(max_workers=max(args.jobs, 1)) as pool:
                records = list(pool.map(job, repaired))
        skipped = 0
        for item, record in zip(repaired, records):
            if record.get("ok") is None:
                skipped += 1
                continue
            _apply_verification(item, record)
            if not record.get("ok"):
                failed.append((item.get("id"), item.get("project"), record.get("reason")))
        checked = len(repaired) - skipped
        ok = checked - len(failed)
        print("重验完成：通过 %d / %d，跳过 %d（非修复样本），用时 %.1fs"
              % (ok, checked, skipped, time.time() - started))
        for sample_id, project, reason in failed:
            print("  [FAIL] #%s %s %s" % (sample_id, project, reason))

    weak = [item for item in repaired if item.get("issue_quality") == "weak"]
    print("弱描述：%d 条 %s" % (len(weak), [item.get("id") for item in weak]))

    if args.dry_run:
        print("dry-run：未写入")
        return 1 if failed else 0
    if failed:
        print("存在重验失败，未写入数据集（避免把未验证的样本当成已验证）")
        return 1

    out = Path(args.output) if args.output else Path(args.dataset)
    out.write_text(json.dumps(repaired, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已写入: " + str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
