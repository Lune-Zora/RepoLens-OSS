"""混合检索：BM25（FTS5）+ 语义向量（可选）的 RRF 融合。

设计动机：
- 纯 FTS5 的短板是「词汇不匹配」：Issue 写 "trim surrounding blanks"，
  代码里写的是 strptime/whitespace，FTS 可能漏召回。
- 纯向量检索的短板是「符号名精确匹配」：parse_date 这类精确符号名，
  向量相似度不一定排在 BM25 前面。
- RRF（Reciprocal Rank Fusion）融合两者排序，取长补短。

依赖策略：
- 配置 REPLENS_EMBED_BASE_URL / REPLENS_EMBED_API_KEY 时，走 OpenAI 兼容
  embeddings 接口（urllib 零依赖）。
- 未配置远程时，若本地安装了 sentence-transformers，则用本地模型（离线可用）。
- 两者都没有时返回 None，调用方自动回退到纯关键字检索，保证零依赖闭环不破。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .indexer import IndexDB, Symbol
from .retriever import (
    Evidence,
    _expand_call_graph,
    _fts_search,
    _is_test_path,
    _like_search,
    _matched_token_count,
    _row_to_symbol,
    tokenize,
)


@dataclass
class EmbeddingSpec:
    name: str
    dim: int = 0


class BaseEmbedder:
    name = "base"

    def embed(self, text: str) -> Optional[List[float]]:
        raise NotImplementedError

    def spec(self) -> EmbeddingSpec:
        return EmbeddingSpec(self.name)


class OpenAICompatEmbedder(BaseEmbedder):
    """OpenAI 兼容 /embeddings 接口（可接自建网关）。"""

    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, model: str = "text-embedding-3-small"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def embed(self, text: str) -> Optional[List[float]]:
        url = self.base_url + "/embeddings"
        payload = {"model": self.model, "input": text[:6000]}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return list(body["data"][0]["embedding"])
        except Exception:
            return None


class FastembedEmbedder(BaseEmbedder):
    """本地 fastembed（ONNX Runtime）轻量向量模型，CPU 可跑，适合轻量化演示。"""

    name = "local-fastembed"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name=model_name)
        self.name = "local-fastembed:" + model_name

    def embed(self, text: str) -> Optional[List[float]]:
        try:
            vec = list(self.model.embed([text]))[0]
            return [float(x) for x in vec]
        except Exception:
            return None


class SentenceTransformerEmbedder(BaseEmbedder):
    """本地 sentence-transformers 模型（可选依赖，离线可用）。"""

    name = "local-st"

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)
        self.name = "local-st:" + model_name

    def embed(self, text: str) -> Optional[List[float]]:
        try:
            return [float(x) for x in self.model.encode(text, normalize_embeddings=True)]
        except Exception:
            return None


def build_embedder() -> Optional[BaseEmbedder]:
    base = os.environ.get("REPLENS_EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("REPLENS_EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if base and key:
        model = os.environ.get("REPLENS_EMBED_MODEL", "text-embedding-3-small")
        return OpenAICompatEmbedder(base, key, model=model)
    try:
        import fastembed  # noqa: F401
        return FastembedEmbedder(
            os.environ.get("REPLENS_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    except Exception:
        pass
    try:
        import sentence_transformers  # noqa: F401
        return SentenceTransformerEmbedder(
            os.environ.get("REPLENS_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    except Exception:
        return None


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _candidate_rows(db: IndexDB, tokens: List[str], limit: int) -> List[sqlite3.Row]:
    """BM25 候选集；FTS 空命中时回退到全符号集（交给语义检索兜底）。"""
    if db.fts_available:
        rows = _fts_search(db, tokens, limit)
        if rows:
            return rows
    else:
        rows = _like_search(db, tokens, limit)
        if rows:
            return rows
    # 词汇不匹配时：全符号参与语义排序
    return db.conn.execute(
        "SELECT id, path, type, name, start_line, end_line, signature, doc, code "
        "FROM symbols").fetchall()


def _embed_symbol(embedder: BaseEmbedder, s: Symbol, cache: dict,
                  db: IndexDB, symbol_id: int) -> Optional[List[float]]:
    if symbol_id in cache:
        return cache[symbol_id]
    vec = embedder.embed((s.name + " " + (s.doc or "") + " " + (s.signature or "")).strip())
    if vec:
        db.save_embedding(symbol_id, vec, embedder.name)
        cache[symbol_id] = vec
    return vec


def retrieve_hybrid(
    issue: str,
    db: IndexDB,
    top_k: int = 5,
    embedder: Optional[BaseEmbedder] = None,
    expand_callgraph: bool = True,
    execution_hints=None,
) -> Tuple[List[Evidence], bool]:
    """混合检索入口。embedder 为空时回退到纯关键字检索。"""
    if embedder is None:
        from .retriever import retrieve
        return retrieve(issue, db, top_k=top_k)

    tokens = tokenize(issue)
    if not tokens:
        return [], True

    cand_limit = max(top_k * 10, 30)
    rows = _candidate_rows(db, tokens, cand_limit)
    if not rows:
        return [], True

    qvec = embedder.embed(issue)
    cache = db.load_embeddings(embedder.name)

    symbols = []
    for r in rows:
        sym = _row_to_symbol(r)
        if qvec is None:
            sym_vec = None
        else:
            sym_vec = _embed_symbol(embedder, sym, cache, db, r["id"])
        symbols.append((sym, float(r["score"]) if "score" in r.keys() else None, sym_vec))

    # 排序去重
    seen = set()
    uniq = []
    for sym, bm25, vec in symbols:
        if sym.ref in seen:
            continue
        seen.add(sym.ref)
        uniq.append((sym, bm25, vec))

    # RRF 融合：bm25 排名与语义排名各取 1/(60+rank)
    # 与纯关键字检索一致：module 粗粒度降权、测试文件降权（优先给源码证据）
    def _bm25_key(item):
        sym, score, _ = item
        if score is None:
            return 1e9
        key = score
        if sym.type == "module":
            key += 3.0
        if _is_test_path(sym.path):
            key += 5.0
        return key

    def _sem_key(item):
        sym, _, vec = item
        c = _cosine(qvec or [], vec or [])
        if _is_test_path(sym.path):
            c -= 0.5
        return c

    bm25_ranked = sorted(uniq, key=_bm25_key)
    if qvec is not None and any(v is not None for _, _, v in uniq):
        sem_ranked = sorted(uniq, key=lambda x: -_sem_key(x))
    else:
        sem_ranked = list(bm25_ranked)

    rrf = {}
    for rank, item in enumerate(bm25_ranked):
        rrf[item[0].ref] = rrf.get(item[0].ref, 0.0) + 1.0 / (60 + rank + 1)
    for rank, item in enumerate(sem_ranked):
        rrf[item[0].ref] = rrf.get(item[0].ref, 0.0) + 1.0 / (60 + rank + 1)

    fused = sorted(uniq, key=lambda x: -rrf.get(x[0].ref, 0.0))

    # 证据不足判定：关键字证据优先，语义层仅在关键字命中不足时兜底。
    # 粗粒度 module 符号要求命中更多实词，避免「语义沾边就硬答」。
    top_sym = fused[0][0]
    matched = _matched_token_count(top_sym, tokens)
    if len(tokens) >= 4 and matched < 3:
        # 长问题实词命中偏少：module 粗粒度证据直接拒答；
        # 函数/方法要求至少 2 个实词命中符号名或签名（避免误杀短名函数）
        if top_sym.type == "module":
            return [], True
        name_hits = sum(1 for t in tokens
                        if t in (top_sym.name + " " + (top_sym.signature or "")).lower())
        if name_hits < 2:
            return [], True
    if len(tokens) >= 2:
        if top_sym.type == "module" and matched < 3:
            return [], True
        if matched < 2:
            return [], True

    evidences: List[Evidence] = []
    for rank, (sym, bm25, vec) in enumerate(fused[:top_k]):
        bm25_rank = next((i for i, it in enumerate(bm25_ranked) if it[0].ref == sym.ref), len(bm25_ranked))
        sem_rank = next((i for i, it in enumerate(sem_ranked) if it[0].ref == sym.ref), len(sem_ranked))
        conf = round(min(1.0, (1.0 / (1 + bm25_rank)) * 0.5
                        + (1.0 / (1 + sem_rank)) * 0.5), 3)
        if bm25 is None and vec is not None:
            source = "semantic_only"
            reason = "关键字未命中，语义检索兜底召回（向量相似度 %.3f）" % _cosine(qvec or [], vec)
        else:
            source = "hybrid_rrf"
            reason = "BM25+语义 RRF 融合命中（bm25_rank=%d, sem_rank=%d）" % (bm25_rank + 1, sem_rank + 1)
        evidences.append(Evidence(symbol=sym, confidence=conf, reason=reason, source=source))

    # 调用图扩展与纯检索保持一致
    if expand_callgraph:
        for ev in list(evidences[:2]):
            for extra in _expand_call_graph(db, ev.symbol):
                if extra.ref not in {e.symbol.ref for e in evidences}:
                    evidences.append(Evidence(
                        symbol=extra, confidence=round(ev.confidence * 0.6, 3),
                        reason="与 " + ev.symbol.name + " 存在调用关系（调用图扩展）",
                        source="call_related"))

    # 证伪式重排序（执行证据可选）
    if execution_hints:
        from .falsify import rerank_evidences
        evidences = rerank_evidences(evidences, execution_hints, db)

    return evidences, False
