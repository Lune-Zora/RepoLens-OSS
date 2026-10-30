"""Strict LLM Demo evidence via standard CLI run and verify; preserves all artifacts."""
from __future__ import annotations
import argparse
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_env

DEFAULT_ISSUE = "date string with surrounding whitespace fails to parse"


def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                            encoding="utf-8", errors="replace")
    return result.stdout.strip() if result.returncode == 0 else None


def _source_files():
    excluded = {"__pycache__", ".pytest_cache", ".replens", ".git", ".venv"}
    files = set()
    for name in ("replens", "web", "scripts", "tests", "examples", "eval", ".github"):
        folder = ROOT / name
        for path in folder.rglob("*") if folder.exists() else ():
            if (path.is_file() and not excluded.intersection(path.relative_to(ROOT).parts)
                    and not path.name.startswith(".env") and path.suffix != ".pyc"):
                files.add(path)
    for name in ("pyproject.toml", "README.md", "LICENSE", "constraints.txt",
                 "requirements.txt", "requirements-lock.txt", ".env.example", ".gitattributes"):
        if (ROOT / name).is_file():
            files.add(ROOT / name)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def _run(label, command, env, output, commands):
    started = time.monotonic()
    print("[cmd] " + " ".join(map(str, command)), flush=True)
    try:
        result = subprocess.run(list(map(str, command)), cwd=ROOT, env=env,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=1200)
        code, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")
        code, stdout, stderr = 124, decode(exc.stdout), decode(exc.stderr)
    (output / (label + ".stdout.txt")).write_text(stdout, encoding="utf-8")
    (output / (label + ".stderr.txt")).write_text(stderr, encoding="utf-8")
    commands.append({"label": label, "argv": list(map(str, command)), "exit_code": code,
                     "seconds": round(time.monotonic() - started, 3)})
    print("[%s] exit=%s" % (label, code), flush=True)
    return code, stdout, stderr


def _trace_summary(directory, require_complete=True):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]
    if require_complete and (not rows or any(row.get("status") not in {"success", "error"} for row in rows)):
        raise ValueError("模型调用记录为空或存在未完成调用")
    phase_names = sorted({"plan", "patch"} | {str(row.get("purpose", "unspecified")) for row in rows})
    phases = {phase: sum(row.get("purpose") == phase for row in rows) for phase in phase_names}
    if require_complete and (not phases["plan"] or not phases["patch"]):
        raise ValueError("缺少计划或补丁的真实模型调用记录")
    rounds = [row.get("context", {}).get("generation_round", 0)
              for row in rows if row.get("purpose") == "patch"]
    usage = {key: sum((row.get("usage") or {}).get(key, 0) or 0 for row in rows)
             for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    return {"call_attempts": len(rows), "by_purpose": phases,
            "completed_json_calls": sum(row["status"] == "success" for row in rows),
            "error_calls": sum(row["status"] == "error" for row in rows),
            "incomplete_calls": sum(row.get("status") not in {"success", "error"} for row in rows),
            "patch_generation_rounds": max(rounds, default=0),
            "actual_repair_rounds": max(0, max(rounds, default=0) - 1),
            "call_seconds_sum": round(sum(row.get("duration_seconds", 0) for row in rows), 3),
            "provider_usage_sum": usage,
            "provider_usage_available": all(isinstance(row.get("usage"), dict) for row in rows),
            "response_models": sorted({row["response_model"] for row in rows if row.get("response_model")})}


def main(argv=None):
    from replens.patchgen import auto_patch_loop
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", default=DEFAULT_ISSUE)
    parser.add_argument("--model")
    parser.add_argument("--test-cmd", default="python -m pytest tests/test_parser.py")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--skip-check", action="store_true")
    parser.add_argument("--output-dir", help="本次新建目录；已有目录不会被覆盖")
    args = parser.parse_args(argv)
    cfg = llm_env.resolve_config(args.env_file)
    if args.model:
        cfg["REPLENS_LLM_MODEL"] = args.model
    cfg["REPLENS_REQUIRE_LLM"] = "1"
    env = dict(os.environ, **cfg, REPLENS_LLM_PROVIDER="", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    demo = ROOT / "examples" / "demo_repo"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (Path(args.output_dir).resolve() if args.output_dir else
              ROOT / "reports" / "llm" / ("demo-llm-" + stamp + "-" + uuid4().hex[:8]))
    if output == demo or demo in output.parents:
        parser.error("交付目录必须在被修复的 Demo 仓库之外")
    if output.exists():
        parser.error("输出目录已存在，拒绝覆盖: " + str(output))
    output.mkdir(parents=True)
    trace_dir = output / "model-requests"
    trace_dir.mkdir()
    env["REPLENS_LLM_TRACE_DIR"] = str(trace_dir)
    commands = []
    before = _source_files()
    summary = {"schema_version": 1, "status": "IN_PROGRESS", "started_at": stamp,
               "source_root": str(ROOT), "git_commit": _git("rev-parse", "HEAD"),
               "git_status_before": _git("status", "--short"), "python": sys.version,
               "settings": {k: v for k, v in cfg.items() if "KEY" not in k},
               "max_repair_rounds": int(cfg.get("REPLENS_LLM_MAX_REPAIR_ROUNDS", inspect.signature(auto_patch_loop).parameters["max_repair_rounds"].default)),
               "commands": commands, "raw_llm_requests_recorded": True}
    _save(output / "summary.json", summary)
    _save(output / "source-files-before.json", before)
    (output / "working-tree-before.diff").write_text(_git("diff", "--binary") or "", encoding="utf-8")
    with zipfile.ZipFile(output / "source-snapshot.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in before:
            archive.write(ROOT / relative, relative)
    print("产物目录: " + str(output), flush=True)
    try:
        prov_root = demo / ".replens" / "provenance"
        old_bundles = {p.resolve() for p in prov_root.iterdir()} if prov_root.exists() else set()
        if not args.skip_check:
            ok, detail = llm_env.check_endpoint(cfg)
            summary["endpoint_check"] = {"ok": ok, "detail": detail}
            if not ok:
                raise ValueError("模型服务不可连接: " + detail)
        code, stdout, stderr = _run("run", [sys.executable, "-m", "replens", "run", str(demo),
            "--issue", args.issue, "--test-cmd", args.test_cmd, "--auto-patch",
            "--acceptance-suite", str(ROOT / "examples" / "acceptance" / "demo.json"),
            "--require-verified", "--format", "json", "--output", str(output / "report.json")],
            env, output, commands)
        if code:
            raise ValueError("标准 CLI run 失败，退出码 " + str(code))
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        if (report.get("status") != "VERIFIED" or report.get("plan", {}).get("generated_by") != "llm"
                or report.get("provenance", {}).get("status") != "WRITTEN"):
            raise ValueError("报告未满足模型计划、VERIFIED 和证据包写入条件")
        if any(token in report.get("patch_note", "") for token in ("规则候选主测试筛选通过", "规则引擎生成")):
            raise ValueError("最终补丁来自规则，不能作为严格模型成功")
        paths = [line.split("证据包已生成:", 1)[1].strip()
                 for line in (stdout + "\n" + stderr).splitlines() if "证据包已生成:" in line]
        if len(paths) != 1:
            raise ValueError("CLI 未返回唯一的本次证据包路径")
        bundle = Path(paths[0]).resolve()
        if bundle in old_bundles:
            raise ValueError("CLI 返回了旧证据包，拒绝作为本次结果")
        if bundle.parent != (demo / ".replens" / "provenance").resolve():
            raise ValueError("CLI 证据包路径不在预期目录")
        frozen_report = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
        if frozen_report != report:
            raise ValueError("本次报告与证据包报告不一致")
        dest = output / "provenance"
        shutil.copytree(bundle, dest)
        shutil.copy2(dest / "report.md", output / "report.md")
        code, _, _ = _run("verify", [sys.executable, "-m", "replens", "verify", str(demo),
            "--provenance", str(dest), "--require-verified", "--format", "json",
            "--output", str(output / "replay.json")], env, output, commands)
        if code:
            raise ValueError("标准 CLI verify 失败，退出码 " + str(code))
        replay = json.loads((output / "replay.json").read_text(encoding="utf-8"))
        required = {"status": "PASS", "materials_status": "PASS", "replay_status": "PASS",
                    "repair_status": "VERIFIED", "validation_matches": True}
        if any(replay.get(key) != value for key, value in required.items()):
            raise ValueError("重放结果未同时满足材料、重放和修复通过")
        summary.update(status="PASS", repair_status=report["status"],
                       plan_generated_by=report["plan"]["generated_by"], patch_note=report["patch_note"],
                       replay=replay, model_calls=_trace_summary(trace_dir))
    except Exception as exc:
        summary.update(status="FAILED", error=str(exc))
        print("失败: " + str(exc), file=sys.stderr)
    finally:
        if "model_calls" not in summary:
            try:
                summary["model_calls"] = _trace_summary(trace_dir, require_complete=False)
            except (ValueError, OSError) as exc:
                summary["trace_error"] = str(exc)
        after = _source_files()
        _save(output / "source-files-after.json", after)
        changes = [p for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]
        summary["source_files_changed"] = changes
        if changes:
            summary.update(status="FAILED", error="执行期间源码发生变化")
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        _save(output / "summary.json", summary)
        lines = ["# 严格模型 Demo 验证", "", "最终状态：" + summary["status"], "",
                 "本目录通过标准 CLI run + verify 生成。实际命令和退出码见 summary.json；",
                 "调用原文、响应、错误、轮次和用量见 model-requests/。",
                 "源码为 source-snapshot.zip；工作区差异及源码前后哈希均保留。", "",
                 "模型调用汇总：", "```json", json.dumps(summary.get("model_calls"), ensure_ascii=False, indent=2),
                 "```", "", "此结果仅覆盖内置 Demo；源码注释含 strip 提示，不代表真实复杂 Bug 的自主修复能力。",
                 "报告内重放状态是打包时快照，最终以 replay.json 为准。",
                 "调用记录不包含鉴权头，并对配置的 API key 脱敏；其中包含任务代码和模型输出。",
                 "未校验模型权重摘要；未开展跨机器或多次稳定性实验。", ""]
        (output / "验证说明.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(output.rglob("*")) if p.is_file() and p != output / "delivery-manifest.json"}
        _save(output / "delivery-manifest.json", {"algorithm": "sha256", "files": manifest})
    return 0 if summary["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
