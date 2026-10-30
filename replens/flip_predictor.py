"""补丁测试翻转预测：用静态调用图预测「哪些测试会被这次修改翻转为通过」。

方法创新点：候选补丁在真正运行前，先用调用图做反事实推理——
「如果改了函数 F，哪些测试会经过 F？」把预测结果与实际测试翻转对比，
输出预测准确率（precision/recall）。这让「测试验证」从被动的结果展示
升级为主动的、可被证伪的预测。
"""
from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from pathlib import Path

from .indexer import IndexDB
from .retriever import _is_test_path

_FAILED_RE = re.compile(r"(?:FAILED|ERROR)\s+([\w./\\]+::[\w]+)")
_FAILED_SHORT_RE = re.compile(r"(?:FAILED|ERROR)\s+(test_[\w]+)")
# pytest 失败节标题：_________________ test_name _________________
_HEADER_RE = re.compile(r"^_{5,}\s*([\w]+)\s*_{5,}$")
# unittest 输出：FAIL: test_parse (test_parser.TestParser) 或 FAIL: test_parse (__main__.TestParser)
_UNITTEST_RE = re.compile(r"^(?:FAIL|ERROR):\s+([\w]+)\s*\(")


def parse_failed_tests(output: str) -> Set[str]:
    """从 pytest/unittest 输出中提取失败测试的函数名集合。"""
    names: Set[str] = set()
    for m in _FAILED_RE.finditer(output):
        test_id = m.group(1)
        if "::" in test_id:
            names.add(test_id.rsplit("::", 1)[1])
    for m in _FAILED_SHORT_RE.finditer(output):
        names.add(m.group(1))
    for raw in output.splitlines():
        s = raw.strip()
        hm = _HEADER_RE.match(s)
        if hm:
            names.add(hm.group(1))
        um = _UNITTEST_RE.match(s)
        if um:
            names.add(um.group(1))
    return names


def seed_names_from_patch(patch_file: str, db: IndexDB) -> Set[str]:
    """从补丁文件中解析「真正被修改的函数/方法名」，作为反事实推理的种子。

    JSON 补丁：用 from 串在索引中反查包含它的最内层函数；
    unified diff：用 hunk 的旧行号范围覆盖的函数。
    """
    import json as _json
    cur = db.conn.cursor()
    seeds: Set[str] = set()
    try:
        text = Path(patch_file).read_text(encoding="utf-8")
    except Exception:
        return seeds

    if patch_file.lower().endswith(".json"):
        try:
            data = _json.loads(text)
        except Exception:
            return seeds
        for rel, rule in data.items():
            snippet = rule.get("from", "")
            if not snippet:
                continue
            rows = cur.execute(
                "SELECT name, code FROM symbols WHERE path=? "
                "AND type IN ('function','method') AND code LIKE ?",
                (rel, "%" + snippet + "%")).fetchall()
            if rows:
                best = min(rows, key=lambda r: len(r["code"]))
                seeds.add(best["name"])
    else:
        hunk_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+")
        current_path = None
        for line in text.splitlines():
            if line.startswith("--- "):
                p = line[4:].strip().split("\t")[0]
                current_path = p[2:] if p.startswith("a/") else p
            hm = hunk_re.match(line)
            if hm and current_path:
                old_start = int(hm.group(1))
                rows = cur.execute(
                    "SELECT name FROM symbols WHERE path=? "
                    "AND type IN ('function','method') AND start_line<=? AND end_line>=?",
                    (current_path, old_start, old_start)).fetchall()
                for r in rows:
                    seeds.add(r["name"])
    return seeds


def predict_flips(db: IndexDB, seed_names: Set[str], max_depth: int = 2,
                  focus_paths: Optional[Set[str]] = None) -> Set[str]:
    """预测会被修改影响的测试函数名集合。

    从被修改函数（种子）出发，沿 calls 表反向扩展（谁调用了它），
    收集位于测试文件中的函数名。focus_paths 给定时，只保留位于
    这些测试文件中的预测（用「失败测试所在文件」缩小反事实范围）。
    """
    if not seed_names:
        return set()
    cur = db.conn.cursor()
    seeds = set(seed_names)

    predicted: Set[str] = set()
    frontier = list(seeds)
    seen = set(frontier)
    for _ in range(max_depth):
        nxt = []
        for name in frontier:
            for r in cur.execute(
                    "SELECT DISTINCT caller_path, caller_name FROM calls "
                    "WHERE callee_name=? LIMIT 50", (name,)):
                caller_path, caller_name = r["caller_path"], r["caller_name"]
                if _is_test_path(caller_path):
                    if focus_paths is None or caller_path in focus_paths:
                        predicted.add(caller_name)
                if caller_name not in seen:
                    seen.add(caller_name)
                    nxt.append(caller_name)
        frontier = nxt
    return predicted


def evaluate_flips(
    predicted: Set[str],
    before_output: str,
    after_output: str,
) -> dict:
    """对比预测翻转与实际翻转，输出 precision / recall。"""
    before_failed = parse_failed_tests(before_output)
    after_failed = parse_failed_tests(after_output)
    actual = {n for n in (before_failed - after_failed)}

    # 预测名与真实测试名按函数名匹配
    hit = {n for n in predicted if n in actual}
    precision = round(len(hit) / len(predicted), 4) if predicted else None
    recall = round(len(hit) / len(actual), 4) if actual else None
    return {
        "predicted": sorted(predicted),
        "actual_flips": sorted(actual),
        "hit": sorted(hit),
        "precision": precision,
        "recall": recall,
        "before_failed": sorted(before_failed),
        "after_failed": sorted(after_failed),
    }
