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
    ResourceEntry(**{'name': 'Python（解释器/标准库）', 'version_source': '3.10.11；python-installed-LICENSE.txt；官方docs.python.org/3.10/license.html', 'license': 'PSF及所附历史许可', 'source_path': 'pyproject.toml', 'usage': '运行环境；源码包不捆绑解释器', 'duty': '解释器或组件再分发按其完整LICENSE保留声明；本次仅要求用户安装', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'SQLite FTS5（组件）', 'version_source': '3.40.1；Python sqlite3；官方sqlite.org/copyright.html', 'license': 'Public Domain（SQLite官方声明）', 'source_path': 'replens/indexer.py', 'usage': '默认检索；不独立再分发SQLite二进制', 'duty': 'SQLite本体为公有领域；外层Python许可仍单独适用', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'fastapi（软件库/工具）', 'version_source': '0.141.1；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web声明依赖；用户安装，不在源码包捆绑', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'uvicorn（软件库/工具）', 'version_source': '0.54.0；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web声明依赖；用户安装，不在源码包捆绑', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pytest（软件库/工具）', 'version_source': '9.1.1；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': '测试声明依赖；用户安装，不捆绑', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'httpx（软件库/工具）', 'version_source': '0.27.2；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': '测试声明依赖；用户安装，不捆绑', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'httpcore（软件库/工具）', 'version_source': '1.0.9；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'anyio（软件库/工具）', 'version_source': '4.15.1；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web声明依赖；用户安装，不在源码包捆绑', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'starlette（软件库/工具）', 'version_source': '1.7.0；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pydantic（软件库/工具）', 'version_source': '2.13.5；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pydantic-core（软件库/工具）', 'version_source': '2.46.5；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'typing_extensions（软件库/工具）', 'version_source': '4.16.0；安装元数据及LICENSE，详见核验索引', 'license': 'PSF-2.0', 'source_path': 'pyproject.toml', 'usage': 'Web声明依赖；用户安装，不在源码包捆绑', 'duty': '保留完整许可与版权；按所选许可满足再分发条件', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'typing-inspection（软件库/工具）', 'version_source': '0.4.4；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'annotated-types（软件库/工具）', 'version_source': '0.8.0；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'annotated-doc（软件库/工具）', 'version_source': '0.0.5；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'sniffio（软件库/工具）', 'version_source': '1.3.1；安装元数据及LICENSE，详见核验索引', 'license': 'MIT OR Apache-2.0', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'idna（软件库/工具）', 'version_source': '3.20；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'certifi（软件库/工具）', 'version_source': '2026.7.22；安装元数据及LICENSE，详见核验索引', 'license': 'MPL-2.0', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑certifi/证书包；若分发或修改受MPL覆盖文件，保留声明并履行对应源码/许可义务', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'exceptiongroup（软件库/工具）', 'version_source': '1.3.1；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'click（软件库/工具）', 'version_source': '8.5.0；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'h11（软件库/工具）', 'version_source': '0.14.0；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'colorama（软件库/工具）', 'version_source': '0.4.6；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-3-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'packaging（软件库/工具）', 'version_source': '24.1；安装元数据及LICENSE，详见核验索引', 'license': 'Apache-2.0 OR BSD-2-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pluggy（软件库/工具）', 'version_source': '1.6.0；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'iniconfig（软件库/工具）', 'version_source': '2.3.0；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pygments（软件库/工具）', 'version_source': '2.21.0；安装元数据及LICENSE，详见核验索引', 'license': 'BSD-2-Clause', 'source_path': 'pyproject.toml', 'usage': 'Web/测试传递依赖；通过安装获取，未捆绑二进制', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'setuptools（软件库/工具）', 'version_source': '80.10.2；安装元数据及LICENSE，详见核验索引', 'license': 'MIT', 'source_path': 'pyproject.toml', 'usage': '构建工具；wheel已构建，本次不捆绑工具', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': 'Ollama（推理服务）', 'version_source': '0.34.4（本机命令）；官方仓库LICENSE当前main已存档', 'license': 'MIT', 'source_path': 'replens/llm.py', 'usage': '本地HTTP推理服务；不分发Ollama软件', 'duty': 'MIT软件副本/实质部分保留版权与许可；具体模型许可另行核对', 'own': '未修改第三方实现；团队完成集成、编排与验证', 'status': '已核验'}),
    ResourceEntry(**{'name': '本地Ollama推理模型（GGUF）', 'version_source': '历史qwen2.5-coder:1.5b；本轮qwen25-7b-unc:latest、qwen35-9b-unc:latest；摘要见T-20261030-09模型记录', 'license': '各本地GGUF的原始来源、转换链路及适用许可待核验', 'source_path': 'replens/llm.py', 'usage': '本地问题理解、测试设计、计划及补丁推理；不随源码或材料分发权重', 'duty': '按实际来源与适用许可核对声明和再分发义务；不把标签名称或官方同名许可等同于本机转换物已核验', 'own': '团队实现调用、检索、验证与界面；未声称自行训练这些模型权重', 'status': '待核验'}),
    ResourceEntry(**{'name': 'tqdm（上游评测源码/补丁来源）', 'version_source': '实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json', 'license': 'MPL-2.0 AND MIT', 'source_path': 'eval/datasets', 'usage': '本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库', 'duty': '按tqdm LICENCE的文件/贡献归属适用MPL和MIT；原样保留；分发受MPL源码须按对应条款，不统一改为MIT', 'own': '团队整理候选、测试适配、环境与验证；上游源码和参考修复不计团队原创/模型自主修复', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pypdf（上游评测源码/补丁来源）', 'version_source': '实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json', 'license': 'BSD-3-Clause', 'source_path': 'eval/datasets', 'usage': '本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '团队整理候选、测试适配、环境与验证；上游源码和参考修复不计团队原创/模型自主修复', 'status': '已核验'}),
    ResourceEntry(**{'name': 'loguru（上游评测源码/补丁来源）', 'version_source': '实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json', 'license': 'MIT', 'source_path': 'eval/datasets', 'usage': '本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '团队整理候选、测试适配、环境与验证；上游源码和参考修复不计团队原创/模型自主修复', 'status': '已核验'}),
    ResourceEntry(**{'name': 'click（上游评测源码/补丁来源）', 'version_source': '实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json', 'license': 'BSD-3-Clause', 'source_path': 'eval/datasets', 'usage': '本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '团队整理候选、测试适配、环境与验证；上游源码和参考修复不计团队原创/模型自主修复', 'status': '已核验'}),
    ResourceEntry(**{'name': 'requests（上游评测源码/补丁来源）', 'version_source': '实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json', 'license': 'Apache-2.0', 'source_path': 'eval/datasets', 'usage': '本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '团队整理候选、测试适配、环境与验证；上游源码和参考修复不计团队原创/模型自主修复', 'status': '已核验'}),
    ResourceEntry(**{'name': '历史Issue/PR文本与描述记录（数据）', 'version_source': 'eval/datasets、eval/curation；原始出处与日期记录', 'license': '不能从仓库软件许可自动推定所有讨论文本授权', 'source_path': 'eval/datasets', 'usage': '保留来源；未作为本轮训练输入；公开分发应先核查逐条授权/最小引用', 'duty': '公开可读不等于任意训练/改编/再分发；保留原链接，未明确授权的长段文本限评审用途', 'own': '团队完成筛选和标注，不主张原文著作权', 'status': '待核验'}),
    ResourceEntry(**{'name': 'fastembed（可选/历史后端）', 'version_source': '0.8.1；本机元数据及原始许可', 'license': 'Apache-2.0', 'source_path': 'replens/retriever_hybrid.py', 'usage': '仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '团队实现可选适配脚本，不把安装状态当运行效果', 'status': '已核验'}),
    ResourceEntry(**{'name': 'torch（可选/历史后端）', 'version_source': '2.14.0；本机元数据及原始许可', 'license': 'Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT', 'source_path': 'scripts/finetune.py', 'usage': '仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包', 'duty': '按torch原始许可及内含第三方通知逐项保留；本次不分发其二进制', 'own': '团队实现可选适配脚本，不把安装状态当运行效果', 'status': '已核验'}),
    ResourceEntry(**{'name': 'transformers（可选/历史后端）', 'version_source': '5.17.0；本机元数据及原始许可', 'license': 'Apache-2.0', 'source_path': 'scripts/finetune.py', 'usage': '仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '团队实现可选适配脚本，不把安装状态当运行效果', 'status': '已核验'}),
    ResourceEntry(**{'name': 'peft（可选/历史后端）', 'version_source': '0.21.0；本机元数据及原始许可', 'license': 'Apache-2.0', 'source_path': 'scripts/finetune.py', 'usage': '仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '团队实现可选适配脚本，不把安装状态当运行效果', 'status': '已核验'}),
    ResourceEntry(**{'name': 'BAAI/bge-small-en-v1.5（可选模型）', 'version_source': '官方模型卡license:mit已存档；本轮未运行、未确定本地权重摘要', 'license': 'MIT（官方模型卡）', 'source_path': 'replens/retriever_hybrid.py', 'usage': '本轮实验不启用；不分发权重', 'duty': '未来启用时固定版本与来源，权重分发保留适用许可声明', 'own': '仅预留向量适配，不列为本轮实测收益', 'status': '不适用'}),
    ResourceEntry(**{'name': 'sentence-transformers（可选后端）', 'version_source': '当前解释器未安装', 'license': '本轮不对未使用版本作许可核验', 'source_path': 'replens/retriever_hybrid.py', 'usage': '本轮未使用，不打包该依赖', 'duty': '未来启用前固定版本及核验许可', 'own': '仅保留导入适配代码', 'status': '不适用'}),
    ResourceEntry(**{'name': 'Qwen2.5-Coder-0.5B-Instruct（可选训练基座）', 'version_source': '官方LICENSE已存档；本轮未加载或训练', 'license': 'Apache-2.0（官方）', 'source_path': 'scripts/finetune.py', 'usage': '训练脚本默认配置；本次不分发权重/适配器，不报微调收益', 'duty': '再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权', 'own': '本轮不主张已有自训权重或微调效果', 'status': '不适用'}),
    ResourceEntry(**{'name': 'python-docx（材料处理工具）', 'version_source': '1.2.0；安装许可原文已归档', 'license': 'MIT', 'source_path': 'docs/02_架构/技术报告.docx', 'usage': '仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具', 'duty': '本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明', 'own': '团队负责材料内容；工具执行排版/提取/渲染', 'status': '已核验'}),
    ResourceEntry(**{'name': 'lxml（材料处理工具）', 'version_source': '6.1.3；安装许可原文已归档', 'license': 'BSD-3-Clause及LICENSES所列组件', 'source_path': 'docs/02_架构/技术报告.docx', 'usage': '仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '团队负责材料内容；工具执行排版/提取/渲染', 'status': '已核验'}),
    ResourceEntry(**{'name': 'pypdf（材料处理工具）', 'version_source': '6.19.0；安装许可原文已归档', 'license': 'BSD-3-Clause', 'source_path': 'docs/02_架构/技术报告.docx', 'usage': '仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具', 'duty': '不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书', 'own': '团队负责材料内容；工具执行排版/提取/渲染', 'status': '已核验'}),
    ResourceEntry(**{'name': 'PyMuPDF（材料处理工具）', 'version_source': '1.28.2；安装许可原文已归档', 'license': 'AGPL-3.0或Artifex商业许可（二选一）', 'source_path': 'docs/02_架构/技术报告.docx', 'usage': '仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具', 'duty': '仅本地读取/渲染PDF，不捆绑库或提供其网络服务；若将库/衍生应用分发或服务化，另核对AGPL/商业授权义务，不套用项目MIT', 'own': '团队负责材料内容；工具执行排版/提取/渲染', 'status': '已核验'}),
    ResourceEntry(**{'name': 'Microsoft Word（文档工具）', 'version_source': '本机COM版本14.0；用户提供模板由Word读取', 'license': '专有软件许可/EULA', 'source_path': 'docs/02_架构/技术报告.docx', 'usage': '仅本机打开、导出PDF；不再分发Word', 'duty': '由使用者确认设备/账号有效授权；不分发软件、字体文件或模板原件', 'own': '作品报告内容与排版为团队交付；Word不作为开源资源', 'status': '待核验'}),
    ResourceEntry(**{'name': 'Codex/OpenAI（生成式开发辅助服务）', 'version_source': '本次开发会话；具体账号/订阅适用合同由使用者确认', 'license': '服务条款；不是开源代码许可', 'source_path': 'project-os', 'usage': '辅助代码分析、修改建议/实现、调试验证和材料整理；不是项目运行依赖', 'duty': '准确披露AI辅助环节；团队复核输出正确性、来源与权利；不把AI生成部分一概称为纯人工', 'own': '团队负责需求/设计取舍、代码审核、运行验收与提交；不估算贡献比例', 'status': '待核验'}),
    ResourceEntry(**{'name': 'Git（版本控制工具）', 'version_source': 'git version 2.54.0.windows.1', 'license': 'GPL-2.0（Git软件；当前仅工具使用）', 'source_path': 'CONTRIBUTING.md', 'usage': '本地版本管理与读取上游提交，不捆绑Git', 'duty': '若再分发Git软件另遵其许可；不把工具许可等同于作品许可', 'own': '团队完成版本选择、变更与记录', 'status': '已核验'}),
]

def render_official_checklist(resources: List[ResourceEntry]) -> str:
    """按附件1官方表头生成《开源及第三方资源使用清单》。"""
    lines = ["# 开源及第三方资源使用清单", "",
             "核验日期：2026-10-30。按附件2参考表的8个字段生成。已核验表示本次使用边界、许可文本或元数据有证据，不是对全部外部权利作保证。", "",
             "| 序号 | 资源名称及类型 | 版本/来源 | 许可证/授权类型 | 使用/开放方式 | 关键义务/限制 | 团队自主修改或开发内容 | 合规状态 |",
             "|---|---|---|---|---|---|---|---|"]
    for i, resource in enumerate(resources, 1):
        values = [str(i), resource.name, resource.version_source, resource.license,
                  resource.usage, resource.duty, resource.own, resource.status]
        lines.append("| " + " | ".join(v.replace("|", "/") for v in values) + " |")
    lines += ["", "核验依据及文件路径：verification-20261030/resource-register.json、installed-packages.json、historical-license-index.json和原始许可证快照。",
              "本清单由replens/compliance.py登记表生成，不会把安装、公开可读或代码文件存在自动当成授权已完成。",
              "待核验项保留具体限制；模型权重和完整上游仓库不随项目源码分发。", ""]
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
