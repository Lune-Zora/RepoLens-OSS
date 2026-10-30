"""评测集自动挖掘器（Benchmark Miner）。

从任意 git 仓库自动挖掘历史修复提交，生成与 eval/datasets/dataset.json 同构的评测集：
- issue：修复提交标题（去掉 fix/BUG 前缀）
- expected：buggy 父提交中「第一个被修改源码文件的受影响函数」的 文件:函数:行号
- 同时导出源码补丁与测试补丁，并可自动验证 fail→pass。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .retriever import _is_test_path

FIX_KEYWORDS = ("fix", "bug", "修复")


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# 前缀剥离后若以这些「虚词」开头，说明原句被截断了，应保留原句。
_DANGLING_STARTS = (
    "that", "the", "a", "an", "to", "of", "for", "in", "on", "when", "while",
    "if", "it", "this", "these", "those", "is", "are", "was", "were", "do",
    "does", "did", "so", "such", "with", "from", "by",
)


def _clean_issue(subject: str) -> str:
    """把提交标题整理成 issue 描述。

    规则（2026-10-29 修订）：
    1. 剥掉常见前缀（fix/bug/rob/…），但用 \\b 词边界，避免把 `Fixes` 截成 `es`；
    2. 剥完若以虚词开头（如 `Fix that X cannot…` → `that X cannot…`），说明句子被截断，
       此时**保留原标题**，不产出残句；
    3. 剥完只剩 1-2 个词（`fix format_num` → `format_num`）同样不剥：
       那不是描述而是一个标识符，保留「Fix format_num」至少还是一句话。
       2026-10-29 实测：8 条弱描述里有 5 条是被这条规则削成名词短语的。
    """
    original = re.sub(r"\s+", " ", subject.strip())
    # 只收明确是「类型标签」的前缀；release/chore 等词也可能是句子开头（如 Release content streams…），
    # 误当前缀会把正文削掉（2026-10-29 实测踩到）。
    stripped = re.sub(r"^(fix|bug|hotfix|rob)\b[:：\s-]*", "",
                      original, flags=re.IGNORECASE).strip()
    if not stripped:
        return original[:200]
    first_word = stripped.split(maxsplit=1)[0].rstrip(":：,.").lower()
    if first_word in _DANGLING_STARTS:
        return original[:200]
    # 3. 句子式前缀（「Fix X」，后面是空格）剥完只剩 1-2 个词时保留原样：
    #    把 fix format_num 剥成 format_num，等于把一句话削成了标识符。
    #    标签式前缀（BUG: / ROB: / fix:）不受此限——分隔符是冒号或连字符时，
    #    作者本意就是打标签，剥掉才是对的。
    head = re.match(r"^(fix|bug|hotfix|rob)", original, flags=re.IGNORECASE)
    separator = original[len(head.group(0)):len(head.group(0)) + 1] if head else ""
    if separator not in (":", "：", "-") and len(_content_words(stripped)) < 3:
        return original[:200]
    return stripped[:200]


_VAGUE_WORDS = ("misc", "various", "stuff", "cleanup", "clean up", "minor",
                "tweaks", "edge cases")
_ISSUE_REF = re.compile(r"\(?#\d+\)?|\b(?:fixes|closes|resolves)\b\s*#?\d*", re.IGNORECASE)
_METADATA_LINE = re.compile(r"^(?:co-authored-by|signed-off-by|reviewed-by|change-id|"
                            r"see also|refs?|cherry picked from)\b", re.IGNORECASE)


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _content_words(text: str) -> List[str]:
    """取用于判「描述是否具体」的词：去掉 issue 引用、标点与纯数字。"""
    cleaned = _ISSUE_REF.sub(" ", text or "")
    return [word for word in re.findall(r"[A-Za-z][A-Za-z0-9_'-]+", cleaned)]


def _is_weak_issue(text: str) -> bool:
    """描述是否弱到不足以作为 issue。

    判据只有两条，宁可漏判也不要误判——误判会把好描述替换成正文里的实现细节：
    1. 实词不超过 3 个（format_num、rich: misc fixes）；
    2. 含 misc / various / edge cases 这类不指向具体行为的词。
    """
    if len(_content_words(text)) <= 3:
        return True
    lowered = _norm_space(text).lower()
    return any(vague in lowered for vague in _VAGUE_WORDS)


def _body_paragraphs(body: str) -> List[str]:
    """把提交正文拆成可当 issue 的段落。

    必须按空行合并折行：提交正文常在 72 列处硬折行，逐行取值会得到
    「When options are being parsed, multiple options may evaluate to have」这种半句
    （2026-10-29 click 实测）。署名行与引用行直接丢弃。
    """
    paragraphs: List[str] = []
    current: List[str] = []

    def flush():
        if current:
            paragraphs.append(_norm_space(" ".join(current)))
            current.clear()

    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if _METADATA_LINE.match(line) or line.startswith(">"):
            continue
        if re.fullmatch(r"[\W_]+", line):
            continue
        if re.match(r"^(?:[-*\u2022]|\d+[.)])\s+", line):
            # 列表项自成一段，但标记本身要去掉：issue 里不该出现「1.」「-」。
            flush()
            paragraphs.append(_norm_space(re.sub(r"^(?:[-*\u2022]|\d+[.)])\s+", "", line)))
            continue
        current.append(line)
    flush()
    return [paragraph for paragraph in paragraphs if paragraph]


def _first_sentence(text: str) -> str:
    """取第一句；不能把列表序号「1.」当成句号（2026-10-29 实测踩到）。"""
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        if match.end() >= 12:
            return text[:match.end()]
    return text


def _descriptive_line(body: str) -> Optional[str]:
    """从提交正文里挑一句能说明「修了什么」的原文。

    为什么不改写标题：本项目要保留「描述来自上游原文」这一属性。
    正文同样是上游原文，只是此前没用上（2026-10-29：tqdm/click 多条标题极简、正文详实）。
    """
    for paragraph in _body_paragraphs(body):
        candidate = _first_sentence(paragraph)
        if len(_content_words(candidate)) >= 4 and not _is_weak_issue(candidate):
            return candidate[:200]
    return None


def issue_from_subject(subject: str, body: str = "",
                       allow_body: bool = False) -> Tuple[str, str]:
    """返回 (issue 描述, 来源)。来源：commit_subject / commit_body。

    allow_body 默认关闭：把标题换成正文是「换出处」，应当由人工通读判定触发
    （scripts/repair_annotations.py 只在判定为 weak 时打开），
    否则启发式会把「pandas>=3 & negative format_interval」这类较短的合格标题
    误换成正文里的某条实现细节（2026-10-29 实测）。
    """
    text = _clean_issue(subject)
    if allow_body:
        # 调用方（repair_annotations）只有在人工判定为 weak 时才打开 allow_body，
        # 所以这里不再重复判弱：判据卡在 4 个实词时会把
        # 「shell completion for nested groups」这种仍是名词短语的标题漏掉。
        candidate = _descriptive_line(body)
        if candidate and not _is_weak_issue(candidate):
            return candidate, "commit_body"
    return text, "commit_subject"


def issue_from_commit(repo: str, commit: str,
                      allow_body: bool = False) -> Tuple[str, str]:
    """同 issue_from_subject，但直接从提交里取标题与正文。"""
    subject = _git(repo, "log", "-1", "--format=%s", commit).stdout.strip()
    body = _git(repo, "log", "-1", "--format=%b", commit).stdout
    return issue_from_subject(subject, body, allow_body=allow_body)


def _first_changed_old_line(diff_text: str) -> Optional[int]:
    """返回「改动位置」在旧文件中的行号。

    两种情况：
    - 有删除行：返回第一条被删除的旧行号；
    - 纯插入：返回插入点**前一行**（插入发生在它之后），而不是 hunk 起始行。

    为什么不能回退到 hunk 起始行（2026-10-29 修复）：
    hunk 起始行往往只是前导上下文，与改动无关。requests 的 `__reduce__` 一例中，
    hunk 从旧 41 行开始、但前 3 行都是上下文，插入实际发生在旧 43 行之后；
    回退到 41 会把归属算成 `__init__`，而真正的落点是其外层类 `JSONDecodeError`。
    """
    old_no = None
    insertion_before = None
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            if insertion_before is not None:
                break  # 上个 hunk 已确定纯插入位置
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+", line)
            if m:
                old_no = int(m.group(1))
            continue
        if old_no is None:
            continue
        if line.startswith(" "):
            old_no += 1
        elif line.startswith("-"):
            return old_no
        elif line.startswith("+") and not line.startswith("+++"):
            if insertion_before is None:
                # 插入发生在 old_no 之前，因此其前一行为 old_no - 1。
                insertion_before = max(old_no - 1, 1)
    return insertion_before


def _first_changed_new_line(diff_text: str) -> Optional[int]:
    """返回改动位置在新文件中的行号（取第一条新增行；无新增则取删除点）。"""
    new_no = None
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            if m:
                new_no = int(m.group(1))
            continue
        if new_no is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            return new_no
        if line.startswith(" "):
            new_no += 1
        elif line.startswith("-"):
            pass
    return None


def _innermost_symbol(source: str, line: int):
    """返回包含指定行的最内层函数/类节点，找不到返回 None。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.lineno <= line <= (node.end_lineno or node.lineno):
                if best is None or node.lineno >= best.lineno:
                    best = node
    return best


def _enclosing_class(source: str, symbol_name: str):
    """返回包含指定名字符号的最内层类节点（若该符号本身就是类则返回 None）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                        and child.name == symbol_name:
                    if best is None or node.lineno >= best.lineno:
                        best = node
                    break
    return best


def _module_symbols(source: str) -> dict:
    """返回 {名字: 起始行}，用于判断某符号是否已在旧版本中存在。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        for child in ast.walk(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.setdefault(child.name, child.lineno)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.setdefault(node.name, node.lineno)
    return out


def attribute_symbol(old_source: str, new_source: str, old_pos: int,
                     new_pos: Optional[int]):
    """决定把改动归给哪个符号，返回 (符号名, 在旧文件中的行号) 或 (None, None)。

    规则（2026-10-29 新增）：
    1. 新文件里包含改动行的最内层符号，若在旧版本中已存在 → 用它（修改已有符号）；
    2. 若该符号是**新增**的（旧版本没有）→ 归到它的外层类（类在旧版本存在，且包含改动区域）；
    3. 否则退回旧文件改动位置的符号。

    为什么需要第 2 条：新增方法的样本若归属到被插入的函数（上下文行所属符号），
    标注会指向未被改动的行；归到外层类则既存在又包含改动，语义正确。

    行号一律取「符号定义行」，不取改动行（2026-10-29 复核 C9 抓到）：
    纯删除的补丁没有新增行（new_pos 为 None）时，旧实现退回 old_pos——
    那是改动行而不是定义行，指标按 ±3 行判定必然落空。
    """
    old_symbols = _module_symbols(old_source)
    node = _innermost_symbol(new_source, new_pos) if new_pos else None
    old_node = _innermost_symbol(old_source, old_pos) if old_pos else None
    if node is not None:
        if node.name in old_symbols:
            # 同名符号可能出现多次；优先用「旧文件里同样位置的那个」，避免张冠李戴。
            if old_node is not None and old_node.name == node.name:
                return node.name, old_node.lineno
            return node.name, old_symbols[node.name]
        cls = _enclosing_class(new_source, node.name)
        if cls is not None and cls.name in old_symbols:
            return cls.name, old_symbols[cls.name]
    if old_node is not None:
        return old_node.name, old_node.lineno
    return None, None


def _symbol_at_line(source: str, line: int) -> Optional[str]:
    """返回包含指定行的最内层函数/类名。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    best: Optional[Tuple[str, int]] = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.lineno <= line <= (node.end_lineno or node.lineno):
                if best is None or node.lineno > best[1]:
                    best = (node.name, node.lineno)
    return best[0] if best else None


def _write_patch_files(items, patches_dir: str, repo: str) -> None:
    Path(patches_dir).mkdir(parents=True, exist_ok=True)
    for it in items:
        src_diff = _git(repo, "diff", it["parent"], it["commit"], "--",
                        *it["src_files"]).stdout
        test_diff = _git(repo, "diff", it["parent"], it["commit"], "--",
                         *it["test_files"]).stdout
        base = f"{it['id']:03d}"
        (Path(patches_dir) / (base + "_src.diff")).write_text(src_diff, encoding="utf-8")
        (Path(patches_dir) / (base + "_test.diff")).write_text(test_diff, encoding="utf-8")


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _changed_right_lines(test_diff: str) -> dict:
    """把测试补丁按文件拆成「新增/修改行的右侧行号」集合。"""
    changed: dict = {}
    current: Optional[str] = None
    right: Optional[int] = None
    for line in test_diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip()
            changed.setdefault(current, set())
            right = None
            continue
        m = _HUNK_RE.match(line)
        if m:
            right = int(m.group(1))
            continue
        if current is None or right is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            changed[current].add(right)
            right += 1
        elif line.startswith(" "):
            right += 1
    return changed


def _test_node_spans(source: str) -> List[Tuple[int, int, str]]:
    """返回 (起始行, 结束行, pytest 节点 id) ——方法必须带上所在类。

    为什么带类名（2026-10-29 实测）：requests 的
    tests/test_requests.py::test_proxy_authorization_not_appended_to_https_request
    是 class TestRequests 的方法。只写函数名时 pytest 报 "ERROR: not found"（退出码 4），
    于是一条有效修复被记成「修复后测试未通过」。pytest 的节点 id 是 文件::类::方法。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    spans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        chain: List[str] = []
        parent = parents.get(node)
        while parent is not None:
            if isinstance(parent, ast.ClassDef):
                chain.append(parent.name)
            parent = parents.get(parent)
        # 装饰器（如 parametrize 的参数列表）可能被改动，而 node.lineno 指向 def 行；
        # 只按 def 行判断会漏掉「只改参数列表」的情况（2026-10-29 loguru 实测）。
        start = node.lineno
        for decorator in getattr(node, "decorator_list", []):
            start = min(start, decorator.lineno)
        node_id = "::".join(list(reversed(chain)) + [node.name])
        spans.append((start, node.end_lineno or node.lineno, node_id))
    return spans


def _enclosing_test_functions(source: str, lines) -> List[str]:
    """给定源码与行号集合，返回覆盖这些行的测试节点 id（模块级函数只有函数名）。"""
    spans = _test_node_spans(source)
    found: List[str] = []
    for line in lines:
        for start, end, name in spans:
            if start <= line <= end and name not in found:
                found.append(name)
    return found


def _affected_test_nodes(test_diff: str, repo: Optional[str] = None,
                         commit: Optional[str] = None) -> List[str]:
    """从测试补丁推导受影响的 pytest 节点。

    为什么需要：原先用「整个测试文件」作为验证命令，只要该文件里存在与本提交无关的失败用例，
    就会把有效修复误判为「修复后测试未通过」。2026-10-29 实测：loguru 旧记录 15/15 全灭，
    改定向后同批提交 6/6 通过。

    为什么不止扫 `+def test_`：2026-10-29 实测 5 条退化样本中，3 条是「修改已有测试函数」，
    2 条改动落在函数体内、hunk 里根本不出现 def。故按行号反查所属测试函数。
    """
    changed = _changed_right_lines(test_diff)
    nodes: List[str] = []

    for path, lines in changed.items():
        if not lines:
            continue
        if repo and commit:
            source = _git(repo, "show", commit + ":" + path).stdout
            for name in _enclosing_test_functions(source, sorted(lines)):
                node = path + "::" + name
                if node not in nodes:
                    nodes.append(node)

    # 兜底：无法取得源码时（或行号映射为空），退回扫描新增的测试定义。
    # 必须按 diff 当前文件归属，否则会把 A 文件的测试名错误挂到 B 文件上。
    if not nodes:
        current: Optional[str] = None
        for line in test_diff.splitlines():
            if line.startswith("+++ b/"):
                current = line[len("+++ b/"):].strip()
                continue
            m = re.match(r"\+\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", line)
            if m and current:
                node = current + "::" + m.group(1)
                if node not in nodes:
                    nodes.append(node)
    return nodes


def _worktree_env(tmp: str) -> dict:
    """构造验证用环境变量：让 worktree 里的源码优先于已安装的同名包。

    为什么必须做（2026-10-29 实测）：click 采用 src 布局且已装进 site-packages，
    在 worktree 里直接 import click 会解析到**已安装版本**，补丁改的源码根本没被执行，
    于是所有候选都被判「修复后测试未通过」——3/3 全灭，看起来像数据不行，实际是测错了代码。
    """
    env = dict(os.environ)
    bootstrap = [str(tmp)]
    src = Path(tmp) / "src"
    if src.is_dir():
        bootstrap.insert(0, str(src))
    existing = env.get("PYTHONPATH")
    if existing:
        bootstrap.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(bootstrap)
    return env


def patch_file_sets(repo: str, parent: str, commit: str):
    """按同一套判据把提交改动的文件分成 (测试文件, 源码文件)。

    数据集只存 parent/commit 与 test_cmd，重验时必须能自己重建补丁现场，
    否则「改完标注再验一遍」只能靠人去翻当时挖出来的 patches 目录。
    """
    files_out = _git(repo, "show", "--name-only", "--format=", commit)
    files = [line.strip() for line in (files_out.stdout or "").splitlines() if line.strip()]
    test_files = [f for f in files if f.endswith(".py") and _is_test_path(f)]
    src_files = [f for f in files if f.endswith(".py") and not _is_test_path(f)]
    return test_files[:3], src_files[:3]


def verify_item(repo: str, parent: str, commit: str, test_files, src_files,
                item_id: int = 0, timeout: int = 120, python: Optional[str] = None) -> dict:
    """在临时 worktree 中复现 fail→pass。

    补丁在现场用 git diff 生成，不再依赖预写好的 patches 目录——
    数据集（eval/datasets/*.json）只存 parent/commit 与文件清单，
    要做到「标注改了就重验」必须能只凭这些字段重建验证现场（2026-10-29）。
    """
    python = python or sys.executable
    tmp = tempfile.mkdtemp(prefix="miner_val_")
    patch_dir = Path(tempfile.mkdtemp(prefix="miner_patch_"))
    test_files = list(test_files or [])
    src_files = list(src_files or [])
    if not test_files or not src_files:
        derived_tests, derived_src = patch_file_sets(repo, parent, commit)
        test_files = test_files or derived_tests
        src_files = src_files or derived_src
        if not test_files or not src_files:
            return {"id": item_id, "ok": False,
                    "reason": "本次提交未同时改动源码与测试，无法复现 fail→pass"}
    try:
        src_patch = patch_dir / "src.diff"
        test_patch = patch_dir / "test.diff"
        src_patch.write_text(_git(repo, "diff", parent, commit, "--", *src_files).stdout,
                             encoding="utf-8")
        test_patch.write_text(_git(repo, "diff", parent, commit, "--", *test_files).stdout,
                              encoding="utf-8")
        if _git(repo, "worktree", "add", "--detach", tmp, parent).returncode != 0:
            return {"id": item_id, "ok": False, "reason": "worktree add 失败"}
        _git(tmp, "config", "core.autocrlf", "false")
        # 应用测试补丁（忽略空白差异，兼容 CRLF 工作区）
        if _git(tmp, "apply", "--ignore-whitespace", str(test_patch)).returncode != 0:
            return {"id": item_id, "ok": False, "reason": "测试补丁应用失败"}
        # 定向到本次补丁新增的测试；提取不到才退回整个测试文件。
        affected = _affected_test_nodes(
            test_patch.read_text(encoding="utf-8", errors="replace"), repo, commit)
        scope = "targeted" if affected else "file"
        targets = affected if affected else test_files
        # -u：输出不缓冲。卡死时日志里至少能看到跑到哪一步，否则只会看到一个空文件。
        test_cmd = [python, "-u", "-m", "pytest"] + targets + ["-q"]
        env = _worktree_env(tmp)
        before_log = patch_dir / "before.log"
        if _run_pytest(test_cmd, tmp, timeout, env, before_log) == 0:
            return {"id": item_id, "ok": False, "reason": "修复前测试未失败", "scope": scope,
                    "targets": targets}
        # 应用源码补丁
        if _git(tmp, "apply", "--ignore-whitespace", str(src_patch)).returncode != 0:
            return {"id": item_id, "ok": False, "reason": "源码补丁应用失败",
                    "scope": scope, "targets": targets}
        after_log = patch_dir / "after.log"
        if _run_pytest(test_cmd, tmp, timeout, env, after_log) != 0:
            text = after_log.read_text(encoding="utf-8", errors="replace") \
                if after_log.exists() else ""
            return {"id": item_id, "ok": False, "reason": "修复后测试未通过",
                    "scope": scope, "targets": targets, "detail": _failure_tail(text)}
        return {"id": item_id, "ok": True, "reason": "fail→pass",
                "scope": scope, "targets": targets}
    except subprocess.TimeoutExpired:
        return {"id": item_id, "ok": False, "reason": "测试超时"}
    finally:
        _git(repo, "worktree", "remove", "--force", tmp)
        shutil.rmtree(patch_dir, ignore_errors=True)


def _kill_tree(process) -> None:
    """连子进程一起杀。

    为什么不用 process.kill()：pytest 会拉起子进程（插件、被测代码的 multiprocessing），
    只杀父进程会留下孤儿继续占用临时 worktree，下一轮 worktree remove 就会卡住
    （2026-10-29 实测：三条 pypdf/click 用例把 4 并发全占满）。
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, text=True)
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _run_pytest(command, cwd, timeout, env, log_path):
    """执行 pytest，输出写文件而不是管道，并用 Popen 自己看门。

    为什么不用 subprocess.run(timeout=)（2026-10-29 实测卡死）：
    1. 管道：pytest 的子进程会继承管道句柄，超时杀掉 pytest 后仍要等 EOF，timeout 形同虚设；
    2. 进程树：即使没有管道，run() 只会杀 pytest 本身，孤儿进程仍占着 worktree。
    这里输出走文件，超时用 taskkill /T 杀整棵树，然后抛 TimeoutExpired 交给调用方记录。
    """
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, env=env)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            raise


def _failure_tail(text: str) -> str:
    """取测试失败输出的最后几行：写进验证结果，避免「未通过」三个字查不出原因。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(lines[-3:])[:300]


def _verification_env(repo: str, parent: str, era: Optional[str]):
    """按时代选出验证用解释器。

    era 取值：None / "off" 用当前解释器；"auto" 按父提交声明选；其它值当作 profile 名。
    返回 (python, env 记录, 选择依据)。环境建不起来时抛 era_env.EraEnvError。
    """
    from . import era_env

    if not era or era == "off":
        return sys.executable, era_env.describe({"profile": era_env.MODERN}), "命令行关闭时代环境"
    if era == "auto":
        profile, note = era_env.select_profile(repo, parent)
    else:
        profile, note = era, "命令行指定 profile"
    setup_py = _git(repo, "show", parent + ":setup.py").stdout
    environment = era_env.ensure(profile, runtime=era_env.runtime_specifiers(setup_py))
    return environment["python"], era_env.describe(environment), note


def mine_repo(
    repo: str,
    max_commits: int = 20,
    validate: bool = False,
    timeout: int = 120,
    output: Optional[str] = None,
    era_env: Optional[str] = None,
) -> dict:
    """挖掘修复提交并输出数据集。"""
    commits: List[Tuple[str, str]] = []
    log = _git(repo, "log", "--all", "--format=%H%x09%s", "-n", "800")
    for line in (log.stdout or "").splitlines():
        if "\t" not in line:
            continue
        h, s = line.split("\t", 1)
        if any(k in s.lower() for k in FIX_KEYWORDS):
            commits.append((h, s))
        if len(commits) >= max_commits * 3:
            break

    items: List[dict] = []
    for commit, subject in commits:
        files_out = _git(repo, "show", "--name-only", "--format=", commit)
        files = [f.strip() for f in (files_out.stdout or "").splitlines() if f.strip()]
        test_files = [f for f in files if f.endswith(".py") and _is_test_path(f)]
        src_files = [f for f in files if f.endswith(".py") and not _is_test_path(f)]
        if not test_files or not src_files:
            continue
        parent = _git(repo, "rev-parse", commit + "^").stdout.strip()
        if not parent:
            continue
        src_diff = _git(repo, "diff", parent, commit, "--", *src_files[:5]).stdout
        if not src_diff:
            continue
        line = _first_changed_old_line(src_diff)
        if line is None:
            continue
        old_src = _git(repo, "show", parent + ":" + src_files[0]).stdout
        new_src = _git(repo, "show", commit + ":" + src_files[0]).stdout
        func, func_line = attribute_symbol(old_src, new_src, line,
                                           _first_changed_new_line(src_diff))
        # 标注格式只接受 path / path:function / path:function:line。
        # 此前 func 为空时会写成 path:line，被 parse_expected 当成「函数名是数字」而判无效。
        expected = [f"{src_files[0]}:{func}:{func_line}" if func else src_files[0]]
        # issue 取标题；标题过弱时退到提交正文（仍是上游原文，不改写）。
        issue, issue_source = issue_from_commit(repo, commit)
        items.append({
            "id": len(items) + 1,
            "category": "bug",
            "issue": issue,
            "issue_source": issue_source,
            "expected": expected,
            "commit": commit,
            "parent": parent,
            "test_files": test_files[:3],
            "src_files": src_files[:3],
        })
        if len(items) >= max_commits:
            break

    patches_dir = str(Path(repo).resolve().parent / ("mined_patches_" + Path(repo).name))
    _write_patch_files(items, patches_dir, repo)

    validation = []
    if validate:
        from .era_env import EraEnvError
        for it in items:
            try:
                python, environment, note = _verification_env(repo, it["parent"], era_env)
            except EraEnvError as exc:
                validation.append({"id": it["id"], "ok": False,
                                   "reason": "时代环境不可用: " + str(exc)[:200]})
                continue
            record = verify_item(repo, it["parent"], it["commit"], it["test_files"],
                                 it["src_files"], item_id=it["id"], timeout=timeout,
                                 python=python)
            record["env"] = environment
            record["env_note"] = note
            validation.append(record)

    project = Path(repo).resolve().name
    dataset = []
    for it in items:
        v = next((x for x in validation if x["id"] == it["id"]), None)
        # 2026-10-28：保留 commit / parent / 文件清单。
        # 此前只导出 id/category/issue/expected，导致已验证的候选**无法提升为修复集样本**
        # （缺 buggy_parent 与 fix_commit 就还原不出 buggy 基线），挖掘成果只能停在定位级。
        entry = {k: it[k] for k in ("id", "category", "issue", "issue_source",
                                     "expected", "commit", "parent")}
        entry["project"] = project
        entry["buggy_parent"] = it["parent"]
        entry["fix_commit"] = it["commit"]
        entry["test_files"] = it["test_files"]
        entry["src_files"] = it["src_files"]
        if v:
            entry["validation"] = v
            if v.get("env", {}).get("profile"):
                entry["env_profile"] = v["env"]["profile"]
        dataset.append(entry)

    result = {
        "repo": repo,
        "mined": len(items),
        "validated_ok": sum(1 for v in validation if v["ok"]),
        "patches_dir": patches_dir,
        "items": dataset,
    }
    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=1), encoding="utf-8")
        result["output"] = str(out_path)
    return result
