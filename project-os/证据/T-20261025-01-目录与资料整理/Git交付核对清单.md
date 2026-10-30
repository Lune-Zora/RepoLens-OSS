# Git 交付核对清单

核对日期：2026-10-26。桌面原项目尚未应用目录迁移；以下是该时点的实际 Git 状态与待审阅材料。工作区副本已经完成整理和定向验证。

## 关键实现与材料

| 文件 | 原目录跟踪状态 | 交付要求 |
|---|---|---|
| `replens/runner.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `replens/test_adapter.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `replens/validation.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `replens/eval_inputs.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `replens/eval_metrics.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `examples/acceptance/demo.json` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `tests/test_patch_loop.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `tests/test_validation.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `tests/test_eval.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `tests/test_eval_inputs.py` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `docs/05_测试/统一修复验收.md` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `docs/05_测试/评测口径与输入隔离.md` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |
| `EXECUTION_LOG.md` | 未跟踪 | 审阅后随依赖实现与对应测试一起纳入交付 |

## 原目录全部未跟踪文件

未跟踪不等于应无条件提交，需确认用途。目录迁移后，旧路径按迁移清单变更。

- `EXECUTION_LOG.md`
- `docs/05_测试/统一修复验收.md`
- `docs/05_测试/评测口径与输入隔离.md`
- `examples/acceptance/demo.json`
- `replens/eval_inputs.py`
- `replens/eval_metrics.py`
- `replens/runner.py`
- `replens/test_adapter.py`
- `replens/validation.py`
- `reports/_demo_raw.json` → `reports/experiments/_demo_raw.json`
- `reports/ablation_demo_smoke.md` → `reports/experiments/ablation_demo_smoke.md`
- `reports/demo_failure.md` → `reports/demos/demo_failure.md`
- `reports/demo_success.md` → `reports/demos/demo_success.md`
- `reports/validate_sample_manifest_test.md` → `reports/archive/validate_sample_manifest_test.md`
- `tests/test_eval.py`
- `tests/test_eval_inputs.py`
- `tests/test_patch_loop.py`
- `tests/test_validation.py`

## 发布前的顺序

1. 通过正常应用权限流程允许原目录写入后，应用已验证的目录迁移。
2. 核对原文件删除项与新路径逐一对应，确保数据、参考补丁和报告没有遗漏。
3. 人工审阅全部已有修改、未跟踪文件及本次整理，选择纳入版本控制的文件。
4. 在允许的测试边界内补当前完整回归，并从干净检出验证交付。
5. 再决定提交与发布；本次没有自动暂存、提交或推送。

## 原目录状态快照

```text
 M .github/workflows/ci.yml
 M ROADMAP.md
 M docs/01_需求/优化计划.md
 M docs/01_需求/答辩大纲.md
 M docs/01_需求/项目简介.md
 M docs/02_架构/创新验收.md
 M docs/02_架构/技术报告.md
 M docs/02_架构/系统分析.md
 M docs/02_架构/设计方案.md
 M docs/05_测试/TEST-真实仓库评测协议.md
 M docs/05_测试/定位改进.md
 M docs/05_测试/消融实验.md
 M docs/06_运维/社区贡献.md
 M docs/07_归档/竞赛复盘.md
 M eval/ablation.py
 M eval/baselines.py
 M eval/eval.py
 M project-os/交接记录.md
 M project-os/项目状态.md
 M pyproject.toml
 M replens/cli.py
 M replens/compliance.py
 M replens/eval_runner.py
 M replens/executor.py
 M replens/patchgen.py
 M replens/provenance.py
 M replens/report.py
 M replens/retriever.py
 M replens/testgen.py
 M scripts/prepare_loguru_eval.py
 M scripts/prepare_real_eval.py
 M scripts/validate_sample_manifest.py
 M tests/test_core.py
 M web/app.py
 M web/static/app.js
 M web/static/index.html
 M 变更日志.md
?? EXECUTION_LOG.md
?? docs/05_测试/统一修复验收.md
?? docs/05_测试/评测口径与输入隔离.md
?? examples/acceptance/
?? replens/eval_inputs.py
?? replens/eval_metrics.py
?? replens/runner.py
?? replens/test_adapter.py
?? replens/validation.py
?? reports/_demo_raw.json
?? reports/ablation_demo_smoke.md
?? reports/demo_failure.md
?? reports/demo_success.md
?? reports/validate_sample_manifest_test.md
?? tests/test_eval.py
?? tests/test_eval_inputs.py
?? tests/test_patch_loop.py
?? tests/test_validation.py
```
