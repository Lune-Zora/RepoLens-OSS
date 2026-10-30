# 2026-10-30 第三方核验材料

- installed-packages.json：本机版本/来源元数据与原始许可路径；installed-license-originals保留完整目录防止同名LICENSE覆盖。
- historical-license-index.json：110个候选父提交的原始许可证/NOTICE；全部有材料，不等于所有Issue讨论文本获再利用授权。
- official-sources.json：官方页面/许可证抓取结果、来源及哈希；抓取失败如实保留。
- ollama-tags.json / ollama-show.json / ollama-local-manifest.json：本地GGUF模型标签、摘要与元数据；未返回license，未闭合转换来源。其他本地模型标签未计为项目使用资源。
- resource-register.json：本次49项资源的逐项登记，与生成器保持一致；已核验42、待核验4、不适用3。

已核验限定于清单所述使用方式、版本/许可文本，不是全面法律意见。待核验4项：本机GGUF来源、Issue/PR文本公开分发边界、Word设备授权、OpenAI账号适用合同。后两项需使用者确认，不能由工具代签。

补充来源对照：已读取Ollama官方registry当前同名标签manifest，本机模型层digest与官方当前层不同，见model-origin-comparison.json。该差异不证明模型未经授权，但不能据此闭合下载/转换来源。
