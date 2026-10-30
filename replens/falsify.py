"""证伪式检索：用运行时证据对静态检索结果进行「假设-证伪」重排序。

方法创新点：传统 RAG 的排序目标是「相似度最高」，本模块的排序目标是
「最不容易被证伪」：
- 失败轨迹命中的符号（execution_confirmed）获得最强加分；
- 位于测试文件/模块级的证据被主动证伪降权（falsified_test / falsified_module）；
- 未被证伪的证据（survived）保持原排序。
由此把检索从「找最像的」变成「淘汰最不可能成立的假设」。
"""
from __future__ import annotations

from typing import List, Optional

from .execution_evidence import ExecutionHint, match_hint_to_symbols
from .indexer import IndexDB
from .retriever import Evidence, _is_test_path


def rerank_evidences(
    evidences: List[Evidence],
    hints: Optional[List[ExecutionHint]],
    db: IndexDB,
) -> List[Evidence]:
    """对证据执行证伪检查与重排序。hints 为空时原样返回。"""
    if not hints:
        return evidences

    hint_map = match_hint_to_symbols(hints, db)

    # 失败轨迹命中的符号若不在候选集中，直接作为一等证据补入
    for ref, info in hint_map.items():
        if ref in {e.symbol.ref for e in evidences}:
            continue
        hint = info["hints"][0][0]
        rows = db.conn.execute(
            "SELECT path,type,name,start_line,end_line,signature,doc,code "
            "FROM symbols WHERE path=? AND start_line<=? AND end_line>=? "
            "AND type IN ('function','method') "
            "ORDER BY CASE type WHEN 'method' THEN 0 ELSE 1 END LIMIT 1",
            (hint.path, hint.line, hint.line)).fetchall()
        if not rows:
            continue
        r = rows[0]
        from .indexer import Symbol
        sym = Symbol(path=r["path"], type=r["type"], name=r["name"],
                     start_line=r["start_line"], end_line=r["end_line"],
                     signature=r["signature"] or "", doc=r["doc"] or "",
                     code=r["code"] or "")
        evidences.append(Evidence(
            symbol=sym, confidence=0.0,
            reason=f"失败轨迹直接命中 {hint.ref}（{hint.frame_func}）",
            source="execution_hint"))

    scored = []
    for e in evidences:
        sym = e.symbol
        score = e.confidence
        status = "survived"
        note = e.reason
        source = e.source

        if sym.ref in hint_map:
            info = hint_map[sym.ref]
            dist = info["min_dist"]
            # 多帧累加加权：同一符号被多个失败帧确认时获得更高加分（封顶避免垄断）
            bonus = min(1.0, info["total_weight"] * 0.6) * max(0.0, 1.0 - dist / 50.0)
            score += bonus
            status = "execution_confirmed"
            source = "execution_confirmed"
            n_frames = len(info["hints"])
            note = (f"失败轨迹 {n_frames} 帧命中 {sym.ref}（累计权重 {info['total_weight']:.2f}）"
                    f"→ 未被执行证伪")
        elif _is_test_path(sym.path):
            score -= 0.35
            status = "falsified_test"
            note = "证伪：位于测试文件，不是源码证据（降权）"
        elif sym.type == "module":
            score -= 0.20
            status = "falsified_module"
            note = "证伪：模块级证据过粗，不足以定位（降权）"

        scored.append((score, status, note, source, e))

    scored.sort(key=lambda x: -x[0])
    out = []
    for rank, (score, status, note, source, e) in enumerate(scored):
        conf = round(min(1.0, max(0.01, score)), 3)
        out.append(Evidence(symbol=e.symbol, confidence=conf,
                            reason=note, source=source))
    return out
