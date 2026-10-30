# 2026-10-30 提交事实核对原始产物

本目录包含本轮真实运行的unit-tests/datasets/localization/baselines/ablation输出及对应JSON，完整命令见[commands.json](commands.json)。

- [事实汇总](facts.json)、[源码遍历](source-inventory.json)。
- [测试原始输出](unit-tests.stdout.txt)。
- [定位](localization.json)、[基线](baselines.json)、[消融](ablation.json)。
- [Gold控制记录](gold-control/run.json)是此前控制材料的原样副本，本轮没有重新运行Gold，且model_repair_credit=false。
- [冻结材料校验清单](frozen-artifacts-before.json)保存本轮开始前历史报告/证据的哈希，用于确认未改写历史。

当前可引用数字与边界见[提交事实与证据](../../../docs/05_测试/提交事实与证据.md)。本轮未启用向量，不提供语义收益结论。原始测试只有本机环境结果。
