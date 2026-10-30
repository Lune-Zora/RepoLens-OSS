"""评测集校验：按 dataset.schema.json 与 datasets.manifest.json 校验全部数据集。

用法：
    python scripts/validate_datasets.py            # 校验并打印覆盖表
    python scripts/validate_datasets.py --quiet    # 只报错误

退出码：0 全部通过；1 存在违规。

存在原因（2026-10-28）：5 份数据集的字段原本分成两套——
3 份只有 id/category/issue/expected，2 份多出 project/buggy_parent/fix_commit/test_cmd。
消费方无法从数据本身判断「这份能不能做修复评测」，容易把定位样本误当修复分母。
本脚本把该区分显式化：schema 定义字段，manifest 声明数据集种类，业务规则校验种类与字段是否自洽。

复用：标注格式解析直接调用 replens.eval_metrics.parse_expected，不重复实现。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replens.eval_metrics import parse_expected  # noqa: E402

DATA_DIR = ROOT / "eval" / "datasets"
# 修复信息块：缺任一项就不能还原 buggy 基线，也就不能做端到端修复评测。
# project 只是标识字段，不属于该块——修复集与非修复集都可以有。
REPAIR_FIELDS = ("buggy_parent", "fix_commit", "test_cmd")


def _type_ok(value, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    return True


def validate_schema(value, schema: dict, path: str) -> list[str]:
    """JSON Schema 子集校验：type/required/properties/additionalProperties/enum/minLength/pattern/items/minimum。"""
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None:
        names = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_ok(value, n) for n in names):
            errors.append(f"{path}: 类型应为 {'/'.join(names)}，实际 {type(value).__name__}")
            return errors

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 取值 {value!r} 不在允许集合 {schema['enum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: 长度 {len(value)} 小于 {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: 不匹配模式 {schema['pattern']}: {value!r}")

    if isinstance(value, int) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errors.append(f"{path}: {value} 小于最小值 {schema['minimum']}")

    if isinstance(value, list) and "items" in schema:
        for index, entry in enumerate(value):
            errors.extend(validate_schema(entry, schema["items"], f"{path}[{index}]"))

    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: 缺少必需字段 {key}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: 出现未声明字段 {key}")
        for key, sub in props.items():
            if key in value:
                errors.extend(validate_schema(value[key], sub, f"{path}.{key}"))

    return errors


def validate_rules(items: list, kind: str) -> list[str]:
    """业务规则：JSON Schema 无法表达的种类与字段自洽性。"""
    errors: list[str] = []
    seen: dict[str, int] = {}

    for index, item in enumerate(items, 1):
        label = f"样本 {item.get('id', index)}"

        key = str(item.get("id"))
        if key in seen:
            errors.append(f"{label}: id 与样本 {seen[key]} 重复")
        else:
            seen[key] = index

        category = item.get("category")
        expected = item.get("expected", [])

        if category == "noise" and expected:
            errors.append(f"{label}: 干扰样本的 expected 必须为空数组，实际 {expected}")
        if category in {"bug", "feature"} and not expected:
            errors.append(f"{label}: {category} 样本的 expected 不能为空")

        for entry in expected:
            try:
                parse_expected(entry)
            except ValueError as exc:
                errors.append(f"{label}: expected 标注无效 {entry!r}（{exc}）")

        if item.get("fail_pass") is False:
            errors.append(f"{label}: fail_pass 为 false——重验未通过的样本不得留在数据集中")
        if item.get("fail_pass") and not (item.get("verified_at") and item.get("env_profile")):
            errors.append(f"{label}: fail_pass 为 true 但缺少 verified_at / env_profile，"
                          f"无法说明何时、用哪一代环境验过")
        if item.get("issue_quality") == "weak" and item.get("fail_pass") is None:
            # 不算错误：弱描述是允许的，但必须能被统计到（见覆盖表最后一列）。
            pass

        present = [f for f in REPAIR_FIELDS if f in item]
        is_noise = category == "noise"

        if is_noise:
            continue

        if kind == "repair":
            if len(present) != len(REPAIR_FIELDS):
                missing = [f for f in REPAIR_FIELDS if f not in item]
                errors.append(f"{label}: 修复集样本缺少 {missing}，不能计入修复评测")
        else:
            if present and len(present) != len(REPAIR_FIELDS):
                errors.append(f"{label}: 修复字段必须全有或全无，当前只有 {present}")

    # 重验证据必须「全有或全无」：只要有一条修复样本带 fail_pass，
    # 就说明这份数据集按新口径维护过，不能再有样本悄悄缺证据（2026-10-29）。
    fixable = [i for i in items
               if i.get("category") in {"bug", "feature"} and i.get("fix_commit")
               and i.get("buggy_parent")]
    flags = [bool(i.get("fail_pass")) for i in fixable]
    if flags and not all(flags):
        errors.append("fail_pass 覆盖不全：%d 条修复样本缺重验记录（缺 %s）"
                      % (flags.count(False),
                         [i.get("id") for i, f in zip(fixable, flags) if not f][:5]))

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 eval/datasets 下的全部评测集")
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    args = parser.parse_args()

    schema = json.loads((DATA_DIR / "dataset.schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((DATA_DIR / "datasets.manifest.json").read_text(encoding="utf-8"))

    problems: dict[str, list[str]] = {}
    rows: list[tuple[str, str, str, int, int, int, int, str]] = []

    for entry in manifest["datasets"]:
        file_name = entry["file"]
        kind = entry["kind"]
        path = DATA_DIR / file_name

        if not path.exists():
            problems[file_name] = [f"清单声明的文件不存在: {path}"]
            continue

        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems[file_name] = [f"JSON 解析失败: {exc}"]
            continue

        errors = validate_schema(items, {"type": "array", "items": schema}, file_name)
        errors += validate_rules(items, kind)
        if errors:
            problems[file_name] = errors

        bugs = sum(1 for i in items if i.get("category") in {"bug", "feature"})
        noise = sum(1 for i in items if i.get("category") == "noise")
        repair_ready = sum(1 for i in items if all(f in i for f in REPAIR_FIELDS))
        weak = sum(1 for i in items if i.get("issue_quality") == "weak")
        rows.append((file_name, entry["kind"], entry["scope"], len(items), bugs, repair_ready,
                     weak, entry["name"]))

    if not args.quiet:
        print("数据集覆盖表")
        print(f"{'文件':<28}{'种类':<14}{'范围':<7}{'样本':>5}{'正向':>5}{'可修复':>7}{'弱描述':>6}  名称")
        for file_name, kind, scope, total, bugs, repair_ready, weak, name in rows:
            print(f"{file_name:<28}{kind:<14}{scope:<7}{total:>5}{bugs:>5}{repair_ready:>7}"
                  f"{weak:>6}  {name}")
        print()

        localization_total = sum(r[4] for r in rows if r[1] == "localization")
        repair_total = sum(r[5] for r in rows if r[1] == "repair")
        weak_total = sum(r[6] for r in rows)
        print(f"可定位评测的正向样本合计: {localization_total + repair_total}")
        print(f"可做端到端修复评测的样本合计: {repair_total}")
        print(f"其中弱描述样本: {weak_total}（保留在分母内，引用指标时须同时给出条数）")
        print("注意：两项分母口径不同，不得混算成同一个成功率。")
        print()

    if problems:
        print("校验未通过：")
        for file_name, errors in problems.items():
            print(f"  {file_name}")
            for error in errors[:20]:
                print(f"    - {error}")
            if len(errors) > 20:
                print(f"    ... 另有 {len(errors) - 20} 条")
        return 1

    if not args.quiet:
        print(f"校验通过：{len(rows)} 份数据集全部符合 schema 与业务规则")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
