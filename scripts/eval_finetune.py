"""评测微调前/后模型在「证据 -> JSON 补丁」任务上的表现。

用法：
    py scripts/eval_finetune.py
输出：valid_json 率与 exact_match 率对比。
"""
import json
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

BASE = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(BASE))

SYSTEM = ("你是开源仓库修复专家。基于给定的问题描述与代码证据，生成最小修改补丁。"
          "只允许修改源码文件，from 字段必须是证据代码中逐字符一致的原文。"
          '严格输出 JSON 对象：{"<相对路径>": {"from": "<原文>", "to": "<替换后>"}}。')


def build_user(item):
    return ("问题描述：" + item["issue"] + "\n\n代码证据：\n" + item["evidence"]
            + "\n\n请输出补丁 JSON。")


def eval_model(client, items):
    valid = exact = 0
    for it in items:
        try:
            data = client.chat_json(SYSTEM, build_user(it), temperature=0.2)
        except Exception:
            data = None
        if isinstance(data, dict) and data:
            valid += 1
            gold = json.loads(it["output"])
            if json.dumps(data, sort_keys=True, ensure_ascii=False) == \
               json.dumps(gold, sort_keys=True, ensure_ascii=False):
                exact += 1
    return valid, exact, len(items)


def main():
    from replens.llm import TransformersLocalLLM
    items = []
    with open(BASE / "eval" / "training" / "heldout_data.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    base = TransformersLocalLLM("Qwen/Qwen2.5-Coder-0.5B-Instruct")
    v1, e1, n = eval_model(base, items)
    print(f"base(0.5B): valid_json={v1}/{n}, exact_match={e1}/{n}")
    del base

    ft_path = BASE / "models" / "replens-qwen2.5-coder-0.5b-instruct-lora"
    ft = TransformersLocalLLM(str(ft_path))
    v2, e2, n2 = eval_model(ft, items)
    print(f"finetuned(0.5B): valid_json={v2}/{n2}, exact_match={e2}/{n2}")
    del ft


if __name__ == "__main__":
    main()
