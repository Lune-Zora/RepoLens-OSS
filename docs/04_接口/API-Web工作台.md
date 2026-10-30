# API-Web工作台

> 适用：RepoLens-OSS Web 可视化工作台（web/app.py，FastAPI）。本文档依据 web/app.py 实际代码整理。

## 1. 概览

- 服务：FastAPI 应用 `web.app:app`，版本 0.3.0。
- 作用：把核心包 replens/ 的能力（init / index / search / plan / run / eval / compliance / mine）暴露为 JSON API，并托管零构建 vanilla JS 前端。
- 启动：`pip install -e .[web]` 后执行 `uvicorn web.app:app --reload`，打开 http://127.0.0.1:8000。
- 认证：无鉴权（本地工作台）。
- 响应格式：成功返回 JSON；失败返回 `{"error": "<信息>"}`，HTTP 400（请求错误）或 500（服务端错误）。

## 2. 元信息 API

### GET /api/env
返回 Python、LLM、embedding 环境状态。

```json
{
  "python": "3.11.0",
  "llm": { "base_url": "", "model": "", "configured": false },
  "embedding": { "remote": false, "local": false }
}
```

### GET /api/repos
返回可选仓库列表，每项含 `path`、`relative`、`name`。

### GET /api/datasets
返回 `eval/datasets/` 下的定位数据集列表，每项含 `name`、`path`。不列出训练、挖掘和参考补丁材料。

## 3. 功能 API

### POST /api/init
导入仓库，识别许可证、依赖、测试框架。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |

### POST /api/index
构建代码结构索引（AST + SQLite FTS5）。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| force | boolean | 否 | 强制重建，默认 false |

成功返回：`symbols`（符号数）、`fts_available`（FTS5 是否可用）、`index_path`。

### POST /api/search
按 Issue 检索定位并附证据。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| issue | string | 是 | Issue 描述 |
| top_k | integer | 否 | 返回条数，默认 5 |
| hybrid | boolean | 否 | 混合检索（BM25+语义向量），默认 false |

成功返回：`insufficient_evidence`（证据是否不足）、`evidences`（证据列表，含 ref/type/name/confidence/reason/source/signature/code）。

### POST /api/plan
生成修改计划。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| issue | string | 是 | Issue 描述 |
| top_k | integer | 否 | 返回条数，默认 5 |
| hybrid | boolean | 否 | 混合检索，默认 false |

成功返回：`insufficient_evidence`、`evidences`、`plan`。

### POST /api/run
完整闭环：定位 → 计划 → 自动补丁 → 测试验证 → 报告。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| issue | string | 是 | Issue 描述 |
| top_k | integer | 否 | 检索条数，默认 5 |
| hybrid | boolean | 否 | 混合检索，默认 false |
| test_cmd | string | 否 | 测试命令，默认按测试框架推导 |
| auto_patch | boolean | 否 | 自动生成补丁，默认 true |
| patch | string | 否 | 指定 unified diff 补丁文件路径（auto_patch=false 时生效） |
| timeout | integer | 否 | 测试超时秒数，默认 120 |
| force_reindex | boolean | 否 | 强制重建索引，默认 false |
| no_provenance | boolean | 否 | 不生成 provenance 目录，默认 false |
| acceptance_suite | string | 否 | 仓库外的独立行为验收 JSON 路径；生成前冻结，本阶段可使用 examples/acceptance/demo.json |

成功返回：`report`（结构化报告）、`status` / `reasons`（修复结论）、`markdown`、`provenance`（证据目录或 null）、`provenance_status`（WRITTEN / FAILED / SKIPPED 及原因）。打包失败仍返回可用的修复报告，不能仅凭 HTTP 200 判断交付成功。
失败返回：HTTP 500，`{"error": "...", "traceback": "..."}`。

### POST /api/verify

从证据包完整重放主测试、辅助检查和独立验收，不调用模型。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 与冻结清单内容一致的原始源码目录，允许移动路径 |
| provenance | string | 是 | Schema 2.0 证据包目录 |
| test_cmd | string | 否 | 只能与冻结的主测试命令一致；默认直接使用包内命令 |
| timeout | integer/null | 否 | 正数；默认复用套件记录的超时 |

返回 `status`（PASS / FAIL / LEGACY）、`materials_status`、`replay_status`、`repair_status`、`reasons`，以及共用渲染器生成的 `markdown`。完成重放时还包含 `phase_checks`（各套件的 before/after 一致性）、`validation` 和 `validation_matches`。材料核验或重放未通过时也返回 HTTP 200 的诊断结构；调用方必须检查这三个结论。模型参数格式错误按 FastAPI 返回 422。

`replay_status=PASS` 与 `repair_status=UNVERIFIED/FAILED/NO_PATCH` 可以同时成立。Web 完整闭环结果区提供重放按钮，使用本次报告保存的仓库和证据包路径。详细格式见 [证据包格式与重放](../05_测试/证据包格式与重放.md)。

### POST /api/eval
运行评测集，输出 hit@1 / hit@5 / func@5 / line_acc / MRR / 拒答率等指标。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| dataset | string | 是 | 数据集文件名（如 dataset.json，按 eval/datasets/ 解析）、相对 eval/ 的分类路径（如 datasets/dataset.json），或绝对路径 |
| top_k | integer | 否 | 检索条数，默认 5 |
| hybrid | boolean | 否 | 混合检索，默认 false |

失败返回：HTTP 500，带 `traceback`。

### POST /api/compliance
合规披露：第三方依赖与开源许可。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |

### POST /api/mine
从仓库历史挖掘评测集。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| repo | string | 是 | 仓库路径 |
| max | integer | 否 | 最大提交数，默认 20 |
| validate | boolean | 否 | 挖掘时运行验证，默认 false |
| timeout | integer | 否 | 超时秒数，默认 120 |
| output | string | 否 | 输出文件路径 |

失败返回：HTTP 500，带 `traceback`。

### POST /api/clone
把远程仓库克隆到本地 `repos/` 目录。仅接受 `http(s)/git/ssh` 协议或 `git@` 形式地址；仓库名经字符白名单过滤。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| url | string | 是 | 远程仓库地址 |

成功返回：`path`、`relative`、`name`、`existed`（目录已存在则为 true）。

### POST /api/fs/list
浏览本地目录，供前端目录选择器使用。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path | string | 否 | 目录路径；为空返回全部盘符 |

成功返回：`path`、`parent`、`entries`（子目录列表，含 `is_repo` 标记）、`is_root`。

## 4. 错误处理

| 状态码 | 含义 | 处理 |
|---|---|---|
| 400 | 请求参数错误或业务失败 | 读取 `error` 字段修正请求 |
| 403 | 无权限访问目录 | 换可用路径 |
| 500 | 服务端异常 | 读取 `error` 与 `traceback`（run/eval/mine 提供）定位 |

## 5. 使用示例

```bash
# 启动服务
pip install -e .[web]
uvicorn web.app:app --reload

# 导入并索引
curl -X POST http://127.0.0.1:8000/api/init -H "Content-Type: application/json" -d "{\"repo\": \"examples/demo_repo\"}"
curl -X POST http://127.0.0.1:8000/api/index -H "Content-Type: application/json" -d "{\"repo\": \"examples/demo_repo\"}"

# 检索定位
curl -X POST http://127.0.0.1:8000/api/search -H "Content-Type: application/json" -d "{\"repo\": \"examples/demo_repo\", \"issue\": \"date string with surrounding whitespace fails to parse\"}"
```
