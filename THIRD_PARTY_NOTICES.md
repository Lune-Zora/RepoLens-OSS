# 第三方资源与开放边界

项目自身LICENSE只适用于团队依法有权许可的部分，不覆盖所有第三方材料。

## 软件依赖

项目不捆绑Python、Ollama或pip依赖的安装包。核验所用版本、许可证和NOTICE原文见reports/compliance/verification-20261030/installed-packages.json及所列原始文件。若以后捆绑或修改第三方组件，按各自许可证满足声明、NOTICE、源码与再分发义务。

## 上游源码、补丁与数据

必要参考补丁和Gold证据保持原归属。tqdm受其LICENCE中MPL-2.0和MIT文件/贡献归属约束；pypdf、click使用BSD-3-Clause；loguru使用MIT；requests使用Apache-2.0，NOTICE保留。历史候选对应的110个父提交许可证原文及索引已归档。不能把参考修复称为团队新发现或模型自主修复。

Issue/PR正文、引用文本不因软件仓库采用开源许可就自动获得完整再利用授权。未闭合的文本授权应限制公开分发，或改用最小必要引用与原链接；不据此宣称所有候选描述已经获再分发授权。

## 模型

0.7运行流程另使用本机qwen25-7b-unc:latest与qwen35-9b-unc:latest进行实际比较，默认选择9B标签。精确本机元数据与逐请求模型名保存在T-20261030-09。三个GGUF均不随交付包分发，来源与适用许可未因模型切换而自动核验完成。

本次正式 Demo 已确认实际调用 Ollama 标签 `qwen2.5-coder:1.5b`，本机 ID `db848539a068`，约 1.1 GB、GGUF / Q4_K_M；原始请求与响应一致。其他已安装模型未计入该次验证。

官方Qwen2.5-Coder-1.5B-Instruct为Apache-2.0，但本机自定义GGUF标签没有license层，现有manifest/digest只能标识本地转换物。经对照，本机模型层与官方仓库当前同名标签的模型层摘要不同；这不等同于认定侵权。未取得原下载/转换来源前，不把官方许可证等同于本地转换物链路已核验；不分发该权重。

## 材料工具

python-docx/lxml/pypdf/PyMuPDF和Word仅用于本地材料制作/检查，不作为本次源码包运行依赖分发。PyMuPDF许可证是AGPL或商业双许可，不将其称为MIT；若未来将其库或衍生应用分发/服务化需另核对义务。

完整8字段清单和待确认项见reports/compliance/open_source_checklist.md。
