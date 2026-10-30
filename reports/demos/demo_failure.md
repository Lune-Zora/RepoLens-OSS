# Demo · 诚实失败案例（拒答）

> 这是一条**真实跑出**的 demo：给 RepoLens 一个它**回答不了**的 Issue。
> 正确行为是「证据不足 → 拒答」，而不是硬编一个看起来合理但其实猜的答案。

## 案例：add GPU acceleration support to the parser

**Issue 文本**

> add GPU acceleration support to the parser

**仓库**：`examples/demo_repo`

**期望结果**：`is_valid_refusal = True`（仓库里没有任何 GPU / CUDA 符号，
本工具应当明确告知「我找不到证据」）

**实际结果**

```text
insufficient = True
returned     = []
top1         = None
correct_refusal = True
```

**为什么是正确行为**

- `examples/demo_repo` 是个 4 模块的小项目，没有 GPU、CUDA、accelerate 之类的符号。
- BM25 没有任何 token 命中，retriever 触发「无 token 也无 hint」拒答分支。
- 不返回任何「看起来相关但实际无关」的候选，避免给用户误导。

**复现命令**

```bash
python replens/cli.py locate examples/demo_repo \
  --issue "add GPU acceleration support to the parser"
# 期望输出: 证据不足（insufficient evidence），不会硬答。
```

## 另两个对照样本

| Issue | insufficient | returned | correct_refusal |
|---|---|---|---|
| integrate neural network to predict holidays | True | [] | True |
| fix blockchain integration with cloud sync | True | [] | True |

三个 noise 案例一致：retriever 宁愿说「我不知道」也不编答案。

## 这条 demo 想证明什么

- **拒答是设计选择，不是失败**：当仓库里没有相关代码，强行给出候选文件只会
  污染后续 patch 生成。这与 GPT-6 §3.2 评审意见一致——「证据不足时明确返回
  拒答而非硬答」。
- **拒答率可被评测**：`replens.eval_runner` 把 `expected=[]` 的样本计为
  `noise`，要求 `refusal_rate` 达到 1.0 才算合格（见 `eval/ablation.py`
  实测结果 `refusal_rate: 1.0`）。
- **不会因为 Issue「看起来很合理」就放水**：GPU / 神经网络 / 区块链这些
  buzzword 单独出现时，retriever 仍然以 token 命中为准。
