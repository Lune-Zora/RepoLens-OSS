"""Localization evaluation with shared metrics and optional audited input packages."""
from __future__ import annotations

from .eval_inputs import evaluation_inputs
from .eval_metrics import (DEFAULT_CANDIDATE_LIMIT, LINE_TOLERANCE, load_dataset,
                           parse_expected as _parse_expected, hit_symbols as _hit_at_k,
                           score_predictions, validate_limits)
from .indexer import build_index
from .retriever import retrieve


def retrieve_predictions(items, db, candidate_limit, mode='bm25_callgraph',
                         embedder=None, execution_hints=None):
    from .retriever_hybrid import retrieve_hybrid
    predictions = []
    for item in items:
        kwargs = dict(top_k=candidate_limit, expand_callgraph=mode != 'bm25_only' and mode != 'bm25_semantic')
        if mode == 'full_execution':
            kwargs['execution_hints'] = execution_hints
        if mode in {'bm25_semantic', 'full', 'full_execution'} and embedder is not None:
            evidence, insufficient = retrieve_hybrid(item['issue'], db, embedder=embedder, **kwargs)
        else:
            evidence, insufficient = retrieve(item['issue'], db, **kwargs)
        predictions.append(([e.symbol for e in evidence], insufficient))
    return predictions


def print_metrics(result):
    k, c = result['top_k'], result['counts']
    print(f"评测结果 (top-{k}, {c['positive']} 正向 + {c['noise']} 干扰；候选符号预算 {result['candidate_limit']}):")
    print(f"  hit@1 (去重文件) = {result['hit@1']} ({c['hit1']}/{c['positive']})")
    print(f"  hit@{k} (去重文件) = {result[f'hit@{k}']} ({c['hitk']}/{c['positive']})")
    print(f"  func_name@{k} (函数短名) = {result[f'func_name@{k}']} ({c['func']}/{c['func_name_eval']})")
    print(f"  symbol_start@{k} (同文件/短名，起始行 ±{LINE_TOLERANCE}) = {result[f'symbol_start@{k}']} ({c['line']}/{c['symbol_start_eval']})")
    print(f"  mrr@{k} (去重文件，截断) = {result[f'mrr@{k}']}")
    print(f"  拒答率 = {result['refusal_rate']} ({c['refused']}/{c['noise']})")
    if c['qualified_unsupported']:
        print(f"  限定名标注暂不支持：{c['qualified_unsupported']} 个样本单列，未冒充短名命中")
    print('  输入模式:', result['input_audit']['mode'])


def run_eval(repo_path: str, dataset_file: str, top_k: int = 5, hybrid: bool = False,
             candidate_limit: int = DEFAULT_CANDIDATE_LIMIT, input_manifest: str = '',
             sample_id: str = '') -> dict:
    validate_limits(top_k, candidate_limit)
    items = load_dataset(dataset_file)  # Invalid data must fail before indexing/model calls.
    embedder = None
    with evaluation_inputs(repo_path, dataset_file, input_manifest, sample_id) as (meta, audit):
        db = build_index(meta, force=True)
        try:
            if hybrid:
                from .retriever_hybrid import build_embedder
                embedder = build_embedder()
                if embedder is None:
                    print('[!] embedding 不可用，回退关键字检索；结果记录实际模式')
            mode = 'full' if embedder is not None else 'bm25_callgraph'
            predictions = retrieve_predictions(items, db, candidate_limit, mode, embedder)
            result = score_predictions(items, predictions, top_k, candidate_limit)
            result.update(requested_hybrid=hybrid, retrieval_mode=mode, input_audit=audit)
        finally:
            db.close()
    print_metrics(result)
    return result
