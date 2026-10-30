"""贡献报告生成。

汇总「定位证据 → 修改计划 → 测试验证」为结构化 JSON 与 Markdown。
"""
from __future__ import annotations

import json
from typing import List, Optional

from .planner import Plan
from .retriever import Evidence


def build_report(
    repo_path: str,
    issue: str,
    evidences: List[Evidence],
    insufficient: bool,
    plan: Optional[Plan],
    test_command: str,
    before_results: list,
    after_results: Optional[list],
    patch_note: str,
    generated_test: str,
    patch_diff: str = "",
    execution_hints: Optional[list] = None,
    flip_prediction: Optional[dict] = None,
    regression_before: Optional[list] = None,
    regression_after: Optional[list] = None,
    regression_test_cmd: str = "",
    validation: Optional[dict] = None,
    framework: str = "",
    generated_kind: str = "none",
    acceptance_before: Optional[list] = None,
    acceptance_after: Optional[list] = None,
    acceptance_test_cmd: str = "",
) -> dict:
    if validation is None:
        from .validation import assess_repair
        validation = assess_repair(before_results, after_results,
                                   regression_after=regression_after, insufficient=insufficient)
    return {
        "schema_version": "2.0",
        "status": validation['status'],
        "reasons": list(validation['reasons']),
        "validation": validation,
        "test_framework": framework,
        "generated_test_kind": generated_kind,
        "acceptance_before": [r.to_dict() for r in (acceptance_before or [])],
        "acceptance_after": [r.to_dict() for r in (acceptance_after or [])],
        "acceptance_test_cmd": acceptance_test_cmd,
        "repo": repo_path,
        "issue": issue,
        "insufficient_evidence": insufficient,
        "evidences": [
            {
                "ref": e.symbol.ref,
                "type": e.symbol.type,
                "name": e.symbol.name,
                "confidence": e.confidence,
                "reason": e.reason,
                "source": e.source,
                "signature": e.symbol.signature,
                "code": e.symbol.code,
            }
            for e in evidences
        ],
        "plan": plan.to_dict() if plan else None,
        "test_command": test_command,
        "patch_note": patch_note,
        "before": [r.to_dict() for r in before_results],
        "after": [r.to_dict() for r in (after_results or [])],
        "generated_test": generated_test,
        "patch_diff": patch_diff,
        "execution_hints": [{"ref": h.ref, "frame_func": h.frame_func, "weight": round(h.weight, 3)}
                            for h in (execution_hints or [])],
        "flip_prediction": flip_prediction,
        "regression_before": [r.to_dict() for r in (regression_before or [])],
        "regression_after": [r.to_dict() for r in (regression_after or [])],
        "regression_test_cmd": regression_test_cmd,
    }


# 失败日志的关键行标记（pytest / unittest）
_FAILURE_MARKERS = ("FAILED", "AssertionError", "=== FAILURES ===", "FAIL:",
                    "ERROR:", "tests failed", "short test summary")


def _failure_lines(r: dict, max_lines: int = 8) -> List[str]:
    """从测试结果中提取失败关键行（哪个测试失败 + 断言内容）。"""
    text = "\n".join([r.get("stdout", "") or "", r.get("stderr", "") or ""])
    out: List[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and any(m in ln for m in _FAILURE_MARKERS):
            out.append(s)
        if len(out) >= max_lines:
            break
    return out


def render_markdown(rep: dict) -> str:
    lines: List[str] = []
    lines.append("# RepoLens-OSS 分析报告\n")
    lines.append(f"## 问题\n\n> {rep['issue']}\n")
    status = rep.get('status', 'UNVERIFIED')
    label = '独立验收通过' if status == 'VERIFIED' else '未完成独立验收'
    lines.append(f"## 修复结论\n\n**{status} — {label}**\n")
    for reason in rep.get('reasons', []):
        lines.append('- ' + reason)
    lines.append('')

    provenance = rep.get('provenance')
    if provenance:
        lines.append('## 证据包与重放\n')
        lines.append('打包状态：`' + provenance['status'] + '`；Schema：`' + provenance['schema_version'] + '`。\n')
        lines.append('材料核验：`' + provenance['materials_status'] + '`；重放一致性：`' + provenance['replay_status'] + '`。\n')
        lines.append('生成证据包后需单独执行 verify；重放一致不等于修复通过。\n')
        for reason in provenance.get('reasons', []):
            lines.append('- ' + reason)
        lines.append('')

    understanding = rep.get('understanding') or {}
    if understanding:
        lines.append('## 问题理解与检索说明\n')
        for label,key in [('理解','summary'),('预期行为','expected_behavior'),('检索表达','search_query'),('范围判断','explanation'),('模型','model')]:
            lines.append('- '+label+'：'+str(understanding.get(key) or '未提供'))
        lines.append('- 已检查目录：'+str(understanding.get('catalog_shown',0))+'/'+str(understanding.get('catalog_total',0)))
    if rep.get('blocked_reason'):
        lines.append('当前阻塞：'+rep['blocked_reason'])
    if rep.get('task_tests'):
        task = rep['task_tests']
        lines.append('## 当前问题测试\n')
        lines.append(('用户明确提供并保持原样' if task.get('kind')=='user_behavioral_examples' else '模型提出并审核')+'、补丁前冻结；不是独立验收。SHA-256：'+str(task.get('sha256','未生成')))
        lines.append('用例数量：'+str(len(task.get('cases',[]))))
    if rep.get('validation',{}).get('preexisting_failures'):
        lines.append('仍存在的既有失败：'+', '.join(rep['validation']['preexisting_failures']))
    lines.append(rep.get('verification_scope','验收仅覆盖给定测试范围。'))

    if rep["insufficient_evidence"]:
        lines.append("## 结论\n\n> 当前仓库证据不足，无法确认该结论。\n")
        return "\n".join(lines)

    lines.append("## 代码定位（证据）\n")
    lines.append("| 文件:行号 | 类型 | 符号 | 置信度 | 来源 | 判断依据 |")
    lines.append("|---|---|---|---|---|---|")
    for e in rep["evidences"]:
        lines.append(
            f"| `{e['ref']}` | {e['type']} | {e['name']} | {e['confidence']} | "
            f"{e['source']} | {e['reason']} |")
    lines.append("")

    plan = rep.get("plan") or {}
    # 关键证据代码片段：评审可对照证据与源码，强化「可证伪」设计主张
    lines.append("## 关键证据代码\n")
    for e in rep["evidences"][:3]:
        code = (e.get("code") or "").strip()
        if not code:
            continue
        code_preview = "\n".join(code.splitlines()[:8])
        lines.append(f"**`{e['ref']}` — {e['name']}**")
        lines.append("```python")
        lines.append(code_preview)
        lines.append("```")
        lines.append("")

    lines.append("## 修改计划\n")
    lines.append(f"- **根因分析**：{plan.get('root_cause', '')}")
    lines.append(f"- **影响范围**：{', '.join(plan.get('affected_scope', [])) or '未知'}")
    lines.append(f"- **修改文件**：{', '.join(plan.get('files_to_modify', [])) or '未知'}")
    lines.append("- **修改步骤**：")
    for s in plan.get("steps", []):
        lines.append(f"  - {s}")
    lines.append("- **风险提示**：")
    for r in plan.get("risks", []):
        lines.append(f"  - {r}")
    lines.append(f"\n计划生成方式：`{plan.get('generated_by', 'rule')}`\n")

    lines.append("## 测试验证\n")
    lines.append(f"测试命令：`{rep['test_command']}`")
    if rep.get("patch_note"):
        lines.append(f"\n补丁：{rep['patch_note']}")
    lines.append("\n### 修复前\n")
    for r in rep.get("before", []):
        valid_status = "；有效" if r.get("valid") else "；⚠️ 无效: " + r.get("invalid_reason", "未知原因")
        lines.append(f"- 命令 `{r['command']}` → exit {r['returncode']}"
                     f"（{'无效' if not r.get('valid') else ('通过' if r['passed'] else '未通过')}{valid_status}）")
        if r.get("summary"):
            s = r["summary"]
            lines.append(f"  - 摘要：passed={s.get('passed', 0)}, failed={s.get('failed', 0)}, "
                         f"errors={s.get('errors', 0)}, skipped={s.get('skipped', 0)}, "
                         f"collected={s.get('collected', 0)}")
        if r.get("timed_out"):
            lines.append("  - ⏱ 超时终止")
        elif not r.get("passed"):
            for fl in _failure_lines(r):
                lines.append(f"  - ❌ {fl}")
    if rep.get("after"):
        lines.append("\n### 修复后\n")
        for r in rep.get("after", []):
            valid_status = "；有效" if r.get("valid") else "；⚠️ 无效: " + r.get("invalid_reason", "未知原因")
            lines.append(f"- 命令 `{r['command']}` → exit {r['returncode']}"
                         f"（{'无效' if not r.get('valid') else ('通过' if r['passed'] else '未通过')}{valid_status}）")
            if r.get("summary"):
                s = r["summary"]
                lines.append(f"  - 摘要：passed={s.get('passed', 0)}, failed={s.get('failed', 0)}, "
                             f"errors={s.get('errors', 0)}, skipped={s.get('skipped', 0)}, "
                             f"collected={s.get('collected', 0)}")
            if not r.get("passed"):
                for fl in _failure_lines(r):
                    lines.append(f"  - ❌ {fl}")

    if rep.get("execution_hints"):
        lines.append("\n## 执行证据（失败轨迹，证伪式重检索）\n")
        lines.append("| 帧位置 | 函数 | 权重 |")
        lines.append("|---|---|---|")
        for h in rep["execution_hints"]:
            lines.append(f"| `{h['ref']}` | {h['frame_func']} | {h['weight']} |")
        lines.append("")
        lines.append("说明：失败轨迹命中的符号获得最强证据权重；测试文件/模块级证据被证伪降权。\n")

    if rep.get("flip_prediction"):
        fp = rep["flip_prediction"]
        lines.append("\n## 测试翻转统计（补丁测试后计算）\n")
        lines.append("- 预测会翻转的测试：" + (", ".join(fp.get('predicted', [])) or "无"))
        lines.append("- 实际翻转的测试：" + (", ".join(fp.get('actual_flips', [])) or "无"))
        lines.append("- 命中：" + (", ".join(fp.get('hit', [])) or "无"))
        lines.append(f"- 预测准确率 precision={fp.get('precision')}，召回率 recall={fp.get('recall')}")
        lines.append("")

    if rep.get("patch_diff"):
        lines.append("\n## 补丁内容（diff）\n")
        lines.append("```diff")
        lines.append(rep["patch_diff"])
        lines.append("```")

    if rep.get("generated_test"):
        lines.append("\n## 自动生成的辅助回归测试（不作为独立验收）\n")
        lines.append("```python")
        lines.append(rep["generated_test"])
        lines.append("```")

    if rep.get("regression_before") or rep.get("regression_after"):
        lines.append("\n## 自动生成的辅助回归结果（含冒烟检查）\n")
        lines.append(f"测试命令：`{rep.get('regression_test_cmd') or 'pytest tests/test_replens_regression.py'}`")
        if rep.get("regression_before"):
            lines.append("\n### 修复前\n")
            for r in rep["regression_before"]:
                lines.append(f"- 命令 `{r['command']}` → exit {r['returncode']}"
                             f"（{'无效: ' + r.get('invalid_reason', '') if not r.get('valid') else ('通过' if r['passed'] else '未通过')}）")
                if r.get("timed_out"):
                    lines.append("  - ⏱ 超时终止")
                elif not r.get("passed"):
                    for fl in _failure_lines(r):
                        lines.append(f"  - ❌ {fl}")
        if rep.get("regression_after"):
            lines.append("\n### 修复后\n")
            for r in rep["regression_after"]:
                lines.append(f"- 命令 `{r['command']}` → exit {r['returncode']}"
                             f"（{'无效: ' + r.get('invalid_reason', '') if not r.get('valid') else ('通过' if r['passed'] else '未通过')}）")
                if not r.get("passed"):
                    for fl in _failure_lines(r):
                        lines.append(f"  - ❌ {fl}")
        lines.append("")

    if rep.get('acceptance_test_cmd'):
        lines.append('\n## 冻结的独立行为验收\n')
        lines.append('命令：`' + rep['acceptance_test_cmd'] + '`\n')
        lines.append('SHA-256：`' + rep.get('validation', {}).get('acceptance_sha256', '') + '`\n')
        for phase, key in [('修复前', 'acceptance_before'), ('修复后', 'acceptance_after')]:
            lines.append('### ' + phase + '\n')
            for result in rep.get(key, []):
                label = '无效: ' + result.get('invalid_reason', '') if not result.get('valid') else ('通过' if result['passed'] else '未通过')
                lines.append('- ' + label + f"（exit {result['returncode']}）")
                for test_id, state in result.get('tests', {}).items():
                    lines.append(f'  - `{test_id}`: {state}')
            lines.append('')
    return "\n".join(lines)


def render_json(rep: dict) -> str:
    return json.dumps(rep, ensure_ascii=False, indent=2)


def render_verification_markdown(result: dict) -> str:
    """CLI and Web share the three independent verification conclusions."""
    lines = ['# RepoLens-OSS 证据包重放报告', '',
             '| 检查 | 结论 |', '|---|---|',
             '| 材料核验 | ' + result['materials_status'] + ' |',
             '| 重放一致性 | ' + result['replay_status'] + ' |',
             '| 修复结论 | ' + result['repair_status'] + ' |', '',
             '重放 PASS 表示结果与记录一致；只有修复结论 VERIFIED 表示通过独立验收。', '']
    for reason in result.get('reasons', []):
        lines.append('- ' + reason)
    checks = result.get('phase_checks', {})
    if checks:
        lines.extend(['', '| 测试套件 | 修复前一致 | 修复后一致 |', '|---|---|---|'])
        for name, phases in checks.items():
            lines.append('| ' + name + ' | ' + str(phases['before']) + ' | ' + str(phases['after']) + ' |')
    return '\n'.join(lines) + '\n'
