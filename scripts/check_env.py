"""环境自检：验证核心包与 Web 层依赖是否满足可运行条件。

用法：
    python scripts/check_env.py            # 检查核心包（零第三方依赖）
    python scripts/check_env.py --with-web # 追加检查 Web 层与测试依赖

退出码：0 全部满足；1 存在缺失或版本不满足。

存在原因：2026-10-28 曾因 typing_extensions 4.11.0 低于 anyio 要求的 4.16.0，
导致 `cannot import name 'sentinel'`，FastAPI 不可导入、web/ 不可用、3 条 HTTP 集成测试失败。
该故障在裸 pytest 中只表现为 3 条用例失败，不易定位，故单列本脚本做前置自检。
"""

from __future__ import annotations

import argparse
import importlib
import sys

# (发行包名, 导入名, 最低版本)
CORE_MODULES = [
    ("replens", "replens", None),
]

WEB_MODULES = [
    ("typing_extensions", "typing_extensions", "4.16.0"),
    ("anyio", "anyio", "4.13"),
    ("fastapi", "fastapi", "0.110"),
    ("starlette", "starlette", "0.46"),
    ("uvicorn", "uvicorn", "0.29"),
]

TEST_MODULES = [
    ("pytest", "pytest", "8"),
    ("httpx", "httpx", "0.27"),
]


def _parse_version(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _check(entries: list[tuple[str, str, str | None]]) -> list[str]:
    problems: list[str] = []
    for dist_name, import_name, minimum in entries:
        try:
            module = importlib.import_module(import_name)
        except Exception as exc:  # 导入期异常（含循环依赖）也算不可用
            problems.append(f"{dist_name}: 无法导入 ({type(exc).__name__}: {exc})")
            continue

        if minimum is None:
            continue

        version = getattr(module, "__version__", None)
        if version is None:
            try:
                from importlib.metadata import version as _v

                version = _v(dist_name)
            except Exception:
                version = None

        if version is None:
            problems.append(f"{dist_name}: 无法确定版本，要求 >= {minimum}")
            continue

        if _parse_version(version) < _parse_version(minimum):
            problems.append(f"{dist_name}: 当前 {version}，要求 >= {minimum}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="RepoLens-OSS 环境自检")
    parser.add_argument("--with-web", action="store_true", help="追加检查 Web 层与测试依赖")
    args = parser.parse_args()

    entries = list(CORE_MODULES)
    if args.with_web:
        entries += WEB_MODULES + TEST_MODULES

    print(f"Python {sys.version.split()[0]}")
    problems = _check(entries)

    if problems:
        print("环境检查未通过：")
        for item in problems:
            print(f"  - {item}")
        print("修复建议：pip install -c constraints.txt -e .[web,test]")
        return 1

    scope = "核心 + Web + 测试" if args.with_web else "核心"
    print(f"环境检查通过（{scope}），共 {len(entries)} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
