"""挖掘器契约：锁定验证口径、标注格式与可提升字段。

存在原因（2026-10-29）：样本长期停在 12 个，查下去是三处实现缺陷，
每一处都会让「已验证的修复」白费：

1. 验证用「整个测试文件」，文件里无关用例失败即判未通过 —— loguru 旧记录 15/15 全灭，
   改定向后同批提交 6/6 通过。
2. 找不到符号时标注写成 `path:line`，不符合 `path[:function[:line]]`，候选被判无效。
3. 输出丢弃 commit/parent，验证再通过也无法提升为修复集样本。
"""

import unittest

from replens.eval_metrics import parse_expected
import os
import tempfile
from pathlib import Path

from replens.miner import (_affected_test_nodes, _body_paragraphs,
                           _changed_right_lines, _clean_issue, _descriptive_line,
                           _enclosing_test_functions, _first_changed_new_line,
                           _first_changed_old_line, _is_weak_issue, _worktree_env,
                           attribute_symbol, issue_from_subject)


class AffectedTestNodeTests(unittest.TestCase):
    """验证命令必须定向到补丁真正影响的测试。"""

    def test_extracts_new_test_from_diff(self):
        diff = (
            "diff --git a/tests/test_demo.py b/tests/test_demo.py\n"
            "--- a/tests/test_demo.py\n"
            "+++ b/tests/test_demo.py\n"
            "@@ -1,2 +1,6 @@\n"
            " def helper():\n"
            "     pass\n"
            "+def test_new_behaviour():\n"
            "+    assert True\n"
        )
        self.assertEqual(_affected_test_nodes(diff), ["tests/test_demo.py::test_new_behaviour"])

    def test_ignores_removed_and_unchanged_lines(self):
        diff = (
            "--- a/tests/test_demo.py\n"
            "+++ b/tests/test_demo.py\n"
            "@@ -1,3 +1,3 @@\n"
            "-def test_removed():\n"
            " def test_context():\n"
            "+# just a comment\n"
        )
        self.assertEqual(_affected_test_nodes(diff), [])

    def test_multiple_files_and_dedup(self):
        diff = (
            "--- a/tests/a.py\n"
            "+++ b/tests/a.py\n"
            "+def test_one():\n"
            "--- a/tests/b.py\n"
            "+++ b/tests/b.py\n"
            "+def test_two():\n"
            "+def test_two():\n"
        )
        self.assertEqual(_affected_test_nodes(diff), ["tests/a.py::test_one", "tests/b.py::test_two"])

    def test_enclosing_function_covers_decorator_lines(self):
        """只改 parametrize 参数列表时，改动行落在装饰器上，必须仍能归属到测试函数。"""
        source = (
            "import pytest\n"
            "\n"
            "@pytest.mark.parametrize(\"a\", [1, 2])\n"
            "def test_decorated(a):\n"
            "    assert a\n"
        )
        # 装饰器在第 3 行，def 在第 4 行；第 3 行必须被判定属于 test_decorated
        self.assertEqual(_enclosing_test_functions(source, [3]), ["test_decorated"])


class ChangePositionTests(unittest.TestCase):
    """纯插入 hunk 的位置必须落在上下文之后，而不是 hunk 起始行。"""

    def test_pure_insertion_skips_leading_context(self):
        diff = (
            "@@ -41,6 +41,16 @@ class Foo:\n"
            "         a = 1\n"
            "         b = 2\n"
            " \n"
            "+    def added(self):\n"
            "+        return 1\n"
            " \n"
            " class Bar:\n"
        )
        # 前导上下文 3 行 -> 插入发生在旧 43 行之后，应返回 43 而不是 41
        self.assertEqual(_first_changed_old_line(diff), 43)
        self.assertEqual(_first_changed_new_line(diff), 44)

    def test_deletion_returns_that_line(self):
        diff = (
            "@@ -10,4 +10,3 @@\n"
            " ctx\n"
            "-removed\n"
            " ctx2\n"
        )
        self.assertEqual(_first_changed_old_line(diff), 11)


class AttributionTests(unittest.TestCase):
    """新增方法应归到外层类：该类在旧版本存在且包含改动，标注才可用。"""

    OLD = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "class Bar:\n"
        "    pass\n"
    )
    NEW = (
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def added(self):\n"
        "        return 2\n"
        "\n"
        "class Bar:\n"
        "    pass\n"
    )

    def test_new_method_attributes_to_enclosing_class(self):
        # 插入点在新文件第 5 行（added 的定义行）
        name, line = attribute_symbol(self.OLD, self.NEW, 3, 5)
        self.assertEqual(name, "Foo")
        self.assertEqual(line, 1)

    def test_modified_existing_symbol_keeps_itself(self):
        old = "def f():\n    return 1\n"
        new = "def f():\n    return 2\n"
        name, line = attribute_symbol(old, new, 2, 2)
        self.assertEqual(name, "f")
        self.assertEqual(line, 1)

    def test_pure_deletion_falls_back_to_definition_line(self):
        """纯删除补丁没有新增行，退回的必须是定义行而不是改动行（C9 抓到的真实缺陷）。"""
        old = (
            "def helper():\n"
            "    return 0\n"
            "\n"
            "def get_named_dest_root(self):\n"
            "    value = 1\n"
            "    return value\n"
        )
        new = (
            "def helper():\n"
            "    return 0\n"
            "\n"
            "def get_named_dest_root(self):\n"
            "    return 1\n"
        )
        # 改动行在旧文件第 5 行（函数体内），new_pos 为 None 模拟纯删除
        name, line = attribute_symbol(old, new, 5, None)
        self.assertEqual(name, "get_named_dest_root")
        self.assertEqual(line, 4)

    def test_duplicate_name_prefers_the_touched_occurrence(self):
        old = (
            "class A:\n"
            "    def render(self):\n"
            "        return 1\n"
            "\n"
            "class B:\n"
            "    def render(self):\n"
            "        return 2\n"
        )
        new = old.replace("        return 2\n", "        return 3\n")
        name, line = attribute_symbol(old, new, 7, 7)
        self.assertEqual((name, line), ("render", 6))


class IssueCleaningTests(unittest.TestCase):
    """前缀剥离不得产出残句，也不得把正文当标签削掉。"""

    def test_keeps_full_sentence_when_strip_would_dangle(self):
        self.assertEqual(_clean_issue("Fix that tqdm_class cannot be passed"),
                         "Fix that tqdm_class cannot be passed")

    def test_strips_type_tag(self):
        self.assertEqual(_clean_issue("BUG: Decode glyph names"), "Decode glyph names")
        self.assertEqual(_clean_issue("ROB: Fix compatibility"), "Fix compatibility")

    def test_does_not_truncate_word_starting_with_prefix(self):
        self.assertEqual(_clean_issue("Fixes for standard mode"), "Fixes for standard mode")

    def test_keeps_release_as_body_word(self):
        self.assertEqual(_clean_issue("Release content streams"), "Release content streams")


class IssueSourceTests(unittest.TestCase):
    """弱描述的两条出路：不把标题削成名词短语；人工判定后可换成上游正文。

    2026-10-29：8 条弱描述里 5 条是被前缀剥离削成名词短语的（fix format_num -> format_num），
    其余能靠提交正文或关联 issue 恢复成完整描述。正文只在人工判定为 weak 时才采用，
    否则启发式会把合格但较短的标题换成正文里的实现细节。
    """

    def test_keeps_prefix_when_only_identifier_remains(self):
        self.assertEqual(_clean_issue("fix format_num"), "fix format_num")

    def test_recognises_vague_titles(self):
        self.assertTrue(_is_weak_issue("rich: misc fixes"))
        self.assertTrue(_is_weak_issue("format_num"))
        self.assertFalse(_is_weak_issue("contrib.concurrent: fix no-len iterables"))

    def test_subject_is_kept_unless_body_is_allowed(self):
        body = "When options are being parsed, multiple options may evaluate to have\n" \
               "defaults of UNSET.\n"
        self.assertEqual(issue_from_subject("misc fixes", body),
                         ("misc fixes", "commit_subject"))

    def test_body_paragraphs_join_hard_wrapped_lines(self):
        body = ("When options are being parsed, multiple options may evaluate to have\n"
                "defaults of UNSET, and some may have None.\n")
        self.assertEqual(_body_paragraphs(body),
                         ["When options are being parsed, multiple options may evaluate "
                          "to have defaults of UNSET, and some may have None."])

    def test_numbered_list_is_not_treated_as_sentence_end(self):
        body = ("1. handle iterables without len, mirroring rich DownloadColumn\n"
                "  - fallback to completed for unit\n")
        issue, source = issue_from_subject("rich: misc fixes", body, allow_body=True)
        self.assertEqual(source, "commit_body")
        self.assertTrue(issue.startswith("handle iterables without len"))

    def test_wrapped_body_becomes_issue_when_allowed(self):
        body = ("We need to extract the args from the fresh context when following nested\n"
                "groups to keep behavior for shell completion.\n")
        issue, source = issue_from_subject("shell completion for nested groups", body,
                                           allow_body=True)
        self.assertEqual(source, "commit_body")
        self.assertIn("fresh context", issue)


class TestNodeSpanTests(unittest.TestCase):
    """pytest 节点 id 形如 文件::类::方法；只给方法名会被判 not found（退出码 4）。"""

    SOURCE = (
        "class TestRequests:\n"
        "    def test_proxy(self):\n"
        "        assert True\n"
        "\n"
        "def test_module_level():\n"
        "    assert True\n"
    )

    def test_method_is_qualified_with_its_class(self):
        self.assertEqual(_enclosing_test_functions(self.SOURCE, [2]), ["TestRequests::test_proxy"])

    def test_module_level_function_keeps_bare_name(self):
        self.assertEqual(_enclosing_test_functions(self.SOURCE, [5]), ["test_module_level"])

    def test_affected_nodes_use_qualified_ids(self):
        diff = (
            "--- a/tests/test_requests.py\n"
            "+++ b/tests/test_requests.py\n"
            "@@ -1,3 +1,4 @@\n"
            " class TestRequests:\n"
            "     def test_proxy(self):\n"
            "+        assert 1\n"
        )
        # 无法取到仓库源码时走兜底扫描：只认新增的 def，这里不应产生节点。
        self.assertEqual(_affected_test_nodes(diff), [])


class WorktreeEnvTests(unittest.TestCase):
    """验证必须在 worktree 的源码上跑，不能落到已安装的同名包。

    2026-10-29 实测：click 采用 src 布局且已装进 site-packages，
    未把 worktree 的 src 放进 PYTHONPATH 时，补丁改的源码根本没被执行，
    3 条候选全部误判为「修复后测试未通过」；修正后 4/4 通过。
    """

    def test_prepends_worktree_and_src(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "src").mkdir()
            env = _worktree_env(directory)
            parts = env["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(parts[0], str(Path(directory) / "src"))
            self.assertIn(directory, parts)

    def test_flat_layout_prepends_worktree_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env = _worktree_env(directory)
            parts = env["PYTHONPATH"].split(os.pathsep)
            self.assertEqual(parts[0], directory)

    def test_keeps_existing_pythonpath(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("PYTHONPATH")
            os.environ["PYTHONPATH"] = "/existing/path"
            try:
                env = _worktree_env(directory)
                self.assertIn("/existing/path", env["PYTHONPATH"].split(os.pathsep))
            finally:
                if old is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = old


class ExpectedFormatTests(unittest.TestCase):
    """标注必须是 path / path:function / path:function:line 三种之一。"""

    def test_path_only_is_valid(self):
        """找不到符号时只写路径——这是修复后的格式。"""
        path, name, line = parse_expected("pypdf/_cmap.py")
        self.assertEqual(path, "pypdf/_cmap.py")
        self.assertIsNone(name)
        self.assertIsNone(line)

    def test_path_line_alone_is_invalid(self):
        """修复前的 `path:line` 写法必须被拒绝，否则会静默产生坏标注。"""
        with self.assertRaises(ValueError):
            parse_expected("pypdf/_cmap.py:38")

