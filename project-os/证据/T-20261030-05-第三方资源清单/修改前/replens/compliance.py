"""第三方依赖合规披露（开源许可证 / 授权合规）。

竞赛形式审查明确要求「开源及第三方资源披露、知识产权与许可合规」。
本模块维护一份与《开源及第三方资源使用清单》完全一致的资源登记表
（RESOURCES），离线生成两份逐项对应的报告：
- reports/compliance/third_party_disclosure.md（第三方依赖与开源许可披露报告）
- reports/compliance/open_source_checklist.md（官方表头使用清单）

零第三方依赖：仅用标准库 re / json / dataclasses。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional

from .repo import RepoMeta, import_repo

# 常见 PyPI 包 -> SPDX 许可证（内置知识库，供离线披露）
KNOWN_LICENSES: Dict[str, str] = {
    "pytest": "MIT",
    "fastapi": "MIT",
    "uvicorn": "BSD-3-Clause",
    "pydantic": "MIT",
    "starlette": "BSD-3-Clause",
    "requests": "Apache-2.0",
    "numpy": "BSD-3-Clause",
    "pandas": "BSD-3-Clause",
    "scikit-learn": "BSD-3-Clause",
    "torch": "BSD-3-Clause",
    "transformers": "Apache-2.0",
    "sentence-transformers": "Apache-2.0",
    "fastembed": "Apache-2.0",
    "onnxruntime": "MIT",
    "jieba": "MIT",
    "tqdm": "MPL-2.0",
    "pypdf": "BSD-3-Clause",
    "rich": "MIT",
    "click": "BSD-3-Clause",
    "typer": "MIT",
    "flask": "BSD-3-Clause",
    "django": "BSD-3-Clause",
    "sqlalchemy": "MIT",
    "pyyaml": "MIT",
    "tomli": "MIT",
}


@dataclass
class ThirdPartyDep:
    name: str
    spec: str = ""
    license: str = "unknown"
    source: str = ""
    usage: str = "运行时依赖"

    def to_dict(self) -> dict:
        return {"name": self.name, "spec": self.spec, "license": self.license,
                "source": self.source, "usage": self.usage}


def _parse_requirements(path: Path) -> List[ThirdPartyDep]:
    deps: List[ThirdPartyDep] = []
    if not path.is_file():
        return deps
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        if s.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[\s]", s, maxsplit=1)[0].strip()
        if name:
            deps.append(ThirdPartyDep(
                name=name.lower(), spec=s, source=path.name,
                license=KNOWN_LICENSES.get(name.lower(), "unknown")))
    return deps


def _parse_pyproject(path: Path) -> List[ThirdPartyDep]:
    deps: List[ThirdPartyDep] = []
    if not path.is_file():
        return deps
    section = ""
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        m = re.match(r"^\[(.+)\]$", s)
        if m:
            section = m.group(1)
            continue
        m2 = re.match(r"^([A-Za-z0-9_\-]+)\s*=\s*\[(.*)\]$", s)
        if not m2:
            continue
        key, body = m2.group(1), m2.group(2)
        if key == "dependencies" and section == "project":
            usage = "运行时依赖"
        elif section == "project.optional-dependencies":
            usage = "可选依赖(" + key + ")"
        else:
            continue
        quoted = re.findall(r'"([^"]+)"', body) + re.findall(r"'([^']+)'", body)
        for spec in quoted:
            name = re.split(r"[<>=!~\[\s]", spec, maxsplit=1)[0].strip()
            if name:
                deps.append(ThirdPartyDep(
                    name=name.lower(), spec=spec, source=path.name,
                    license=KNOWN_LICENSES.get(name.lower(), "unknown"), usage=usage))
    return deps


def collect_deps(meta: RepoMeta) -> List[ThirdPartyDep]:
    root = Path(meta.path)
    deps: List[ThirdPartyDep] = []
    seen = set()
    for dep_file in meta.dependency_files:
        p = root / dep_file
        if dep_file == "pyproject.toml":
            parsed = _parse_pyproject(p)
        elif dep_file == "requirements.txt":
            parsed = _parse_requirements(p)
        else:
            continue
        for d in parsed:
            key = (d.name, d.source)
            if key in seen:
                continue
            seen.add(key)
            deps.append(d)
    return deps


def render_disclosure_markdown(meta: RepoMeta, deps: List[ThirdPartyDep]) -> str:
    """基于依赖解析结果渲染披露报告（供单元测试与简单场景使用）。"""
    lines = ["# 第三方依赖与开源许可披露报告", "",
             "仓库: " + meta.name, "",
             "本报告由 RepoLens-OSS 离线生成，用于竞赛作品形式审查中的「开源及第三方资源披露、"
             "知识产权与许可合规」要求。",
             "",
             "## 依赖清单", "",
             "| 依赖 | 版本约束 | 许可证 | 来源文件 | 使用方式 | 合规风险 |",
             "|---|---|---|---|---|---|"]
    for d in deps:
        risk = "需人工确认" if d.license == "unknown" else "许可宽松，风险低"
        lines.append("| " + d.name + " | " + d.spec + " | " + d.license
                     + " | " + d.source + " | " + d.usage + " | " + risk + " |")
    if not deps:
        lines.append("| 无第三方运行时依赖（核心包纯标准库） | - | - | - | - | - |")
    lines += ["", "## 说明", "",
              "1. 许可证信息来自内置知识库；标记 unknown 的依赖请在提交前人工核对并补充。",
              "2. 核心包 replens/ 零第三方依赖（纯 Python 标准库），Web 层与向量检索层为可选依赖。",
              "3. 使用方式分为运行时依赖与可选依赖，可选依赖仅影响对应功能开关。",
              ""]
    return "\n".join(lines)


@dataclass
class ResourceEntry:
    """一条第三方资源登记项：两份报告（披露报告/使用清单）共用。"""
    name: str
    version_source: str
    license: str
    source_path: str
    usage: str
    duty: str
    own: str
    status: str = "待核验"


# 资源登记表（唯一事实来源）：覆盖 pyproject 可选依赖、评测资源、本地模型与
# 上游源码工作区。reports/compliance/third_party_disclosure.md 与
# reports/compliance/open_source_checklist.md 均由此表渲染，条目数完全一致。
RESOURCES: List[ResourceEntry] = [
    ResourceEntry(
        "fastapi（软件库）", "fastapi>=0.110 / pyproject.toml", "MIT",
        "pyproject.toml（[project.optional-dependencies] web）",
        "可选 Web 层（web/app.py 的 /api/analyze、/api/run）",
        "保留版权与许可声明", "无修改，仅调用"),
    ResourceEntry(
        "uvicorn（软件库）", "uvicorn>=0.29 / pyproject.toml", "BSD-3-Clause",
        "pyproject.toml（[project.optional-dependencies] web）",
        "可选 Web 层运行服务器",
        "保留版权与许可声明", "无修改，仅调用"),
    ResourceEntry(
        "fastembed（模型推理框架）", "pip 包 / Apache-2.0", "Apache-2.0",
        "replens/retriever_hybrid.py",
        "混合检索本地向量推理",
        "保留 LICENSE/NOTICE", "无修改，仅调用"),
    ResourceEntry(
        "BAAI/bge-small-en-v1.5（向量模型）", "Hugging Face / MIT", "MIT",
        "replens/retriever_hybrid.py（默认模型名）",
        "Issue 语义编码",
        "保留模型许可声明", "无修改，仅推理"),
    ResourceEntry(
        "pytest（测试框架）", "PyPI / MIT", "MIT",
        "tests/、.github/workflows/ci.yml",
        "单元测试与评测执行",
        "保留版权声明", "无修改"),
    ResourceEntry(
        "SQLite FTS5（检索引擎）", "Python 标准库 / Public Domain", "Public Domain",
        "replens/indexer.py",
        "符号全文索引",
        "无", "无修改", status="本次实际使用"),
    ResourceEntry(
        "Ollama（本地推理服务）", "0.34.4 / MIT", "MIT",
        "replens/llm.py",
        "本地 LLM 推理（可选）",
        "保留版权与许可声明", "无修改，仅调用"),
    ResourceEntry(
        "Qwen2.5-Coder-1.5B-Instruct（LLM 模型）", "Ollama/ModelScope / Apache-2.0", "Apache-2.0",
        "replens/llm.py（默认模型名）",
        "计划与补丁生成（可选）",
        "保留 LICENSE/NOTICE", "无修改，仅推理"),
    ResourceEntry(
        "sentence-transformers（embedding 后端）", "pip 包 / Apache-2.0", "Apache-2.0",
        "replens/retriever_hybrid.py（备选后端）",
        "混合检索本地向量推理（备选）",
        "保留 LICENSE/NOTICE", "无修改，仅调用"),
    ResourceEntry(
        "tqdm（上游开源项目源码）", "repos/tqdm_buggy、repos/tqdm / MPL-2.0", "MPL-2.0",
        "repos/tqdm_buggy/、repos/tqdm/、eval/datasets/real_dataset_tqdm.json",
        "真实仓库评测与 PR-ready 补丁（total=inf，上游 #651）",
        "保留版权与许可声明；修改需按 MPL-2.0 说明（本项目仅生成补丁，未修改上游分发）",
        "评测工作区；补丁为本项目产出，参照上游提交 ef15578"),
    ResourceEntry(
        "pypdf（上游开源项目源码）", "repos/pypdf_buggy、repos/pypdf / BSD-3-Clause", "BSD-3-Clause",
        "repos/pypdf_buggy/、repos/pypdf/、eval/datasets/real_dataset_pypdf.json",
        "真实仓库评测与 PR-ready 补丁（outline 折叠，上游 #2994）",
        "保留版权与许可声明",
        "评测工作区；补丁为本项目产出，参照上游提交 db93ea65"),
    ResourceEntry(
        "Qwen2.5-Coder-0.5B-Instruct（LLM 模型，LoRA 基座）", "Hugging Face Qwen/Qwen2.5-Coder-0.5B-Instruct / Apache-2.0", "Apache-2.0",
        "scripts/finetune.py（MODEL_NAME）、models/replens-qwen2.5-coder-0.5b-instruct-lora/",
        "LoRA 微调基座（0.5B smoke 训练与评测）",
        "保留 LICENSE/NOTICE",
        "无修改，仅训练/推理；LoRA 适配器权重为本项目产出"),
]


def render_official_checklist(resources: List[ResourceEntry]) -> str:
    """按附件1官方表头生成《开源及第三方资源使用清单》。"""
    lines = ["# 开源及第三方资源使用清单", "",
             "（按《AIC·AI+开源竞赛规则及作品提交要求》附件表头填写）", "",
             "| 序号 | 资源名称及类型 | 版本/来源 | 许可证/授权类型 | 源码路径 | 使用场景 | "
             "关键义务/限制 | 团队自主修改或开发内容 | 合规状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(resources, 1):
        lines.append("| " + str(i) + " | " + r.name + " | " + r.version_source
                     + " | " + r.license + " | " + r.source_path + " | " + r.usage
                     + " | " + r.duty + " | " + r.own + " | " + r.status + " |")
    lines += ["", "填写说明：本项目核心包 replens/ 零第三方运行时依赖（纯 Python 标准库）；",
              "上表列出可选功能、评测与微调所使用资源，两份报告（本清单与 "
              "reports/compliance/third_party_disclosure.md）均由同一资源登记表生成、逐项对应。", ""]
    return "\n".join(lines)


def render_full_disclosure(resources: List[ResourceEntry],
                           unregistered: Optional[List[ThirdPartyDep]] = None) -> str:
    """渲染与使用清单逐项对应的披露报告。

    unregistered：自动扫描 pyproject/requirements 后发现的、未在 RESOURCES 中登记的依赖。
    若非空，在报告末尾追加警告段，提示形式审查前补充登记或移除。
    """
    lines = ["# 第三方依赖与开源许可披露报告", "",
             "仓库: RepoLens-OSS", "",
             "本报告由 RepoLens-OSS 离线生成，用于竞赛作品形式审查中的「开源及第三方资源披露、"
             "知识产权与许可合规」要求。",
             "",
             "## 依赖清单", "",
             "| 序号 | 资源名称及类型 | 版本/来源 | 许可证 | 源码路径 | 使用场景 | 关键义务/风险 | 合规状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(resources, 1):
        lines.append("| " + str(i) + " | " + r.name + " | " + r.version_source
                     + " | " + r.license + " | " + r.source_path + " | " + r.usage
                     + " | " + r.duty + " | " + r.status + " |")
    if unregistered:
        lines += ["", "## ⚠️ 自动扫描发现未登记依赖", "",
                  "以下依赖在 pyproject.toml / requirements.txt 中声明，但未在上方 RESOURCES 中登记。"
                  "形式审查前请二选一：(a) 在 `replens/compliance.py` 的 RESOURCES 中补充登记条目；"
                  "(b) 从 pyproject/requirements 中移除（若实际并不需要）。",
                  "",
                  "| 依赖 | 版本约束 | 来源文件 | 使用方式 | 风险 |",
                  "|---|---|---|---|---|"]
        for d in unregistered:
            lines.append("| " + d.name + " | " + d.spec + " | " + d.source
                         + " | " + d.usage + " | 未登记，形式审查前需补充 |")
        lines.append("")
    lines += ["", "## 说明", "",
              "1. 本报告与 reports/compliance/open_source_checklist.md 由同一资源登记表生成，条目数完全一致。",
              "2. 核心包 replens/ 零第三方依赖（纯 Python 标准库），Web 层、向量检索层与 LLM 层为可选依赖。",
              "3. 许可证状态来自登记表或内置知识库；提交前仍需逐项核验来源、版本和授权条件。", ""]
    return "\n".join(lines)


def _project_resources(repo_path: str) -> List[ResourceEntry]:
    """只把当前仓库实际涉及的登记项纳入报告，避免把缺失工作区写成已验证。"""
    root = Path(repo_path).resolve()
    if not (root / "replens").is_dir():
        return []
    out = []
    for resource in RESOURCES:
        found = False
        for raw in re.split(r"[、,，；;]+", resource.source_path):
            candidate = raw.strip().split("（", 1)[0].strip()
            if candidate and (root / candidate).exists():
                found = True
                break
        out.append(replace(resource, status=resource.status if found else "未找到，需提交前核验"))
    return out


def _find_unregistered_deps(resources: List[ResourceEntry], meta: RepoMeta) -> List[ThirdPartyDep]:
    """健康检查：扫描 pyproject/requirements，找出 RESOURCES 未登记的依赖。

    当 `replens/` 存在时，build_disclosure 走 RESOURCES 渲染路径，
    `collect_deps` 的结果被忽略——本函数把它"复活"成健康检查，
    防止 pyproject 新增依赖而 RESOURCES 漏更新这种自打脸场景。
    """
    registered = set()
    for r in resources:
        # name 形如 "fastapi（软件库）" / "Qwen2.5-Coder-1.5B-Instruct（LLM 模型）"，
        # 取左括号前的主名小写作为匹配键。
        primary = r.name.split("（", 1)[0].strip().lower()
        if primary:
            registered.add(primary)
    scanned = collect_deps(meta)
    return [d for d in scanned if d.name.lower() not in registered]


def build_disclosure(repo_path: str) -> dict:
    meta = import_repo(repo_path)
    resources = _project_resources(repo_path)
    unregistered: List[ThirdPartyDep] = []
    if resources:
        unregistered = _find_unregistered_deps(resources, meta)
        md = render_full_disclosure(resources, unregistered=unregistered)
        checklist = render_official_checklist(resources)
    else:
        deps = collect_deps(meta)
        md = render_disclosure_markdown(meta, deps)
        checklist = "# 开源及第三方资源使用清单\n\n" + md
    out_dir = Path(repo_path) / "reports" / "compliance"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "third_party_disclosure.md"
    out.write_text(md, encoding="utf-8")
    out2 = out_dir / "open_source_checklist.md"
    out2.write_text(checklist, encoding="utf-8")
    return {"meta": meta.to_dict(),
            "resources": [r.__dict__ for r in resources],
            "unregistered": [d.to_dict() for d in unregistered],
            "report": str(out), "official_checklist": str(out2)}
