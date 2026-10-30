"""命令行入口：init / index / search / plan / run / eval。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .compliance import build_disclosure
from .miner import mine_repo
from .provenance import build_provenance, verify_provenance
from .indexer import IndexDB, build_index
from .planner import Plan, build_planner
from .repo import import_repo
from .report import build_report, render_json, render_markdown, render_verification_markdown
from .retriever import Evidence, _is_test_path, retrieve
from .retriever_hybrid import build_embedder, retrieve_hybrid
from .executor import run_test_suite
from .testgen import generate_regression_test


def _index_path(repo_path: str) -> str:
    return str(Path(repo_path) / ".replens" / "index.db")


def _default_test_command(meta) -> str:
    if meta.test_framework == "pytest":
        return "pytest"
    if meta.test_framework == "unittest":
        return "python -m unittest"
    return "pytest"


def _regression_test_command(meta) -> str:
    """独立回归测试命令：按仓库测试框架适配，不再固定 pytest。"""
    if meta.test_framework == "unittest":
        return "python -m unittest discover -s tests -p test_replens_regression.py"
    return "pytest tests/test_replens_regression.py"


def _load_db(repo_path: str) -> IndexDB:
    db_path = _index_path(repo_path)
    if not os.path.exists(db_path):
        print(f"[!] 尚未建索引，先执行: replens index {repo_path}", file=sys.stderr)
        sys.exit(1)
    return IndexDB(db_path)


def _do_retrieve(issue, db, top_k, hybrid=False, execution_hints=None):
    """按需选择混合检索；embedding 不可用时回退关键字检索。"""
    if hybrid:
        emb = build_embedder()
        if emb is None:
            print("[!] 未配置 embedding（REPLENS_EMBED_BASE_URL/KEY 或本地 sentence-transformers），"
                  "回退关键字检索", file=sys.stderr)
            return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)
        return retrieve_hybrid(issue, db, top_k=top_k, embedder=emb,
                               execution_hints=execution_hints)
    return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)


def cmd_init(args) -> None:
    meta = import_repo(args.repo)
    d = meta.to_dict()
    if args.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(f"仓库: {d['name']} ({d['path']})")
        print(f"许可证: {d['license']}"
              f"{'（高置信）' if d['license_confident'] else '（低置信/未知）'}"
              f"  [文件: {d['license_file'] or '未找到'}]")
        print(f"依赖文件: {', '.join(d['dependency_files']) or '无'}")
        print(f"测试框架: {d['test_framework']}  测试目录: {', '.join(d['test_dirs']) or '无'}")
        print(f"Python 文件数: {len(d['python_files'])}")


def cmd_index(args) -> None:
    meta = import_repo(args.repo)
    db = build_index(meta, force=args.force_reindex)
    n = db.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    print(f"索引完成：{n} 个符号"
          f"（FTS5 {'可用' if db.fts_available else '降级为 LIKE'}）")
    db.close()


def cmd_search(args) -> None:
    db = _load_db(args.repo)
    from .issue_understanding import understand_and_retrieve
    evidences, insufficient, understanding = understand_and_retrieve(args.issue, db, args.top_k, hybrid=args.hybrid, repo_root=args.repo)
    if insufficient:
        print(understanding.explanation)
        db.close()
        return
    for i, e in enumerate(evidences, 1):
        print(f"{i}. {e.symbol.ref}  [{e.symbol.type}] {e.symbol.name}  "
              f"置信度={e.confidence}  ({e.source})")
        print(f"   理由: {e.reason}")
    db.close()


def cmd_plan(args) -> None:
    db = _load_db(args.repo)
    from .issue_understanding import understand_and_retrieve
    evidences, insufficient, understanding = understand_and_retrieve(args.issue, db, args.top_k, hybrid=args.hybrid, repo_root=args.repo)
    planner = build_planner()
    plan = planner.generate(args.issue, evidences)
    print(json.dumps(dict(plan.to_dict(), understanding=understanding.to_dict()), ensure_ascii=False, indent=2))
    db.close()


def cmd_run(args) -> None:
    """统一通过 replens.runner.run_repair 编排，CLI 只做参数解析与渲染。"""
    from .runner import RepairRequest, run_repair

    req = RepairRequest(
        repo=args.repo,
        issue=args.issue,
        top_k=args.top_k,
        hybrid=args.hybrid,
        test_cmd=args.test_cmd or "",
        auto_patch=args.auto_patch,
        patch=args.patch or "",
        timeout=args.timeout,
        force_reindex=args.force_reindex,
        extra_files={},
        acceptance_suite=args.acceptance_suite or "",
        model=getattr(args,"model", ""),
        behavior_examples=json.loads(Path(args.behavior_examples).read_text(encoding="utf-8")) if getattr(args,"behavior_examples",None) else [],
    )

    result = run_repair(req, build_provenance=None if args.no_provenance else build_provenance)

    out = render_markdown(result.report) if args.format == "md" else render_json(result.report)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"报告已写入: {args.output}")
    else:
        print(out)
    if result.provenance_dir:
        print(f"证据包已生成: {result.provenance_dir}",
              file=sys.stderr if args.format == 'json' else sys.stdout)
    if result.provenance_status['status'] == 'FAILED':
        print('证据包生成失败: ' + '；'.join(result.provenance_status['reasons']), file=sys.stderr)
        raise SystemExit(2)
    if args.require_verified and result.status != 'VERIFIED':
        print('修复验收未通过: ' + result.status + '；' + '；'.join(result.reasons), file=sys.stderr)
        raise SystemExit(2)


def _patch_target_paths(patch_file: str) -> List[str]:
    """从 JSON 补丁或 unified diff 中提取被修改文件的相对路径。"""
    paths = []
    if patch_file.lower().endswith(".json"):
        try:
            import json as _json
            data = _json.loads(Path(patch_file).read_text(encoding="utf-8"))
            paths = list(data.keys())
        except Exception:
            pass
    else:
        try:
            for line in Path(patch_file).read_text(encoding="utf-8").splitlines():
                if line.startswith("--- "):
                    p = line[4:].strip().split("\t")[0]
                    if p.startswith("a/"):
                        p = p[2:]
                    paths.append(p)
        except Exception:
            pass
    return paths


def cmd_mine(args) -> None:
    """自动挖掘修复提交生成评测集。"""
    result = mine_repo(args.repo, max_commits=args.max, validate=args.validate,
                       timeout=args.timeout, output=args.output,
                       era_env=getattr(args, "era_env", None))
    print(f"挖掘到 {result['mined']} 条候选样本；验证通过 {result['validated_ok']} 条")
    print(f"补丁目录: {result['patches_dir']}")
    if result.get("output"):
        print(f"数据集已写入: {result['output']}")
    for it in result["items"]:
        v = it.get("validation")
        mark = ("PASS" if (v and v["ok"]) else ("FAIL" if v else "未验证"))
        print(f"  [{mark}] {it['id']}: {it['issue'][:60]} -> {it['expected'][0]}")


def cmd_era_env(args) -> None:
    """查看/构建/清除时代环境（离线预置与排查用）。"""
    from . import era_env

    if args.list:
        print(f"时代环境根目录: {era_env.env_root()}")
        available = sorted(p.stem for p in era_env.LOCK_DIR.glob("*.txt"))
        print("可用 profile: " + ", ".join(available))
        built = era_env.built_environments()
        if not built:
            print("  （尚未构建任何环境；用 --profile 构建）")
        for entry in built:
            print("  %-12s %-6s urllib3=%-8s %s" % (entry["profile"], entry["tag"],
                                                    entry["config"] or "-", entry["python"]))
        print(f"当前解释器标签: {era_env.current_tag()}"
              f"（可用 REPLENS_ERA_PY_TAG 指定用哪一版环境）")
        return

    if not args.profile:
        raise SystemExit("需要 --profile（或使用 --list 查看可用 profile）")
    tag = era_env.interpreter_tag(args.python) if args.python else None
    if args.reset:
        print("已删除" if era_env.reset(args.profile, tag) else "不存在，无需删除")
        return

    runtime = []
    if args.repo and args.commit:
        import subprocess
        setup_py = subprocess.run(["git", "-C", args.repo, "show",
                                   args.commit + ":setup.py"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace").stdout
        runtime = era_env.runtime_specifiers(setup_py)
    environment = era_env.ensure(args.profile, runtime=runtime, base_python=args.python,
                                 timeout=args.timeout)
    print("profile: " + environment["profile"])
    print("python : " + environment["python"])
    print("新建   : " + ("是" if environment["created"] else "否（复用）"))
    for name in ("urllib3", "pytest", "pytest-httpbin"):
        version = (environment.get("packages") or {}).get(name)
        if version:
            print(f"{name:<14}: {version}")


def cmd_verify(args) -> None:
    """分别报告材料核验、重放一致性和统一修复结论。"""
    result = verify_provenance(args.repo, args.provenance, args.test_cmd or '', timeout=args.timeout)
    out = render_json(result) if args.format == 'json' else render_verification_markdown(result)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out, encoding='utf-8')
    else:
        print(out)
    if result["status"] != "PASS":
        raise SystemExit(1)
    if args.require_verified and result['repair_status'] != 'VERIFIED':
        raise SystemExit(2)


def cmd_compliance(args) -> None:
    """生成第三方依赖与开源许可披露报告。"""
    result = build_disclosure(args.repo)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"披露报告已写入: {result['report']}")
        print(f"识别到 {len(result['resources'])} 项第三方资源（使用清单与披露报告逐项对应）。")


def cmd_eval(args) -> None:
    from .eval_runner import run_eval
    try:
        result = run_eval(args.repo, args.dataset, top_k=args.top_k, hybrid=args.hybrid,
                          candidate_limit=args.candidate_limit, input_manifest=args.input_manifest,
                          sample_id=args.sample_id)
        if args.output:
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    except (ValueError, OSError) as exc:
        print('评测输入错误: ' + str(exc), file=sys.stderr)
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="replens", description="RepoLens-OSS 开源贡献辅助工作台")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="导入仓库并识别元信息")
    p_init.add_argument("repo")
    p_init.add_argument("--json", action="store_true")
    p_init.set_defaults(func=cmd_init)

    p_index = sub.add_parser("index", help="构建代码结构索引")
    p_index.add_argument("repo")
    p_index.add_argument("--force-reindex", action="store_true",
                         help="强制重建（忽略已有索引）")
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Issue 检索定位 + 证据")
    p_search.add_argument("repo")
    p_search.add_argument("--issue", required=True)
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--hybrid", action="store_true",
                        help="启用 BM25+语义向量 RRF 混合检索")
    p_search.set_defaults(func=cmd_search)

    p_plan = sub.add_parser("plan", help="生成修改计划")
    p_plan.add_argument("repo")
    p_plan.add_argument("--issue", required=True)
    p_plan.add_argument("--top-k", type=int, default=5)
    p_plan.add_argument("--hybrid", action="store_true",
                        help="启用 BM25+语义向量 RRF 混合检索")
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="完整闭环：定位→计划→测试验证→报告")
    p_run.add_argument("repo")
    p_run.add_argument("--issue", required=True)
    p_run.add_argument("--model", default="", help="覆盖本次模型选择，不改全局配置")
    p_run.add_argument("--behavior-examples", help="可选行为样例JSON数组文件；冻结用户预期，不由模型改写")
    p_run.add_argument("--top-k", type=int, default=5)
    p_run.add_argument("--hybrid", action="store_true",
                        help="启用 BM25+语义向量 RRF 混合检索")
    p_run.add_argument("--patch", help="候选补丁文件（unified diff 或 JSON）")
    p_run.add_argument("--auto-patch", action="store_true",
                        help="自动生成补丁并验证（LLM 可用时用 LLM，否则规则兜底）")
    p_run.add_argument("--test-cmd", help="测试命令（默认按框架自动）")
    p_run.add_argument("--acceptance-suite", help="仓库外的冻结独立行为验收 JSON（command/files）")
    p_run.add_argument("--require-verified", action="store_true",
                       help="仅 VERIFIED 返回 0；其他验收状态在写入报告后返回 2（用于 CI）")
    p_run.add_argument("--timeout", type=int, default=120)
    p_run.add_argument("--force-reindex", action="store_true",
                       help="强制重建索引（默认复用已有索引）")
    p_run.add_argument("--format", choices=["md", "json"], default="md")
    p_run.add_argument("--no-provenance", action="store_true",
                        help="不生成贡献证据包")
    p_run.add_argument("--output", help="报告输出文件路径")
    p_run.set_defaults(func=cmd_run)

    p_mine = sub.add_parser("mine", help="自动挖掘修复提交生成评测集")
    p_mine.add_argument("repo")
    p_mine.add_argument("--max", type=int, default=20, help="最大挖掘数量")
    p_mine.add_argument("--validate", action="store_true", help="在临时 worktree 中验证 fail→pass")
    p_mine.add_argument("--timeout", type=int, default=120)
    p_mine.add_argument("--era-env", nargs="?", const="auto", default=None,
                        help="按时代构造验证环境：auto=按父提交的依赖声明选择；"
                             "或指定 profile（如 urllib3-1）；缺省关闭")
    p_mine.add_argument("--output", help="数据集输出 JSON 路径")
    p_mine.set_defaults(func=cmd_mine)

    p_era = sub.add_parser("era-env", help="查看/构建/清除时代环境")
    p_era.add_argument("--list", action="store_true", help="列出可用 profile 与构建状态")
    p_era.add_argument("--profile", help="profile 名（如 urllib3-1）")
    p_era.add_argument("--repo", help="从该仓库读取 setup.py（配合 --commit）")
    p_era.add_argument("--commit", help="读取该提交的 install_requires 作为运行期依赖")
    p_era.add_argument("--python", help="构建 venv 用的基础解释器（默认当前解释器）")
    p_era.add_argument("--timeout", type=int, default=900)
    p_era.add_argument("--reset", action="store_true", help="删除该 profile 的环境")
    p_era.set_defaults(func=cmd_era_env)

    p_verify = sub.add_parser("verify", help="校验贡献证据包（哈希+重放测试）")
    p_verify.add_argument("repo")
    p_verify.add_argument("--provenance", required=True, help="证据包目录")
    p_verify.add_argument("--test-cmd", default='', help="可选；必须与证据包冻结主命令一致")
    p_verify.add_argument("--timeout", type=int, help="可选重放超时；默认沿用冻结值")
    p_verify.add_argument("--format", choices=['md', 'json'], default='md')
    p_verify.add_argument("--output", help="重放报告输出文件路径")
    p_verify.add_argument("--require-verified", action='store_true',
                          help="重放一致且修复 VERIFIED 才返回 0；修复未验证返回 2")
    p_verify.set_defaults(func=cmd_verify)

    p_comp = sub.add_parser("compliance", help="生成第三方依赖与许可合规披露报告")
    p_comp.add_argument("repo")
    p_comp.add_argument("--json", action="store_true")
    p_comp.set_defaults(func=cmd_compliance)

    p_eval = sub.add_parser("eval", help="跑评测集，输出命中率等指标")
    p_eval.add_argument("repo")
    p_eval.add_argument("--dataset", required=True)
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--candidate-limit", type=int, default=100, help="固定符号检索预算，各对照方法须一致")
    p_eval.add_argument("--input-manifest", default="", help="冻结允许输入清单；只从核验后的材料包建索引")
    p_eval.add_argument("--sample-id", default="", help="隔离评测的单个冻结样本 ID")
    p_eval.add_argument("--output", default="", help="另存含分子分母、实际输入哈希的 JSON 结果")
    p_eval.add_argument("--hybrid", action="store_true",
                        help="启用混合检索评测")
    p_eval.set_defaults(func=cmd_eval)

    return p


def _configure_stdio() -> None:
    """Windows 控制台默认 GBK 编码无法输出报告中的 emoji/Unicode 符号，导致演示崩溃。

    将标准流重配置为 UTF-8（errors=replace 兜底），保证中文 Windows 环境下
    replens run 直接打印 Markdown 报告不再抛 UnicodeEncodeError。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Python 早期版本或流已关闭/不支持 reconfigure 时忽略
            pass


def main(argv: Optional[List[str]] = None) -> None:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
