"""把控制端产出打包为交付证据包（repo-control）。

存在原因（2026-10-28）：
  `repo-control/` 原本是手工整理的阶段 A 交付包，控制端脚本改动后无法确定性重建，
  且 `使用说明.txt` 描述的 `payload/` 目录实际不存在，包的还原流程是断的。
  本脚本把「打包」这一步脚本化：给定控制端输出目录，产出自校验的交付包。

分工：
  1. 跑控制端：python scripts/restore_tqdm_reset_inf.py --repo ... --work-dir ... --output <staging>/validation/tqdm-reset-inf
  2. 打包：    python scripts/build_delivery.py --staging <staging> --target <repo-control> --label <YYYYMMDD>

退出码：0 成功；1 输入不满足（例如控制端结果不是 PASS）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 交付包登记哈希的项目文件（相对仓库根）。改为显式清单，便于审查新增/删除。
MANIFEST_PATHS = (
    "replens/provenance.py",
    "replens/executor.py",
    "replens/miner.py",
    "replens/era_env.py",
    "replens/eval_metrics.py",
    "tests/test_provenance.py",
    "tests/test_restore_sample.py",
    "tests/test_env_contract.py",
    "tests/test_datasets.py",
    "tests/test_era_env.py",
    "tests/test_repair_script.py",
    "tests/test_module_hygiene.py",
    "tests/test_delivery_contract.py",
    "scripts/restore_tqdm_reset_inf.py",
    "scripts/run_ci.py",
    "scripts/check_env.py",
    "scripts/check_web.py",
    "scripts/validate_datasets.py",
    "scripts/build_delivery.py",
    "scripts/realbug/__init__.py",
    "scripts/realbug/cli.py",
    "scripts/realbug/common.py",
    "scripts/realbug/constants.py",
    "scripts/realbug/environment.py",
    "scripts/realbug/errors.py",
    "scripts/realbug/gitops.py",
    "scripts/realbug/pipeline.py",
    "scripts/realbug/suites.py",
    "scripts/realbug/upstream.py",
    "constraints.txt",
    "requirements-lock.txt",
    "eval/datasets/dataset.schema.json",
    "eval/datasets/datasets.manifest.json",
    "eval/envs/urllib3-1.txt",
    "eval/curation/issue_upgrade.json",
    "docs/05_测试/单样本真实Bug恢复.md",
    "docs/05_测试/证据包格式与重放.md",
    "docs/05_测试/评测口径总表.md",
    "docs/文档地图.md",
    "README.md",
    "ROADMAP.md",
    "project-os/项目状态.md",
    "变更日志.md",
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_control_run(control_dir):
    run_file = control_dir / "run.json"
    if not run_file.exists():
        raise SystemExit("找不到控制端结果: " + str(run_file))
    record = json.loads(run_file.read_text(encoding="utf-8"))
    if record.get("status") != "PASS":
        raise SystemExit("控制端结果不是 PASS（status=" + str(record.get("status"))
                         + ", failed_stage=" + str(record.get("failed_stage")) + "），拒绝打包")
    if not (control_dir / "evidence_bundle").is_dir():
        raise SystemExit("缺少证据包目录: " + str(control_dir / "evidence_bundle"))
    return record


def _build_manifest(repo_root, label, control):
    files = []
    missing = []
    for rel in MANIFEST_PATHS:
        path = repo_root / rel
        if not path.exists():
            missing.append(rel)
            continue
        files.append({"path": rel, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    if missing:
        raise SystemExit("MANIFEST_PATHS 中以下文件不存在: " + ", ".join(missing))
    replay = control.get("cross_worktree_replay", {})
    return {
        "label": label,
        "created_at_unix": control.get("completed_at_unix"),
        "source_root": str(repo_root),
        "scope": "控制端结果 + 项目文件哈希；控制材料不进入模型输入",
        "control_run": {
            "status": control.get("status"),
            "sample_id": control.get("sample_id"),
            "buggy_parent": control.get("buggy_parent"),
            "fix_commit": control.get("fix_commit"),
            "materials_status": replay.get("materials_status"),
            "replay_status": replay.get("replay_status"),
            "repair_status": replay.get("repair_status"),
            "model_repair_credit": control.get("model_repair_credit"),
        },
        "files": files,
    }


def _build_summary(manifest):
    control = manifest["control_run"]
    return {
        "label": manifest["label"],
        "status": control["status"],
        "sample_id": control["sample_id"],
        "materials_status": control["materials_status"],
        "replay_status": control["replay_status"],
        "repair_status": control["repair_status"],
        "model_repair_credit": control["model_repair_credit"],
        "tracked_files": len(manifest["files"]),
    }


USAGE_TEMPLATE = "阶段 B 交付包（{label}）\n\n本包由脚本生成，不手工整理。重建方式：\n\n1. 跑控制端，产出新的证据目录：\n\n    python scripts/restore_tqdm_reset_inf.py \\\n      --repo <含两个完整提交的 tqdm clone> \\\n      --work-dir <仓库外已存在的临时父目录> \\\n      --output <staging>/validation/tqdm-reset-inf\n\n2. 打包：\n\n    python scripts/build_delivery.py --staging <staging> --target <本目录> --label {label}\n\n## 包内结构\n\n| 路径 | 内容 | 使用边界 |\n|---|---|---|\n| validation/tqdm-reset-inf-{label}/ | 本轮控制端结果：run.json、Schema 2.0 证据包、原始日志、补丁、环境与许可材料 | 控制材料，禁止进入模型检索、提示词、候选生成或训练集 |\n| validation/attempt-history/ | 既往轮次结果，按标签保留，不覆盖 | 同上 |\n| manifest-{label}.json | 本轮登记的 {count} 个项目文件字节数与 SHA256，附控制端结论 | 用于校验包与仓库是否一致 |\n| delivery_summary-{label}.json | 本轮结论摘要 | 供快速核对 |\n\n## 与阶段 A 包的差异\n\n- 阶段 A 的 使用说明.txt 描述了一个 payload/ 目录，但该目录从未随包提供，\n  因此阶段 A 包无法一键还原。本版不再声明不存在的内容。\n- 阶段 A 只登记 21 个文件；本版登记 {count} 个，覆盖控制端拆分后的全部模块与新增测试。\n- 控制端脚本已做阶段化拆分（scripts/realbug/），入口 scripts/restore_tqdm_reset_inf.py 保持兼容。\n\n## 校验方式\n\n    python -c \"import json,hashlib,pathlib; m=json.load(open('manifest-{label}.json',encoding='utf-8')); print('不一致:', [f['path'] for f in m['files'] if hashlib.sha256(pathlib.Path(f['path']).read_bytes()).hexdigest()!=f['sha256']] or '无')\"\n\n## 边界\n\n- 结果来自参考生产补丁验证，model_repair_credit=false，不计入模型自主修复。\n- 仅覆盖同步 reset 行为，不代表上游全量测试通过。\n- 跨机器重放需相同 Python、平台与发行包版本，见 validation 下的 environment.json。\n"


def main():
    parser = argparse.ArgumentParser(description="打包 repo-control 交付证据")
    parser.add_argument("--staging", required=True, help="含 validation/tqdm-reset-inf 的目录")
    parser.add_argument("--target", required=True, help="交付包目录（repo-control）")
    parser.add_argument("--label", required=True, help="本轮标签，如 20261029")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写盘")
    args = parser.parse_args()

    staging = Path(args.staging).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    control_dir = staging / "validation" / "tqdm-reset-inf"

    control = _check_control_run(control_dir)
    manifest = _build_manifest(ROOT, args.label, control)
    summary = _build_summary(manifest)

    dest = target / "validation" / ("tqdm-reset-inf-" + args.label)
    manifest_path = target / ("manifest-" + args.label + ".json")
    summary_path = target / ("delivery_summary-" + args.label + ".json")
    usage_path = target / ("使用说明-" + args.label + ".md")

    print("控制端结论: status=%s materials=%s replay=%s repair=%s" % (
        summary["status"], summary["materials_status"],
        summary["replay_status"], summary["repair_status"]))
    print("登记文件: %d 个" % summary["tracked_files"])
    print("控制材料 -> " + str(dest))
    print("清单     -> " + str(manifest_path))
    print("摘要     -> " + str(summary_path))
    print("说明     -> " + str(usage_path))

    if args.dry_run:
        print("dry-run：未写入任何文件")
        return 0

    if dest.exists():
        raise SystemExit("目标已存在，拒绝覆盖: " + str(dest))
    shutil.copytree(control_dir, dest)

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    usage_path.write_text(USAGE_TEMPLATE.format(label=args.label, count=len(manifest["files"])),
                          encoding="utf-8")
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())