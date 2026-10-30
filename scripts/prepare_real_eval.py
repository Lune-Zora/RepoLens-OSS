"""真实评测集扩展：为每个历史 bug 构造 buggy 工作区并跑定位评测。

用法：
    py scripts/prepare_real_eval.py [--project tqdm|pypdf] [--limit N]

原理（SWE-bench 方法论）：
1. 对 eval/datasets/real_dataset_extended.json 中每个正向样本，用 `git worktree add --detach`
   在 buggy_parent 提交上创建临时工作区（源码保持 buggy 状态）。
2. 直接从 git 重新生成该 bug 的测试补丁（避免 JSON 转义问题），`git apply` 到
   临时工作区，使「修复前」能通过对应测试复现失败。
3. 生成仅含该样本的临时数据集，跑 `replens eval` 输出定位指标。
4. 汇总所有 bug 的指标，清理 worktree。

依赖：本地 repos/tqdm 与 repos/pypdf 为完整 git 仓库；eval/reference_patches/real_bugs.json 为调研产出。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_bugs() -> dict:
    with open(ROOT / "eval" / "reference_patches" / "real_bugs.json", encoding="utf-8") as f:
        data = json.load(f)
    return {b["fix_commit"]: b for b in data}


def load_dataset() -> list:
    with open(ROOT / "eval" / "datasets" / "real_dataset_extended.json", encoding="utf-8") as f:
        return json.load(f)


def _test_file_of(test_patch: str) -> str:
    """从 test_patch 的 `--- a/<file>` 行提取测试文件路径。"""
    for line in test_patch.splitlines():
        if line.startswith("--- a/"):
            return line[len("--- a/"):].strip().split("\t")[0]
    return ""


def prepare_workspace(project: str, buggy_parent: str, fix_commit: str,
                      test_file: str) -> str:
    """在临时目录 checkout buggy_parent 并应用测试补丁，返回工作区路径。"""
    repo = ROOT / "repos" / project
    tmp = tempfile.mkdtemp(prefix=f"replens_{project}_")
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", tmp, buggy_parent],
        check=True, capture_output=True)
    if test_file:
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", buggy_parent, fix_commit, "--", test_file],
            capture_output=True)
        patch_file = Path(tmp) / "_test.patch"
        patch_file.write_bytes(diff.stdout)
        r = subprocess.run(["git", "-C", str(tmp), "apply", str(patch_file)],
                           capture_output=True)
        if r.returncode != 0:
            print(f"[!] 测试补丁应用失败: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return tmp


def run_eval_one(workspace: str, item: dict, hybrid: bool = False) -> dict:
    """对单个 bug 跑 replens eval，返回指标（结构化调用，避免输出解析）。"""
    sys.path.insert(0, str(ROOT))
    from replens.eval_runner import run_eval
    # 标注由控制端临时目录持有，不能放进被索引的 buggy 工作区。
    with tempfile.TemporaryDirectory(prefix='replens_eval_labels_') as label_dir:
        ds = Path(label_dir) / 'dataset.json'
        ds.write_text(json.dumps([item], ensure_ascii=False), encoding="utf-8")
        res = run_eval(workspace, str(ds), top_k=5, hybrid=hybrid)
    return {
        "hit@1": res.get("hit@1"),
        "hit@5": res.get("hit@5"),
        "func@5": res.get("func@5"),
        "line_acc@5": res.get("line_acc@5"),
        "mrr": res.get("mrr"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", choices=["tqdm", "pypdf"], default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--hybrid", action="store_true", help="启用混合检索（BM25+语义向量）")
    args = ap.parse_args()

    bugs = load_bugs()
    dataset = load_dataset()
    pos = [it for it in dataset if it.get("expected")]

    rows = []
    for it in pos:
        if args.project and it["project"] != args.project:
            continue
        bug = bugs.get(it["fix_commit"])
        if not bug:
            print(f"[!] 找不到 {it['fix_commit']} 的 patch，跳过")
            continue
        test_file = _test_file_of(bug.get("test_patch", ""))
        print(f"[i] {it['project']} {it['fix_commit'][:8]} ({it['expected'][0]})")
        ws = prepare_workspace(it["project"], bug["buggy_parent"],
                               bug["fix_commit"], test_file)
        try:
            m = run_eval_one(ws, it, hybrid=args.hybrid)
            m["project"] = it["project"]
            m["target"] = it["expected"][0]
            rows.append(m)
        finally:
            subprocess.run(["git", "-C", str(ROOT / "repos" / it["project"]),
                            "worktree", "remove", "--force", ws], capture_output=True)
            shutil.rmtree(ws, ignore_errors=True)
        if args.limit and len(rows) >= args.limit:
            break

    print("\n=== 扩展评测汇总 ===")
    for r in rows:
        print(f"{r['project']:6} {r['target']:45} hit@1={r.get('hit@1')} "
              f"hit@5={r.get('hit@5')} func@5={r.get('func@5')} "
              f"line_acc={r.get('line_acc@5')} MRR={r.get('mrr')}")


if __name__ == "__main__":
    main()
