"""LLM 配置解析与连通性自检（零依赖、跨平台）。

目的：把「本机接口地址 / 模型名」变成可替换的运行期配置，避免写死进代码或文档。
优先级：真实环境变量 > .env 文件 > 代码内默认值。

用法：
    python scripts/llm_env.py                 # 打印解析后的配置并做连通性自检
    python scripts/llm_env.py --json          # 输出 JSON（供脚本消费）
    python scripts/llm_env.py --no-check      # 只看配置，不检查连通性
    python scripts/llm_env.py --env-file X    # 指定配置文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "REPLENS_LLM_BASE_URL": "http://127.0.0.1:11434/v1",
    "REPLENS_LLM_MODEL": "qwen35-9b-unc:latest",
    "REPLENS_LLM_API_KEY": "",
    "REPLENS_LLM_N_CANDIDATES": "2",
    "REPLENS_REQUIRE_LLM": "1",
    "REPLENS_LLM_TIMEOUT": "180",
    "REPLENS_LLM_MAX_TOKENS": "2200",
    "REPLENS_LLM_REASONING_EFFORT": "none",
    "REPLENS_LLM_MAX_REPAIR_ROUNDS": "1",
}
KEYS = tuple(DEFAULTS)


def parse_env_file(path) -> dict:
    """解析简单的 KEY=VALUE 配置文件；文件不存在时返回空字典。"""
    data = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, OSError):
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        data[key] = val
    return data


def resolve_config(env_file=None) -> dict:
    """按「环境变量 > .env > 默认值」解析配置。空字符串视为未设置。"""
    file_cfg = parse_env_file(env_file) if env_file else {}
    cfg = {}
    for key in KEYS:
        val = os.environ.get(key)
        if not val:
            val = file_cfg.get(key)
        if not val:
            val = DEFAULTS[key]
        cfg[key] = val
    return cfg


def apply_config(cfg: dict) -> dict:
    """把配置写回当前进程环境变量（供命令行子进程继承）。空值不写入。"""
    for key, val in cfg.items():
        if val:
            os.environ[key] = str(val)
    return cfg


def mask(value) -> str:
    if not value:
        return ""
    value = str(value)
    if len(value) <= 4:
        return "***"
    return value[:2] + "***" + value[-2:]


def check_endpoint(cfg: dict, timeout: int = 5):
    """GET {base}/models 探测 OpenAI 兼容服务是否可用。返回 (ok, detail)。"""
    base = (cfg.get("REPLENS_LLM_BASE_URL") or "").rstrip("/")
    if not base:
        return False, "未配置 REPLENS_LLM_BASE_URL"
    url = base + "/models"
    try:
        req = urllib.request.Request(url, method="GET")
        key = cfg.get("REPLENS_LLM_API_KEY") or ""
        if key:
            req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return False, "连接失败: " + str(exc)
    models = []
    try:
        payload = json.loads(body)
        items = payload.get("data") or payload.get("models") or []
        for item in items:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name")
                if name:
                    models.append(str(name))
    except (ValueError, AttributeError):
        pass
    detail = "可连接；模型: " + (", ".join(models) if models else "（未返回模型列表）")
    return True, detail


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="RepoLens LLM 配置自检")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-check", action="store_true")
    args = parser.parse_args(argv)

    cfg = resolve_config(args.env_file)
    if args.no_check:
        ok, detail = True, "已跳过连通性检查"
    else:
        ok, detail = check_endpoint(cfg)

    if args.json:
        shown = {k: (mask(v) if "KEY" in k else v) for k, v in cfg.items()}
        print(json.dumps({"config": shown, "reachable": ok, "detail": detail},
                         ensure_ascii=False, indent=2))
    else:
        print("LLM 配置（环境变量 > .env > 默认值）：")
        for key in KEYS:
            shown = mask(cfg[key]) if "KEY" in key else cfg[key]
            print("  " + key + " = " + str(shown))
        print("连通性: " + ("OK — " if ok else "FAIL — ") + detail)
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
