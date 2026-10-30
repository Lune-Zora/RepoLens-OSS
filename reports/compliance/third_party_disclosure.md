# 第三方依赖与开源许可披露报告

仓库: RepoLens-OSS

本报告由 RepoLens-OSS 离线生成，用于竞赛作品形式审查中的「开源及第三方资源披露、知识产权与许可合规」要求。

## 依赖清单

| 序号 | 资源名称及类型 | 版本/来源 | 许可证 | 源码路径 | 使用场景 | 关键义务/风险 | 合规状态 |
|---|---|---|---|---|---|---|---|---|
| 1 | Python（解释器/标准库） | 3.10.11；python-installed-LICENSE.txt；官方docs.python.org/3.10/license.html | PSF及所附历史许可 | pyproject.toml | 运行环境；源码包不捆绑解释器 | 解释器或组件再分发按其完整LICENSE保留声明；本次仅要求用户安装 | 已核验 |
| 2 | SQLite FTS5（组件） | 3.40.1；Python sqlite3；官方sqlite.org/copyright.html | Public Domain（SQLite官方声明） | replens/indexer.py | 默认检索；不独立再分发SQLite二进制 | SQLite本体为公有领域；外层Python许可仍单独适用 | 已核验 |
| 3 | fastapi（软件库/工具） | 0.141.1；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web声明依赖；用户安装，不在源码包捆绑 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 4 | uvicorn（软件库/工具） | 0.54.0；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web声明依赖；用户安装，不在源码包捆绑 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 5 | pytest（软件库/工具） | 9.1.1；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | 测试声明依赖；用户安装，不捆绑 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 6 | httpx（软件库/工具） | 0.27.2；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | 测试声明依赖；用户安装，不捆绑 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 7 | httpcore（软件库/工具） | 1.0.9；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 8 | anyio（软件库/工具） | 4.15.1；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web声明依赖；用户安装，不在源码包捆绑 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 9 | starlette（软件库/工具） | 1.7.0；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 10 | pydantic（软件库/工具） | 2.13.5；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 11 | pydantic-core（软件库/工具） | 2.46.5；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 12 | typing_extensions（软件库/工具） | 4.16.0；安装元数据及LICENSE，详见核验索引 | PSF-2.0 | pyproject.toml | Web声明依赖；用户安装，不在源码包捆绑 | 保留完整许可与版权；按所选许可满足再分发条件 | 已核验 |
| 13 | typing-inspection（软件库/工具） | 0.4.4；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 14 | annotated-types（软件库/工具） | 0.8.0；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 15 | annotated-doc（软件库/工具） | 0.0.5；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 16 | sniffio（软件库/工具） | 1.3.1；安装元数据及LICENSE，详见核验索引 | MIT OR Apache-2.0 | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 17 | idna（软件库/工具） | 3.20；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 18 | certifi（软件库/工具） | 2026.7.22；安装元数据及LICENSE，详见核验索引 | MPL-2.0 | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑certifi/证书包；若分发或修改受MPL覆盖文件，保留声明并履行对应源码/许可义务 | 已核验 |
| 19 | exceptiongroup（软件库/工具） | 1.3.1；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 20 | click（软件库/工具） | 8.5.0；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 21 | h11（软件库/工具） | 0.14.0；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 22 | colorama（软件库/工具） | 0.4.6；安装元数据及LICENSE，详见核验索引 | BSD-3-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 23 | packaging（软件库/工具） | 24.1；安装元数据及LICENSE，详见核验索引 | Apache-2.0 OR BSD-2-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 24 | pluggy（软件库/工具） | 1.6.0；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 25 | iniconfig（软件库/工具） | 2.3.0；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 26 | pygments（软件库/工具） | 2.21.0；安装元数据及LICENSE，详见核验索引 | BSD-2-Clause | pyproject.toml | Web/测试传递依赖；通过安装获取，未捆绑二进制 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 27 | setuptools（软件库/工具） | 80.10.2；安装元数据及LICENSE，详见核验索引 | MIT | pyproject.toml | 构建工具；wheel已构建，本次不捆绑工具 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 28 | Ollama（推理服务） | 0.34.4（本机命令）；官方仓库LICENSE当前main已存档 | MIT | replens/llm.py | 本地HTTP推理服务；不分发Ollama软件 | MIT软件副本/实质部分保留版权与许可；具体模型许可另行核对 | 已核验 |
| 29 | 本地Ollama推理模型（GGUF） | 历史qwen2.5-coder:1.5b；本轮qwen25-7b-unc:latest、qwen35-9b-unc:latest；摘要见T-20261030-09模型记录 | 各本地GGUF的原始来源、转换链路及适用许可待核验 | replens/llm.py | 本地问题理解、测试设计、计划及补丁推理；不随源码或材料分发权重 | 按实际来源与适用许可核对声明和再分发义务；不把标签名称或官方同名许可等同于本机转换物已核验 | 待核验 |
| 30 | tqdm（上游评测源码/补丁来源） | 实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json | MPL-2.0 AND MIT | eval/datasets | 本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库 | 按tqdm LICENCE的文件/贡献归属适用MPL和MIT；原样保留；分发受MPL源码须按对应条款，不统一改为MIT | 已核验 |
| 31 | pypdf（上游评测源码/补丁来源） | 实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json | BSD-3-Clause | eval/datasets | 本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 32 | loguru（上游评测源码/补丁来源） | 实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json | MIT | eval/datasets | 本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 33 | click（上游评测源码/补丁来源） | 实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json | BSD-3-Clause | eval/datasets | 本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 34 | requests（上游评测源码/补丁来源） | 实际候选buggy_parent的LICENSE/NOTICE已从本地Git提取；historical-license-index.json | Apache-2.0 | eval/datasets | 本地评测；源码包保留必要参考补丁/证据，不捆绑完整上游仓库 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 35 | 历史Issue/PR文本与描述记录（数据） | eval/datasets、eval/curation；原始出处与日期记录 | 不能从仓库软件许可自动推定所有讨论文本授权 | eval/datasets | 保留来源；未作为本轮训练输入；公开分发应先核查逐条授权/最小引用 | 公开可读不等于任意训练/改编/再分发；保留原链接，未明确授权的长段文本限评审用途 | 待核验 |
| 36 | fastembed（可选/历史后端） | 0.8.1；本机元数据及原始许可 | Apache-2.0 | replens/retriever_hybrid.py | 仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 37 | torch（可选/历史后端） | 2.14.0；本机元数据及原始许可 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | scripts/finetune.py | 仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包 | 按torch原始许可及内含第三方通知逐项保留；本次不分发其二进制 | 已核验 |
| 38 | transformers（可选/历史后端） | 5.17.0；本机元数据及原始许可 | Apache-2.0 | scripts/finetune.py | 仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 39 | peft（可选/历史后端） | 0.21.0；本机元数据及原始许可 | Apache-2.0 | scripts/finetune.py | 仅可选/历史代码；本轮无向量或微调效果成绩，不捆绑安装包 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 已核验 |
| 40 | BAAI/bge-small-en-v1.5（可选模型） | 官方模型卡license:mit已存档；本轮未运行、未确定本地权重摘要 | MIT（官方模型卡） | replens/retriever_hybrid.py | 本轮实验不启用；不分发权重 | 未来启用时固定版本与来源，权重分发保留适用许可声明 | 不适用 |
| 41 | sentence-transformers（可选后端） | 当前解释器未安装 | 本轮不对未使用版本作许可核验 | replens/retriever_hybrid.py | 本轮未使用，不打包该依赖 | 未来启用前固定版本及核验许可 | 不适用 |
| 42 | Qwen2.5-Coder-0.5B-Instruct（可选训练基座） | 官方LICENSE已存档；本轮未加载或训练 | Apache-2.0（官方） | scripts/finetune.py | 训练脚本默认配置；本次不分发权重/适配器，不报微调收益 | 再分发附Apache-2.0，保留适用声明/NOTICE，修改须标注；不授予商标权 | 不适用 |
| 43 | python-docx（材料处理工具） | 1.2.0；安装许可原文已归档 | MIT | docs/02_架构/技术报告.docx | 仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具 | 本次不捆绑库安装包；若再分发其代码/实质部分，保留版权、许可证及免责声明 | 已核验 |
| 44 | lxml（材料处理工具） | 6.1.3；安装许可原文已归档 | BSD-3-Clause及LICENSES所列组件 | docs/02_架构/技术报告.docx | 仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 45 | pypdf（材料处理工具） | 6.19.0；安装许可原文已归档 | BSD-3-Clause | docs/02_架构/技术报告.docx | 仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具 | 不捆绑安装包；再分发须保留版权、条件与免责声明；BSD-3另不得用作者名背书 | 已核验 |
| 46 | PyMuPDF（材料处理工具） | 1.28.2；安装许可原文已归档 | AGPL-3.0或Artifex商业许可（二选一） | docs/02_架构/技术报告.docx | 仅辅助生成或检查材料；不是作品运行时必需，不捆绑工具 | 仅本地读取/渲染PDF，不捆绑库或提供其网络服务；若将库/衍生应用分发或服务化，另核对AGPL/商业授权义务，不套用项目MIT | 已核验 |
| 47 | Microsoft Word（文档工具） | 本机COM版本14.0；用户提供模板由Word读取 | 专有软件许可/EULA | docs/02_架构/技术报告.docx | 仅本机打开、导出PDF；不再分发Word | 由使用者确认设备/账号有效授权；不分发软件、字体文件或模板原件 | 待核验 |
| 48 | Codex/OpenAI（生成式开发辅助服务） | 本次开发会话；具体账号/订阅适用合同由使用者确认 | 服务条款；不是开源代码许可 | project-os | 辅助代码分析、修改建议/实现、调试验证和材料整理；不是项目运行依赖 | 准确披露AI辅助环节；团队复核输出正确性、来源与权利；不把AI生成部分一概称为纯人工 | 待核验 |
| 49 | Git（版本控制工具） | git version 2.54.0.windows.1 | GPL-2.0（Git软件；当前仅工具使用） | CONTRIBUTING.md | 本地版本管理与读取上游提交，不捆绑Git | 若再分发Git软件另遵其许可；不把工具许可等同于作品许可 | 已核验 |

## 说明

1. 本报告与 reports/compliance/open_source_checklist.md 由同一资源登记表生成，条目数完全一致。
2. 核心包 replens/ 零第三方依赖（纯 Python 标准库），Web 层、向量检索层与 LLM 层为可选依赖。
3. 许可证状态来自登记表或内置知识库；提交前仍需逐项核验来源、版本和授权条件。
