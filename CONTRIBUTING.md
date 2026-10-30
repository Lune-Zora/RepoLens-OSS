# Contributing

欢迎贡献。本项目核心原则：让每一个判断都能被代码证据和测试结果证伪或证实。

## 开发流程
1. Fork 并 clone 仓库；
2. 创建分支：feat/xxx 或 fix/xxx；
3. 修改代码并补充单元测试（tests/ 目录）；
4. 运行 py -m pytest tests/ -q 确保全绿；
5. 运行 py -m replens eval examples/demo_repo --dataset eval/datasets/dataset.json 确认无回归；
6. 提交 PR，描述改动与验证结果。

## 代码风格
- Python 3.10+，核心包零第三方依赖；
- 新增模块需在 docs/02_架构/设计方案.md 变更记录中登记；
- 涉及检索/补丁的改动需附消融或评测数据。

## 测试
- 单元测试：`py -m pytest tests/ -q`
- 评测：`py -m replens eval examples/demo_repo --dataset eval/datasets/dataset.json`
- 混合评测：`py -m replens eval examples/demo_repo --dataset eval/datasets/dataset.json --hybrid`
- 消融：`py eval/ablation.py examples/demo_repo --dataset eval/datasets/dataset.json --name demo --hybrid`

> 执行证据（证伪式检索）的消融须用 `--execution-test "<真实测试命令>"` 显式指定，
> 执行证据只来自真实运行的失败轨迹；**禁止用标注答案（expected 目标函数名）定向采集证据**，
> 否则构成 ground-truth 泄漏。

## 许可
MIT。第三方资源使用见 reports/compliance/open_source_checklist.md。
