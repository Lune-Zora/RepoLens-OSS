# Demo · 真实可复现成功案例

> 这是一条**真实跑通**的端到端 demo：给一个 Issue，RepoLens-OSS 把它定位到正确文件与函数。

## 案例 1：parse_date 未 strip 前后空白

**Issue 文本**

> parse_date should strip surrounding whitespace before parsing YYYY-MM-DD

**仓库**：`examples/demo_repo`（小而自洽，符号有真实 doc/code）

**期望定位**：`src/dateparse/parser.py:parse_date`

**实际结果（hit@1 = True，hit@5 = True）**

| 排名 | 候选 (path:line) | 置信度 | 命中来源 |
|---|---|---|---|
| 1 | `src/dateparse/parser.py:9` | 0.870 | keyword_match（函数名/文档命中 `parse_date`、`strip`） |

**为什么命中**：`parser.py:9` 的符号名 `parse_date` 和 docstring 里的 `strip` /
`whitespace` 同时命中 Issue 关键词；BM25 把这一条排在第一。

**复现命令**

```bash
python replens/cli.py locate examples/demo_repo \
  --issue "parse_date should strip surrounding whitespace before parsing YYYY-MM-DD"
```

## 案例 2：is_valid_date 误拒带空白的合法日期

**Issue 文本**

> is_valid_date incorrectly rejects whitespace-padded date strings

**期望定位**：`src/dateparse/validator.py:is_valid_date`

**实际结果（hit@1 = True）**

| 排名 | 候选 | 置信度 | 命中来源 |
|---|---|---|---|
| 1 | `src/dateparse/validator.py:8` | 0.870 | keyword_match（`is_valid_date` 函数名 + 文档 `whitespace`） |

**为什么命中**：与案例 1 同源，但本案例关键词偏 `valid / reject`，使得
`validator.py` 的 `is_valid_date` 排名更靠前；BM25 的关键词权重让两条
都能在 top-1 命中正确文件。

**复现命令**

```bash
python replens/cli.py locate examples/demo_repo \
  --issue "is_valid_date incorrectly rejects whitespace-padded date strings"
```

## 这条 demo 想证明什么

- **检索是确定性计算**：不依赖 LLM，给定 Issue 就能稳定拿到前几名候选。
- **粒度足够细**：top-1 就直接命中目标函数（带行号），不需要二次过滤。
- **小仓库也成立**：examples/demo_repo 总共只有 4 个模块，BM25 仍能分得清
  `parser` 与 `validator` 的差别。
