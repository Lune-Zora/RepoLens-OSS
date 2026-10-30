# ADR-003 全仓换行统一为 LF

> 状态：已采纳
> 日期：2026-10-28
> 关联：[T-20261028-02 新建远端版本管理](../../../project-os/任务/T-20261028-02-新建远端版本管理.md)

## 背景

首次建库后实测 `git ls-files --eol`：索引内 **105 个 i/crlf、83 个 i/lf** 混杂。
本机 `core.autocrlf=true`，会让 LF 文件在检出时变 CRLF、提交时再转回，产生大量无意义 diff，也掩盖真实改动。

## 备选方案

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 保持 autocrlf=true，各机器自适应 | 不动文件 | diff 噪音大；`*.patch` 补丁文件会被破坏 |
| B. 关闭 autocrlf，全仓归一 LF，加 `.gitattributes` 固化 | 跨平台一致、diff 干净 | 一次性 106 文件改动 |
| C. 只加 `.gitattributes` 不归一现有文件 | 改动小 | 存量 CRLF 继续存在，问题不解决 |

## 决策

选 **B**。`core.autocrlf=false` + 全仓归一 + `.gitattributes`。

关键细节：`*.patch` / `*.diff` 必须用 `-text` 禁用换行转换——补丁文件自带 CRLF 语义，被转换后 hunk 匹配会失败。

## 后果

- 收益：索引内 `i/crlf` 从 105 降为 0；新增 `.gitattributes` 后其它机器不会再引入 CRLF。
- 代价：106 文件的换行提交（+8606/−8590），会污染 `git blame`，建议配合 `.git-blame-ignore-revs`。
- 需验证：归一前后 `pytest` 结果必须完全相同（实测均为 126 passed / 3 failed / 81 subtests，未引入回归）。