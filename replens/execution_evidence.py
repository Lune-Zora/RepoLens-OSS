"""执行证据采集：从失败测试的输出中提取「运行时证据」。

方法创新点：传统检索只看静态代码（BM25/向量/调用图），本模块把
「测试失败轨迹（traceback）」当作一等证据 —— 失败测试在哪个文件哪一行、
哪个函数里炸掉，就是最强的定位信号。该信号不依赖 LLM，完全由测试运行时产生。

输出：ExecutionHint 列表，供证伪式检索重排序使用。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .indexer import IndexDB, Symbol

# Python 标准 traceback：File "path", line N, in func
_FRAME_RE = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')
# pytest 短 traceback：path.py:N: in func
_PYTEST_FRAME_RE = re.compile(r"^(.+?\.py):(\d+): in (\w+)$")


@dataclass
class ExecutionHint:
    """一条运行时证据。"""
    path: str          # 仓库相对路径
    line: int
    frame_func: str
    weight: float = 1.0

    @property
    def ref(self) -> str:
        return f"{self.path}:{self.line}"


_TEMP_RE = re.compile(r"replens_[^/\\]+[/\\](.*)$")


def _rel_path(abs_path: str, repo_root: str) -> Optional[str]:
    """把绝对路径转成仓库相对路径；不在仓库内返回 None。

    测试在临时副本中运行，traceback 路径指向 replens_* 临时目录；
    这里把临时目录前缀剥掉，还原成仓库相对路径。
    """
    root = os.path.abspath(repo_root).replace("\\", "/")
    p = abs_path.replace("\\", "/")
    if p.startswith(root + "/"):
        return p[len(root) + 1:]
    m = _TEMP_RE.search(p)
    if m:
        return m.group(1)
    return None


def parse_traceback_frames(output: str, repo_root: str) -> List[ExecutionHint]:
    """从测试输出中解析仓库内源码帧。

    兼容两种格式：
    - Python 标准 traceback：File "path", line N, in func
    - pytest 短 traceback：path.py:N: in func（路径可能是仓库相对路径或临时副本绝对路径）
    """
    hints: List[ExecutionHint] = []
    seen = set()

    def _add(path: str, line: int, func: str) -> None:
        if not path.endswith(".py"):
            return
        if path.startswith(".."):
            return
        rel = _rel_path(path, repo_root) if os.path.isabs(path) else path.replace("\\", "/")
        if not rel or rel.startswith(".."):
            return
        if "site-packages" in rel or rel.startswith("lib/"):
            return
        if (rel, line) in seen:
            return
        seen.add((rel, line))
        hints.append(ExecutionHint(path=rel, line=line, frame_func=func))

    for m in _FRAME_RE.finditer(output):
        _add(m.group(1), int(m.group(2)), m.group(3))

    for raw in output.splitlines():
        s = raw.strip()
        pm = _PYTEST_FRAME_RE.match(s)
        if not pm:
            continue
        _add(pm.group(1), int(pm.group(2)), pm.group(3))

    # 越靠近异常点的帧越有价值：给靠后的帧更高权重
    n = len(hints)
    for i, h in enumerate(hints):
        h.weight = 0.5 + 0.5 * (i + 1) / max(n, 1)
    return hints


def match_hint_to_symbols(hints: List[ExecutionHint], db: IndexDB) -> dict:
    """把执行帧映射到索引中的符号，并对同一符号的多个帧做累加加权。

    返回 {symbol_ref: {"symbol": Symbol, "hints": [(hint, distance), ...],
    "min_dist": int, "total_weight": float}}。
    v0.7 改进：同一符号被多个失败帧命中时不再只保留最近一帧，
    而是累加各帧权重（多帧确认 > 单帧命中）。
    """
    out = {}
    if not hints:
        return out
    cur = db.conn.cursor()
    for h in hints:
        rows = cur.execute(
            "SELECT path,type,name,start_line,end_line,signature,doc,code FROM symbols "
            "WHERE path=? AND start_line<=? AND end_line>=? ORDER BY "
            "CASE type WHEN 'method' THEN 0 WHEN 'function' THEN 1 "
            "WHEN 'class' THEN 2 ELSE 3 END LIMIT 3",
            (h.path, h.line, h.line)).fetchall()
        for r in rows:
            sym = Symbol(path=r["path"], type=r["type"], name=r["name"],
                         start_line=r["start_line"], end_line=r["end_line"],
                         signature=r["signature"] or "", doc=r["doc"] or "",
                         code=r["code"] or "")
            if sym.start_line <= h.line <= sym.end_line:
                distance = 0  # 帧落在符号体内：完全命中
            else:
                distance = min(abs(h.line - sym.start_line), abs(h.line - sym.end_line))
            entry = out.setdefault(sym.ref, {
                "symbol": sym, "hints": [], "min_dist": 10**9, "total_weight": 0.0})
            entry["hints"].append((h, distance))
            entry["min_dist"] = min(entry["min_dist"], distance)
            entry["total_weight"] += h.weight
    return out


def collect_execution_evidence(
    repo_root: str,
    test_command: str,
    timeout: int = 120,
    allowed_commands: Optional[list] = None,
) -> Tuple[List[ExecutionHint], str]:
    """在临时副本中运行测试并采集失败轨迹。返回 (hints, 失败输出)。"""
    from .executor import run_test_suite
    before, _, _ = run_test_suite(
        repo_root, test_command, timeout=timeout,
        patch_file=None, extra_files=None, allowed_commands=allowed_commands)
    output = ""
    for r in before:
        output += (r.stderr or "") + "\n" + (r.stdout or "") + "\n"
    hints = parse_traceback_frames(output, repo_root)
    return hints, output
