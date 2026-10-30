"""生成补丁生成任务的指令微调数据（jsonl）。

数据来源：
- demo 仓库 6 个内置 bug（含正确补丁）
- tqdm total=inf、pypdf outline 折叠两个真实修复
- 8 个合成的 strip 类变体（数据增强，覆盖不同函数名/参数名）

输出：eval/training/training_data.jsonl（train）与 eval/training/heldout_data.jsonl（评测留出集）
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

def mk(issue, evidence, patch):
    return {"issue": issue, "evidence": evidence,
            "output": json.dumps(patch, ensure_ascii=False)}

def strip_patch(path, return_line, arg):
    return {path: {
        "from": "    " + return_line,
        "to": "    " + arg + " = " + arg + ".strip()\n    " + return_line,
    }}

train = []
heldout = []

# ---- demo 仓库 6 个 bug ----
train.append(mk(
    "date string with surrounding whitespace fails to parse",
    "src/dateparse/parser.py:9 function parse_date",
    {"src/dateparse/parser.py": {
        "from": '    return datetime.datetime.strptime(s, "%Y-%m-%d").date()',
        "to": '    s = s.strip()\n    return datetime.datetime.strptime(s, "%Y-%m-%d").date()'}}))

train.append(mk(
    "whitespace around the date makes is_valid_date fail",
    "src/dateparse/validator.py:8 function is_valid_date",
    {"src/dateparse/validator.py": {
        "from": "        parse_date(s)",
        "to": "        s = s.strip()\n        parse_date(s)"}}))

train.append(mk(
    "days_between returns a negative value when a < b",
    "src/dateparse/utils.py:5 function days_between",
    {"src/dateparse/utils.py": {
        "from": "    return (da - db).days",
        "to": "    return abs((da - db).days)"}}))

train.append(mk(
    "is_leap_year wrongly treats 1900 as a leap year",
    "src/dateparse/utils.py:30 function is_leap_year",
    {"src/dateparse/utils.py": {
        "from": "    if year % 100 == 0:\n        return True",
        "to": "    if year % 100 == 0:\n        return False"}}))

heldout.append(mk(
    "format_date renders single-digit months as 2024-1-5",
    "src/dateparse/formatter.py:5 function format_date",
    {"src/dateparse/formatter.py": {
        "from": '    return f"{d.year}-{d.month}-{d.day}"',
        "to": '    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"'}}))

heldout.append(mk(
    "validate_range rejects equal start and end dates",
    "src/dateparse/validator.py:29 function validate_range",
    {"src/dateparse/validator.py": {
        "from": "    return s < e",
        "to": "    return s <= e"}}))

# ---- 真实仓库修复 ----
train.append(mk(
    "handle total=inf in format_meter to avoid OverflowError",
    "tqdm/std.py:463 function format_meter",
    {"tqdm/std.py": {
        "from": "        if total and n >= (total + 0.5):  # allow float imprecision (#849)",
        "to": '        if total == float("inf"):\n            # infinite total: behave the same as unknown total (#651)\n            total = None\n        elif total and n >= (total + 0.5):  # allow float imprecision (#849)'}}))

train.append(mk(
    "Make is_open=False collapse outline items",
    "pypdf/_writer.py:1779 function add_outline_item_destination",
    {"pypdf/_writer.py": {
        "from": "            page_destination.inc_parent_counter_outline\n            if is_open\n            else (lambda x, y: 0),  # noqa: ARG005",
        "to": "            page_destination.inc_parent_counter_outline"}}))

# ---- 合成 strip 变体（数据增强）----
variants = [
    ("parse_input", "src/app.py:12", "text", "return normalize(text)"),
    ("clean_name", "src/user.py:5", "name", "return lookup(name)"),
    ("parse_number", "src/calc.py:3", "s", "return int(s)"),
    ("normalize_email", "src/mail.py:8", "addr", "return validate(addr)"),
    ("parse_path", "src/fs.py:20", "p", "return resolve(p)"),
    ("parse_id", "src/db.py:15", "raw", "return int(raw)"),
    ("trim_query", "src/api.py:33", "q", "return execute(q)"),
    ("parse_date2", "src/dt.py:10", "value", "return parse(value)"),
]
for func, path, arg, ret in variants:
    train.append(mk(
        func + " fails when " + arg + " has surrounding whitespace",
        path + " function " + func,
        strip_patch(path, ret, arg)))

out1 = BASE / "eval" / "training" / "training_data.jsonl"
out2 = BASE / "eval" / "training" / "heldout_data.jsonl"
out1.parent.mkdir(parents=True, exist_ok=True)
with open(out1, "w", encoding="utf-8") as f:
    for item in train:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
with open(out2, "w", encoding="utf-8") as f:
    for item in heldout:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
print("train:", len(train), "heldout:", len(heldout))
print("written:", out1, out2)
