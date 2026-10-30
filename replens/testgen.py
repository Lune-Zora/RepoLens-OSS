"""回归测试生成。

基于检索到的函数/方法证据，生成可被 pytest/unittest 收集并运行的回归测试。

两层输出（诚实区分，避免「生成的测试无法验证修复」的形式主义）：
1. 行为回归测试（behavioral）：针对可识别的高置信模式（如 whitespace/strip 类
   Issue），从证据 docstring 中提取示例输入，生成「输入不变性」断言——
   `f(" X ") == f("X")`。该断言在修复前会 fail（异常/结果不一致），修复后 pass，
   真正能验证修复有效。
2. 冒烟测试（smoke）：函数存在 + 签名一致。仅防误删/防破坏接口，明确标注
   「不验证行为」，不再伪造「边界输入不崩溃」这类吞异常、永远通过的断言。
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import List, Optional

from .retriever import Evidence, _is_test_path, tokenize

# 与 patchgen 规则引擎一致：这些关键词对应「首尾空白不应改变结果」的语义
STRIP_HINTS = {"strip", "whitespace", "trim", "space", "spaces", "blank", "pad", "padded"}

# 从 docstring 提取示例输入：形如 e.g. "2024-01-01" / like "..." / '...'
_EXAMPLE_RE = re.compile(r"""["']([^"'\n]{2,40})["']""")


def _package_import_info(repo_root: str, rel_path: str):
    """返回 (import_root, module_name)，找不到包时返回 None。"""
    path = Path(repo_root).resolve() / rel_path
    parent = path.parent
    package_parts = []
    while (parent / "__init__.py").is_file():
        package_parts.insert(0, parent.name)
        parent = parent.parent
    if not package_parts:
        return None
    return parent, ".".join(package_parts + [path.stem])


def _find_method_owner(repo_root: str, rel_path: str, func_name: str) -> Optional[str]:
    """找到方法所属的类，避免把 method 当成模块函数调用。"""
    try:
        tree = ast.parse((Path(repo_root) / rel_path).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == func_name:
                    return node.name
    return None


def _module_loader_code(repo_root: str, rel_path: str, owner: Optional[str] = None) -> str:
    """生成动态加载目标文件的代码片段。

    使用占位符 __TMP_REPO__，由执行器在写入临时副本时替换为实际路径，
    确保回归测试加载的是「临时副本」里的目标代码（而非原仓库）。
    """
    info = _package_import_info(repo_root, rel_path)
    if info:
        import_root, module_name = info
        root_rel = import_root.relative_to(Path(repo_root).resolve()).as_posix()
        return (
            "import importlib, pathlib, sys\n"
            f"sys.path.insert(0, str(pathlib.Path('__TMP_REPO__') / {root_rel!r}))\n"
            f"_mod = importlib.import_module({module_name!r})\n"
            + (f"_obj = _mod.{owner}()\n" if owner else "")
        )
    abs_path = os.path.join("__TMP_REPO__", rel_path).replace("\\", "/")
    return (
        "import importlib.util, sys, pathlib\n"
        f"_path = pathlib.Path({abs_path!r})\n"
        "_spec = importlib.util.spec_from_file_location('_target_module', _path)\n"
        "_mod = importlib.util.module_from_spec(_spec)\n"
        "sys.modules['_target_module'] = _mod\n"
        "_spec.loader.exec_module(_mod)\n"
        + (f"_obj = _mod.{owner}()\n" if owner else "")
    )


def _parse_required_params(symbol) -> Optional[int]:
    """从 symbol.code 解析函数的必填参数个数（无默认值的位置参数）。"""
    try:
        tree = ast.parse(symbol.code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and node.name == symbol.name:
                return len(node.args.args) - len(node.args.defaults)
    except Exception:
        pass
    return None


def _issue_mentions_whitespace(issue: str) -> bool:
    return bool(set(tokenize(issue)) & STRIP_HINTS)


def _extract_example_input(doc: str) -> Optional[str]:
    """从 docstring 中提取一个示例输入字符串（如 "2024-01-01"）。

    只取「看起来像数据值」的字符串，跳过纯说明性英文短语。
    """
    if not doc:
        return None
    for m in _EXAMPLE_RE.finditer(doc):
        val = m.group(1)
        # 跳过明显的说明词（纯英文字母/空格短语，如 "Args"、"Returns" 里的内容）
        if re.fullmatch(r"[A-Za-z][A-Za-z ._]{2,}", val):
            continue
        return val
    return None


def _whitespace_example(target, issue: str) -> Optional[str]:
    """若 issue 为 whitespace/strip 类且 docstring 有示例输入，返回该示例输入。"""
    if not _issue_mentions_whitespace(issue):
        return None
    return _extract_example_input(target.doc)


def _behavioral_pytest(target_expr: str, func_name: str, example: str) -> List[str]:
    return [
        f"def test_regression_{func_name}_whitespace_invariance():",
        "    # 行为回归：首尾空白不应改变函数结果（对应 issue 的 whitespace/strip 语义）",
        f"    assert {target_expr}({(' ' + example + ' ')!r}) == {target_expr}({example!r})",
        "",
    ]


def _behavioral_unittest(target_expr: str, func_name: str, example: str) -> List[str]:
    return [
        f"    def test_regression_{func_name}_whitespace_invariance(self):",
        "        # 行为回归：首尾空白不应改变函数结果",
        f"        self.assertEqual({target_expr}({(' ' + example + ' ')!r}),",
        f"                         {target_expr}({example!r}))",
        "",
    ]


def _smoke_pytest(loader: str, target_expr: str, func_name: str, required: Optional[int]) -> List[str]:
    lines = [
        "# 冒烟测试（smoke）：仅验证接口存在与签名一致，不验证业务行为。",
        "# 行为级验证由仓库自有测试 + 上方行为回归测试承担。",
        loader,
        "",
        f"def test_regression_{func_name}_exists_and_callable():",
        f"    assert callable({target_expr}), "
        f"\"函数 {func_name} 不存在或不可调用\"",
        "",
    ]
    if required is not None:
        lines += [
            f"def test_regression_{func_name}_signature():",
            "    # 校验必填参数个数与源码定义一致",
            "    import inspect",
            "    try:",
            f"        params = inspect.signature({target_expr}).parameters",
            "    except (ValueError, TypeError):",
            "        return  # 无法获取签名，跳过",
            "    required_count = sum(1 for p in params.values()",
            "        if p.default is inspect.Parameter.empty",
            "        and p.kind in (inspect.Parameter.POSITIONAL_ONLY,",
            "                      inspect.Parameter.POSITIONAL_OR_KEYWORD))",
            f"    assert required_count == {required}, "
            f"f\"必填参数个数应为 {required}，实际 {{required_count}}\"",
            "",
        ]
    return lines


def generate_regression_test(
    repo_root: str,
    evidences: List[Evidence],
    issue: str = "",
    framework: str = "pytest",
    failure_output: str = "",
) -> str:
    """生成回归测试代码字符串。

    framework 为 pytest 时生成 pytest 函数式用例；
    为 unittest 时生成 unittest.TestCase 用例。
    failure_output 预留：供后续从失败轨迹提取更精确的行为断言。
    """
    funcs = [e.symbol for e in evidences if e.symbol.type in ("function", "method")]
    if not funcs:
        return "# 未定位到可测试的函数，无法生成回归测试。\n"

    # 优先选源码（非测试文件）里的函数作为回归测试目标
    src_funcs = [f for f in funcs if not _is_test_path(f.path)]
    target = (src_funcs or funcs)[0]
    owner = _find_method_owner(repo_root, target.path, target.name) if target.type == "method" else None
    loader = _module_loader_code(repo_root, target.path, owner)
    func_name = target.name
    target_expr = f"_obj.{func_name}" if owner else f"_mod.{func_name}"
    required = _parse_required_params(target)
    if owner and required is not None:
        try:
            tree = ast.parse(target.code)
            fn = next(n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == target.name)
            if fn.args.args and fn.args.args[0].arg in {"self", "cls"}:
                required -= 1
        except Exception:
            pass
    example = _whitespace_example(target, issue)

    if framework == "unittest":
        return _generate_unittest_case(target, loader, target_expr, func_name, required, issue, example)

    header = [
        "# 由 RepoLens-OSS 生成的回归测试",
        f"# 目标: {target.path}:{target.start_line} 函数 `{func_name}`",
        (f"# 对应 Issue: {' '.join(issue.splitlines())}" if issue else ""),
        "",
    ]
    behavior = _behavioral_pytest(target_expr, func_name, example) if example else []
    smoke = _smoke_pytest(loader, target_expr, func_name, required)
    return "\n".join(header + behavior + smoke)


def _generate_unittest_case(target, loader: str, target_expr: str, func_name: str,
                             required, issue: str, example: Optional[str]) -> str:
    """生成 unittest.TestCase 风格的回归测试（冒烟 + 行为断言）。"""
    header = [
        "# 由 RepoLens-OSS 生成的回归测试（unittest）",
        f"# 目标: {target.path}:{target.start_line} 函数 {func_name}",
        (f"# 对应 Issue: {' '.join(issue.splitlines())}" if issue else ""),
        "import inspect, unittest",
        loader,
        "",
    ]
    body = [
        "class TestRepoLensRegression(unittest.TestCase):",
        "    @classmethod",
        "    def setUpClass(cls):",
        "        cls._mod = _mod",
        "        cls._target = staticmethod(" + target_expr + ")",
        "",
    ]
    if example:
        body += _behavioral_unittest("self._target", func_name, example)
    body += [
        "    def test_" + func_name + "_exists_and_callable(self):",
        "        self.assertTrue(callable(self._target),",
        "                        '函数 " + func_name + " 不存在或不可调用')",
    ]
    if required is not None:
        body += [
            "",
            "    def test_" + func_name + "_signature(self):",
            "        params = inspect.signature(self._target).parameters",
            "        required_count = sum(1 for p in params.values()",
            "            if p.default is inspect.Parameter.empty",
            "            and p.kind in (inspect.Parameter.POSITIONAL_ONLY,",
            "                          inspect.Parameter.POSITIONAL_OR_KEYWORD))",
            f"        self.assertEqual(required_count, {required}, '必填参数个数应与源码一致')",
        ]
    return "\n".join(header + body)
