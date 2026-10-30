"""仓库导入与元信息识别。

职责：读取一个本地开源仓库，识别许可证、依赖清单、测试框架与运行方式，
为后续索引与安全执行提供必要上下文。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

# 常见许可证的关键词特征（按宽松程度排序，命中即判）
LICENSE_PATTERNS = [
    ("MIT", ["MIT License", "Permission is hereby granted, free of charge"]),
    ("Apache-2.0", ["Apache License", "Version 2.0, January 2004"]),
    ("BSD-3-Clause", ["Redistribution and use in source and binary forms"]),
    ("BSD-2-Clause", ["Redistribution and use in source and binary forms"]),
    ("GPL-3.0", ["GNU GENERAL PUBLIC LICENSE", "Version 3"]),
    ("GPL-2.0", ["GNU GENERAL PUBLIC LICENSE", "Version 2"]),
    ("LGPL-3.0", ["GNU LESSER GENERAL PUBLIC LICENSE"]),
    ("MPL-2.0", ["Mozilla Public License", "Version 2.0"]),
    ("Unlicense", ["This is free and unencumbered software released into the public domain"]),
]

LICENSE_FILE_NAMES = [
    "LICENSE", "LICENSE.txt", "LICENSE.md", "LICENSE.rst",
    "COPYING", "COPYING.txt", "COPYING.md",
    "LICENCE", "LICENCE.txt",
]

# 依赖清单文件
DEP_FILES = ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile"]

# 统一排除目录：仓库导入、Python/文档收集、索引与执行副本共用同一策略，
# 防止工具自身运行产物（.replens 证据包/候选补丁）进入下一轮索引。
EXCLUDE_DIRS = {".git", ".replens", ".venv", "venv", "__pycache__", ".tox",
                "build", "dist", "node_modules", ".idea", ".vscode",
                ".mypy_cache", ".pytest_cache", "htmlcov", "site-packages"}


@dataclass
class RepoMeta:
    """仓库元信息。"""
    path: str
    name: str = ""
    license: str = "unknown"            # 识别出的许可证类型
    license_file: Optional[str] = None  # LICENSE 文件相对路径
    license_confident: bool = False     # 是否高置信度识别
    dependency_files: List[str] = field(default_factory=list)
    test_framework: str = "unknown"     # pytest / unittest / nose / unknown
    test_dirs: List[str] = field(default_factory=list)
    python_files: List[str] = field(default_factory=list)
    document_files: List[str] = field(default_factory=list)
    readme: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_license(text: str) -> tuple[str, bool]:
    """根据文本内容识别许可证类型。返回 (类型, 是否高置信)。"""
    upper = text.upper()
    if "GNU GENERAL PUBLIC LICENSE" in upper:
        if "VERSION 3" in upper:
            return "GPL-3.0", True
        if "VERSION 2" in upper:
            return "GPL-2.0", True
    if "GNU LESSER GENERAL PUBLIC LICENSE" in upper:
        if "VERSION 3" in upper:
            return "LGPL-3.0", True
    if "REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS" in upper:
        if "NEITHER THE NAME" in upper or "NOR THE NAMES" in upper:
            return "BSD-3-Clause", True
        return "BSD-2-Clause", True
    if "MIT LICENSE" in upper or "PERMISSION IS HEREBY GRANTED, FREE OF CHARGE" in upper:
        return "MIT", True
    if "APACHE LICENSE" in upper and "VERSION 2.0" in upper:
        return "Apache-2.0", True
    if "MOZILLA PUBLIC LICENSE" in upper and "VERSION 2.0" in upper:
        return "MPL-2.0", True
    if "THIS IS FREE AND UNENCUMBERED SOFTWARE" in upper:
        return "Unlicense", True
    return "unknown", False


def _find_license(root: Path) -> tuple[Optional[str], str, bool]:
    """在仓库根目录查找 LICENSE 文件并识别类型。"""
    for fname in LICENSE_FILE_NAMES:
        cand = root / fname
        if cand.is_file():
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""
            lic, confident = _detect_license(text)
            return fname, lic, confident
    return None, "unknown", False


def _detect_test_framework(root: Path) -> tuple[str, List[str]]:
    """识别测试框架与测试目录。"""
    framework = "unknown"
    test_dirs: List[str] = []

    for dep in DEP_FILES:
        p = root / dep
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if dep == "pyproject.toml" and ("pytest" in content or "pytest" in p.name):
            if re.search(r"\[tool\.pytest", content) or "pytest" in content:
                framework = "pytest"
        elif "pytest" in content:
            framework = "pytest"
        elif "nose" in content:
            framework = "nose"

    # 扫描测试目录（约定优先）
    for d in ["tests", "test"]:
        if (root / d).is_dir():
            test_dirs.append(d)
    # 若没有独立测试目录，找以 test_ 开头的文件所在目录
    if not test_dirs:
        dirs = set()
        for py in root.rglob("test_*.py"):
            if set(py.relative_to(root).parts) & EXCLUDE_DIRS:
                continue
            dirs.add(str(py.parent.relative_to(root)) or ".")
        test_dirs = sorted(dirs)

    # unittest 兜底：源码里 import unittest 且未识别出 pytest
    if framework == "unknown":
        for py in root.rglob("*.py"):
            if set(py.relative_to(root).parts) & EXCLUDE_DIRS:
                continue
            try:
                if "import unittest" in py.read_text(encoding="utf-8", errors="ignore"):
                    framework = "unittest"
                    break
            except Exception:
                continue

    return framework, test_dirs


def _collect_dependencies(root: Path) -> List[str]:
    return [d for d in DEP_FILES if (root / d).is_file()]


def _collect_python_files(root: Path) -> List[str]:
    """收集仓库内所有 Python 源文件（排除运行产物、虚拟环境、缓存目录）。"""
    exclude_dirs = EXCLUDE_DIRS | {"reports", "project-os", "__replens_task__", "__replens_acceptance__"}
    files: List[str] = []
    for py in root.rglob("*.py"):
        parts = set(py.relative_to(root).parts)
        if parts & exclude_dirs:
            continue
        # 统一用 posix 正斜杠，保证跨平台（评测集 expected 用正斜杠）
        files.append(py.relative_to(root).as_posix())
    return sorted(files)


def _collect_document_files(root: Path) -> List[str]:
    """收集可用于 Issue 定位的仓库文档。"""
    extensions = {".md", ".rst", ".txt"}
    exclude_dirs = EXCLUDE_DIRS | {"reports", "project-os", "__replens_task__", "__replens_acceptance__"}
    files: List[str] = []
    for doc in root.rglob("*"):
        if not doc.is_file() or doc.suffix.lower() not in extensions:
            continue
        if set(doc.relative_to(root).parts) & exclude_dirs:
            continue
        files.append(doc.relative_to(root).as_posix())
    return sorted(files)


def import_repo(path: str | os.PathLike) -> RepoMeta:
    """导入本地仓库，返回 RepoMeta。"""
    root = Path(path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"不是有效目录: {root}")

    lic_file, lic, confident = _find_license(root)
    test_fw, test_dirs = _detect_test_framework(root)
    deps = _collect_dependencies(root)
    py_files = _collect_python_files(root)
    readme = next((str(n) for n in ["README.md", "README.rst", "README.txt", "README"]
                   if (root / n).is_file()), None)

    return RepoMeta(
        path=str(root),
        name=root.name,
        license=lic,
        license_file=lic_file,
        license_confident=confident,
        dependency_files=deps,
        test_framework=test_fw,
        test_dirs=test_dirs,
        python_files=py_files,
        document_files=_collect_document_files(root),
        readme=readme,
    )
