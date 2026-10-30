"""复核挖掘得到的修复样本：机械一致性校验 + 人工过目清单。

两层分工（2026-10-29）：

  「人工复核语义」不可自动化，但其中大部分是**可机械验证的事实**：
  提交是否存在、父子关系是否成立、标注符号是否真被这次提交改动、
  测试目标是否出现在测试补丁里。先机械验掉，人工只看 issue 描述是否准确。

检查项：

| 编号 | 检查 | 失败含义 |
|---|---|---|
| C1 | buggy_parent / fix_commit 存在 | 仓库缺少提交，样本不可复现 |
| C2 | fix_commit 的父提交等于 buggy_parent | 基线非紧邻父提交，还原状态可能不准 |
| C3 | expected 路径在本次提交改动内 | 标注指向未改动文件 |
| C4 | expected 函数被本次提交触及 | 标注符号与修复无关 |
| C5 | test_cmd 目标出现在测试补丁里 | 验证命令不可复现 |
| C6 | issue 与它的上游出处一致 | 描述被篡改或张冠李戴 |
| C9 | expected.line 是该符号的定义行 | 行号口径错（指标用 ±3 判定，标错必然落空） |

退出码：0 全部通过；1 存在 FAIL。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# 人工选定的「弱描述 → 上游原文」升级表。source=upstream_* 的样本只能在这里取证，
# 因为那段文字不在仓库里（issue/PR 的原文），但出处与抓取日期都在案。
CURATION = ROOT / "eval" / "curation" / "issue_upgrade.json"


def _upgrade_index() -> dict:
    if not CURATION.exists():
        return {}
    data = json.loads(CURATION.read_text(encoding="utf-8"))
    return {entry["fix_commit"]: entry for entry in data.get("entries", [])}


def _definition_lines(repo, rev, path, name):
    """该符号在指定版本里的定义行（不含装饰器行）。

    口径必须与 miner.attribute_symbol 一致：它用的是 ast 节点的 lineno，
    而 _function_spans 把装饰器算进起始行，两者混用会把带装饰器的样本判错。
    """
    source = _git(repo, "show", rev + ":" + path).stdout
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name]


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _norm(text):
    return " ".join(str(text).lower().replace(chr(96), "").replace("*", "").split())


def _force_strip(text):
    """无条件剥掉类型前缀（不看是否会产生残句）。

    数据集里存在按旧规则生成的描述——「alpha handling」这种去掉 Fix 的写法。
    它仍然来自同一句上游标题，C6 若只认新规则的输出，就会把历史样本误判成篡改。
    """
    return _norm(re.sub(r"^(fix|bug|hotfix|rob)\b[:：\s-]*", "", str(text), flags=re.IGNORECASE))


def _norm_issue(text):
    """与 miner 使用同一套清理规则。

    不要在复核器里另写一份前缀规则：两边规则一旦不同，C6 就会把一致的样本判成不一致。
    2026-10-29 实测：miner 修了「剥前缀产生残句」的规则，复核器没同步，导致误报。
    """
    from replens.miner import _clean_issue
    return _norm(_clean_issue(str(text)))


def _changed_paths(repo, parent, commit):
    out = _git(repo, "diff", "--name-only", parent, commit).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _touched_lines(repo, parent, commit, path):
    diff = _git(repo, "diff", parent, commit, "--", path).stdout
    lines = set()
    right = None
    for line in diff.splitlines():
        m = HUNK.match(line)
        if m:
            right = int(m.group(1))
            continue
        if right is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.add(right)
            right += 1
        elif line.startswith("-") and not line.startswith("---"):
            # 纯删除同样算「触及」：删掉的行在新文件里对应位置 right。
            # 只统计新增行会把「只删不增」的修复误判为未触及（2026-10-29 pypdf 实测）。
            lines.add(right)
        elif line.startswith(" "):
            right += 1
    return lines


def _function_spans(repo, commit, path, name):
    """返回该文件里所有同名函数的行范围。

    同名方法可能出现在多个类中（如 loguru 的 _simple_sinks.py 有 5 个 write），
    只取第一个匹配会把真正被改动的那个漏掉（2026-10-29 实测）。
    """
    source = _git(repo, "show", commit + ":" + path).stdout
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                start = node.lineno
                for decorator in getattr(node, "decorator_list", []):
                    start = min(start, decorator.lineno)
                spans.append((start, node.end_lineno or node.lineno))
    return spans


def _is_test_path(path):
    lowered = path.lower()
    return ("/test" in lowered or lowered.startswith("test") or "/tests/" in lowered
            or "/testing/" in lowered)


def _patch_hash(repo, parent, commit):
    """补丁内容的哈希。

    为什么不用 tree 哈希：同一改动可能落到不同基准提交上（cherry-pick、分支合并），
    tree 不同但补丁完全相同。2026-10-29 click 的 fish completion 两条即属此类，
    用 tree 判重会漏，用补丁内容判重才抓得到。
    """
    import hashlib
    diff = _git(repo, "diff", parent, commit).stdout
    return hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()


def review_item(item, repos, duplicates=None, upgrades=None):
    checks = []
    project = item.get("project")
    repo = repos.get(project)
    if not repo:
        return {"id": item.get("id"), "project": project, "verdict": "FAIL",
                "issue": item.get("issue"),
                "checks": [("C0", "FAIL", "未提供仓库路径: " + str(project))]}

    parent = item.get("buggy_parent")
    commit = item.get("fix_commit")

    def add(name, ok, detail):
        checks.append((name, "PASS" if ok else "FAIL", detail))

    commit_ok = _git(repo, "cat-file", "-e", commit + "^{commit}").returncode == 0
    parent_ok = _git(repo, "cat-file", "-e", parent + "^{commit}").returncode == 0
    add("C1", commit_ok and parent_ok, "提交存在" if commit_ok and parent_ok else "缺少提交")
    if not (commit_ok and parent_ok):
        return {"id": item.get("id"), "project": project, "verdict": "FAIL",
                "issue": item.get("issue"), "checks": checks}

    actual = _git(repo, "rev-parse", commit + "^").stdout.strip()
    same_parent = actual.startswith(parent[:12]) or parent.startswith(actual[:12])
    add("C2", same_parent, "父提交一致" if same_parent else "父提交为 " + actual[:12])

    changed = _changed_paths(repo, parent, commit)
    expected_paths = [e.split(":")[0] for e in item.get("expected", [])]
    missing = [p for p in expected_paths if p not in changed]
    add("C3", not missing,
        "标注路径均在改动内" if not missing else "标注路径未被改动: " + ", ".join(missing))

    touched_ok = True
    detail = []
    for entry in item.get("expected", []):
        parts = entry.split(":")
        if len(parts) < 2:
            continue
        path, name = parts[0], parts[1]
        spans = _function_spans(repo, commit, path, name)
        if not spans:
            touched_ok = False
            detail.append(name + " 未找到")
            continue
        lines = _touched_lines(repo, parent, commit, path)
        if not any(start <= n <= end for start, end in spans for n in lines):
            touched_ok = False
            detail.append(name + " 未被改动触及")
    add("C4", touched_ok, "; ".join(detail) if detail else "标注符号均被改动触及")

    test_cmd = item.get("test_cmd") or ""
    targets = [t for t in test_cmd.split() if "::" in t or t.endswith(".py")]
    test_changed = [p for p in changed if "test" in p.lower() and p.endswith(".py")]
    targets_ok = bool(targets) and bool(test_changed)
    add("C5", targets_ok, "测试目标可定位" if targets_ok else "test_cmd 无目标或本次未改测试")

    subject = _git(repo, "log", "-1", "--format=%s", commit).stdout.strip()
    body = _git(repo, "log", "-1", "--format=%b", commit).stdout
    issue = item.get("issue") or ""
    source = item.get("issue_source") or ""
    # 两种形态都要认：原文标题，以及按 miner 规则清理后的标题。
    # 规则 3 会让「Fix length detection」这类短句保留前缀，只比一种形态必然误报。
    variants_a = {_norm(subject), _norm_issue(subject), _force_strip(subject)}
    variants_b = {_norm(issue), _norm_issue(issue)}
    # miner 的 _clean_issue 会把标题截到 200 字，故允许前缀关系（2026-10-29 实测踩到）。
    same = any(left == right or left.startswith(right) or right.startswith(left)
               for left in variants_a for right in variants_b)
    detail = "与提交标题一致"
    if not same and source == "commit_body":
        # 描述取自正文首句：必须真的是正文原文，而不是人写的「意思相近」。
        same = _norm(issue) in _norm(body)
        detail = "与提交正文一致" if same else "与提交标题、正文都不同"
    elif not same and source in {"upstream_issue", "upstream_pull"}:
        entry = (upgrades or {}).get(commit)
        same = bool(entry) and _norm(entry.get("issue")) == _norm(issue)
        detail = ("与上游原文一致（%s）" % entry.get("ref")) if same else \
            "上游出处缺失或不一致（见 eval/curation/issue_upgrade.json）"
    add("C6", same, detail if same else detail + ": " + subject[:44])

    # C7：必须改动至少一个非测试源码文件。只改测试的提交不是源码修复。
    source_changed = [p for p in changed
                      if p.endswith(".py") and not _is_test_path(p)]
    add("C7", bool(source_changed),
        "有源码改动: " + ", ".join(source_changed[:2]) if source_changed
        else "只改测试文件，无源码修复")

    # C9：expected.line 必须是该符号在 buggy 版本里的定义行。
    # 指标用 abs(符号起始行 - line) <= 3 判定，标成函数体内某行必然落空；
    # 2026-10-29 就是因为旧挖掘器填「改动行」而让 85/96 条标注失效。
    line_ok = True
    line_detail = []
    for entry in item.get("expected", []):
        parts = entry.split(":")
        if len(parts) < 3:
            continue
        path, name, line_text = parts[0], parts[1], parts[2]
        definitions = _definition_lines(repo, parent, path, name)
        if not definitions:
            continue
        if int(line_text) not in definitions:
            line_ok = False
            line_detail.append("%s 定义行应为 %s，标注为 %s"
                               % (name, ",".join(str(n) for n in definitions), line_text))
    add("C9", line_ok, "; ".join(line_detail) if line_detail else "行号均为符号定义行")

    # C8：同一 tree 哈希不得出现两次。标题不同、提交不同，内容也可能完全一致。
    if duplicates is not None:
        peers = duplicates.get(item.get("id"), [])
        add("C8", not peers, "补丁内容唯一" if not peers
            else "与样本 %s 的补丁内容完全相同（重复）" % peers)

    verdict = "PASS" if not any(c[1] == "FAIL" for c in checks) else "WARN"
    return {"id": item.get("id"), "project": project, "issue": issue,
            "verdict": verdict, "checks": checks}


def _duplicate_groups(items, repos):
    """返回 {id: [与它 tree 哈希相同的其它 id]}。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for item in items:
        repo = repos.get(item.get("project"))
        if not repo or not item.get("fix_commit"):
            continue
        key = _patch_hash(repo, item.get("buggy_parent"), item["fix_commit"])
        buckets[(item.get("project"), key)].append(item.get("id"))
    mapping = {}
    for ids in buckets.values():
        if len(ids) < 2:
            continue
        # 只标记「非首条」为重复：保留一条，避免把整组都裁掉。
        ordered = sorted(ids)
        keeper = ordered[0]
        for i in ordered[1:]:
            mapping[i] = [keeper]
    return mapping


def _parse_repos(value):
    repos = {}
    for chunk in value.split(","):
        if not chunk.strip():
            continue
        name, _, path = chunk.partition("=")
        repos[name.strip()] = str(Path(path.strip()).expanduser().resolve())
    return repos


def main():
    parser = argparse.ArgumentParser(description="复核挖掘得到的修复样本")
    parser.add_argument("dataset", help="数据集 JSON")
    parser.add_argument("--repos", required=True, help="project=path，逗号分隔")
    parser.add_argument("--markdown", help="输出 Markdown 复核清单")
    parser.add_argument("--json", dest="json_out", help="输出 JSON 报告")
    parser.add_argument("--prune-to", help="把机械校验未通过的样本剔除后，写出干净数据集到此路径")
    args = parser.parse_args()

    items = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    # 预计算重复组：按 (project, 补丁内容哈希) 分组，组内多于一条即互为重复。
    # 用补丁内容而非标题或 tree：标题相似不可靠，tree 在 cherry-pick 场景下会漏。
    repos = _parse_repos(args.repos)
    duplicates = _duplicate_groups(items, repos)
    upgrades = _upgrade_index()
    results = [review_item(item, repos, duplicates, upgrades) for item in items]

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    warned = sum(1 for r in results if r["verdict"] == "WARN")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    print("复核 %d 条：PASS %d，WARN %d，FAIL %d" % (len(results), passed, warned, failed))

    for result in results:
        if result["verdict"] == "PASS":
            continue
        print("  [%s] id=%s %s" % (result["verdict"], result.get("id"),
                                   str(result.get("issue"))[:52]))
        for name, status, detail in result["checks"]:
            if status == "FAIL":
                print("        %s %s" % (name, detail))

    if args.prune_to:
        # 只保留结论为 PASS 的样本：机械校验不通过的标注不可信，不能进正式分母。
        keep = {r["id"] for r in results if r["verdict"] == "PASS"}
        pruned = [item for item in items if item.get("id") in keep]
        for index, item in enumerate(pruned, 1):
            item["id"] = index
        out = Path(args.prune_to)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        removed = [r["id"] for r in results if r["verdict"] != "PASS"]
        print("裁剪：保留 %d 条，剔除 %d 条 %s" % (len(pruned), len(removed), removed))
        print("干净数据集: " + str(out))

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print("JSON: " + str(out))

    if args.markdown:
        out = Path(args.markdown)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# 挖掘样本复核清单",
            "",
            "> 数据集：%s　共 %d 条" % (args.dataset, len(results)),
            "> 机械校验结果：PASS %d　WARN %d　FAIL %d" % (passed, warned, failed),
            "",
            "> 本页只做机械一致性校验：提交存在性、父子关系、标注路径是否被改动、",
            "> 标注函数是否被改动触及、测试目标是否在测试补丁中、issue 与提交标题是否一致。",
            "> issue 描述是否准确反映修复行为，仍需人工过目 —— 见文末勾选栏。",
            "",
            "| ID | 项目 | 结论 | 未通过项 |",
            "|---|---|---|---|",
        ]
        for result in results:
            bad = ["%s %s" % (n, d) for n, s, d in result["checks"] if s == "FAIL"]
            lines.append("| %s | %s | %s | %s |" % (
                result.get("id"), result.get("project"), result.get("verdict"),
                "<br>".join(bad) if bad else "-"))
        lines += ["", "## 人工过目栏", "",
                  "> 逐条确认：issue 描述是否准确反映该修复的行为？",
                  "> 确认后打勾；发现描述失真的，记录备注并在数据集中修正或剔除。", ""]
        for result in results:
            lines.append("- [ ] id=%s　%s" % (result.get("id"), str(result.get("issue"))[:80]))
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("Markdown: " + str(out))

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
