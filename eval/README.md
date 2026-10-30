# 评测与训练材料

> 更新：2026-10-29。目录分类用于识别材料用途；真实隔离仍由允许输入清单控制，不能仅靠文件夹名称认证。

| 位置 | 用途 | 使用边界 |
|---|---|---|
| `eval.py`、`baselines.py`、`ablation.py` | 主评测独立入口、基线、消融 | 脚本入口保留，统一调用核心计分逻辑 |
| `datasets/dataset.json` | Demo 定位集：20 正向描述 + 5 干扰 | 描述数不等于独立 Bug 数；不是自主修复率 |
| `datasets/real_dataset_*.json` | 历史真实仓库定位材料 | 使用前恢复精确提交与环境，审核去重及标注；不代表当前已复验 |
| `datasets/real_dataset_mined.json` | **自动挖掘修复集**（100 条：pypdf 46 / click 22 / loguru 15 / tqdm 12 / requests 5） | 三道关：worktree 验证 fail→pass、机械复核 C1–C9、人工通读语义；每条带 `fail_pass` / `verified_at` / `env_profile` |
| `envs/` | **时代环境锁**（如 `urllib3-1.txt`）：按候选父提交的依赖声明选定的依赖组合 | 验证历史提交时必需；锁文件入库，venv 建在用户缓存，不污染全局 |
| `curation/` | **人工选定的描述出处**（`issue_upgrade.json`）：弱描述 → 上游 issue 标题 / PR 正文首句 | 只换出处、不改写；每条带 URL 与抓取日期，复核器据此核对 |
| `datasets/datasets.manifest.json` | **数据集清单**：声明每份数据集的种类（localization / repair）与范围 | 决定该文件能支撑哪类实验；分母不得跨文件混算 |
| `datasets/dataset.schema.json` | **样本字段 schema**（JSON Schema draft-07） | 单一事实源，由 `scripts/validate_datasets.py` 执行 |
| `training/` | 指令微调训练与留出 JSONL | 当前 14 条训练、2 条留出；模型训练留出不等于冻结独立行为验收 |
| `mined/` | 自动挖掘的候选材料 | 早期产物（缺提交元数据），已由 `datasets/real_dataset_mined.json` 取代；保留作历史 |
| `reference_patches/` | 参考补丁、包含答案的历史 Bug 元数据 | 控制端验证使用；禁止进入模型检索和提示词 |
| `reference_patches/contrib/` | 历史 format-patch 材料 | 不是已经合并的上游贡献证明 |

## 数据集口径（必读）

6 份数据集分两类，**分母不同，禁止混算**：

| 种类 | 数量 | 能支撑 |
|---|---|---|
| `localization`（dataset / tqdm / pypdf） | 可定位正向样本 24 | hit@k / func@k / MRR / 拒答率 |
| `repair`（loguru / extended / mined） | 可修复样本 **112** | 上述全部 + 端到端修复评测 |

112是候选条目数，不是本次模型实验的执行分母或成功数。最新定位与基线实测见[提交事实与证据](../docs/05_测试/提交事实与证据.md)。

完整口径与局限见 [评测口径总表](../docs/05_测试/评测口径总表.md)；人工通读结论见 [人工通读记录](../docs/05_测试/人工通读记录.md)。

## 常用命令

```bash
# 校验全部数据集（schema + 业务规则），CI 必跑
python scripts/validate_datasets.py

# 挖掘 + 验证 + 提升（扩充分母）
# --era-env auto：按候选父提交的依赖声明选时代环境（历史测试代码依赖 urllib3 1.x 时必须开）
python -m replens mine <repo> --max N --validate --timeout 240 --era-env auto --output mined.json
python scripts/promote_mined.py mined.json --output eval/datasets/real_dataset_mined.json
python scripts/review_mined.py eval/datasets/real_dataset_mined.json --repos ... --prune-to eval/datasets/real_dataset_mined.json

# 改了标注或描述之后必须重验（默认开启；--no-verify 只重算不验证）
python scripts/repair_annotations.py eval/datasets/real_dataset_mined.json --repos ... --jobs 2 --era-env auto

# 人工策展的样本（描述是手写的）只重验、不重算
python scripts/repair_annotations.py eval/datasets/real_dataset_loguru.json --repos ... --verify-only

# 时代环境：查看 / 预置 / 重建（离线场景先预置，避免验证时联网）
python -m replens era-env --list
python -m replens era-env --profile urllib3-1 --repo <仓库> --commit <提交> [--python <解释器>]
python -m replens era-env --profile urllib3-1 --reset

# 用旧解释器建环境后，让当前解释器复用它
REPLENS_ERA_PY_TAG=py38 python -m replens mine <repo> --validate --era-env auto
```


```bash
python -m replens eval examples/demo_repo --dataset eval/datasets/dataset.json
python eval/baselines.py examples/demo_repo --dataset eval/datasets/dataset.json
python eval/ablation.py examples/demo_repo --dataset eval/datasets/dataset.json --name demo
```

基线和消融默认结果写入 `reports/runs/`；自定义输出使用 `--output`。Web 数据集下拉只列 `datasets/`，不混入训练、挖掘或含参考答案的文件。

## 路径迁移

旧的 `eval/dataset.json`、`eval/real_dataset_*.json` 移入 `datasets/`；训练与挖掘文件分别移入同名分类目录；`real_bugs.json`、原 `real_patches/` 与 `contrib/` 移入 `reference_patches/`。

文件内容保持不变。完整旧→新映射及 SHA-256 见 [迁移清单](../project-os/证据/T-20261025-01-目录与资料整理/路径迁移.json)。历史执行记录中的旧命令需按映射调整路径。

验收标准：[统一修复验收](../docs/05_测试/统一修复验收.md)、[定位指标与输入隔离](../docs/05_测试/评测口径与输入隔离.md)。
