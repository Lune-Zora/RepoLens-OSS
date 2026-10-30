"""修复验证脚本：索引污染 / 补丁白名单 / unittest 失败解析 / 严格模式。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, r"E:\gitproject\ossnewpj")
ROOT = Path(r"E:\gitproject\ossnewpj")


def test_index_pollution():
    from replens.repo import import_repo
    demo = ROOT / "examples" / "demo_repo"
    marker = demo / ".replens" / "extra_files" / "tests" / "test_replens_regression.py"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("def test_fake():\n    assert False\n", encoding="utf-8")
    try:
        meta = import_repo(str(demo))
        assert not any(".replens" in p for p in meta.python_files), meta.python_files
        assert not any(".replens" in p for p in meta.document_files)
        print("PASS index pollution: python_files =", len(meta.python_files))
    finally:
        marker.unlink()


def test_patch_whitelist():
    from replens.repo import RepoMeta
    from replens.patchgen import validate_rules
    demo = ROOT / "examples" / "demo_repo"
    meta = RepoMeta(path=str(demo))
    ok, msg = validate_rules(
        meta, {"src/dateparse/parser.py": {"from": "x", "to": "y"}},
        evidence_paths=["src/dateparse/utils.py"])
    assert not ok and "白名单" in msg, msg
    ok2, msg2 = validate_rules(
        meta, {"tests/test_parser.py": {"from": "x", "to": "y"}},
        evidence_paths=["tests/test_parser.py"])
    assert not ok2 and "测试" in msg2, msg2
    ok3, msg3 = validate_rules(
        meta, {"pyproject.toml": {"from": "x", "to": "y"}},
        evidence_paths=["pyproject.toml"])
    assert not ok3 and "配置" in msg3, msg3
    print("PASS patch whitelist:", msg, "|", msg2, "|", msg3)


def test_unittest_failed_parse():
    from replens.flip_predictor import parse_failed_tests
    out = "FAIL: test_parse (test_parser.TestParser)\nERROR: test_formatter (test_formatter.TestFormatter)"
    names = parse_failed_tests(out)
    assert "test_parse" in names and "test_formatter" in names, names
    print("PASS unittest failed parse:", sorted(names))


def test_strict_mode():
    os.environ["REPLENS_REQUIRE_LLM"] = "1"
    os.environ.pop("REPLENS_LLM_BASE_URL", None)
    os.environ.pop("OPENAI_BASE_URL", None)
    from replens.planner import build_planner
    try:
        build_planner()
        raise AssertionError("should raise")
    except RuntimeError as e:
        print("PASS strict mode:", e)
    finally:
        os.environ.pop("REPLENS_REQUIRE_LLM", None)


def test_llm_planner_no_fallback():
    """严格模式下 LLM 空响应/异常不得回退规则。"""
    from replens.planner import LLMPlanner
    from replens.indexer import Symbol
    from replens.retriever import Evidence

    class EmptyLLM:
        def chat_json(self, system, user, temperature=None):
            return None

    class BoomLLM:
        def chat_json(self, system, user, temperature=None):
            raise RuntimeError("service down")

    sym = Symbol(path="a.py", type="function", name="f", start_line=1, end_line=2,
                 signature="def f(x)", code="def f(x):\n    return x\n")
    ev = Evidence(symbol=sym, confidence=1.0, reason="r", source="s")
    os.environ["REPLENS_REQUIRE_LLM"] = "1"
    try:
        for llm in (EmptyLLM(), BoomLLM()):
            p = LLMPlanner(llm)
            try:
                p.generate("issue", [ev])
                raise AssertionError("should raise for " + type(llm).__name__)
            except RuntimeError as e:
                print("PASS llm no fallback:", type(llm).__name__, str(e)[:40])
    finally:
        os.environ.pop("REPLENS_REQUIRE_LLM", None)


if __name__ == "__main__":
    test_index_pollution()
    test_patch_whitelist()
    test_unittest_failed_parse()
    test_strict_mode()
    test_llm_planner_no_fallback()
    print("ALL PASS")
