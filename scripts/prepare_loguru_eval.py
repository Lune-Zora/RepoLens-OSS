"""loguru 扩展评测：对新增仓库的 4 个验证通过的 bug 跑定位评测。

用法：
    py scripts/prepare_loguru_eval.py [--hybrid]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LOURU_BUGS = [
    {
        "id": 1,
        "fix_commit": "c71a057f123d",
        "buggy_parent": "2a17be730e11",
        "test_files": ["tests/test_filesink_rotation.py"],
        "test_kw": "weekday",
        "issue": "first rotation of weekday at time ignoring the weekday",
        "expected": ["loguru/_file_sink.py:__init__:109"],
    },
    {
        "id": 2,
        "fix_commit": "2a2c59e1c47d",
        "buggy_parent": "36da8cca1445",
        "test_files": ["tests/test_ansimarkup_extended.py"],
        "test_kw": "hex",
        "issue": "hex color short code expansion",
        "expected": ["loguru/_colorizer.py:_get_ansicode:319"],
    },
    {
        "id": 3,
        "fix_commit": "ffe1617122cc",
        "buggy_parent": "62952bd97465",
        "test_files": ["tests/test_standard_handler.py"],
        "test_kw": "handler",
        "issue": "the .level of standard Handler previously ignored",
        "expected": ["loguru/_simple_sinks.py:write:70"],
    },
    {
        "id": 4,
        "fix_commit": "7b30cd046a59",
        "buggy_parent": "97a50cf75faf",
        "test_files": ["tests/test_filesink_rotation.py"],
        "test_kw": "time_rotation",
        "issue": "parsing of 12-hour rotation times without seconds",
        "expected": ["loguru/_string_parsers.py:parse_time:300"],
    },
]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hybrid", action="store_true")
    args = ap.parse_args()

    repo = ROOT / "repos" / "loguru"
    from replens.eval_runner import run_eval

    rows = []
    for bug in LOURU_BUGS:
        tmp = tempfile.mkdtemp(prefix="replens_loguru_")
        try:
            r = git(repo, "worktree", "add", "--detach", tmp, bug["buggy_parent"])
            if r.returncode != 0:
                print(f"[!] worktree fail {bug['id']}")
                continue
            git(tmp, "config", "core.autocrlf", "false")
            test_patch = Path(tmp) / "_t.patch"
            test_patch.write_bytes(git(repo, "diff", bug["buggy_parent"], bug["fix_commit"],
                                       "--", *bug["test_files"]).stdout.encode())
            if git(tmp, "apply", str(test_patch)).returncode != 0:
                print(f"[!] test patch fail {bug['id']}")
                continue
            with tempfile.TemporaryDirectory(prefix='replens_eval_labels_') as label_dir:
                ds = Path(label_dir) / 'dataset.json'
                ds.write_text(json.dumps([{k: bug[k] for k in ("id", "issue", "expected")}],
                                         ensure_ascii=False), encoding="utf-8")
                res = run_eval(str(tmp), str(ds), top_k=5, hybrid=args.hybrid)
            row = {
                "project": "loguru",
                "target": bug["expected"][0],
                "hit@1": res.get("hit@1"),
                "hit@5": res.get("hit@5"),
                "func@5": res.get("func@5"),
                "line_acc@5": res.get("line_acc@5"),
                "mrr": res.get("mrr"),
            }
            rows.append(row)
            print(f"[i] loguru {bug['id']} {bug['expected'][0]} -> "
                  f"hit@1={row['hit@1']} hit@5={row['hit@5']} func@5={row['func@5']} "
                  f"line_acc={row['line_acc@5']} MRR={row['mrr']}")
        finally:
            git(repo, "worktree", "remove", "--force", tmp)

    print("\n=== loguru 扩展评测汇总 ===")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    out = ROOT / "reports" / "runs" / "loguru_eval_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写入", out)


if __name__ == "__main__":
    main()
