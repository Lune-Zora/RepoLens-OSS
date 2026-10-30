# 验收记录-Web隔离与发布

- 验收日期：2026-10-07
- 结论：通过
- 验收说明：本地冒烟通过。pip install -e .[web] 成功；uvicorn 启动后 /api/env、/api/repos、/api/datasets、POST /api/init 均返回正确（python 3.10.11、demo_repo、8 数据集、init 识别 MIT/pytest/6 文件）。公开部署隔离与鉴权未实现，按运维手册执行。
- 验证命令：pip install -e .[web] && uvicorn web.app:app --host 127.0.0.1 --port 8000；Invoke-RestMethod 冒烟 4 个端点
- 证据：project-os/证据/T-20261007-00-修复验证/ 与本次运行日志
- 关联任务：project-os/任务/T-20261007-12-Web隔离与发布.md
