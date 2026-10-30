"""Shared localization metrics, identities, cutoffs and dataset validation.

File ranking deduplicates the returned symbol pool before truncating to k.
Function names are bare names; line localization compares symbol start lines.
Qualified-name/statement localization is deliberately not claimed here.
"""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re

LINE_TOLERANCE = 3
DEFAULT_CANDIDATE_LIMIT = 100


def validate_limits(top_k, candidate_limit=DEFAULT_CANDIDATE_LIMIT):
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError('top_k 必须是 1–100 的整数')
    if type(candidate_limit) is not int or not top_k <= candidate_limit <= 1000:
        raise ValueError('candidate_limit 必须是 top_k–1000 的整数')


def relative_path(value):
    if (not isinstance(value, str) or not value or value.strip() != value
            or '\\' in value or ':' in value or '\x00' in value):
        raise ValueError('必须使用非空 POSIX 相对路径: ' + repr(value))
    parts = value.split('/')
    if any(p in {'', '.', '..'} for p in parts) or PurePosixPath(value).is_absolute():
        raise ValueError('路径不能是绝对路径或包含空段、.、..: ' + value)
    return value


def parse_expected(value):
    if not isinstance(value, str):
        raise ValueError('expected 条目必须是字符串')
    parts = value.split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('expected 必须为 path[:function[:line]]: ' + value)
    path = relative_path(parts[0])
    name = parts[1] if len(parts) > 1 else None
    if name is not None and (not name or not all(p.isidentifier() for p in name.split('.'))):
        raise ValueError('expected 函数名无效: ' + value)
    line = None
    if len(parts) == 3:
        if not re.fullmatch(r'[1-9][0-9]*', parts[2]):
            raise ValueError('expected 行号必须是正整数: ' + value)
        line = int(parts[2])
    return path, name, line


def validate_dataset(items):
    if not isinstance(items, list) or not items:
        raise ValueError('评测集必须是非空 JSON 数组')
    seen = set()
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f'样本 {index} 必须是对象')
        key = item.get('id')
        if type(key) not in (int, str) or (isinstance(key, str) and not key.strip()):
            raise ValueError(f'样本 {index} id 必须是整数或非空字符串')
        if str(key) in seen:
            raise ValueError('样本 id 重复: ' + str(key))
        seen.add(str(key))
        if not isinstance(item.get('issue'), str) or not item['issue'].strip():
            raise ValueError(f'样本 {index} issue 必须是非空字符串')
        expected = item.get('expected')
        if not isinstance(expected, list):
            raise ValueError(f'样本 {index} expected 必须是数组（干扰样本显式用 []）')
        targets = [parse_expected(e) for e in expected]
        if len(targets) != len(set(targets)):
            raise ValueError(f'样本 {index} expected 存在重复标注')
    return items


def load_dataset(path):
    try:
        return validate_dataset(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取评测集: ' + str(exc)) from exc


def unique_paths(paths):
    return list(dict.fromkeys(paths))


def file_scores(paths, targets, top_k):
    ranked = unique_paths(paths)[:top_k]
    rank = next((i for i, p in enumerate(ranked, 1) if p in targets), None)
    return dict(hit1=int(rank == 1), hitk=int(rank is not None),
                rr=1.0 / rank if rank else 0.0, ranked=ranked)


def hit_symbols(symbols, targets, field):
    for g in symbols:
        for path, name, line in targets:
            if g.path != path:
                continue
            if field == 'path':
                return True
            callable_match = g.type in {'function', 'method'} and name and '.' not in name and g.name == name
            if field == 'func' and callable_match:
                return True
            if field == 'line' and line is not None and callable_match and abs(g.start_line - line) <= LINE_TOLERANCE:
                return True
    return False


def score_predictions(items, predictions, top_k=5, candidate_limit=DEFAULT_CANDIDATE_LIMIT):
    """Predictions are (symbols, insufficient), one per item; retrieval sees only issue."""
    validate_limits(top_k, candidate_limit)
    validate_dataset(items)
    if len(items) != len(predictions):
        raise ValueError('预测数量必须与样本数量一致')
    counts = dict(positive=0, noise=0, func_name_eval=0, symbol_start_eval=0,
                  qualified_unsupported=0, hit1=0, hitk=0, func=0, line=0,
                  rr_sum=0.0, refused=0, positive_refused=0, evidence_hit=0)
    details = []
    for item, (symbols, insufficient) in zip(items, predictions):
        # A refusal cannot simultaneously receive localization credit.
        raw = list(symbols)
        got = [] if insufficient else raw
        targets = [parse_expected(e) for e in item['expected']]
        score = file_scores([g.path for g in got], {t[0] for t in targets}, top_k)
        detail = dict(id=item['id'], type='positive' if targets else 'noise',
                      expected=item['expected'], returned=[g.ref for g in got[:top_k]],
                      ranked_files=score['ranked'], insufficient=bool(insufficient),
                      returned_pool_size=len(got), returned_distinct_files=len(unique_paths(g.path for g in got)))
        if not targets:
            counts['noise'] += 1
            correct = bool(insufficient or not got)
            counts['refused'] += correct
            detail['correct_refusal'] = correct
        else:
            counts['positive'] += 1
            counts['positive_refused'] += bool(insufficient or not got)
            counts['hit1'] += score['hit1']
            counts['hitk'] += score['hitk']
            counts['rr_sum'] += score['rr']
            func_targets = [t for t in targets if t[1] and '.' not in t[1]]
            line_targets = [t for t in func_targets if t[2] is not None]
            unsupported = [item['expected'][i] for i, t in enumerate(targets) if t[1] and '.' in t[1]]
            counts['qualified_unsupported'] += bool(unsupported)
            func_hit = hit_symbols(got[:top_k], func_targets, 'func') if func_targets else None
            line_hit = hit_symbols(got[:top_k], line_targets, 'line') if line_targets else None
            counts['func_name_eval'] += bool(func_targets)
            counts['symbol_start_eval'] += bool(line_targets)
            counts['func'] += bool(func_hit)
            counts['line'] += bool(line_hit)
            evidence_hit = any(hit_symbols(got, [t], 'func' if t[1] else 'path') for t in targets)
            counts['evidence_hit'] += evidence_hit
            detail.update(file_hit=bool(score['hitk']), file_rr=score['rr'],
                          func_name_hit=func_hit, symbol_start_hit=line_hit,
                          unsupported_qualified_targets=unsupported)
        details.append(detail)
    pct = lambda x, n: round(x/n, 4) if n else None
    result = dict(schema_version='3.0', top_k=top_k, candidate_limit=candidate_limit,
                  total_positive=counts['positive'], total_noise=counts['noise'],
                  total_func_eval=counts['func_name_eval'], total_line_eval=counts['symbol_start_eval'],
                  total_qualified_unsupported=counts['qualified_unsupported'],
                  counts=counts, details=details,
                  refusal_rate=pct(counts['refused'], counts['noise']),
                  positive_refusal_rate=pct(counts['positive_refused'], counts['positive']),
                  recall_evidences=pct(counts['evidence_hit'], counts['positive']))
    result.update({'hit@1': pct(counts['hit1'], counts['positive']),
                   f'hit@{top_k}': pct(counts['hitk'], counts['positive']),
                   f'func_name@{top_k}': pct(counts['func'], counts['func_name_eval']),
                   f'symbol_start@{top_k}': pct(counts['line'], counts['symbol_start_eval']),
                   f'mrr@{top_k}': pct(counts['rr_sum'], counts['positive'])})
    result['metric_definitions'] = {
        'file': '先按文件首次出现顺序去重，再取前 k；MRR 在同一 k 截断，未命中为 0',
        'function': '前 k 个符号的 path + 函数/方法短名；限定名不支持并单列',
        'line': f'前 k 个函数/方法符号须匹配同一 path + 短名 + 起始行 ±{LINE_TOLERANCE}；不是语句定位',
        'denominators': '文件/MRR=全部正向样本；函数/行分别为具有受支持标注的样本；拒答=干扰样本',
        'pool': '固定 candidate_limit 检索符号池（可能带调用图扩展）；不等于全仓库无限深度排名',
    }
    # Historical consumers at k=5 remain readable; never publish misleading @5 for other k.
    result['mrr'] = result[f'mrr@{top_k}']
    if top_k == 5:
        result.update({'func@5': result['func_name@5'], 'line_acc@5': result['symbol_start@5']})
        result['legacy_aliases'] = {'func@5': 'func_name@5', 'line_acc@5': 'symbol_start@5', 'mrr': 'mrr@5'}
    return result
