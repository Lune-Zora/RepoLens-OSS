# RUNBOOK-本地运行与发布验收

> 依据：审查报告第 11、12、13 节。本地可信使用先行；公开部署前必须完成隔离与鉴权。

## 1. 本地运行

```powershell
# 1) 固定虚拟环境（示例 Python 3.10）
& 'C:\Users\19248\AppData\Local\Programs\Python\Python310\python.exe' -m venv .venv
& './.venv/Scripts/python.exe' -m pip install --upgrade pip setuptools
& './.venv/Scripts/python.exe' -m pip install -c constraints.txt -e '.[web,test]'

# 2) 核心测试
& './.venv/Scripts/python.exe' -m unittest discover -s tests -q
& './.venv/Scripts/python.exe' -m pytest tests -q

# 3) CLI 闭环；此处为规则或模型配置下的入口，模型演示须先按配置说明设置变量。
& './.venv/Scripts/python.exe' -m replens run examples/demo_repo `
  --issue 'date string with surrounding whitespace fails to parse' `
  --auto-patch --test-cmd 'python -m unittest discover -s tests' `
  --acceptance-suite examples/acceptance/demo.json --require-verified --format json
```

## 2. Web 运行与冒烟

模型变量须在启动 Web 的同一个终端设置，具体命令见 [模型配置与启动](../模型配置与启动.md)。
普通 CLI/Web 不自动读取 `.env`；自检脚本不会修改父终端，服务重启后才继承新变量。
接口 `llm.configured` 只表示地址已配置，不能当作推理通过。

```powershell
& './.venv/Scripts/python.exe' -m pip install -e '.[web]'
& './.venv/Scripts/python.exe' -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

冒烟检查：
- GET http://127.0.0.1:8000/api/env 返回 python/llm/embedding 状态。
- GET http://127.0.0.1:8000/api/repos 返回 examples/demo_repo。
- POST /api/init、/api/index、/api/search、/api/run 依次可走通。

## 3. 发布验收（新目录安装）

- 从发布版本克隆/解压到全新目录；按上述命令安装运行。
- 确认 LICENSE、README.md、核心代码、测试、Demo 与复现文档进入同一版本。
- 确认 .gitignore 生效：会话记录、密钥、构建产物不入库。

## 4. 隔离与安全边界（公开部署前必须完成）

| 项 | 现状 | 公开部署要求 |
|---|---|---|
| 文件隔离 | 临时副本 copytree | 容器/受限进程，网络与文件系统隔离 |
| 命令执行 | 白名单 + 超时 | 加上资源配额、只读根目录、禁止出网 |
| 鉴权 | 无 | 登录、租户隔离、速率限制 |
| 外部仓库 | 本地可信路径 | 上传内容扫描、仓库大小与依赖限制 |

## 5. 提交前检查

- [ ] 两份正式附件已取得并逐项核对
- [ ] 每条完成声明有对应版本运行证据
- [ ] 模型/规则、人工补丁验证/自主修复分别披露
- [ ] 参考补丁、训练材料、评测答案未进入模型输入
- [ ] 失败、环境错误、无效样本与总预算如实记录
- [ ] 资源清单只标实际核实项，许可与来源有依据
- [ ] 新目录按 README 安装运行通过
- [ ] 按本次用户提供的截止时间（2026-10-30 20:30，北京时间）提前完成提交并留回执；赛段材料要求以当前通知为准
