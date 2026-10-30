"""交付契约：保证「声称的证据」在仓库里真实存在，且挖掘结果可被提升。

存在原因（2026-10-28）：

1. `reports/runs/` 被 .gitignore 排除，导致 `scripts/run_ci.py` 写出的 CI 报告**从未入库**。
   于是「本地 CI 8/8 通过」这句话在仓库里没有任何可查证据。
2. `replens/miner.py` 组装输出时只保留 id/category/issue/expected，
   **主动丢弃了 commit 与 parent**，使已验证 fail→pass 的候选无法提升为修复集样本
   （缺 buggy_parent / fix_commit 就还原不出 buggy 基线）。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CiEvidenceTests(unittest.TestCase):
    """CI 报告目录必须入库可查。"""

    def test_ci_report_directory_is_tracked(self):
        result = subprocess.run(
            ["git", "check-ignore", "-q", "reports/ci/最新一次.json"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0,
                            "reports/ci/ 被 .gitignore 排除，CI 证据将无法入库")

    def test_run_ci_writes_into_committed_directory(self):
        source = (ROOT / "scripts" / "run_ci.py").read_text(encoding="utf-8")
        self.assertIn('"reports" / "ci"', source,
                      "run_ci.py 的报告目录应指向入库的 reports/ci")
        self.assertNotIn('"reports" / "runs"', source,
                         "run_ci.py 不应再写入被忽略的 reports/runs")

    def test_latest_pointer_exists_after_run(self):
        latest = ROOT / "reports" / "ci" / "最新一次.json"
        self.assertTrue(latest.exists(), "缺少 reports/ci/最新一次.json")
        payload = json.loads(latest.read_text(encoding="utf-8"))
        self.assertIn("summary", payload)
        self.assertIn("steps", payload)
        self.assertEqual(payload["summary"]["failed"], 0,
                         "最新一次 CI 报告存在失败步骤")


class MinerPromotableTests(unittest.TestCase):
    """挖掘结果必须带 buggy_parent / fix_commit / project，才能提升为修复集样本。"""

    def _git(self, repo, *args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    def _commit(self, repo, message):
        self._git(repo, "add", "-A")
        self._git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                  "commit", "-m", message)
        return self._git(repo, "rev-parse", "HEAD").stdout.strip()

    def test_mined_entries_are_promotable(self):
        from replens.miner import mine_repo

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "toyrepo"
            (repo / "src").mkdir(parents=True)
            (repo / "tests").mkdir()
            self._git(str(repo), "init", "-q")

            (repo / "src" / "calc.py").write_text(
                "def add(a, b):\n    return a - b\n", encoding="utf-8")
            (repo / "tests" / "test_calc.py").write_text(
                "from src.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
                encoding="utf-8")
            buggy = self._commit(str(repo), "initial")

            (repo / "src" / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8")
            # miner 要求同一次修复提交里既有源码改动、也有测试改动（真实项目通常如此）；
            # 只改源码不改测试时不会被挖掘，这是既有行为，不在本测试断言范围。
            (repo / "tests" / "test_calc_regression.py").write_text(
                "from src.calc import add\n\n\ndef test_add_regression():\n"
                "    assert add(2, 3) == 5\n",
                encoding="utf-8")
            fixed = self._commit(str(repo), "fix: correct add() subtraction error")

            result = mine_repo(str(repo), max_commits=5, validate=False)
            self.assertGreaterEqual(result["mined"], 1, "未挖到样本")

            entry = result["items"][0]
            self.assertEqual(entry.get("fix_commit"), fixed)
            self.assertEqual(entry.get("buggy_parent"), buggy)
            self.assertEqual(entry.get("project"), "toyrepo")
            self.assertIn("test_files", entry)
            self.assertIn("src_files", entry)
