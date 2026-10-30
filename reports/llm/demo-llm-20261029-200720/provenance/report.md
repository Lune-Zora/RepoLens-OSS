# RepoLens-OSS 分析报告

## 问题

> date string with surrounding whitespace fails to parse

## 修复结论

**VERIFIED — 独立验收通过**

- 目标失败明确转为 PASS，原有通过测试保持通过，冻结独立行为验收通过

## 证据包与重放

打包状态：`WRITTEN`；Schema：`2.0`。

材料核验：`NOT_CHECKED`；重放一致性：`NOT_RUN`。

生成证据包后需单独执行 verify；重放一致不等于修复通过。


## 代码定位（证据）

| 文件:行号 | 类型 | 符号 | 置信度 | 来源 | 判断依据 |
|---|---|---|---|---|---|
| `src/dateparse/parser.py:9` | function | parse_date | 1.0 | execution_confirmed | 失败轨迹 1 帧命中 src/dateparse/parser.py:9（累计权重 1.00）→ 未被执行证伪 |
| `src/dateparse/validator.py:8` | function | is_valid_date | 0.85 | keyword_match | 符号名/文档命中关键词: date, string, str, surrounding, surround |
| `src/dateparse/utils.py:25` | function | add_days | 0.7 | keyword_match | 符号名/文档命中关键词: date, string, str |
| `src/dateparse/parser.py:9` | function | parse_date | 0.6 | execution_confirmed | 失败轨迹 1 帧命中 src/dateparse/parser.py:9（累计权重 1.00）→ 未被执行证伪 |
| `src/dateparse/utils.py:5` | function | days_between | 0.55 | keyword_match | 符号名/文档命中关键词: date, string, str |
| `src/dateparse/validator.py:29` | function | validate_range | 0.4 | keyword_match | 符号名/文档命中关键词: date, string, str |
| `tests/test_parser.py:8` | method | test_parse_date | 0.25 | call_related | 证伪：位于测试文件，不是源码证据（降权） |
| `tests/test_parser.py:17` | method | test_parse_date_with_whitespace | 0.25 | call_related | 证伪：位于测试文件，不是源码证据（降权） |

## 关键证据代码

**`src/dateparse/parser.py:9` — parse_date**
```python
def parse_date(s):
    """Parse a date string in YYYY-MM-DD format.

    Args:
        s: the date string, e.g. "2024-01-01".

    Returns:
        datetime.date object.
```

**`src/dateparse/validator.py:8` — is_valid_date**
```python
def is_valid_date(s):
    """Check whether a date string is a valid YYYY-MM-DD date.

    Args:
        s: date string like "2024-01-01".

    Returns:
        True if the string parses to a valid date, False otherwise.
```

**`src/dateparse/utils.py:25` — add_days**
```python
def add_days(s, n):
    """Add n days to a date string and return a date object.

    Args:
        s: date string "YYYY-MM-DD".
        n: integer number of days (can be negative).

    Returns:
```

## 修改计划

- **根因分析**：The input date string is not stripped of surrounding whitespace, so a string like ' 2024-01-01 ' will fail to parse.
- **影响范围**：src/dateparse/parser.py, src/dateparse/validator.py, src/dateparse/utils.py, src/dateparse/parser.py, src/dateparse/utils.py, src/dateparse/validator.py, tests/test_parser.py, tests/test_parser.py
- **修改文件**：src/dateparse/parser.py, src/dateparse/validator.py, src/dateparse/utils.py, src/dateparse/parser.py, src/dateparse/utils.py, src/dateparse/validator.py, tests/test_parser.py, tests/test_parser.py
- **修改步骤**：
  - Add a call to `s.strip()` before parsing the date string in `src/dateparse/parser.py` and `src/dateparse/utils.py`.
  - Update the `is_valid_date` function in `src/dateparse/validator.py` to call `s.strip()` before parsing the date string.
- **风险提示**：
  - Potential issues with incorrect date parsing due to surrounding whitespace.

计划生成方式：`llm`

## 测试验证

测试命令：`python -m pytest tests/test_parser.py`

补丁：LLM 生成 2 个有效候选。；规则候选主测试筛选通过，待最终验收。规则引擎生成 strip 补丁（在 parse_date 函数体首行前插入 s = s.strip()）。

### 修复前

- 命令 `python -m pytest tests/test_parser.py` → exit 1（未通过；有效）
  - 摘要：passed=2, failed=1, errors=0, skipped=0, collected=3
  - ❌ ================================== FAILURES ===================================
  - ❌ =========================== short test summary info ===========================
  - ❌ FAILED tests/test_parser.py::ParserTests::test_parse_date_with_whitespace - ValueError: time data ' 2024-01-01 ' does not match format '%Y-%m-%d'

### 修复后

- 命令 `python -m pytest tests/test_parser.py` → exit 0（通过；有效）
  - 摘要：passed=3, failed=0, errors=0, skipped=0, collected=3

## 执行证据（失败轨迹，证伪式重检索）

| 帧位置 | 函数 | 权重 |
|---|---|---|
| `src/dateparse/parser.py:23` | parse_date | 1.0 |

说明：失败轨迹命中的符号获得最强证据权重；测试文件/模块级证据被证伪降权。


## 测试翻转预测（反事实推理）

- 预测会翻转的测试：test_parse_date, test_parse_date_with_whitespace
- 实际翻转的测试：tests/test_parser.py::ParserTests::test_parse_date_with_whitespace
- 命中：tests/test_parser.py::ParserTests::test_parse_date_with_whitespace
- 预测准确率 precision=0.5，召回率 recall=1.0


## 补丁内容（diff）

```diff
--- a/src/dateparse/parser.py
+++ b/src/dateparse/parser.py
@@ -20,6 +20,7 @@
         like " 2024-01-01 " will fail to parse. This is the bug to fix:
         call s.strip() before parsing.
     """
+    s = s.strip()
     return datetime.datetime.strptime(s, "%Y-%m-%d").date()
 
 
```

## 自动生成的辅助回归测试（不作为独立验收）

```python
# 由 RepoLens-OSS 生成的回归测试
# 目标: src/dateparse/parser.py:9 函数 `parse_date`
# 对应 Issue: date string with surrounding whitespace fails to parse

def test_regression_parse_date_whitespace_invariance():
    # 行为回归：首尾空白不应改变函数结果（对应 issue 的 whitespace/strip 语义）
    assert _mod.parse_date(' 2024-01-01 ') == _mod.parse_date('2024-01-01')

# 冒烟测试（smoke）：仅验证接口存在与签名一致，不验证业务行为。
# 行为级验证由仓库自有测试 + 上方行为回归测试承担。
import importlib, pathlib, sys
sys.path.insert(0, str(pathlib.Path('__TMP_REPO__') / 'src'))
_mod = importlib.import_module('dateparse.parser')


def test_regression_parse_date_exists_and_callable():
    assert callable(_mod.parse_date), "函数 parse_date 不存在或不可调用"

def test_regression_parse_date_signature():
    # 校验必填参数个数与源码定义一致
    import inspect
    try:
        params = inspect.signature(_mod.parse_date).parameters
    except (ValueError, TypeError):
        return  # 无法获取签名，跳过
    required_count = sum(1 for p in params.values()
        if p.default is inspect.Parameter.empty
        and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD))
    assert required_count == 1, f"必填参数个数应为 1，实际 {required_count}"

```

## 自动生成的辅助回归结果（含冒烟检查）

测试命令：`pytest tests/test_replens_regression.py`

### 修复前

- 命令 `pytest tests/test_replens_regression.py` → exit 1（未通过）
  - ❌ ================================== FAILURES ===================================
  - ❌ =========================== short test summary info ===========================
  - ❌ FAILED tests/test_replens_regression.py::test_regression_parse_date_whitespace_invariance - ValueError: time data ' 2024-01-01 ' does not match format '%Y-%m-%d'

### 修复后

- 命令 `pytest tests/test_replens_regression.py` → exit 0（通过）


## 冻结的独立行为验收

命令：`python -m unittest discover -s __replens_acceptance__ -p test_date_behavior.py`

SHA-256：`1aacb96e419b642cacc224843609d7aab7fcad4da6266bdff2d87b80e9b7688d`

### 修复前

- 未通过（exit 1）
  - `test_date_behavior.DateBehavior.test_invalid_date_stays_invalid`: PASS
  - `test_date_behavior.DateBehavior.test_tab_and_newline_padding`: ERROR
  - `test_date_behavior.DateBehavior.test_unpadded_date`: PASS

### 修复后

- 通过（exit 0）
  - `test_date_behavior.DateBehavior.test_invalid_date_stays_invalid`: PASS
  - `test_date_behavior.DateBehavior.test_tab_and_newline_padding`: PASS
  - `test_date_behavior.DateBehavior.test_unpadded_date`: PASS
