"""补丁自动生成与验证闭环。

定位证据 → 生成候选补丁（规则 / LLM）→ 结构化校验 → 临时副本应用 → 测试反馈 → 修复重试。

设计原则：
1. 补丁只能在「已检索到的证据」范围内生成，LLM 不得凭空引用不存在的文件或代码。
2. 补丁必须通过结构化校验：路径限定在仓库内、扩展名白名单、from 串必须在目标文件中真实存在。
3. 测试失败时把失败日志回喂 LLM 做一次修复重试；仍失败则如实报告，绝不编造成功。
"""
from __future__ import annotations

import ast
import json
import os
from .model_settings import selected_model
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .repo import RepoMeta
from .retriever import Evidence, _is_test_path, tokenize

ALLOWED_PATCH_EXTS = {".py", ".json", ".md", ".toml", ".txt", ".rst", ".yaml", ".yml", ".ini", ".cfg"}

# 测试配置/构建配置路径段：补丁不得修改，防止改变测试收集或执行命令。
TEST_CONFIG_SEGMENTS = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg",
                        "pyproject.toml", "setup.py", "requirements.txt"}


def _llm_required() -> bool:
    return os.environ.get("REPLENS_REQUIRE_LLM", "").lower() in {"1", "true", "yes"}


class PatchGenError(Exception):
    pass


@dataclass
class AttemptRecord:
    """一次候选尝试的完整记录（用于 Best-of-N 反馈与候选选择）。

    修复 GPT-6 §3.4：之前只用 last_before/last_after 反馈重试，
    出现 SECOND_FAILURE 配 FIRST_PATCH 的错配；现在每条尝试保留
    自己的 before/after 与补丁内容，反馈严格对应。
    """
    candidate_id: str
    patch_file: str
    patch_note: str
    patch_content: str
    before: list          # 该次尝试的修复前测试结果
    after: Optional[list] # 该次尝试的修复后测试结果（None 表示补丁应用失败）
    success: bool
    failure_kind: str     # patch_apply_failed / before_invalid / after_invalid / still_failing


def _failure_kind(before, after) -> str:
    """粗略归类失败原因，供报告呈现。"""
    if not after:
        return "patch_apply_failed"
    if not before or any(not r.valid for r in before):
        return "before_invalid"
    if after and any(not r.valid for r in after):
        return "after_invalid"
    return "still_failing"


def _failed_test_count(results) -> Optional[int]:
    """统计实际失败/错误数；缺摘要或失败退出却没有计数时返回未知。"""
    if not results:
        return None
    total = 0
    for result in results:
        summary = getattr(result, "summary", None)
        if not isinstance(summary, dict) or not summary:
            return None
        failed, errors = summary.get("failed", 0), summary.get("errors", 0)
        if any(type(n) is not int or n < 0 for n in (failed, errors)):
            return None
        count = failed + errors
        if not result.passed and count == 0:
            return None
        total += count
    return total


def _failed_attempt_rank(attempt: AttemptRecord) -> tuple:
    applied = bool(attempt.after) and attempt.patch_note.startswith("补丁应用成功")
    valid = (applied and bool(attempt.before)
             and all(r.valid and not r.timed_out for r in attempt.before + attempt.after))
    # 有效执行优于无效执行；未应用成功或未产生 after 不能视为零失败。
    tier = 0 if valid else (1 if applied else 2)
    count = _failed_test_count(attempt.after)
    return tier, count is None, count if count is not None else 0


def _compare_failed(a: AttemptRecord, b: AttemptRecord) -> int:
    """有效执行优先，再按失败/错误数排序；相同结果保持原顺序。

    汇总计数不能证明没有新增回归，不能用 Python 对象 id 比较测试身份。
    """
    a_rank, b_rank = _failed_attempt_rank(a), _failed_attempt_rank(b)
    return (a_rank > b_rank) - (a_rank < b_rank)


def _pick_best_failed(attempts: List[AttemptRecord]) -> Optional[AttemptRecord]:
    """从失败尝试中选执行有效且已知失败/错误数最少的候选。"""
    valid = [a for a in attempts if a.before or a.after]
    if not valid:
        return attempts[0] if attempts else None
    best = valid[0]
    for a in valid[1:]:
        if _compare_failed(a, best) < 0:
            best = a
    return best


def _rel_safe(meta: RepoMeta, rel: str) -> bool:
    """补丁目标路径必须位于仓库内，且扩展名在白名单内。"""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return False
    if Path(rel).suffix.lower() not in ALLOWED_PATCH_EXTS:
        return False
    root = Path(meta.path).resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return target.is_file()


def validate_rules(meta: RepoMeta, rules: dict,
                   evidence_paths: Optional[List[str]] = None) -> Tuple[bool, str]:
    """校验 JSON 补丁规则：路径合法 + 证据白名单 + from 串真实存在于目标文件。

    evidence_paths 为允许修改的证据文件列表（相对路径）；默认 None 表示不启用白名单，
    但所有测试文件与测试/构建配置路径始终被拒绝。
    """
    if not isinstance(rules, dict) or not rules:
        return False, "补丁规则为空或不是 JSON 对象"
    evidence_set = None
    if evidence_paths is not None:
        evidence_set = {p.replace("\\", "/").lower() for p in evidence_paths}
    for rel, rule in rules.items():
        if not isinstance(rel, str) or not isinstance(rule, dict):
            return False, "补丁规则结构错误"
        if not _rel_safe(meta, rel):
            return False, "补丁目标路径不合法: " + rel
        if "from" not in rule or "to" not in rule:
            return False, "补丁规则缺少 from/to 字段: " + rel
        if not isinstance(rule["from"], str) or not isinstance(rule["to"], str):
            return False, "补丁规则 from/to 必须是字符串: " + rel
        if not rule["from"]:
            return False, "补丁规则 from 不能为空: " + rel
        normalized = rel.replace("\\", "/").lower()
        if _is_test_path(rel):
            return False, "补丁不得修改测试文件: " + rel
        parts = set(Path(rel).parts)
        if parts & TEST_CONFIG_SEGMENTS:
            return False, "补丁不得修改测试/构建配置: " + rel
        if evidence_set is not None and normalized not in evidence_set:
            return False, "补丁目标不在证据白名单内: " + rel
        src = (Path(meta.path) / rel).read_text(encoding="utf-8", errors="ignore")
        old = rule["from"]
        if old not in src and old.replace("\n", "\r\n") not in src:
            return False, "from 串在目标文件中不存在: " + rel
        if src.count(old) != 1 and src.count(old.replace("\n", "\r\n")) != 1:
            return False, "from 串匹配不唯一: " + rel
    return True, "补丁规则校验通过"


def write_patch(meta: RepoMeta, rules: dict,
                evidence_paths: Optional[List[str]] = None) -> str:
    """把规则写入仓库 .replens/candidate_patch.json，返回补丁文件路径。"""
    ok, msg = validate_rules(meta, rules, evidence_paths=evidence_paths)
    if not ok:
        raise PatchGenError(msg)
    out_dir = Path(meta.path) / ".replens" / "candidates"
    out_dir.mkdir(parents=True, exist_ok=True)
    patch_file = out_dir / ("candidate_" + uuid.uuid4().hex + ".json")
    patch_file.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(patch_file)


def _first_body_anchor(symbol, code_lines: List[str]) -> Optional[Tuple[str, str, str]]:
    """在函数体第一条语句前插入一行，返回 (from串, to串, 参数名)。"""
    try:
        tree = ast.parse("\n".join(code_lines))
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol.name:
            args = node.args.posonlyargs + node.args.args
            if not args or not node.body:
                return None
            arg_name = args[0].arg
            body_idx = 0
            # 跳过 docstring，保证插入点在真正的第一条语句之前
            if (len(node.body) > 1
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                body_idx = 1
            body_lineno = node.body[body_idx].lineno
            if body_lineno - 1 >= len(code_lines):
                return None
            anchor = code_lines[body_lineno - 1]
            indent = anchor[: len(anchor) - len(anchor.lstrip())]
            new_line = indent + arg_name + " = " + arg_name + ".strip()"
            return anchor, new_line + "\n" + anchor, arg_name
    return None


class PatchGenerator:
    def generate(self, meta: RepoMeta, issue: str, evidences: List[Evidence],
                 feedback: str = "") -> Tuple[Optional[str], str]:
        raise NotImplementedError


class RulePatchGenerator(PatchGenerator):
    """零 key 兜底：仅对高置信的空白/空格处理类问题生成 .strip() 补丁。

    对其它类型问题诚实返回 None（规则引擎拒绝编造补丁），此时系统仍输出
    修复前测试结果与修改计划，不伪造修复成功。
    """

    STRIP_HINTS = {"strip", "whitespace", "trim", "space", "spaces", "blank", "pad", "padded"}

    def generate(self, meta, issue, evidences, feedback=""):
        if feedback:
            return None, "规则引擎不支持基于测试反馈的修复重试，请配置 LLM。"
        if not evidences:
            return None, "无定位证据，规则引擎无法生成补丁。"
        tokens = set(tokenize(issue))
        if not (tokens & self.STRIP_HINTS):
            return None, "规则引擎未识别到高置信的空白处理模式（strip/whitespace/trim 等）。"
        top = evidences[0].symbol
        if top.type not in ("function", "method"):
            return None, "定位到的首个符号不是函数/方法，规则引擎不生成补丁。"
        code_lines = top.code.splitlines()
        if not code_lines:
            return None, "证据缺少代码片段，规则引擎无法生成补丁。"
        anchor = _first_body_anchor(top, code_lines)
        if not anchor:
            return None, "无法解析函数体，规则引擎不生成补丁。"
        from_str, to_str, arg = anchor
        if arg + " = " + arg + ".strip()" in top.code:
            return None, "代码中已存在等价 strip 处理，规则引擎不重复生成。"
        try:
            patch_file = write_patch(meta, {top.path: {"from": from_str, "to": to_str}},
                                     evidence_paths=[top.path])
            note = ("规则引擎生成 strip 补丁（在 " + top.name + " 函数体首行前插入 "
                    + arg + " = " + arg + ".strip()）。")
            return patch_file, note
        except PatchGenError as e:
            return None, str(e)


class LLMPatchGenerator(PatchGenerator):
    """基于证据 + 测试反馈的 LLM 补丁生成，带结构化校验与 Best-of-N 候选采样。"""

    def __init__(self, llm, model: str = "gpt-4o-mini", n_candidates: int = 4):
        self.llm = llm
        self.model = model
        self.n_candidates = max(1, n_candidates)
        self._generation_round = 0
        self.last_errors = []

    def _build_prompts(self, issue, evidences, feedback=""):
        # 关键修复（2026-10-29）：此前只喂 evidences[:4]，真实 bug 文件（如 tqdm/std.py）
        # 常因检索排序排在第 5 位之后而被截断，模型根本没看到要改的代码 → 候选为 0。
        # 现在：生产源码证据全部纳入（最多 8 条，供修改），测试文件单独标为「仅供上下文、
        # 禁止修改」（最多 2 条，帮助模型理解预期行为），并要求 from 带足够上下文以唯一匹配。
        system = (
            "你是开源仓库修复专家。基于给定的代码证据与测试反馈，生成最小修改补丁。"
            "只允许修改标记为【可修改】的生产源码文件；标记为【仅供上下文，禁止修改】的"
            "测试文件只用于理解预期行为，绝不能出现在补丁里。"
            "from 字段必须是目标文件中逐字符一致的原文（含缩进与标点），"
            "且要包含足够上下文（可多行），保证在整个文件里唯一匹配。"
            '严格输出 JSON 对象，格式：{"<相对路径>": {"from": "<原文>", "to": "<替换后>"}}。'
            '若无法生成可靠补丁，输出 {"reason": "<原因>"}。不要输出任何多余文字。'
        )
        selected = [e for e in evidences if e.source == "intent_target"]
        prod = selected or [e for e in evidences if not _is_test_path(e.symbol.path)]
        context_only = [e for e in evidences if e not in prod and not _is_test_path(e.symbol.path)]
        tests = [e for e in evidences if _is_test_path(e.symbol.path)]
        ordered = ([("可修改", e) for e in prod[:8]]
                   + [("仅供上下文，禁止修改", e) for e in (context_only[:3] + tests[:2])])
        if not ordered:
            ordered = [("可修改", e) for e in evidences[:8]]
        ctx = []
        for tag, e in ordered:
            # A replacement must quote the real source, not presentation line prefixes.
            code = "\n".join(e.symbol.code.splitlines()[:100])
            ctx.append("【" + tag + "】文件 " + e.symbol.ref + " " + e.symbol.type + " "
                       + e.symbol.name + " 证据来源: " + e.reason)
            ctx.append("[代码开始]")
            ctx.append(code)
            ctx.append("[代码结束]")
        user = "问题描述：" + issue + "\n\n代码证据：\n" + "\n".join(ctx)
        if feedback:
            user += "\n\n上一次补丁的测试反馈（失败日志节选）：\n" + feedback[:3000]
            user += "\n请分析失败原因，修正补丁并重新输出 JSON。"
        return system, user

    def generate_candidates(self, meta, issue, evidences, feedback=""):
        """Best-of-N：以不同温度采样多个候选，返回通过结构化校验的候选列表。"""
        if not evidences:
            return []
        self._generation_round += 1
        self.last_errors = []
        system, user = self._build_prompts(issue, evidences, feedback)
        candidates = []
        seen = set()
        for i in range(self.n_candidates):
            temperature = round(0.2 + 0.15 * i, 2)
            context = getattr(self.llm, "trace_context", None)
            if isinstance(context, dict):
                context.update(generation_round=self._generation_round, candidate_index=i + 1)
            try:
                from .response_schemas import chat_structured, object_schema
                selected=[e for e in evidences if e.source=='intent_target']
                editable=selected or [e for e in evidences if not _is_test_path(e.symbol.path)]
                paths=sorted({e.symbol.path for e in editable})
                properties={path:object_schema({'from':{'type':'string','minLength':1},'to':{'type':'string'}}) for path in paths}
                patch_schema={'anyOf':[{'type':'object','properties':properties,'additionalProperties':False,'minProperties':1},
                                       object_schema({'reason':{'type':'string'}})]}
                data = chat_structured(self.llm,system,user,patch_schema,temperature=temperature)
            except Exception as exc:
                self.last_errors.append(str(exc))
                continue
            if not data:
                continue
            if "reason" in data and set(data.keys()) == {"reason"}:
                self.last_errors.append(str(data["reason"]))
                continue
            try:
                pf = write_patch(meta, data,
                                 evidence_paths=[e.symbol.path for e in evidences])
            except PatchGenError as exc:
                self.last_errors.append(str(exc))
                continue
            from .task_tests import scope_violation
            violation = scope_violation(meta.path, pf, evidences)
            if violation:
                self.last_errors.append(violation)
                continue
            content = Path(pf).read_text(encoding="utf-8")
            if content in seen:
                continue
            seen.add(content)
            candidates.append((pf, "LLM 候选补丁 #" + str(len(candidates))
                               + " 通过结构化校验（temperature=" + str(temperature) + "）。"))
        return candidates

    def generate(self, meta, issue, evidences, feedback=""):
        """兼容旧接口：返回第一个有效候选。"""
        cands = self.generate_candidates(meta, issue, evidences, feedback)
        if cands:
            return cands[0]
        return None, "LLM 未生成有效候选补丁。"


def build_patch_generator() -> PatchGenerator:
    provider = os.environ.get("REPLENS_LLM_PROVIDER", "")
    if provider == "transformers":
        from .llm import TransformersLocalLLM
        model_path = os.environ.get("REPLENS_LLM_MODEL", "Qwen/Qwen2.5-Coder-1.5B-Instruct")
        n_candidates = int(os.environ.get("REPLENS_LLM_N_CANDIDATES", "3"))
        client = TransformersLocalLLM(model_path)
        return LLMPatchGenerator(client, model=model_path, n_candidates=n_candidates)

    base = os.environ.get("REPLENS_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("REPLENS_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if base:
        from .llm import OpenAICompatLLM
        is_local = ("11434" in base or "localhost" in base)
        default_model = "qwen35-9b-unc:latest" if is_local else "gpt-4o-mini"
        model = selected_model(default_model)
        n_candidates = int(os.environ.get("REPLENS_LLM_N_CANDIDATES", "4"))
        client = OpenAICompatLLM(base, key, model=model, use_response_format=is_local,
                                 purpose="patch")
        return LLMPatchGenerator(client, model=model, n_candidates=n_candidates)
    if _llm_required():
        raise RuntimeError("REPLENS_REQUIRE_LLM=1，但未配置 LLM 服务，拒绝使用有限规则引擎")
    return RulePatchGenerator()


def _failure_feedback(results) -> str:
    """从测试结果中提取失败日志（供修复重试）。"""
    parts = []
    for r in results or []:
        if not r.passed:
            parts.append(r.stderr or "")
            parts.append(r.stdout or "")
    return "\n".join(parts)[-4000:]


def auto_patch_loop(meta: RepoMeta, issue: str, evidences: List[Evidence],
                    test_command: str, timeout: int = 120,
                    extra_files: Optional[dict] = None,
                    max_repair_rounds: int = 3) -> Tuple[Optional[str], str, list, Optional[list]]:
    """补丁自动生成 + 验证闭环（Best-of-N 测试筛选 + 规则兜底 + 多轮修复重试）。

    流程：
    1. LLM 以不同温度采样 N 个候选补丁（结构化校验去重）
    2. 依次在临时副本中测试，第一个让「修复后」通过的候选胜出
    3. 全部失败 → 规则引擎兜底（零成本、可解释）
    4. 规则也失败 → 用结构化失败反馈（失败测试名 + 帧位置 + 日志 +
       上一轮补丁内容）回喂 LLM，最多修复重试 max_repair_rounds 轮
    始终返回 (补丁文件, 说明, before结果, after结果)。
    未选出通过验证的补丁时，补丁文件和 after 均为 None，只报告修复前基线。
    """
    from .executor import run_test_suite
    from .validation import assess_primary

    if any(e.source == "intent_target" for e in evidences):
        max_repair_rounds = int(os.environ.get("REPLENS_LLM_MAX_REPAIR_ROUNDS", "2"))
    if not evidences:
        before, _, _ = run_test_suite(
            meta.path, test_command, timeout=timeout,
            patch_file=None, extra_files=extra_files)
        return None, "定位证据不足，未生成补丁，仅输出修复前基线。", before, None

    gen = build_patch_generator()

    def _try_candidates(candidates, note_prefix):
        """依次测试候选补丁，返回 (attempts, note_prefix)。

        attempts 是 AttemptRecord 列表，每条记录保留：
          - candidate_id, patch_file, patch_note
          - before/after 测试结果（与该补丁的尝试严格对应）
          - patch_content（用于下次反馈）
          - success / failure_kind

        首个候选通过就停止；全部失败时按有效性和实际失败/错误数选择反馈对象。
        """
        attempts: List[AttemptRecord] = []
        for idx, (pf, n2) in enumerate(candidates):
            before, after, pnote = run_test_suite(
                meta.path, test_command, timeout=timeout,
                patch_file=pf, extra_files=extra_files)
            try:
                patch_content = Path(pf).read_text(encoding="utf-8")
            except Exception:
                patch_content = ""
            primary = assess_primary(before, after)
            success = (primary['status'] == 'PRIMARY_PASSED'
                       and pnote.startswith("补丁应用成功"))
            if not success and primary['reasons']:
                pnote += '；' + '；'.join(primary['reasons'])
            attempts.append(AttemptRecord(
                candidate_id=note_prefix + "#" + str(idx),
                patch_file=pf,
                patch_note=pnote,
                patch_content=patch_content,
                before=before, after=after,
                success=success,
                failure_kind="" if success else _failure_kind(before, after),
            ))
            if success:
                break
        return attempts, note_prefix

    candidates = []
    note = ""
    if isinstance(gen, LLMPatchGenerator):
        candidates = gen.generate_candidates(meta, issue, evidences)
        note = "LLM 生成 " + str(len(candidates)) + " 个有效候选。"
        if not candidates and gen.last_errors:
            note += "候选未通过：" + "；".join(gen.last_errors[:3])
    else:
        pf, n2 = gen.generate(meta, issue, evidences)
        note = n2
        if pf:
            candidates = [(pf, n2)]

    last_best: Optional[AttemptRecord] = None

    if candidates:
        attempts, _ = _try_candidates(candidates, note)
        # 优先取首个 success 的候选（保持 Best-of-N 原语义）
        win = next((a for a in attempts if a.success), None)
        if win is not None:
            return win.patch_file, note + "；" + win.patch_note + "（主测试筛选通过，待最终验收）", \
                   win.before, win.after
        # 全部失败：选有效执行且实际失败/错误数较少的候选供反馈用。
        last_best = _pick_best_failed(attempts)

    # 规则引擎兜底（离线零 key 能力）；严格模式禁止规则代替模型
    if not isinstance(gen, RulePatchGenerator) and not _llm_required():
        rule = RulePatchGenerator()
        pf_rule, note_rule = rule.generate(meta, issue, evidences)
        if pf_rule:
            before2, after2, pnote2 = run_test_suite(
                meta.path, test_command, timeout=timeout,
                patch_file=pf_rule, extra_files=extra_files)
            try:
                rule_content = Path(pf_rule).read_text(encoding="utf-8")
            except Exception:
                rule_content = ""
            primary2 = assess_primary(before2, after2)
            success2 = (primary2['status'] == 'PRIMARY_PASSED'
                        and pnote2.startswith("补丁应用成功"))
            if not success2 and primary2['reasons']:
                pnote2 += '；' + '；'.join(primary2['reasons'])
            rule_attempt = AttemptRecord(
                candidate_id="rule_fallback",
                patch_file=pf_rule,
                patch_note=pnote2,
                patch_content=rule_content,
                before=before2, after=after2,
                success=success2,
                failure_kind="" if success2 else _failure_kind(before2, after2),
            )
            if success2:
                return pf_rule, note + "；规则候选主测试筛选通过，待最终验收。" + note_rule, \
                       before2, after2
            last_best = rule_attempt if (last_best is None or
                                          _compare_failed(rule_attempt, last_best) < 0) \
                                       else last_best

    # LLM 多轮修复重试（结构化失败反馈 + 上一轮 best-attempt 的补丁内容）
    if isinstance(gen, LLMPatchGenerator):
        for round_no in range(1, max_repair_rounds + 1):
            # 反馈严格对应 last_best 的失败（修复 GPT-6 §3.4 的 SECOND_FAILURE+FIRST_PATCH 错配）
            fb = "候选格式/范围校验：" + "；".join(getattr(gen,"last_errors",[])[:4]) if getattr(gen,"last_errors",[]) else ""
            if last_best:
                fb = ("候选验证失败（" + last_best.failure_kind + "）："
                      + last_best.patch_note + "\n"
                      + _failure_feedback(last_best.after or last_best.before))
            if last_best and last_best.patch_content:
                fb += "\n\n上一轮候选补丁内容（" + last_best.candidate_id + "）：\n" \
                      + last_best.patch_content[:2000]
            retry_candidates = gen.generate_candidates(meta, issue, evidences, feedback=fb)
            if not retry_candidates:
                break
            attempts, _ = _try_candidates(
                retry_candidates,
                note + "；第 " + str(round_no) + " 轮修复重试（反馈驱动）：")
            win = next((a for a in attempts if a.success), None)
            if win is not None:
                return win.patch_file, win.patch_note, win.before, win.after
            cand_best = _pick_best_failed(attempts)
            if cand_best is not None:
                last_best = cand_best if (last_best is None or
                                          _compare_failed(cand_best, last_best) < 0) \
                                     else last_best

    # 未接受补丁时不展示候选的 after，避免被报告当成已应用的修复结果。
    if last_best is not None:
        return (
            None,
            note + "；未选出通过验证的补丁；最佳失败候选："
            + last_best.failure_kind + "，" + last_best.patch_note + "；仅输出修复前基线。",
            last_best.before,
            None,
        )
    before, _, _ = run_test_suite(
        meta.path, test_command, timeout=timeout,
        patch_file=None, extra_files=extra_files)
    return None, note + "；未选出通过验证的补丁，仅输出修复前基线。", before, None
