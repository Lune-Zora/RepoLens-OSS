"""Fair file-ranking baselines: same file universe, k, and truncated MRR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from replens.eval_inputs import evaluation_inputs
from replens.eval_metrics import (DEFAULT_CANDIDATE_LIMIT, file_scores, load_dataset,
                                  parse_expected, validate_dataset, validate_limits)
from replens.eval_runner import retrieve_predictions
from replens.indexer import build_index
from replens.retriever import tokenize


def target_paths(item):
    return {parse_expected(e)[0] for e in item['expected']}


def file_universe(meta):
    return sorted(set(meta.python_files + meta.document_files))


def _aggregate(items, rankings, top_k):
    validate_dataset(items)
    validate_limits(top_k)
    if len(items) != len(rankings):
        raise ValueError('预测数量与样本数不一致')
    positive = hit1 = hitk = 0
    rr = 0.0
    for item, ranked in zip(items, rankings):
        if not item['expected']:
            continue
        positive += 1
        # Multiple random seeds are averaged per sample, not counted as extra Bugs.
        per_seed = [file_scores(r, target_paths(item), top_k) for r in ranked]
        hit1 += sum(s['hit1'] for s in per_seed) / len(per_seed)
        hitk += sum(s['hitk'] for s in per_seed) / len(per_seed)
        rr += sum(s['rr'] for s in per_seed) / len(per_seed)
    pct = lambda n: round(n/positive, 4) if positive else None
    return {'hit@1': pct(hit1), f'hit@{top_k}': pct(hitk), f'mrr@{top_k}': pct(rr),
            'total_positive': positive, 'top_k': top_k,
            'counts': {'hit1': hit1, 'hitk': hitk, 'rr_sum': rr},
            'definition': '相同文件全集；文件先去重；MRR 与 hit 使用同一 k 截断'}


def random_baseline(meta, items, seeds=5, top_k=5):
    if type(seeds) is not int or seeds < 1:
        raise ValueError('seeds 必须是正整数')
    rankings = []
    for item in items:
        per_seed = []
        for seed in range(seeds):
            ranked = file_universe(meta)
            seed_bytes = (str(item['id']) + ':' + str(seed)).encode('utf-8')
            random.Random(int.from_bytes(hashlib.sha256(seed_bytes).digest(), 'big')).shuffle(ranked)
            per_seed.append(ranked)
        rankings.append(per_seed)
    return _aggregate(items, rankings, top_k)


def grep_baseline(meta, items, top_k=5):
    texts = {p: (Path(meta.path)/p).read_text(encoding='utf-8', errors='replace').lower()
             for p in file_universe(meta)}
    rankings = []
    for item in items:
        tokens = tokenize(item['issue'])
        scores = {p: sum(text.count(t) for t in tokens) for p, text in texts.items()}
        ranked = sorted((p for p in scores if scores[p] > 0), key=lambda p: (-scores[p], p))
        rankings.append([ranked])
    return _aggregate(items, rankings, top_k)


def replens_baseline(meta, items, top_k=5, hybrid=False, candidate_limit=DEFAULT_CANDIDATE_LIMIT):
    validate_limits(top_k, candidate_limit)
    db = build_index(meta, force=True)
    try:
        embedder = None
        if hybrid:
            from replens.retriever_hybrid import build_embedder
            embedder = build_embedder()
        mode = 'full' if embedder is not None else 'bm25_callgraph'
        predictions = retrieve_predictions(items, db, candidate_limit, mode, embedder)
        rankings = [[[g.path for g in symbols] if not insufficient else []]
                    for symbols, insufficient in predictions]
        result = _aggregate(items, rankings, top_k)
        result.update(retrieval_mode=mode, candidate_limit=candidate_limit, requested_hybrid=hybrid)
        return result
    finally:
        db.close()


def run_baselines(repo, dataset, top_k=5, hybrid=False, candidate_limit=DEFAULT_CANDIDATE_LIMIT,
                  input_manifest='', sample_id=''):
    validate_limits(top_k, candidate_limit)
    items = load_dataset(dataset)
    with evaluation_inputs(repo, dataset, input_manifest, sample_id) as (meta, audit):
        universe = file_universe(meta)
        rows = [('random(5 seeds)', random_baseline(meta, items, top_k=top_k)),
                ('grep(token count)', grep_baseline(meta, items, top_k)),
                ('RepoLens BM25', replens_baseline(meta, items, top_k, candidate_limit=candidate_limit))]
        if hybrid:
            m = replens_baseline(meta, items, top_k, hybrid, candidate_limit)
            rows.append(('RepoLens hybrid' if m['retrieval_mode'] == 'full' else 'RepoLens BM25 (embedding unavailable)', m))
        return dict(top_k=top_k, candidate_limit=candidate_limit, file_universe=universe,
                    input_audit=audit, rows=rows)


def main():
    p = argparse.ArgumentParser(description='RepoLens 公平文件定位基线')
    p.add_argument('repo')
    p.add_argument('--dataset', required=True)
    p.add_argument('--hybrid', action='store_true')
    p.add_argument('--top-k', type=int, default=5)
    p.add_argument('--candidate-limit', type=int, default=DEFAULT_CANDIDATE_LIMIT)
    p.add_argument('--input-manifest', default='')
    p.add_argument('--sample-id', default='')
    p.add_argument('--output', default='reports/runs/eval_baselines.md')
    args = p.parse_args()
    try:
        result = run_baselines(args.repo, args.dataset, args.top_k, args.hybrid, args.candidate_limit,
                               args.input_manifest, args.sample_id)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    k = args.top_k
    lines = ['# RepoLens 文件定位基线', '',
             f'相同文件全集：{len(result["file_universe"])} 个源码/文档文件；符号检索预算 {args.candidate_limit}。',
             '先按文件去重再取 k；所有方法 MRR 均在同一 k 截断。', '',
             f'| 方法 | hit@1 | hit@{k} | mrr@{k} | 正向分母 |', '|---|---|---|---|---|']
    for name, metrics in result['rows']:
        lines.append(f'| {name} | {metrics["hit@1"]} | {metrics[f"hit@{k}"]} | {metrics[f"mrr@{k}"]} | {metrics["total_positive"]} |')
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines), encoding='utf-8')
    out.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
