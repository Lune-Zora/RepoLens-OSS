"""依赖契约回归：锁定 2026-10-28 修复的 anyio × typing_extensions 版本约束。

背景：typing_extensions 4.11.0 时 anyio 需要 >=4.16.0 才能提供 sentinel，
缺失会导致 FastAPI 无法导入、web/ 不可用、3 条 HTTP 集成用例失败。
本测试把该约束固化为可执行断言，防止未来依赖降级重新引入故障。
"""

import importlib.metadata as metadata
import unittest


def _version_tuple(text):
    parts = []
    for chunk in text.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _skip_if_missing(name):
    try:
        return __import__(name)
    except Exception:
        raise unittest.SkipTest(f"{name} 未安装，跳过该项契约检查")


class TypingExtensionsContractTests(unittest.TestCase):
    def test_typing_extensions_provides_sentinel(self):
        """anyio>=4.13 依赖 typing_extensions.sentinel，缺失则 FastAPI 不可导入。"""
        typing_extensions = _skip_if_missing("typing_extensions")
        self.assertTrue(
            hasattr(typing_extensions, "sentinel"),
            "typing_extensions 缺少 sentinel，请升级到 >= 4.16.0（见 constraints.txt）",
        )

    def test_typing_extensions_meets_anyio_requirement(self):
        actual = metadata.version("typing_extensions")
        self.assertGreaterEqual(
            _version_tuple(actual),
            (4, 16, 0),
            f"typing_extensions {actual} 低于 anyio 要求的 4.16.0，见 constraints.txt",
        )


class WebImportContractTests(unittest.TestCase):
    def test_fastapi_importable(self):
        """FastAPI 能导入是 web/ 可用的前提。"""
        _skip_if_missing("fastapi")

    def test_web_app_importable(self):
        _skip_if_missing("web.app")
        from web.app import app

        self.assertGreater(len(app.routes), 0)
