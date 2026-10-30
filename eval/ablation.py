"""Ablations share the scorer, pool budget and audited inputs with run_eval."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from replens.eval_inputs import evaluation_inputs
from replens.eval_metrics import (DEFAULT_CANDIDATE_LIMIT, load_dataset, score_predictions,
                                  validate_dataset, validate_limits)
from replens.eval_runner import retrieve_predictions
from replens.indexer import build_index


def _metrics(items, db, top_k, mode, embedder=None, execution_hints=None,
             candidate_limit=DEFAULT_CANDIDATE_LIMIT):
    validate_limits(top_k, candidate_limit)
    validate_dataset(items)
    if mode not in {'bm25_only', 'bm25_callgraph', 'bm25_semantic', 'full', 'full_execution'}:
        raise ValueError('未知消融模式: ' + mode)
    if mode in {'bm25_semantic', 'full'} and embedder is None:
        raise ValueError('语义消融模式需要实际可用的 embedder')
    predictions = retrieve_predictions(items, db, candidate_limit, mode, embedder, execution_hints)
    result = score_predictions(items, predictions, top_k, candidate_limit)
    result.update(retrieval_mode=mode,
                  semantic_enabled=embedder is not None and mode in {'bm25_semantic', 'full', 'full_execution'})
    return result


def _diff_lines(rows):
    by_mode = dict(rows)
    lines = ['## 实测差值', '']
    for first, second, label in [('bm25_only', 'bm25_callgraph', '加入调用图'),
                                 ('bm25_semantic', 'full', '语义模式加入调用图'),
                                 ('bm25_callgraph', 'full', '加入语义排序')]:
        if first not in by_mode or second not in by_mode:
            continue
        a, b = by_mode[first], by_mode[second]
        k = a['top_k']
        lines.extend(['### ' + label + f'（{first} → {second}）', ''])
        for metric in ['hit@1', f'hit@{k}', f'func_name@{k}', f'symbol_start@{k}',
                       f'mrr@{k}', 'recall_evidences', 'refusal_rate']:
            if a.get(metric) is None or b.get(metric) is None:
                continue
            delta = round(b[metric] - a[metric], 4)
            lines.append(f'- {metric}: {a[metric]} → {b[metric]}（差值 {delta:+.4f}）')
        lines.append('')
    return lines


def run_ablation(repo, dataset, top_k=5, hybrid=False, candidate_limit=DEFAULT_CANDIDATE_LIMIT,
                  input_manifest='', sample_id='', execution_test=''):
    validate_limits(top_k, candidate_limit)
    items = load_dataset(dataset)
    with evaluation_inputs(repo, dataset, input_manifest, sample_id) as (meta, audit):
        db = build_index(meta, force=True)
        try:
            modes, embedder, hints = ['bm25_only', 'bm25_callgraph'], None, None
            if hybrid:
                from replens.retriever_hybrid import build_embedder
                embedder = build_embedder()
                if embedder is not None:
                    modes += ['bm25_semantic', 'full']
            if execution_test:
                if input_manifest:
                    raise ValueError('隔离定位 v1 不在模型输入包中执行测试；失败日志材料留待 T5/T6 消费')
                from replens.execution_evidence import collect_execution_evidence
                hints, _ = collect_execution_evidence(meta.path, execution_test)
                modes.append('full_execution')
            rows = [(mode, _metrics(items, db, top_k, mode, embedder, hints, candidate_limit)) for mode in modes]
            return dict(top_k=top_k, candidate_limit=candidate_limit, input_audit=audit,
                        requested_hybrid=hybrid, semantic_available=embedder is not None, rows=rows)
        finally:
            db.close()


def main():
    p = argparse.ArgumentParser(description='统一口径的定位消融')
    p.add_argument('repo')
    p.add_argument('--dataset', required=True)
    p.add_argument('--name', default='ablation')
    p.add_argument('--top-k', type=int, default=5)
    p.add_argument('--candidate-limit', type=int, default=DEFAULT_CANDIDATE_LIMIT)
    p.add_argument('--hybrid', action='store_true')
    p.add_argument('--execution-test', default='')
    p.add_argument('--input-manifest', default='')
    p.add_argument('--sample-id', default='')
    p.add_argument('--output', default='')
    args = p.parse_args()
    try:
        result = run_ablation(args.repo, args.dataset, args.top_k, args.hybrid, args.candidate_limit,
                               args.input_manifest, args.sample_id, args.execution_test)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    k = args.top_k
    lines = ['# 定位消融：' + args.name, '', f'固定符号候选预算 {args.candidate_limit}；文件排名先去重；MRR 截断于 {k}。',
             '函数为短名匹配，行号为同一函数的符号起始行，不是限定名或语句定位。', '',
             f'| 模式 | hit@1 | hit@{k} | func_name@{k} | symbol_start@{k} | mrr@{k} | 文件/函数/行分母 |',
             '|---|---|---|---|---|---|---|']
    for mode, m in result['rows']:
        lines.append(f'| {mode} | {m["hit@1"]} | {m[f"hit@{k}"]} | {m[f"func_name@{k}"]} | {m[f"symbol_start@{k}"]} | {m[f"mrr@{k}"]} | {m["total_positive"]}/{m["total_func_eval"]}/{m["total_line_eval"]} |')
    lines.extend(['', *_diff_lines(result['rows'])])
    out = Path(args.output) if args.output else Path('reports/runs') / ('ablation_' + Path(args.name).name + '.md')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines), encoding='utf-8')
    out.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
