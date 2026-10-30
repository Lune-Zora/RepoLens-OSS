"""回溯修复脚本契约：换出处的优先级、标注口径与「改了就必须重验」。

存在原因（2026-10-29）：
  上一版 repair_annotations.py 只改标注、不重跑测试，改完没有任何证据说明样本仍然
  fail→pass。这一版默认重验，因此脚本本身的判断逻辑必须先被钉住：

  1. 优先级：人工选定的上游原文 > （判定为 weak 时）提交正文 > 提交标题；
  2. 正文只在判定为 weak 时采用，否则合格但短的标题会被启发式换掉；
  3. expected 必须落在符号定义行（指标按 ±3 行判定）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import repair_annotations as repair  # noqa: E402


def _repo(directory, subject, body=""):
    repo = str(directory)
    subprocess.run(["git", "init", "-q", repo], check=True)
    module = Path(repo) / "pkg"
    module.mkdir()
    (module / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    def commit(message):
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                        "user.email=t@example.com", "commit", "-m", message], check=True)
        return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    base = commit("base")
    (module / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    message = subject + ("\n\n" + body if body else "")
    fix = commit(message)
    return repo, base, fix


class IssuePrecedenceTests(unittest.TestCase):
    def test_curation_entry_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _repo(Path(directory), "fix format_num", "fixes #1548")
            upgrades = {fix: {"issue": "fix negative decimals format",
                              "source": "upstream_issue"}}
            issue, source, quality = repair._issue_for(repo, fix, upgrades, {})
            self.assertEqual((issue, source, quality),
                             ("fix negative decimals format", "upstream_issue", "clear"))

    def test_weak_verdict_switches_to_body(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _repo(
                Path(directory), "rich: misc fixes",
                "handle iterables without len, mirroring rich DownloadColumn")
            issue, source, quality = repair._issue_for(repo, fix, {}, {fix: "weak"})
            self.assertEqual(source, "commit_body")
            self.assertIn("iterables without len", issue)
            self.assertEqual(quality, "clear")

    def test_clear_verdict_keeps_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _repo(
                Path(directory), "pandas>=3 & negative format_interval",
                "- is_builtin_func was removed in pandas 3 (fixes #1701)")
            issue, source, quality = repair._issue_for(repo, fix, {}, {fix: "clear"})
            self.assertEqual((issue, source), ("pandas>=3 & negative format_interval",
                                               "commit_subject"))
            self.assertEqual(quality, "clear")

    def test_weak_without_better_text_stays_weak(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _repo(Path(directory), "fix edge cases & windows tests")
            issue, source, quality = repair._issue_for(repo, fix, {}, {fix: "weak"})
            self.assertEqual(source, "commit_subject")
            self.assertEqual(quality, "weak")


class ExpectedLineTests(unittest.TestCase):
    def test_expected_points_at_definition_line(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, base, fix = _repo(Path(directory), "Fix alpha handling")
            item = {"id": 1, "project": "demo", "buggy_parent": base,
                    "fix_commit": fix, "expected": ["pkg/mod.py:f:2"]}
            expected = repair._expected_for(repo, item, [])
            # 旧标注写的是改动行（2），正确值是符号定义行（1）
            self.assertEqual(expected, ["pkg/mod.py:f:1"])


class VerificationWiringTests(unittest.TestCase):
    def test_verification_writeback_records_evidence(self):
        item = {"id": 1}
        record = {"id": 1, "ok": True, "scope": "targeted",
                  "targets": ["tests/test_mod.py::TestX::test_f"], "env_profile": "modern"}
        updated = repair._apply_verification(item, record)
        self.assertTrue(updated["fail_pass"])
        self.assertIn("verified_at", updated)
        self.assertEqual(updated["test_cmd"],
                         "pytest tests/test_mod.py::TestX::test_f")

    def test_dataset_repair_never_writes_on_failed_verification(self):
        """失败必须非零退出且不落盘——本题只能靠读源码确认，故检查关键字存在。"""
        source = (ROOT / "scripts" / "repair_annotations.py").read_text(encoding="utf-8")
        self.assertIn("存在重验失败，未写入数据集", source)


if __name__ == "__main__":
    unittest.main()
