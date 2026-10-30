"""测试执行：临时副本 + 白名单命令 + 超时。

执行约束（不等同于安全沙箱）：
1. 文件层：shutil.copytree 复制到临时目录，在副本上应用补丁。
2. 命令层：白名单校验，拒绝白名单之外的任何命令。
3. 进程层：subprocess + timeout，超时强制终止并回收。

补丁支持两种格式：
- JSON 替换规则：{"相对路径": {"from": 旧串, "to": 新串}}（跨平台最稳）
- unified diff：用 git apply 应用（更接近真实 PR）
"""
from __future__ import annotations

import difflib
import re
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_ALLOWED_COMMANDS = [
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "python -m unittest discover",
]

_IGNORE_DIRS = {".git", ".replens", "__pycache__", ".venv", "venv", ".tox", "build",
                "dist", "node_modules", ".idea", ".vscode"}


@dataclass
class TestResult:
    command: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    passed: bool = False
    valid: bool = True
    # 新增：从输出解析的测试摘要（passed/failed/errors/skipped/collected/ran）
    summary: Optional[Dict[str, int]] = None
    # 新增：明确无效原因（便于报告呈现）
    invalid_reason: str = ""
    tests: Dict[str, str] = field(default_factory=dict)
    structured: bool = False
    framework: str = ""

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout[-4000:],
            "stderr": self.stderr[-4000:],
            "timed_out": self.timed_out,
            "passed": self.passed,
            "valid": self.valid,
            "summary": self.summary,
            "invalid_reason": self.invalid_reason,
            "tests": self.tests,
            "structured": self.structured,
            "framework": self.framework,
        }


class CommandNotAllowedError(Exception):
    pass


# shell 元字符：命令里含这些一律拒绝（即使 subprocess 用 list 不经过 shell 解释，
# 也要防止未来有人把 shell 开关改回来，以及防止参数里夹带危险字符）
_SHELL_META = set("&|;><$`\n\r")


# 测试摘要解析（pytest / unittest 输出兼容）
_SUMMARY_PATTERNS = [
    ("passed", re.compile(r"(\d+)\s+passed", re.IGNORECASE)),
    ("failed", re.compile(r"(\d+)\s+failed", re.IGNORECASE)),
    ("errors", re.compile(r"(\d+)\s+errors?", re.IGNORECASE)),
    ("skipped", re.compile(r"(\d+)\s+skipped", re.IGNORECASE)),
    # unittest 风格：OK (skipped=N) 或 FAILED (failures=N, errors=N)
    ("unittest_skipped", re.compile(r"skipped=(\d+)", re.IGNORECASE)),
    ("unittest_failures", re.compile(r"(?<!expected )\bfailures=(\d+)", re.IGNORECASE)),
    ("unittest_errors", re.compile(r"errors=(\d+)", re.IGNORECASE)),
    ("xfailed", re.compile(r"(\d+)\s+xfailed", re.IGNORECASE)),
    ("xpassed", re.compile(r"(\d+)\s+xpassed", re.IGNORECASE)),
    ("unittest_xfailed", re.compile(r"expected failures=(\d+)", re.IGNORECASE)),
    ("unittest_xpassed", re.compile(r"unexpected successes=(\d+)", re.IGNORECASE)),
    ("collected", re.compile(r"collected\s+(\d+)\s+items?", re.IGNORECASE)),
    ("ran", re.compile(r"ran\s+(\d+)\s+tests?", re.IGNORECASE)),
]


def _parse_test_summary(text: str) -> dict:
    """从 pytest/unittest 输出解析测试摘要。

    返回 {'passed': int, 'failed': int, 'errors': int, 'skipped': int,
          'collected': int, 'ran': int}，找不到的字段为 0。
    unittest 风格的 OK (skipped=N) 也会累计到 skipped，
    并按 ran-skipped 反推 passed（unittest 没有显式 passed 数字）。
    """
    out = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
           "collected": 0, "ran": 0, "xfailed": 0, "xpassed": 0}
    if not text:
        return out
    for name, pat in _SUMMARY_PATTERNS:
        matches = pat.findall(text)
        if matches:
            if name == "unittest_skipped":
                target_key = "skipped"
            elif name == "unittest_failures":
                target_key = "failed"
            elif name == "unittest_errors":
                target_key = "errors"
            elif name == "unittest_xfailed":
                target_key = "xfailed"
            elif name == "unittest_xpassed":
                target_key = "xpassed"
            else:
                target_key = name
            new_val = int(matches[-1])
            out[target_key] = max(out[target_key], new_val)
    # unittest 末尾：OK 视为全部通过，passed = ran - skipped - errors - failed
    if re.search(r"\bOK\b", text) and out["ran"] > 0:
        out["passed"] = max(
            out["ran"] - out["skipped"] - out["errors"] - out["failed"] - out['xfailed'] - out['xpassed'], 0)
    return out


def _compute_valid(stdout: str, stderr: str, returncode: int, timed_out: bool) -> tuple:
    """判定测试运行是否产生有效结果。

    失效场景（任一即视为无效）：
    - 缺依赖（No module named / ModuleNotFoundError）
    - 零测试收集（pytest: collected 0 items / exit 5；unittest: Ran 0 tests）
    - pytest "0 passed"（含全部跳过）
    - 超时

    返回 (valid: bool, summary: dict, reason: str)。
    """
    text = (stdout or "") + "\n" + (stderr or "")
    summary = _parse_test_summary(text)
    if timed_out:
        return False, summary, "测试超时"
    low = text.lower()
    if "no module named" in low or "modulenotfounderror" in low:
        return False, summary, "缺依赖（No module named）"
    if "collected 0 items" in low or "ran 0 tests" in low:
        return False, summary, "零测试收集"
    if returncode == 5:  # pytest: no tests collected
        return False, summary, "pytest 退出码 5（无测试）"
    # pytest: "0 passed, X skipped" 表示没有实际通过的测试
    if summary["passed"] == 0 and summary["failed"] == 0 and summary["errors"] == 0:
        return False, summary, "没有实际执行测试（全跳过、仅收集或零测试）"
    return True, summary, ""


def _is_allowed(command: str, allowed: List[str]) -> bool:
    """严格白名单校验。

    与旧版的字符串前缀匹配不同，这里：
    1. 拒绝含 shell 元字符的命令（防注入）；
    2. shlex 分词后，要求 argv 前缀与某个白名单条目的 argv 完全一致
       （例如 `python -m unittest && rm -rf /` 因含 `&` 被拒；
         `python -c "..."` 因 argv[:2]=['python','-c'] 不匹配任何白名单被拒）。
    """
    cmd = command.strip()
    if not cmd:
        return False
    if any(ch in _SHELL_META for ch in cmd):
        return False
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if not argv:
        return False
    for a in allowed:
        a_argv = shlex.split(a.strip())
        if a_argv and argv[:len(a_argv)] == a_argv:
            return True
    return False


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _write(p: str, content: str) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def _safe_target(tmp_root: str, rel: str) -> Optional[str]:
    """解析补丁目标，并确保它位于临时副本内。"""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    root = os.path.realpath(tmp_root)
    target = os.path.realpath(os.path.join(root, rel))
    try:
        if os.path.commonpath([root, target]) != root:
            return None
    except ValueError:
        return None
    return target


def _apply_json_patch(tmp_root: str, patch_file: str) -> str:
    try:
        rules = json.loads(_read(patch_file))
    except json.JSONDecodeError as e:
        return f"补丁 JSON 解析失败: {e}"
    applied = 0
    for rel, rule in rules.items():
        fp = _safe_target(tmp_root, rel)
        if not fp or not os.path.isfile(fp):
            return f"补丁目标文件不存在: {rel}"
        if not isinstance(rule, dict) or not isinstance(rule.get("from"), str) or not isinstance(rule.get("to"), str):
            return f"补丁规则结构错误: {rel}"
        content = _read(fp)
        old_text = rule.get("from", "")
        new_text = rule.get("to", "")
        if old_text not in content:
            # Windows/CRLF 仓库的补丁通常以 LF 编写，自动尝试 CRLF 变体
            crlf_old = old_text.replace("\n", "\r\n")
            crlf_new = new_text.replace("\n", "\r\n")
            if crlf_old in content:
                old_text, new_text = crlf_old, crlf_new
            else:
                return f"补丁未匹配到目标内容: {rel}"
        if not old_text:
            return f"补丁 from 不能为空: {rel}"
        if content.count(old_text) != 1:
            return f"补丁未唯一匹配目标内容: {rel}"
        content = content.replace(old_text, new_text, 1)
        _write(fp, content)
        applied += 1
    return f"补丁应用成功（JSON 替换规则，共 {applied} 处）。"


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _apply_unified_diff(tmp_root: str, patch_file: str) -> str:
    """用 difflib 风格的 hunk 解析器应用 unified diff（零外部依赖，跨平台）。

    不再依赖 git apply：临时副本没有 .git 目录，git apply 必然失败。
    解析 ---/+++/@@ 头与 hunk，逐行校验上下文后重建目标文件。
    """
    try:
        text = _read(patch_file)
    except OSError as e:
        return f"补丁读取失败: {e}"

    file_patches = []
    target = None
    hunks = []
    cur = None
    for raw in text.splitlines():
        if raw.startswith("--- "):
            if cur is not None:
                hunks.append(cur)
                cur = None
            if target is not None and hunks:
                file_patches.append((target, hunks))
                hunks = []
            target = raw[4:].strip().split("\t")[0]
            if target.startswith("a/"):
                target = target[2:]
        elif raw.startswith("+++ ") or raw.startswith("diff --git ") or raw.startswith("index "):
            continue
        elif raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if not m:
                return "无法解析 hunk 头: " + raw
            if cur is not None:
                hunks.append(cur)
            cur = {"old_start": int(m.group(1)),
                   "old_len": int(m.group(2) or 1),
                   "lines": []}
        elif cur is not None:
            if raw.startswith("+") or raw.startswith("-") or raw.startswith(" "):
                cur["lines"].append(raw)
            # 忽略 \ No newline 等附加标记行
    if cur is not None:
        hunks.append(cur)
    if target is not None and hunks:
        file_patches.append((target, hunks))

    if not file_patches:
        return "补丁文件为空或无法解析目标文件"
    pending_writes = []
    for target, target_hunks in file_patches:
        if target.startswith("b/"):
            target = target[2:]
        fp = _safe_target(tmp_root, target)
        if not fp or not os.path.isfile(fp):
            return f"补丁目标文件不存在或越界: {target}"
        old_lines = _read(fp).splitlines()
        new_lines: List[str] = []
        pos = 0
        for h in target_hunks:
            old_start = h["old_start"]
            if old_start - 1 < pos:
                return f"hunk 顺序错误或重叠: {target}"
            new_lines.extend(old_lines[pos:old_start - 1])
            pos = old_start - 1
            consumed = 0
            for ln in h["lines"]:
                if ln.startswith(" "):
                    ctx = ln[1:]
                    if pos >= len(old_lines) or old_lines[pos] != ctx:
                        return f"上下文不匹配: {target} 第 {pos + 1} 行"
                    new_lines.append(old_lines[pos]); pos += 1; consumed += 1
                elif ln.startswith("-"):
                    expected = ln[1:]
                    if pos >= len(old_lines) or old_lines[pos] != expected:
                        return f"删除行不匹配: {target} 第 {pos + 1} 行"
                    pos += 1; consumed += 1
                elif ln.startswith("+"):
                    new_lines.append(ln[1:])
            if consumed != h["old_len"]:
                return f"hunk 行数与声明不符: {target} ({consumed} != {h['old_len']})"
        new_lines.extend(old_lines[pos:])
        pending_writes.append((fp, "\n".join(new_lines) + "\n"))
    for fp, content in pending_writes:
        _write(fp, content)
    return f"补丁应用成功（unified diff，共 {len(file_patches)} 个文件）。"


def _apply_patch(tmp_root: str, patch_file: str) -> str:
    if patch_file.lower().endswith(".json"):
        return _apply_json_patch(tmp_root, patch_file)
    return _apply_unified_diff(tmp_root, patch_file)


def _write_extra_files(tmp_root: str, extra_files: Dict[str, str]) -> None:
    for rel, content in extra_files.items():
        target = _safe_target(tmp_root, rel)
        if not target:
            raise ValueError("附加测试路径越界: " + str(rel))
        # 把 testgen 生成的占位符替换为临时副本实际路径
        content = content.replace("__TMP_REPO__", tmp_root.replace("\\", "/"))
        _write(target, content)


def _resolve_command(cmd: str) -> List[str]:
    """把裸命令解析为可执行的参数列表。

    关键：不依赖 PATH 里的 python/pytest，改用当前解释器 sys.executable，
    保证用装好 pytest 的受管 Python 来运行。
    """
    parts = shlex.split(cmd)
    if not parts:
        return parts
    head = parts[0]
    if head == "pytest":
        return [sys.executable, "-m", "pytest"] + parts[1:]
    if head in ("python", "python3"):
        return [sys.executable] + parts[1:]
    return parts


def _copytree(src: str, dst: str) -> None:
    def _ignore(d, names):
        return [n for n in names if n in _IGNORE_DIRS]
    shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)


def render_patch_as_diff(repo_root: str, patch_file: str) -> str:
    """把补丁文件渲染成 unified diff 文本（供报告展示）。"""
    if patch_file.lower().endswith(".json"):
        try:
            rules = json.loads(_read(patch_file))
        except Exception:
            return _read(patch_file)
        out = []
        for rel, rule in rules.items():
            orig = _read(os.path.join(repo_root, rel))
            new = orig.replace(rule["from"], rule["to"])
            diff = difflib.unified_diff(
                orig.splitlines(), new.splitlines(),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="")
            out.extend(diff)
        return "\n".join(out)
    return _read(patch_file)


def test_framework(command: str) -> str:
    parts = shlex.split(command)
    if parts[:1] == ['pytest'] or parts[:3] == ['python', '-m', 'pytest']:
        return 'pytest'
    if parts[:3] == ['python', '-m', 'unittest']:
        return 'unittest'
    raise ValueError('不支持的测试框架: ' + command)


def _run_one(test_command: str, cwd: str, timeout: int) -> TestResult:
    framework = test_framework(test_command)
    parts = shlex.split(test_command)
    arguments = parts[1:] if parts[0] == 'pytest' else parts[3:]
    report_root = tempfile.mkdtemp(prefix='replens_result_')
    report_path = Path(report_root) / 'results.json'
    args = [sys.executable, '-B', str(Path(__file__).with_name('test_adapter.py')),
            str(report_path), framework] + arguments
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1'))
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        tests, structured = {}, False
        reason = ''
        summary = None
        try:
            data = json.loads(report_path.read_text(encoding='utf-8'))
            tests = data['tests']
            statuses = {'PASS', 'FAIL', 'ERROR', 'SKIP', 'XFAIL', 'XPASS'}
            structured = (data.get('schema') == 1 and data.get('completed') is True
                          and data.get('framework') == framework and isinstance(tests, dict)
                          and all(isinstance(k, str) and k and v in statuses for k, v in tests.items()))
            if not structured:
                reason = '测试适配器未完成或结果格式无效'
            elif data.get('collection_errors'):
                reason = '测试收集或导入失败'
            elif not any(s in {'PASS', 'FAIL', 'ERROR'} for s in tests.values()):
                reason = '没有实际执行测试（全跳过、预期失败、仅收集或零测试）'
            summary = {name: sum(s == state for s in tests.values()) for name, state in
                       [('passed', 'PASS'), ('failed', 'FAIL'), ('errors', 'ERROR'),
                        ('skipped', 'SKIP'), ('xfailed', 'XFAIL'), ('xpassed', 'XPASS')]}
            summary.update(collected=data.get('collected', 0), ran=len(tests))
        except (OSError, ValueError, TypeError, KeyError):
            reason = '缺少有效的结构化测试结果'
            tests, structured = {}, False
        if 'no module named' in (stdout + stderr).lower():
            reason = '缺依赖（No module named）'
        if proc.returncode not in (0, 1):
            reason = reason or '测试框架异常退出（exit %s）' % proc.returncode
        valid = not reason
        return TestResult(
            command=test_command, returncode=proc.returncode,
            stdout=stdout, stderr=stderr,
            timed_out=False, passed=(valid and proc.returncode == 0
                                      and all(s in {'PASS', 'SKIP', 'XFAIL'} for s in tests.values())),
            valid=valid, summary=summary, invalid_reason=reason,
            tests=tests, structured=structured, framework=framework)
    except subprocess.TimeoutExpired as exc:
        def timeout_text(value):
            return value.decode('utf-8', 'replace') if isinstance(value, bytes) else (value or '')
        return TestResult(
            command=test_command, returncode=-1, timed_out=True,
            stdout=timeout_text(exc.stdout), stderr=timeout_text(exc.stderr), valid=False,
            invalid_reason=f"测试超时（>{timeout}s），已被强制终止。", framework=framework)
    except OSError as exc:
        return TestResult(test_command, -1, valid=False, stderr=str(exc),
                          invalid_reason='测试进程启动失败', framework=framework)
    finally:
        shutil.rmtree(report_root)


def _prepare_tmp(repo_root: str, extra_files: Optional[Dict[str, str]]) -> str:
    tmp_root = tempfile.mkdtemp(prefix="replens_")
    try:
        _copytree(repo_root, tmp_root)
        if extra_files:
            _write_extra_files(tmp_root, extra_files)
    except BaseException:
        shutil.rmtree(tmp_root)
        raise
    return tmp_root


def run_tests(
    repo_root: str,
    test_command: str,
    timeout: int = 120,
    patch_file: Optional[str] = None,
    extra_files: Optional[Dict[str, str]] = None,
    allowed_commands: Optional[List[str]] = None,
) -> tuple[List[TestResult], str, str]:
    """在临时副本中执行测试。返回 (结果列表, 临时目录, 补丁说明)。"""
    allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS
    if not _is_allowed(test_command, allowed):
        raise CommandNotAllowedError(
            f"命令不在白名单内，拒绝执行: {test_command!r}")

    tmp_root = _prepare_tmp(repo_root, extra_files)
    patch_note = ""
    if patch_file and os.path.isfile(patch_file):
        patch_note = _apply_patch(tmp_root, os.path.abspath(patch_file))
    result = _run_one(test_command, tmp_root, timeout)
    return [result], tmp_root, patch_note


def run_test_suite(
    repo_root: str,
    test_command: str,
    timeout: int = 120,
    patch_file: Optional[str] = None,
    extra_files: Optional[Dict[str, str]] = None,
    allowed_commands: Optional[List[str]] = None,
) -> tuple[List[TestResult], Optional[List[TestResult]], str]:
    """独立干净临时副本中分别跑修复前后，避免前测写文件污染后测。

    返回 (before_results, after_results, patch_note)。无补丁时 after 为 None。
    """
    allowed = allowed_commands or DEFAULT_ALLOWED_COMMANDS
    if not _is_allowed(test_command, allowed):
        raise CommandNotAllowedError(
            f"命令不在白名单内，拒绝执行: {test_command!r}")

    def protected(root):
        out = {}
        configs = {'pyproject.toml', 'pytest.ini', 'setup.cfg', 'tox.ini', 'conftest.py',
                   'setup.py', 'sitecustomize.py', 'usercustomize.py'}
        for p in Path(root).rglob('*'):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if (any(part in {'tests', 'test', '__replens_acceptance__'} for part in rel.parts)
                    or p.name.startswith('test_') or p.name.endswith('_test.py')
                    or p.name in configs):
                # 附加测试中的占位符会展开成不同副本路径，比较时还原。
                content = p.read_bytes().replace(str(root).replace('\\', '/').encode('utf-8'), b'__TMP_REPO__')
                content = content.replace(str(root).encode('utf-8'), b'__TMP_REPO__')
                out[rel.as_posix()] = hashlib.sha256(content).hexdigest()
        return out

    tmp_root = _prepare_tmp(repo_root, extra_files)
    try:
        frozen_tests = protected(tmp_root)
        before = [_run_one(test_command, tmp_root, timeout)]
    finally:
        shutil.rmtree(tmp_root)
    after = None
    patch_note = ""
    if patch_file:
        if not os.path.isfile(patch_file):
            return before, None, '补丁文件不存在'
        tmp_root = _prepare_tmp(repo_root, extra_files)
        try:
            patch_note = _apply_patch(tmp_root, os.path.abspath(patch_file))
            if patch_note.startswith("补丁应用成功"):
                if protected(tmp_root) != frozen_tests:
                    patch_note = '补丁应用失败：修改了受保护的测试或测试配置文件'
                else:
                    after = [_run_one(test_command, tmp_root, timeout)]
        finally:
            shutil.rmtree(tmp_root)
    return before, after, patch_note
