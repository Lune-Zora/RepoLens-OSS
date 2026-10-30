import json
import shutil
import tempfile
import unittest
from pathlib import Path

from replens.compliance import build_disclosure
from replens.executor import _apply_json_patch, _apply_unified_diff, run_test_suite
from replens.indexer import build_index
from replens.patchgen import validate_rules
from replens.repo import import_repo
from replens.retriever import retrieve
from replens.testgen import generate_regression_test


ROOT = Path(__file__).resolve().parents[1]


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="replens_test_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_documents_are_indexed_and_issue_is_located(self):
        repo = ROOT / "examples" / "demo_repo"
        meta = import_repo(repo)
        self.assertIn("docs/usage.md", meta.document_files)
        db = build_index(meta, db_path=str(self.tmp / "index.db"), force=True)
        rows = db.conn.execute("SELECT path FROM symbols WHERE type='document'").fetchall()
        self.assertTrue(rows)
        evidence, insufficient = retrieve("date string whitespace", db)
        self.assertFalse(insufficient)
        self.assertEqual(evidence[0].symbol.path, "src/dateparse/parser.py")
        db.close()

    def test_patch_validation_rejects_unsafe_rules(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        (repo / "api.py").write_text("VALUE = 0\n", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_api.py").write_text("VALUE = 0\n", encoding="utf-8")
        meta = import_repo(repo)
        self.assertFalse(validate_rules(meta, {"api.py": {"from": "", "to": "x"}})[0])
        self.assertFalse(validate_rules(meta, {
            "tests/test_api.py": {"from": "VALUE = 0", "to": "VALUE = 1"}
        })[0])

    def test_json_patch_cannot_escape_and_replaces_once(self):
        root = self.tmp / "root"
        root.mkdir()
        (root / "api.py").write_text("VALUE = 0\n", encoding="utf-8")
        outside = self.tmp / "outside.txt"
        outside.write_text("ORIGINAL\n", encoding="utf-8")
        escape = self.tmp / "escape.json"
        escape.write_text(json.dumps({"../outside.txt": {"from": "ORIGINAL", "to": "CHANGED"}}), encoding="utf-8")
        self.assertIn("不存在", _apply_json_patch(str(root), str(escape)))
        self.assertEqual(outside.read_text(encoding="utf-8"), "ORIGINAL\n")

    def test_unified_diff_checks_deletions_and_supports_multiple_files(self):
        root = self.tmp / "root"
        root.mkdir()
        (root / "a.py").write_text("a0\n", encoding="utf-8")
        (root / "b.py").write_text("b0\n", encoding="utf-8")
        stale = self.tmp / "stale.patch"
        stale.write_text("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-expected\n+bad\n", encoding="utf-8")
        self.assertIn("删除行不匹配", _apply_unified_diff(str(root), str(stale)))
        multi = self.tmp / "multi.patch"
        multi.write_text(
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a0\n+a1\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-b0\n+b1\n", encoding="utf-8")
        self.assertTrue(_apply_unified_diff(str(root), str(multi)).startswith("补丁应用成功"))
        self.assertEqual((root / "a.py").read_text(encoding="utf-8"), "a1\n")
        self.assertEqual((root / "b.py").read_text(encoding="utf-8"), "b1\n")

    def test_demo_regression_is_fail_then_pass(self):
        repo = self.tmp / "demo"
        shutil.copytree(ROOT / "examples" / "demo_repo", repo)
        meta = import_repo(repo)
        db = build_index(meta, db_path=str(self.tmp / "demo.db"), force=True)
        row = db.conn.execute(
            "SELECT path,type,name,start_line,end_line,signature,doc,code "
            "FROM symbols WHERE path=? AND name=? AND type='function'",
            ("src/dateparse/parser.py", "parse_date"),
        ).fetchone()
        from replens.indexer import Symbol
        from replens.retriever import Evidence
        symbol = Symbol(**{key: row[key] for key in Symbol.__dataclass_fields__})
        generated = generate_regression_test(str(repo), [Evidence(symbol, 1, "test", "keyword_match")],
                                             "parse date whitespace", framework="unittest")
        db.close()
        from replens.patchgen import RulePatchGenerator
        patch_file, _ = RulePatchGenerator().generate(meta, "parse date whitespace", [Evidence(symbol, 1, "test", "keyword_match")])
        before, after, note = run_test_suite(
            str(repo), "python -m unittest discover -s tests", patch_file=patch_file,
            extra_files={"tests/test_replens_regression.py": generated}, timeout=30)
        self.assertFalse(before[0].passed)
        self.assertTrue(after and after[0].passed, note + "\n" + str(after[0].to_dict() if after else None))

    def test_generated_test_supports_relative_import_and_method(self):
        repo = self.tmp / "package_repo"
        (repo / "pkg").mkdir(parents=True)
        (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "pkg" / "helper.py").write_text("PREFIX = 'x'\n", encoding="utf-8")
        (repo / "pkg" / "api.py").write_text(
            "from .helper import PREFIX\n\n"
            "class Parser:\n    def parse_value(self, value):\n        return PREFIX + value\n\n"
            "def read_value(value):\n    return PREFIX + value\n", encoding="utf-8")
        (repo / "tests").mkdir()
        meta = import_repo(repo)
        db = build_index(meta, db_path=str(self.tmp / "package.db"), force=True)
        from replens.indexer import Symbol
        from replens.retriever import Evidence
        rows = db.conn.execute(
            "SELECT path,type,name,start_line,end_line,signature,doc,code FROM symbols "
            "WHERE path='pkg/api.py' AND type IN ('function','method') ORDER BY name"
        ).fetchall()
        for row in rows:
            symbol = Symbol(**{key: row[key] for key in Symbol.__dataclass_fields__})
            generated = generate_regression_test(str(repo), [Evidence(symbol, 1, "test", "keyword_match")],
                                                 "read value", framework="unittest")
            result, _, _ = run_test_suite(
                str(repo), "python -m unittest discover -s tests", extra_files={"tests/test_generated.py": generated}, timeout=30)
            self.assertTrue(result[0].passed, result[0].stderr)
        db.close()

    def test_compliance_flags_unregistered_dependencies(self):
        """pyproject 声明了 RESOURCES 未登记的依赖时，报告里应出现警告段。

        修复前：replens/ 存在时直接走 RESOURCES 渲染，collect_deps 被完全跳过，
        即使 pyproject 新增依赖也悄无声息。
        修复后：build_disclosure 始终跑一次 collect_deps 做健康检查，
        未登记依赖出现在 dict['unregistered'] 与报告末尾的「自动扫描发现未登记依赖」段。
        """
        repo = self.tmp / "compliance_repo"
        (repo / "replens").mkdir(parents=True)
        (repo / "replens" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n'
            'dependencies = ["newfakelib>=0.1"]\n',
            encoding="utf-8")
        result = build_disclosure(str(repo))
        self.assertIn("unregistered", result)
        names = {d["name"] for d in result["unregistered"]}
        self.assertIn("newfakelib", names)
        report = Path(result["report"]).read_text(encoding="utf-8")
        self.assertIn("未登记", report)
        self.assertIn("newfakelib", report)
        # 警告段应在「## 说明」之前，避免被常规说明盖过去
        warn_idx = report.find("自动扫描发现未登记依赖")
        note_idx = report.find("## 说明")
        self.assertGreater(warn_idx, 0)
        self.assertLess(warn_idx, note_idx)

    def test_compliance_does_not_flag_already_registered_dependencies(self):
        """现有仓库（fastapi/uvicorn 已在 RESOURCES）不应触发警告。"""
        result = build_disclosure(str(ROOT))
        self.assertEqual(result["unregistered"], [])


if __name__ == "__main__":
    unittest.main()
