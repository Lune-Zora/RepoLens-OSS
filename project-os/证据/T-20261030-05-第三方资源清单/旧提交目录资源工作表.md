# 开源及第三方资源使用清单

（按《AIC·AI+开源竞赛规则及作品提交要求》附件表头填写）

| 序号 | 资源名称及类型 | 版本/来源 | 许可证/授权类型 | 源码路径 | 使用场景 | 关键义务/限制 | 团队自主修改或开发内容 | 合规状态 |
|---|---|---|---|---|---|---|---|---|
| 1 | fastapi（软件库） | fastapi>=0.110 / pyproject.toml | MIT | pyproject.toml（[project.optional-dependencies] web） | 可选 Web 层（web/app.py 的 /api/analyze、/api/run） | 保留版权与许可声明 | 无修改，仅调用 | 待核验 |
| 2 | uvicorn（软件库） | uvicorn>=0.29 / pyproject.toml | BSD-3-Clause | pyproject.toml（[project.optional-dependencies] web） | 可选 Web 层运行服务器 | 保留版权与许可声明 | 无修改，仅调用 | 待核验 |
| 3 | fastembed（模型推理框架） | pip 包 / Apache-2.0 | Apache-2.0 | replens/retriever_hybrid.py | 混合检索本地向量推理 | 保留 LICENSE/NOTICE | 无修改，仅调用 | 待核验 |
| 4 | BAAI/bge-small-en-v1.5（向量模型） | Hugging Face / MIT | MIT | replens/retriever_hybrid.py（默认模型名） | Issue 语义编码 | 保留模型许可声明 | 无修改，仅推理 | 待核验 |
| 5 | pytest（测试框架） | PyPI / MIT | MIT | tests/、.github/workflows/ci.yml | 单元测试与评测执行 | 保留版权声明 | 无修改 | 待核验 |
| 6 | SQLite FTS5（检索引擎） | Python 标准库 / Public Domain | Public Domain | replens/indexer.py | 符号全文索引 | 无 | 无修改 | 本次实际使用 |
| 7 | Ollama（本地推理服务） | 0.34.4 / MIT | MIT | replens/llm.py | 本地 LLM 推理（可选） | 保留版权与许可声明 | 无修改，仅调用 | 待核验 |
| 8 | Qwen2.5-Coder-1.5B-Instruct（LLM 模型） | Ollama/ModelScope / Apache-2.0 | Apache-2.0 | replens/llm.py（默认模型名） | 计划与补丁生成（可选） | 保留 LICENSE/NOTICE | 无修改，仅推理 | 待核验 |
| 9 | sentence-transformers（embedding 后端） | pip 包 / Apache-2.0 | Apache-2.0 | replens/retriever_hybrid.py（备选后端） | 混合检索本地向量推理（备选） | 保留 LICENSE/NOTICE | 无修改，仅调用 | 待核验 |
| 10 | tqdm（上游开源项目源码） | repos/tqdm_buggy、repos/tqdm / MPL-2.0 | MPL-2.0 | repos/tqdm_buggy/、repos/tqdm/、eval/datasets/real_dataset_tqdm.json | 真实仓库评测与 PR-ready 补丁（total=inf，上游 #651） | 保留版权与许可声明；修改需按 MPL-2.0 说明（本项目仅生成补丁，未修改上游分发） | 评测工作区；补丁为本项目产出，参照上游提交 ef15578 | 待核验 |
| 11 | pypdf（上游开源项目源码） | repos/pypdf_buggy、repos/pypdf / BSD-3-Clause | BSD-3-Clause | repos/pypdf_buggy/、repos/pypdf/、eval/datasets/real_dataset_pypdf.json | 真实仓库评测与 PR-ready 补丁（outline 折叠，上游 #2994） | 保留版权与许可声明 | 评测工作区；补丁为本项目产出，参照上游提交 db93ea65 | 待核验 |
| 12 | Qwen2.5-Coder-0.5B-Instruct（LLM 模型，LoRA 基座） | Hugging Face Qwen/Qwen2.5-Coder-0.5B-Instruct / Apache-2.0 | Apache-2.0 | scripts/finetune.py（MODEL_NAME）、models/replens-qwen2.5-coder-0.5b-instruct-lora/ | LoRA 微调基座（0.5B smoke 训练与评测） | 保留 LICENSE/NOTICE | 无修改，仅训练/推理；LoRA 适配器权重为本项目产出 | 待核验 |

填写说明：本项目核心包 replens/ 零第三方运行时依赖（纯 Python 标准库）；
上表列出可选功能、评测与微调所使用资源，两份报告（本清单与 reports/compliance/third_party_disclosure.md）均由同一资源登记表生成、逐项对应。
