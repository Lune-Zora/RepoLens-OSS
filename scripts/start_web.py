"""从任意工作目录启动本地工作台，显式加载演示脚本的九项 LLM 配置。

用法：python scripts/start_web.py [--port 8000] [--env-file PATH]
普通 uvicorn 入口仍不自动读取 .env；本脚本是单独的配置加载入口。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.llm_env import apply_config, resolve_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="启动 RepoLens-OSS 本地 Web 工作台")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--model", default="", help="本次默认模型，也可在网页中选择其他已安装模型")
    parser.add_argument("--env-file", default=str(ROOT / ".env"))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")
    try:
        import uvicorn
    except ImportError:
        print("缺少 Web 依赖，请在项目根目录运行：")
        print("python -m pip install -c constraints.txt -e '.[web,test]'")
        return 1
    cfg = resolve_config(args.env_file)
    if args.model: cfg["REPLENS_LLM_MODEL"] = args.model
    apply_config(cfg)
    from datetime import datetime
    os.environ.setdefault("REPLENS_LLM_TRACE_DIR", str(ROOT / ".replens" / "web-model-requests" / datetime.now().strftime("%Y%m%d-%H%M%S")))
    os.chdir(ROOT)
    print(f"工作台：http://127.0.0.1:{args.port}/", flush=True)
    print(f"模型：{cfg['REPLENS_LLM_MODEL']}；地址：{cfg['REPLENS_LLM_BASE_URL']}", flush=True)
    print("服务启动后请保持本进程运行；Ctrl+C 停止。配置展示不等于推理验证。", flush=True)
    uvicorn.run("web.app:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
