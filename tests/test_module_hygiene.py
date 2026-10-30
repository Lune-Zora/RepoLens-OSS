"""模块卫生检查：拦截「导入名被局部变量遮蔽」和「未定义名字」两类静默缺陷。

存在原因（2026-10-28）：

把 695 行控制端脚本拆为 `scripts/realbug/` 后，出现两个缺陷，而当时 11 条单元测试**全部通过**：

1. `pipeline.py` 里 `upstream = upstream._verify_upstream(...)`——模块级导入名在同一函数内被赋值，
   Python 判其为局部变量，右侧立即 `UnboundLocalError`。
2. `gitops.py` 漏导入 `urlsplit`，运行到该分支才 `NameError`。

两者都只在跑真实流程时暴露。本测试用 AST 静态扫描，把这类问题提前到单元测试阶段。
"""

import ast
import builtins
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "scripts" / "realbug"
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__package__"}


def _module_level_names(tree):
    """模块级定义的函数、类、导入名与常量。"""
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _imported_names(tree):
    """仅模块级导入名——这些才是「被遮蔽会出问题」的名字。"""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
    return names


class ShadowingTests(unittest.TestCase):
    """导入名不得在函数内被赋值为同名局部变量。"""

    def test_no_import_name_shadowed_inside_functions(self):
        problems = []
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = _imported_names(tree)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                        if sub.id in imported:
                            problems.append(
                                f"{path.name}:{sub.lineno} 函数 {node.name} 内把导入名 "
                                f"{sub.id!r} 赋成局部变量（会触发 UnboundLocalError）"
                            )
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = sub.args
                        for arg in (args.posonlyargs + args.args + args.kwonlyargs
                                    + [args.vararg, args.kwarg]):
                            if arg is not None and arg.arg in imported:
                                problems.append(
                                    f"{path.name}:{sub.lineno} 参数名 {arg.arg!r} 与导入名冲突"
                                )
        self.assertEqual(problems, [], "导入名被遮蔽:\n" + "\n".join(problems))


class UndefinedNameTests(unittest.TestCase):
    """模块内引用的名字必须有定义，避免运行到该分支才 NameError。"""

    def test_no_undefined_names(self):
        problems = []
        for path in sorted(PACKAGE.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            defined = _module_level_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    for arg in (args.posonlyargs + args.args + args.kwonlyargs
                                + [args.vararg, args.kwarg]):
                        if arg is not None:
                            defined.add(arg.arg)
                if isinstance(node, ast.Lambda):
                    args = node.args
                    for arg in (args.posonlyargs + args.args + args.kwonlyargs
                                + [args.vararg, args.kwarg]):
                        if arg is not None:
                            defined.add(arg.arg)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    defined.add(node.id)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                if isinstance(node, ast.ExceptHandler) and node.name:
                    defined.add(node.name)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        defined.add((alias.asname or alias.name).split(".")[0])
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        defined.add(alias.asname or alias.name)
                if isinstance(node, ast.comprehension):
                    for sub in ast.walk(node.target):
                        if isinstance(sub, ast.Name):
                            defined.add(sub.id)

            used = {n.id for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            missing = sorted(used - defined - BUILTINS)
            if missing:
                problems.append(f"{path.name}: 未定义 {missing}")
        self.assertEqual(problems, [], "存在未定义名字:\n" + "\n".join(problems))


class EntryContractTests(unittest.TestCase):
    """入口必须能在无 replens 依赖的导入方式下加载（scripts 目录直接入 sys.path）。"""

    def test_package_importable(self):
        import sys
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import realbug

        for name in ("run", "main", "RestoreError", "pipeline", "gitops", "upstream"):
            self.assertTrue(hasattr(realbug, name), f"realbug 缺少 {name}")

    def test_stage_functions_present(self):
        import sys
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from realbug import pipeline

        expected = [
            "_stage_preflight",
            "_stage_upstream_verification",
            "_stage_environment",
            "_stage_independent_acceptance",
            "_stage_buggy_worktree",
            "_stage_buggy_baseline",
            "_stage_gold_validation",
            "_stage_cross_worktree_replay",
            "_stage_complete",
        ]
        missing = [name for name in expected if not hasattr(pipeline, name)]
        self.assertEqual(missing, [], f"缺少阶段函数: {missing}")
        self.assertEqual(len(pipeline.STAGES), len(expected))
