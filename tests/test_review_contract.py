"""复核器契约：保证「机械校验」既有效又不误报。

存在原因（2026-10-29）：

  裸跑复核器时先出现 12 条 WARN，逐条查下来**全是判据自身的错**：
  1. C6 拿提交标题与 issue 直接比，但 miner 会剥掉 fix:/BUG: 前缀，导致误报；
  2. C4 只统计新增行，纯删除的修复被判「未触及」；
  3. C4 取第一个同名函数，而 loguru 的 _simple_sinks.py 有 5 个 write。

  这三处若不复核，就会把 15 条有效样本全部标成可疑。故把判据固定下来。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_mined as review  # noqa: E402


class IssueNormalizationTests(unittest.TestCase):
    """C6 必须对齐 miner 的前缀剥离规则，否则会大面积误报。"""

    def test_strips_fix_prefix(self):
        self.assertEqual(review._norm_issue("fix: handle total=inf"),
                         review._norm_issue("handle total=inf"))

    def test_strips_bug_prefix(self):
        self.assertEqual(review._norm_issue("BUG: Resolve indirect widths"),
                         review._norm_issue("Resolve indirect widths"))

    def test_keeps_prefix_when_remainder_is_a_fragment(self):
        """剥完只剩两个词时 miner 保留前缀，复核器必须跟着改，否则误报。"""
        self.assertEqual(review._norm_issue("Fix length detection"), "fix length detection")
        self.assertEqual(review._norm_issue("Fix deprecated label formatting"),
                         "deprecated label formatting")

    def test_keeps_genuinely_different_text(self):
        self.assertNotEqual(review._norm_issue("fix: alpha"),
                            review._norm_issue("beta"))

    def test_ignores_backticks_and_asterisks(self):
        self.assertEqual(review._norm_issue("Fix `enqueue=True` usage"),
                         review._norm_issue("enqueue=True usage"))


class RepoArgumentTests(unittest.TestCase):
    """project=path 解析必须容忍空格与空项。"""

    def test_parses_multiple(self):
        repos = review._parse_repos(" tqdm=/a , pypdf=/b ,, loguru=/c ")
        self.assertEqual(set(repos), {"tqdm", "pypdf", "loguru"})

    def test_ignores_empty_chunks(self):
        self.assertEqual(review._parse_repos(",,,"), {})


class TestPathClassificationTests(unittest.TestCase):
    """C7 依赖「是否测试文件」的判定，判错会把源码修复误杀或把测试改动放过。"""

    def test_recognises_test_paths(self):
        for path in ("tests/test_demo.py", "tests/unit/test_x.py",
                     "test_top.py", "src/pkg/tests/test_y.py"):
            self.assertTrue(review._is_test_path(path), path)

    def test_source_paths_are_not_tests(self):
        for path in ("src/click/core.py", "pypdf/_cmap.py", "tqdm/std.py",
                     "latest/thing.py"):
            self.assertFalse(review._is_test_path(path), path)


class DuplicateGroupTests(unittest.TestCase):
    """重复必须按补丁内容判定：标题相似不可靠，tree 哈希在 cherry-pick 下会漏。"""

    def _make_repo(self, directory):
        import subprocess
        repo = str(directory)
        subprocess.run(["git", "init", "-q", repo], check=True)
        def commit(msg):
            subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
            subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                            "user.email=t@e.com", "commit", "-m", msg], check=True)
            return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        from pathlib import Path
        (Path(repo) / "m.py").write_text("V = 1\n", encoding="utf-8")
        base = commit("base")
        (Path(repo) / "m.py").write_text("V = 2\n", encoding="utf-8")
        first = commit("fix one")
        return repo, base, first

    def test_same_patch_detected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, first = self._make_repo(Path(directory))
            h1 = review._patch_hash(repo, base, first)
            h2 = review._patch_hash(repo, base, first)
            self.assertEqual(h1, h2)

    def test_groups_keep_first_and_flag_rest(self):
        items = [{"id": 1, "project": "p", "fix_commit": "a", "buggy_parent": "b"}]
        # 用桩替换取哈希的函数，专注验证分组规则本身
        original = review._patch_hash
        review._patch_hash = lambda *a, **k: "SAMEHASH"
        try:
            items.append({"id": 2, "project": "p", "fix_commit": "c", "buggy_parent": "b"})
            mapping = review._duplicate_groups(items, {"p": "/nowhere"})
        finally:
            review._patch_hash = original
        self.assertEqual(mapping, {2: [1]})


def _make_fix_repo(directory, subject="Fix alpha handling"):
    """两个提交的最小仓库：base 定义 f()，fix 改 f() 并加测试。"""
    import subprocess
    from pathlib import Path
    repo = str(directory)
    subprocess.run(["git", "init", "-q", repo], check=True)

    def commit(message):
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                        "user.email=t@example.com", "commit", "-m", message], check=True)
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    pkg = Path(repo) / "pkg"
    pkg.mkdir()
    (pkg / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    base = commit("base")
    (pkg / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    tests = Path(repo) / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("def test_f():\n    assert True\n", encoding="utf-8")
    fix = commit(subject)
    return repo, base, fix


def _item(base, fix, **overrides):
    item = {"id": 1, "project": "demo", "fix_commit": fix, "buggy_parent": base,
            "expected": ["pkg/mod.py:f:1"], "test_cmd": "pytest tests/test_mod.py::test_f",
            "issue": "alpha handling"}
    item.update(overrides)
    return item


def _check(result, name):
    return next(status for key, status, _detail in result["checks"] if key == name)


class IssueSourceCheckTests(unittest.TestCase):
    """C6 必须认可「换了出处」的描述，但要能机械验证出处，不能口说无凭。"""

    def test_subject_derived_issue_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _make_fix_repo(Path(directory))
            result = review.review_item(_item(base, fix, issue_source="commit_subject"),
                                        {"demo": repo})
            self.assertEqual(_check(result, "C6"), "PASS")

    def test_body_derived_issue_must_be_literal_body_text(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _make_fix_repo(
                Path(directory), subject="Fix alpha handling\n\nalpha fails when the value is empty")
            ok = review.review_item(
                _item(base, fix, issue="alpha fails when the value is empty",
                      issue_source="commit_body"), {"demo": repo})
            self.assertEqual(_check(ok, "C6"), "PASS")
            invented = review.review_item(
                _item(base, fix, issue="alpha behaves oddly in some cases",
                      issue_source="commit_body"), {"demo": repo})
            self.assertEqual(_check(invented, "C6"), "FAIL")

    def test_upstream_issue_requires_curation_entry(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _make_fix_repo(Path(directory))
            original = review.CURATION
            curation = Path(directory) / "issue_upgrade.json"
            curation.write_text(json.dumps({"entries": [
                {"fix_commit": fix, "issue": "alpha fails when empty", "ref": "demo#1"}
            ]}), encoding="utf-8")
            review.CURATION = curation
            try:
                ok = review.review_item(
                    _item(base, fix, issue="alpha fails when empty",
                          issue_source="upstream_issue"), {"demo": repo},
                    upgrades=review._upgrade_index())
                self.assertEqual(_check(ok, "C6"), "PASS")
                missing = review.review_item(
                    _item(base, fix, issue="alpha fails when empty",
                          issue_source="upstream_issue"), {"demo": repo}, upgrades={})
                self.assertEqual(_check(missing, "C6"), "FAIL")
            finally:
                review.CURATION = original


class DefinitionLineCheckTests(unittest.TestCase):
    """C9：标注行号必须是符号定义行，否则指标（±3 行）必然落空。"""

    def test_definition_line_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _make_fix_repo(Path(directory))
            result = review.review_item(_item(base, fix), {"demo": repo})
            self.assertEqual(_check(result, "C9"), "PASS")

    def test_body_line_fails(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _make_fix_repo(Path(directory))
            result = review.review_item(_item(base, fix, expected=["pkg/mod.py:f:2"]),
                                        {"demo": repo})
            self.assertEqual(_check(result, "C9"), "FAIL")


class DatasetShapeTests(unittest.TestCase):
    """正式修复集的字段必须足以做机械复核。"""

    def test_mined_dataset_has_reviewable_fields(self):
        path = ROOT / "eval" / "datasets" / "real_dataset_mined.json"
        items = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(items, "挖掘数据集为空")
        for item in items:
            for field in ("project", "buggy_parent", "fix_commit", "test_cmd", "expected"):
                self.assertIn(field, item, "样本 %s 缺少 %s" % (item.get("id"), field))
            self.assertIn(item.get("validation_scope"), {"targeted", "file"})


if __name__ == "__main__":
    unittest.main()
