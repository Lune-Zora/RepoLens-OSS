"""检索定位与证据绑定。

把 Issue 文本转化为一组「带文件:行号 的代码证据」。
定位是确定性计算：FTS5/关键词检索 + 调用图扩展，不依赖 LLM。
证据不足时明确返回「证据不足」而非硬答。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .indexer import IndexDB, Symbol

# 英文停用词：过滤语法功能词与超常见动词，避免干扰样本误命中
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "else", "for", "to", "of",
    "in", "on", "at", "by", "with", "from", "is", "are", "was", "were", "be",
    "been", "being", "how", "what", "why", "when", "where", "which", "who",
    "this", "that", "these", "those", "it", "its", "as", "not", "no", "so",
    "do", "does", "did", "can", "could", "should", "would", "will", "up",
    "down", "out", "about", "into", "over", "under", "get", "use", "using",
    "used", "have", "has", "had", "we", "you", "i", "me", "my",
}


@dataclass
class Evidence:
    """一条代码证据。"""
    symbol: Symbol
    confidence: float      # 0~1 置信度
    reason: str            # 判断相关的原因
    source: str            # keyword_match / call_related


# ---------- 分词 ----------

def tokenize(text: str) -> List[str]:
    """把 Issue 文本切成可检索的 token。

    - 英文按字母数字序列提取，下划线/驼峰切分（parse_date → parse, date）
    - 中文连续片段作为一个整体 token 保留
    """
    result: List[str] = []
    for raw in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", raw):
            result.append(raw.lower())
            continue
        # 下划线切分
        for part in re.split(r"_+", raw):
            # 驼峰切分：先在小写/数字与大写之间插入下划线
            split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", part)
            for w in re.split(r"_+", split):
                w = w.lower()
                if len(w) < 2 and w not in ("id", "ai"):
                    continue
                if w in STOPWORDS:
                    continue
                variants = [w]
                # 轻量形态归一化：让 parsed/parsing、spaces 等 Issue 词能命中
                # parse/space，避免依赖完整 NLP 模型才能完成基础定位。
                if len(w) > 4 and w.endswith("ed"):
                    variants.extend([w[:-2], w[:-1]])
                if len(w) > 5 and w.endswith("ing"):
                    variants.extend([w[:-3], w[:-3] + "e"])
                if len(w) > 3 and w.endswith("s"):
                    variants.append(w[:-1])
                for variant in variants:
                    if variant and variant not in result:
                        result.append(variant)
    return result


# ---------- 检索实现 ----------

def _is_test_path(path: str) -> bool:
    """判断路径是否为测试文件（用于源码优先排序）。"""
    p = path.lower().replace("\\", "/")
    parts = p.split("/")
    if any(part in ("tests", "test") for part in parts):
        return True
    base = parts[-1]
    return base.startswith("test_") or base.endswith("_test.py")


def _fts_search(db: IndexDB, tokens: List[str], limit: int) -> List[sqlite3.Row]:
    query = " OR ".join(f'"{t}"' for t in tokens)
    cur = db.conn.cursor()
    rows = cur.execute(
        """
        SELECT s.id, s.path, s.type, s.name, s.start_line, s.end_line,
               s.signature, s.doc, s.code,
               bm25(symbols_fts) AS score
        FROM symbols_fts
        JOIN symbols s ON s.id = symbols_fts.rowid
        WHERE symbols_fts MATCH ?
        ORDER BY score ASC
        LIMIT ?
        """, (query, limit)).fetchall()
    return rows


def _like_search(db: IndexDB, tokens: List[str], limit: int) -> List[dict]:
    """FTS5 不可用时的降级：LIKE 命中计数（纯 Python 计算，无 SQL 拼接，防注入）。"""
    rows = db.conn.execute(
        "SELECT id, path, type, name, start_line, end_line, "
        "signature, doc, code FROM symbols").fetchall()
    scored: List[dict] = []
    for r in rows:
        hay = " ".join([r["name"], r["doc"] or "", r["code"] or ""]).lower()
        hits = sum(1 for t in tokens if t in hay)
        if hits > 0:
            d = dict(r)
            d["score"] = -float(hits)  # 命中越多越负（与 bm25 同向：越小越靠前）
            scored.append(d)
    scored.sort(key=lambda x: x["score"])
    return scored[:limit]


def _row_to_symbol(row) -> Symbol:
    return Symbol(
        path=row["path"], type=row["type"], name=row["name"],
        start_line=row["start_line"], end_line=row["end_line"],
        signature=row["signature"] or "", doc=row["doc"] or "",
        code=row["code"] or "",
    )


def _same_dir(path_a: str, path_b: str) -> bool:
    """判断两个路径是否同目录（用于调用图扩展的邻近优先）。"""
    import posixpath
    return posixpath.dirname(path_a) == posixpath.dirname(path_b)


def _symbols_by_import(db: IndexDB, name: str, prefer_path: str = "") -> List[Symbol]:
    """callee 无直接同名符号时，通过 imports 表解析导入别名/模块末段。"""
    cur = db.conn.cursor()
    out: List[Symbol] = []
    seen = set()
    for row in cur.execute(
            "SELECT module, name FROM imports WHERE path=? AND (name=? OR module LIKE ?)",
            (prefer_path, name, "%." + name)):
        candidates = {row["name"]}
        if row["module"]:
            candidates.add(row["module"].split(".")[-1])
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)
            for s in _symbols_by_name(db, cand, prefer_path):
                out.append(s)
    return out


def _expand_call_graph(db: IndexDB, seed: Symbol, depth: int = 1) -> List[Symbol]:
    """以命中符号为种子，沿调用图扩展 1 跳（callee + caller），补充证据。

    v0.7 改进：callee 名无直接符号时回退到 imports 表解析（别名/导入），
    caller 扩展按「同文件 > 同目录 > 其它」邻近优先排序并限幅，减少跨文件同名噪声。
    """
    if seed.type not in ("function", "method"):
        return []
    extra: List[Symbol] = []
    seen = set()
    cur = db.conn.cursor()
    # 被调函数（callee）：seed 调用了谁
    for row in cur.execute(
            "SELECT DISTINCT callee_name FROM calls "
            "WHERE caller_path=? AND caller_name=?", (seed.path, seed.name)):
        syms = _symbols_by_name(db, row["callee_name"], seed.path)
        if not syms:
            syms = _symbols_by_import(db, row["callee_name"], seed.path)
        for s in syms:
            if s.ref not in seen:
                seen.add(s.ref)
                extra.append(s)
    # 调用方（caller）：谁调用了 seed（邻近优先，减少跨文件同名噪声）
    rows = cur.execute(
            "SELECT DISTINCT caller_path, caller_name FROM calls "
            "WHERE callee_name=?", (seed.name,)).fetchall()
    rows_sorted = sorted(
        rows,
        key=lambda r: 0 if r["caller_path"] == seed.path
        else (1 if _same_dir(r["caller_path"], seed.path) else 2))
    for row in rows_sorted[:10]:
        sym = _symbol_by_ref(db, row["caller_path"], row["caller_name"])
        if sym and sym.ref not in seen:
            seen.add(sym.ref)
            extra.append(sym)
    return extra


def _symbols_by_name(db: IndexDB, name: str, prefer_path: str = "") -> List[Symbol]:
    cur = db.conn.cursor()
    rows = cur.execute(
        "SELECT path,type,name,start_line,end_line,signature,doc,code "
        "FROM symbols WHERE name=?", (name,)).fetchall()
    out = [_row_to_symbol(r) for r in rows]
    # 优先返回同文件的定义，避免跨文件同名函数干扰
    if prefer_path:
        out.sort(key=lambda s: 0 if s.path == prefer_path else 1)
    return out


def _symbol_by_ref(db: IndexDB, path: str, name: str) -> Optional[Symbol]:
    cur = db.conn.cursor()
    row = cur.execute(
        "SELECT path,type,name,start_line,end_line,signature,doc,code "
        "FROM symbols WHERE path=? AND name=? LIMIT 1", (path, name)).fetchone()
    return _row_to_symbol(row) if row else None


# ---------- 对外接口 ----------

def retrieve(
    issue: str,
    db: IndexDB,
    top_k: int = 5,
    expand_callgraph: bool = True,
    execution_hints=None,
) -> Tuple[List[Evidence], bool]:
    """检索定位。

    返回 (证据列表, 是否证据不足)。
    证据不足 = 无任何 token 命中（FTS 空），或 top 结果命中的不同实词数 < 2
    （多实词 issue 只命中 1 词视为偶然命中）。

    关键改动（修复 GPT-6 §3.2）：
    当 execution_hints 非空时，延迟拒答判定，先把轨迹候选与静态候选并集，
    再评估证据是否充分。无 token 命中 + 无 hint → 拒答；有 hint 但无静态命中
    → 仍允许保留 hint 候选（标记 source="execution_hint"，置信度较低）。
    """
    tokens = tokenize(issue)
    has_hints = bool(execution_hints)

    # 无 token 也无 hint：才直接拒答
    if not tokens and not has_hints:
        return [], True

    rows = []
    if tokens:
        if db.fts_available:
            rows = _fts_search(db, tokens, top_k * 3)
        else:
            rows = _like_search(db, tokens, top_k * 3)

    # 无静态命中时：只有 hint 才能挽救，否则才拒答
    if not rows and not has_hints:
        return [], True

    # 排序优先级：function/class 优先于 module（粗粒度降权），源码优先于测试文件
    if rows:
        rows = sorted(rows, key=lambda r: float(r["score"])
                      + (3.0 if r["type"] == "module" else 0.0)
                      + (20.0 if r["type"] == "document" else 0.0)
                      + (100.0 if _is_test_path(r["path"]) else 0.0))
        top_score = float(rows[0]["score"])

        # 证据不足判定（仅在 has_hints=False 时严格执行，避免误杀有真实轨迹的情况）
        if not has_hints:
            top_sym = _row_to_symbol(rows[0])
            matched_count = _matched_token_count(top_sym, tokens)
            if len(tokens) >= 4 and matched_count < 3:
                if top_sym.type == "module":
                    return [], True
                name_hits = sum(1 for t in tokens
                                if t in (top_sym.name + " " + (top_sym.signature or "")).lower())
                if name_hits < 2:
                    return [], True
            if len(tokens) >= 2 and matched_count < 2:
                return [], True
    else:
        top_score = 0.0

    evidences: List[Evidence] = []
    seen_refs = set()

    # 静态候选
    if rows:
        for rank, r in enumerate(rows[:top_k]):
            sym = _row_to_symbol(r)
            if sym.ref in seen_refs:
                continue
            seen_refs.add(sym.ref)
            score = float(r["score"])
            if score >= 0:
                score_ratio = 1.0
            else:
                score_ratio = min(1.0, max(0.0, top_score / score))
            rank_decay = max(0.1, 1.0 - 0.15 * rank)
            conf = rank_decay * score_ratio
            reason = _build_reason(sym, tokens)
            evidences.append(Evidence(symbol=sym, confidence=round(conf, 3),
                                      reason=reason, source="keyword_match"))

    # 调用图扩展（对排名靠前的 function/method）
    if expand_callgraph and evidences:
        for ev in list(evidences[:2]):
            for extra in _expand_call_graph(db, ev.symbol):
                if extra.ref not in seen_refs:
                    seen_refs.add(extra.ref)
                    evidences.append(Evidence(
                        symbol=extra, confidence=round(ev.confidence * 0.6, 3),
                        reason=f"与 {ev.symbol.name} 存在调用关系（调用图扩展）",
                        source="call_related"))

    # 证伪式重排序（执行证据可选）：即使静态为空也允许 hint 补入
    if has_hints:
        from .falsify import rerank_evidences
        evidences = rerank_evidences(evidences, execution_hints, db)

    return evidences, False


def _matched_token_count(sym: Symbol, tokens: List[str]) -> int:
    """统计符号的 name/doc/code 里命中了多少个不同 token。"""
    hay = " ".join([sym.name, sym.doc or "", sym.code or ""]).lower()
    return sum(1 for t in tokens if t in hay)


def _build_reason(sym: Symbol, tokens: List[str]) -> str:
    haystack = (sym.name + " " + sym.doc + " " + sym.signature).lower()
    matched = [t for t in tokens if t in haystack]
    if matched:
        return f"符号名/文档命中关键词: {', '.join(matched[:5])}"
    return f"代码片段命中检索词（{sym.type} @ {sym.path}:{sym.start_line}）"
