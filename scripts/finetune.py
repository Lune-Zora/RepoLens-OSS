"""可选 LoRA 训练脚本，默认基座 Qwen2.5-Coder-0.5B-Instruct。

它与正式 Demo 通过 Ollama 调用的 qwen2.5-coder:1.5b 分开配置。
本次提交未运行此训练脚本，也未使用它生成的适配器或合并权重。
REPLENS_FT_MODEL 可覆盖训练基座。

用法：
    $env:HF_ENDPOINT="https://hf-mirror.com"
    py scripts/finetune.py
输出：
    models/replens-qwen2.5-coder-0.5b-instruct-lora/（默认基座对应的输出目录）
"""
import json
import os
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import torch
from transformers import (AutoModelForCausalLM, AutoTokenizer,
                          TrainingArguments, Trainer)
from peft import LoraConfig, get_peft_model, TaskType

BASE = Path(__file__).resolve().parent.parent
MODEL_NAME = os.environ.get("REPLENS_FT_MODEL", "Qwen/Qwen2.5-Coder-0.5B-Instruct")
OUT_DIR = BASE / "models" / ("replens-" + MODEL_NAME.split("/")[-1].lower() + "-lora")

SYSTEM = ("你是开源仓库修复专家。基于给定的问题描述与代码证据，生成最小修改补丁。"
          "只允许修改源码文件，from 字段必须是证据代码中逐字符一致的原文。"
          '严格输出 JSON 对象：{"<相对路径>": {"from": "<原文>", "to": "<替换后>"}}。')


def load_items(path):
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def build_messages(item):
    user = ("问题描述：" + item["issue"] + "\n\n代码证据：\n" + item["evidence"]
            + "\n\n请输出补丁 JSON。")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
        {"role": "assistant", "content": item["output"]},
    ]


def main():
    train_items = load_items(BASE / "eval" / "training" / "training_data.jsonl")
    max_samples = int(os.environ.get("REPLENS_FT_MAX_SAMPLES", "0") or "0")
    if max_samples > 0:
        train_items = train_items[:max_samples]
        print("smoke mode: 使用", len(train_items), "条样本")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME, trust_remote_code=True, torch_dtype=torch.float16,
        device_map="auto")

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    def tokenize(item):
        msgs = build_messages(item)
        text = tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=False)
        tok = tokenizer(text, truncation=True, max_length=512)
        tok["labels"] = tok["input_ids"].copy()  # 因果语言模型：labels=input_ids
        return tok

    dataset = [tokenize(it) for it in train_items]

    # CPU 微调（无 CUDA 时的降级方案）：fp16 关闭，减少 epoch 与序列长度
    args = TrainingArguments(
        output_dir=str(BASE / "models" / "lora_checkpoint"),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1,
        num_train_epochs=1,
        learning_rate=2e-4,
        fp16=False,
        logging_steps=1,
        save_strategy="no",
        report_to=[],
        dataloader_num_workers=0,
    )
    trainer = Trainer(model=model, args=args, train_dataset=dataset)
    trainer.train()

    merged = model.merge_and_unload()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(OUT_DIR, safe_serialization=True)
    tokenizer.save_pretrained(OUT_DIR)
    print("saved merged model to", OUT_DIR)


if __name__ == "__main__":
    main()
