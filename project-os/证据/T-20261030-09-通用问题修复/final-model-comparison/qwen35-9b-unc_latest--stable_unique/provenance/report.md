# RepoLens-OSS 分析报告

## 问题

> 对列表去重时需要保持各元素首次出现的顺序。例如 [4,2,4,1] 应当得到 [4,2,1]；空列表仍返回空列表，输入列表不能被修改。元素均可哈希。

## 修复结论

**VERIFIED — 独立验收通过**

- 目标失败明确转为 PASS，原有通过测试保持通过，冻结独立行为验收通过

## 证据包与重放

打包状态：`WRITTEN`；Schema：`2.0`。

材料核验：`NOT_CHECKED`；重放一致性：`NOT_RUN`。

生成证据包后需单独执行 verify；重放一致不等于修复通过。


## 问题理解与检索说明

- 理解：列表去重时顺序被打乱，期望保持首次出现顺序。
- 预期行为：输入 [4,2,4,1] 应返回 [4,2,1]；空列表返回空列表；原列表不被修改。
- 检索表达：unique_values
- 范围判断：当前实现 `unique_values` 使用 `set()` 去重，但 `set` 是无序的（或基于哈希），导致顺序丢失。应改为遍历列表并记录已见元素以保留首次出现顺序。
- 模型：qwen35-9b-unc:latest
- 已检查目录：2/2
## 当前问题测试

模型提出、补丁前冻结；不是独立验收。SHA-256：7394fc8642a7e8b6c095740beb2baf6b955ef1c73abbb77afc709a5328cb82cb
用例数量：3
当前问题的冻结用例、原有通过测试保持及独立验收；不保证未覆盖行为
## 代码定位（证据）

| 文件:行号 | 类型 | 符号 | 置信度 | 来源 | 判断依据 |
|---|---|---|---|---|---|
| `api.py:1` | function | unique_values | 0.0 | intent_target | 当前实现 `unique_values` 使用 `set()` 去重，但 `set` 是无序的（或基于哈希），导致顺序丢失。应改为遍历列表并记录已见元素以保留首次出现顺序。 |
| `api.py:5` | function | count_values | 0.85 | keyword_match | 符号名/文档命中关键词: values, value |
| `README.md:1` | document | README.md | 0.55 | keyword_match | 代码片段命中检索词（document @ README.md:1） |
| `tests/test_public.py:6` | method | test_empty | 0.4 | keyword_match | 代码片段命中检索词（method @ tests/test_public.py:6） |
| `tests/test_public.py:5` | method | test_one | 0.6 | call_related | 与 unique_values 存在调用关系（调用图扩展） |
| `tests/test_public.py:7` | method | test_count | 0.51 | call_related | 与 count_values 存在调用关系（调用图扩展） |

## 关键证据代码

**`api.py:1` — unique_values**
```python
def unique_values(values):
    return list(set(values))
```

**`api.py:5` — count_values**
```python
def count_values(values):
    return len(values)
```

**`README.md:1` — README.md**
```python
# stable_unique

A small Python utility package. API is defined in api.py.
```

## 修改计划

- **根因分析**：函数 `unique_values` 直接使用 `set(values)` 进行去重，由于 `set` 的无序性（或哈希依赖），导致输入列表 `[4,2,4,1]` 等顺序丢失。
- **影响范围**：api.py
- **修改文件**：api.py
- **修改步骤**：
  - 在 `unique_values` 函数内部初始化一个空列表 `seen` 用于记录已出现的元素。
  - 遍历输入列表 `values`，对每个元素执行：若该元素不在 `seen` 中，则将其加入 `seen`。
  - 返回 `seen` 列表作为结果，从而保留首次出现的顺序。
- **风险提示**：
  - 若输入列表为空，需确保逻辑能正确返回空列表（当前逻辑天然满足）。
  - 若元素不可哈希，使用 `in` 操作符可能会报错（题目假设元素可哈希，风险较低）。

计划生成方式：`llm`

## 测试验证

测试命令：`python -m pytest tests __replens_task__/test_current_issue.py`

补丁：LLM 生成 1 个有效候选。；补丁应用成功（JSON 替换规则，共 1 处）。（主测试筛选通过，待最终验收）

### 修复前

- 命令 `python -m pytest tests __replens_task__/test_current_issue.py` → exit 1（未通过；有效）
  - 摘要：passed=5, failed=1, errors=0, skipped=0, collected=6
  - ❌ ================================== FAILURES ===================================
  - ❌ E       AssertionError: Lists differ: [1, 2, 4] != [4, 2, 1]
  - ❌ __replens_task__\test_current_issue.py:13: AssertionError
  - ❌ =========================== short test summary info ===========================
  - ❌ FAILED __replens_task__/test_current_issue.py::TaskTests::test_issue_00 - Ass...

### 修复后

- 命令 `python -m pytest tests __replens_task__/test_current_issue.py` → exit 0（通过；有效）
  - 摘要：passed=6, failed=0, errors=0, skipped=0, collected=6

## 测试翻转统计（补丁测试后计算）

- 预测会翻转的测试：test_empty, test_one
- 实际翻转的测试：__replens_task__/test_current_issue.py::TaskTests::test_issue_00
- 命中：无
- 预测准确率 precision=0.0，召回率 recall=0.0


## 补丁内容（diff）

```diff
--- a/api.py
+++ b/api.py
@@ -1,5 +1,11 @@
 def unique_values(values):
-    return list(set(values))
+    seen = set()
+    result = []
+    for value in values:
+        if value not in seen:
+            seen.add(value)
+            result.append(value)
+    return result
 
 
 def count_values(values):
```

## 自动生成的辅助回归测试（不作为独立验收）

```python
# Model-proposed task tests, frozen before patch generation.
import unittest

class TaskTests(unittest.TestCase):
    def test_issue_00(self):
        import importlib.util, sys, pathlib
        _path = pathlib.Path('__TMP_REPO__/api.py')
        _spec = importlib.util.spec_from_file_location('_target_module', _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules['_target_module'] = _mod
        _spec.loader.exec_module(_mod)
        actual = _mod.unique_values(*[[4, 2, 4, 1]], **{})
        self.assertEqual(actual, [4, 2, 1])

    def test_issue_01(self):
        import importlib.util, sys, pathlib
        _path = pathlib.Path('__TMP_REPO__/api.py')
        _spec = importlib.util.spec_from_file_location('_target_module', _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules['_target_module'] = _mod
        _spec.loader.exec_module(_mod)
        actual = _mod.unique_values(*[[]], **{})
        self.assertEqual(actual, [])

    def test_issue_02(self):
        import importlib.util, sys, pathlib
        _path = pathlib.Path('__TMP_REPO__/api.py')
        _spec = importlib.util.spec_from_file_location('_target_module', _path)
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules['_target_module'] = _mod
        _spec.loader.exec_module(_mod)
        actual = _mod.unique_values(*[['a', 'b', 'a', 'c']], **{})
        self.assertEqual(actual, ['a', 'b', 'c'])


```

## 冻结的独立行为验收

命令：`python -m unittest discover -s __replens_acceptance__ -p test_behavior.py`

SHA-256：`13c64a01bcff739bf3b3fd6a48bde444c843ccee8820aa51e330639a086e65cf`

### 修复前

- 未通过（exit 1）
  - `test_behavior.IndependentBehavior.test_empty`: PASS
  - `test_behavior.IndependentBehavior.test_helper`: PASS
  - `test_behavior.IndependentBehavior.test_input_unchanged`: PASS
  - `test_behavior.IndependentBehavior.test_order`: PASS
  - `test_behavior.IndependentBehavior.test_strings`: FAIL

### 修复后

- 通过（exit 0）
  - `test_behavior.IndependentBehavior.test_empty`: PASS
  - `test_behavior.IndependentBehavior.test_helper`: PASS
  - `test_behavior.IndependentBehavior.test_input_unchanged`: PASS
  - `test_behavior.IndependentBehavior.test_order`: PASS
  - `test_behavior.IndependentBehavior.test_strings`: PASS
