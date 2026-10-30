"""修改计划生成。

基于检索到的证据，生成结构化修改计划：
问题原因分析、影响范围、修改文件列表、修改步骤、风险提示、回归测试。

两种实现：
- RulePlanner：零 key 兜底，用模板 + 证据拼装，保证离线可跑。
- LLMPlanner：注入证据上下文，让 LLM 生成更高质量的计划；失败时回退到 RulePlanner。
"""
from __future__ import annotations

import json
import os
from .model_settings import selected_model
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .retriever import Evidence


@dataclass
class Plan:
    root_cause: str = ""
    affected_scope: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    regression_test: str = ""
    generated_by: str = "rule"  # rule / llm

    def to_dict(self) -> dict:
        return asdict(self)


class Planner:
    def generate(self, issue: str, evidences: List[Evidence]) -> Plan:
        raise NotImplementedError


def _llm_required() -> bool:
    return os.environ.get("REPLENS_REQUIRE_LLM", "").lower() in {"1", "true", "yes"}


class RulePlanner(Planner):
    """规则兜底：不依赖 LLM，基于证据拼装计划。"""

    def generate(self, issue: str, evidences: List[Evidence]) -> Plan:
        if not evidences:
            return Plan(
                root_cause="证据不足，无法确认问题根因。",
                affected_scope=[],
                files_to_modify=[],
                steps=["补充问题描述或确认目标仓库是否包含相关代码。"],
                risks=["无依据推断风险"],
                regression_test="",
                generated_by="rule",
            )

        top = evidences[0].symbol
        files = sorted({e.symbol.path for e in evidences})
        funcs = [e.symbol for e in evidences if e.symbol.type in ("function", "method")]

        root_cause = (
            f"根据检索证据，问题最可能位于 {top.path}:{top.start_line} "
            f"的 {top.type} `{top.name}`。"
        )
        if top.signature:
            root_cause += f" 签名：`{top.signature}`。"
        elif funcs and funcs[0].signature:
            root_cause += f" 签名：`{funcs[0].signature}`。"

        # 追加证据代码首行，便于人工快速复核根因
        if top.code:
            code_lines = top.code.splitlines()
            if code_lines:
                first_line = code_lines[0].strip()
                if first_line:
                    root_cause += f" 关键代码：`{first_line[:120]}`。"

        steps = [
            f"阅读 {top.path} 第 {top.start_line}-{top.end_line} 行的实现。",
        ]
        if funcs:
            steps.append(f"确认 `{funcs[0].name}` 的输入边界条件与现有测试覆盖。")
        steps.append("在临时副本中应用候选修改并运行白名单测试。")

        risks = [
            "本计划由规则模板生成，未经过大模型语义推理，需人工复核。",
            "仅覆盖检索到的局部证据，可能遗漏相关调用方。",
        ]

        return Plan(
            root_cause=root_cause,
            affected_scope=files,
            files_to_modify=files,
            steps=steps,
            risks=risks,
            regression_test=self._build_test_hint(evidences),
            generated_by="rule",
        )

    @staticmethod
    def _build_test_hint(evidences: List[Evidence]) -> str:
        funcs = [e.symbol for e in evidences if e.symbol.type in ("function", "method")]
        if not funcs:
            return ""
        f = funcs[0]
        return (
            f"# 建议在测试中覆盖 `{f.name}` 的边界输入（如空字符串、带空格、None 等）。\n"
            f"# 目标文件: {f.path} 函数定义位于第 {f.start_line} 行。"
        )


class LLMPlanner(Planner):
    """LLM 生成计划，失败回退 RulePlanner。"""

    def __init__(self, llm, model: str = "gpt-4o-mini"):
        self.llm = llm
        self.model = model
        self.fallback = RulePlanner()

    def _fail(self, issue: str, evidences: List[Evidence], reason: str) -> Plan:
        if _llm_required():
            raise RuntimeError("严格模式：禁止规则回退。" + reason)
        return self.fallback.generate(issue, evidences)

    def generate(self, issue: str, evidences: List[Evidence]) -> Plan:
        if not evidences:
            return self.fallback.generate(issue, evidences)

        system = (
            "你是开源贡献辅助工具。基于给定的代码证据生成结构化修改计划。"
            "只允许引用提供的证据，不得编造不存在的文件或行号。"
            "严格输出 JSON，字段：root_cause(字符串)、affected_scope(数组)、"
            "files_to_modify(文件路径字符串数组，不含行号)、steps(字符串数组)、risks(字符串数组)、regression_test(字符串)。"
            '示例格式：{"root_cause":"原因","affected_scope":["path.py"],"files_to_modify":["path.py"],"steps":["步骤"],"risks":["风险"],"regression_test":"测试需求"}。'
        )
        ctx_lines = []
        for e in evidences[:10]:
            numbered = "\n".join(
                f"{e.symbol.start_line + i:04d} | {line}"
                for i, line in enumerate(e.symbol.code.splitlines()[:60])
            )
            ctx_lines.append(
                f"- evidence={e.symbol.ref} ({e.symbol.type} {e.symbol.name}): {e.reason}\n"
                f"[代码]\n{numbered}\n[/代码]"
            )
        user = (
            f"问题描述：{issue}\n\n"
            f"检索到的代码证据：\n" + "\n".join(ctx_lines)
        )

        try:
            from .response_schemas import chat_structured, object_schema, strings
            paths=sorted({e.symbol.path for e in evidences})
            schema=object_schema({'root_cause':{'type':'string','maxLength':300},'affected_scope':dict(strings(paths),maxItems=len(paths)),
                                  'files_to_modify':dict(strings(paths),minItems=1,maxItems=len(paths)),
                                  'steps':{'type':'array','maxItems':4,'items':{'type':'string','maxLength':120}},
                                  'risks':{'type':'array','maxItems':3,'items':{'type':'string','maxLength':100}},
                                  'regression_test':{'type':'string','maxLength':180}})
            data = chat_structured(self.llm,system,user,schema,temperature=0.2)
        except Exception as e:
            return self._fail(issue, evidences, "LLM 调用异常: " + str(e))
        if not data:
            return self._fail(issue, evidences, "LLM 返回空响应")
        if not isinstance(data, dict):
            return self._fail(issue, evidences, "LLM 返回不是 JSON 对象")
        evidence_paths = {e.symbol.path for e in evidences}
        as_text = lambda value: value if isinstance(value, str) else ""
        as_list = lambda value: [x for x in value if isinstance(x, str)] if isinstance(value, list) else []
        affected = as_list(data.get("affected_scope", []))
        raw_files = data.get("files_to_modify", [])
        files = []
        for item in raw_files if isinstance(raw_files,list) else []:
            path = item if isinstance(item,str) else (item.get('file') or item.get('file_path') or item.get('path')) if isinstance(item,dict) else None
            if path in evidence_paths and path not in files: files.append(path)
        def text_items(items):
            if not isinstance(items,list): return []
            return [x if isinstance(x,str) else '；'.join(str(x.get(k,'')) for k in ('action','description') if x.get(k))
                    for x in items if isinstance(x,(str,dict))]
        if not as_text(data.get("root_cause")) or not files:
            return self._fail(issue, evidences, "LLM 返回缺少 root_cause 或有效 files_to_modify")
        return Plan(
            root_cause=as_text(data.get("root_cause")),
            affected_scope=[p for p in affected if p in evidence_paths],
            files_to_modify=files,
            steps=text_items(data.get("steps", [])),
            risks=as_list(data.get("risks", [])),
            regression_test=as_text(data.get("regression_test", "")),
            generated_by="llm",
        )


def build_planner() -> Planner:
    """根据环境变量构建 planner。

    - 设置了 OPENAI_BASE_URL / OPENAI_API_KEY 时用 LLMPlanner
    - 否则用 RulePlanner 兜底
    """
    base = os.environ.get("REPLENS_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("REPLENS_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if base:
        from .llm import OpenAICompatLLM
        # 本地 Ollama 无需 key；未显式指定模型时按 base_url 自动选择
        is_local = ("11434" in base or "localhost" in base)
        default_model = "qwen35-9b-unc:latest" if is_local else "gpt-4o-mini"
        model = selected_model(default_model)
        client = OpenAICompatLLM(base, key, model=model, use_response_format=is_local,
                                 purpose="plan")
        return LLMPlanner(client, model=model)
    if _llm_required():
        raise RuntimeError("REPLENS_REQUIRE_LLM=1，但未配置 REPLENS_LLM_BASE_URL 或 OPENAI_BASE_URL")
    return RulePlanner()
