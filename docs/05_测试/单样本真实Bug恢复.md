# 单样本真实 Bug 恢复

`scripts/restore_tqdm_reset_inf.py` 是 T4-A 第一份样本的控制端入口。它验证的是公开上游 Gold，不生成模型补丁，不计入模型修复成功率。所有 Gold、独立验收、原始测试日志、环境记录和证据包都写到上游仓库之外；不得把该控制目录加入定位评测或模型输入清单。

## 前置条件

- 本地已有 tqdm Git clone，remote 为 `github.com/tqdm/tqdm` 或列明镜像 `gitee.com/mirrors/tqdm`，且包含完整提交 `5bf58c2d61410e5a17da3440df921fe34c6c38bf` 与 `426a0988a1e9970041f5a649ae8a6376c5293fbe`。镜像身份单独披露；入口以固定完整提交 ID、唯一父子关系和 `git fsck --full` 核验本地内容，不把镜像 URL 或本地校验写成在线官方认证。脚本不联网克隆或拉取。
- 使用一个准备好的 Python 环境运行脚本；其中 pytest 可从当前解释器启动。脚本不安装依赖。它记录 Python、平台、全部已安装发行包版本，以及上游提交中的 `pyproject.toml`、`setup.py`、`tox.ini` 和 requirements 等环境材料。
- `--work-dir` 是已存在的仓库外临时父目录；`--output` 是仓库外、尚不存在的控制结果目录。

先从可信上游来源准备仓库和环境。确认 `git remote -v` 与提交可用后，在 RepoLens 根目录执行：

```powershell
New-Item -ItemType Directory -Force ..\replens-realbug-work | Out-Null
python scripts/restore_tqdm_reset_inf.py `
  --repo 'C:\Users\56514\Desktop\人工智能\tqdm' `
  --work-dir ..\replens-realbug-work `
  --output ..\replens-realbug-results\tqdm-reset-infinity
```

`--repo` 可以指向仓库外的只读输入目录；路径以本机实际位置为准。目录不存在或缺少提交时停止；不要把文档里的目标/补丁直接写入源码目录来替代 Git 校验。

## 自动门槛

入口依次核对来源 remote、完整 commit、修复提交父子关系、Git 对象完整性、目标源码、`LICENCE` 中的 MPL-2.0/MIT 以及项目的 `MPL-2.0 AND MIT` 声明，再核对测试/生产 diff 路径；任何一项不符立即终止。输入仓库只读，入口在 `--work-dir` 内通过本地 Git 传输创建独立对象副本，再创建两个 detached worktree；不建立硬链接，不修改输入仓库的 Git 注册信息。Windows 用户 SID 不同的情形只对精确目录设置单次 Git 命令信任，不修改全局配置。

主命令固定为 `python -m pytest -o addopts= tests/tests_tqdm.py -k reset`，运行原始 `test_reset`、新增 `test_reset_inf`、原始 `test_disabled_reset` 共 3 条同步测试，保留上游断言和 fixture。显式清除继承的 `addopts`，因此不会要求本机未安装的 asyncio 插件；未知 `timeout`/asyncio 配置项产生的警告保留在原始日志中。超时由 RepoLens 外部执行器控制。这个同步测试配置及原始 `pyproject.toml` 均被记录，不代表完整上游测试套件通过。

目标行为必须在 buggy tree 有效失败，同一目标在 Gold 后必须通过，原有两条 PASS 必须保持。冻结的两个独立断言检查“无限 total 按未知值处理”和“有限 total 计数仍正常”；其命令同样显式清除继承的 `addopts`。入口不会检索、调用模型或生成候选；独立材料只传给验收执行器和最终打包器。

只有上述检查通过才写入 Schema 2.0 包。入口用相同 Python、平台和发行包清单，在第二个新建 worktree 中重放主测试与独立验收；源码哈希、材料核验、逐测试结果及统一结论必须都通过。结束时移除两个 worktree 和本地 Git 对象副本，并比较输入仓库全部文件（包括 `.git`）的前后哈希；变化或清理失败均使流程失败。

成功输出位于控制目录：

```text
run.json                       提交、环境、每步状态及最终三层重放结论
sample.json                    可用于后续控制端整理的单样本摘要
requirements.freeze.txt        本机全部已安装发行包的 Name==version 清单
environment.json               Python、平台、依赖版本及上游环境文件哈希
upstream-environment/          从 buggy commit 取出的依赖/测试配置原文
upstream-license/              上游 LICENCE 原文及其使用边界
independent-acceptance.json    完整冻结的独立验收内容
patches/                       原始上游测试补丁和 Gold 生产补丁
logs/<阶段>/before/             补丁前未截断 stdout/stderr 及其哈希
logs/<阶段>/after/              补丁后未截断 stdout/stderr 及其哈希
logs/replay-<套件>/             第二个 worktree 的重放原始日志
logs/git/                       Git 命令记录、二进制输出及其哈希
evidence_bundle/                Schema 2.0 可重放证据包
cross-worktree-replay.json      第二工作树的逐阶段重放结果
```

`evidence_bundle` 的测试日志字段仍遵循 RepoLens 报告的节选长度；完整原始输出由仓库外 `logs/` 按阶段和 before/after 分开保留，不会相互覆盖。超时结果保留已捕获的 stdout/stderr；第二个 worktree 的重放也保存全部测试输出。`run.json.raw_log_manifest` 逐文件记录字节数与 SHA-256；原始日志写入失败会让流程失败。所有执行器临时副本和结果目录都放在 `--work-dir` 下的本次受管目录，并在结束时清理。依赖清单固定名称和版本，不包含 PyPI wheel 文件哈希，也不自动重建另一台机器上的相同环境；跨机器重放需要先准备同一环境。上游文件和 Gold 补丁是评测控制材料，不是模型输入材料。

任何步骤失败时，进程非零退出并在 `run.json` 标注失败阶段；未通过基线/Gold/独立验收时不会生成证据包，也不会将样本标记为有效。只有成功完成重建 worktree 重放，才将状态记为 PASS。

若参数本身无效、输出目录已存在或不可写，入口直接非零退出并写控制台错误，不覆盖已有记录。`tests/test_restore_sample.py` 的内部夹具只验证日志与失败处理，不是 tqdm 真实 Bug 证据。

## 2026-10-27 实跑

本地 Gitee 镜像样本已完成：主测试 `2 PASS + 1 FAIL → 3 PASS`；独立验收 `1 PASS + 1 FAIL → 2 PASS`；第二个真实 worktree 的源码哈希一致、材料核验 PASS、逐测试重放 PASS、修复状态 VERIFIED。Python 3.13.6 / pytest 9.1.1；输入仓库（含 Git 元数据）前后哈希一致，临时对象和 worktree 已清理。详见[验收记录](../../project-os/证据/T-20261027-01-tqdm-reset-inf/验收记录.md)。这是参考 Gold 验证，不是模型自主修复。
