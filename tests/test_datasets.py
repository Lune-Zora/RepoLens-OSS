"""评测集契约回归：锁定 schema 与业务规则，防止数据口径退化。

背景（2026-10-28）：5 份数据集原本字段分两套——3 份只有定位字段，2 份含修复字段。
消费方无法从数据判断「能否做修复评测」，容易把定位样本误当修复分母。
本测试确保：schema 生效、修复字段全有或全无、干扰样本与正向样本的 expected 语义正确。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "eval" / "datasets"


class DatasetValidatorTests(unittest.TestCase):
    def test_all_datasets_pass_validation(self):
        """仓库内全部数据集必须通过 scripts/validate_datasets.py。"""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate_datasets.py"), "--quiet"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, "数据集校验失败:\n" + result.stdout + result.stderr)

    def test_manifest_covers_every_dataset_file(self):
        """manifest 必须声明 datasets 目录下每一个数据集文件，避免新集游离。"""
        manifest = json.loads((DATA_DIR / "datasets.manifest.json").read_text(encoding="utf-8"))
        declared = {entry["file"] for entry in manifest["datasets"]}
        # 以下文件不是数据集：清单本身、字段 schema、以及人工通读判定表。
        non_datasets = {"datasets.manifest.json", "dataset.schema.json", "人工复核判定.json"}
        actual = {p.name for p in DATA_DIR.glob("*.json") if p.name not in non_datasets}
        self.assertEqual(sorted(declared), sorted(actual))

    def test_repair_datasets_have_complete_repair_block(self):
        """修复集的 bug 样本必须同时具备 buggy_parent / fix_commit / test_cmd。"""
        manifest = json.loads((DATA_DIR / "datasets.manifest.json").read_text(encoding="utf-8"))
        required = ("buggy_parent", "fix_commit", "test_cmd")
        for entry in manifest["datasets"]:
            if entry["kind"] != "repair":
                continue
            items = json.loads((DATA_DIR / entry["file"]).read_text(encoding="utf-8"))
            for item in items:
                if item.get("category") == "noise":
                    continue
                missing = [f for f in required if f not in item]
                self.assertFalse(missing, f'{entry["file"]} 样本 {item.get("id")} 缺少 {missing}')

    def test_noise_items_have_empty_expected(self):
        """干扰样本的 expected 必须为空数组，否则会被误计为定位成功。"""
        manifest = json.loads((DATA_DIR / "datasets.manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["datasets"]:
            items = json.loads((DATA_DIR / entry["file"]).read_text(encoding="utf-8"))
            for item in items:
                if item.get("category") == "noise":
                    self.assertEqual(item["expected"], [], f'{entry["file"]} 样本 {item.get("id")} 干扰样本带了 expected')
