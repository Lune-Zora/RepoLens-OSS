"""按时代匹配的验证环境（era environment）。

为什么需要（2026-10-29 实测）：

  requests 的 11 条候选里 9 条在同一个解释器里必然被判失败，而原因与修复无关：

      tests/__init__.py:8: from urllib3.exceptions import SNIMissingWarning
      E   ImportError: cannot import name 'SNIMissingWarning' from 'urllib3.exceptions'

  SNIMissingWarning 在 urllib3 2.0 被移除，conftest 导入失败后 pytest 以退出码 4
  （用法错误）结束。把它当成「修复无效」是错的——被测代码与测试代码都属于另一个时代。

  上一轮试过「装 requests 的测试依赖」，没解决问题：瓶颈不是缺包，而是版本矩阵。
  2019 年的 requests 写的是 urllib3>=1.21.1,<1.27，2020 年才放宽到 <3。

做法（本模块）：

  1. 从候选父提交里读 setup.py / setup.cfg / requirements*.txt 的 urllib3 约束；
     读不到约束时再看测试代码是否直接导入 urllib3 1.x 的已移除名称；
  2. 映射到一份入库的时代锁文件 eval/envs/<profile>.txt（评审可见、可复现）；
  3. 在 <仓库>/.miner_envs/ 下建 venv（默认路径，已被 .gitignore 排除），
     先装锁文件里的测试工具，再装该提交 install_requires 里的运行期依赖；
  4. 返回该 venv 的 python，验证时用它跑 pytest，并把 profile / 版本写进验证结果。

不做的事：

  - 不改当前解释器与全局 site-packages（上一轮装 pytest-httpbin 把 greenlet 降级就是教训）；
  - 不猜测「哪个版本能跑」：约束全部来自该提交自己的声明；
  - 没有网络、装不上时不静默降级，返回明确失败原因，由调用方记录。

已知边界：

  Python 版本不随时代切换。2018-2019 年的提交在 py3.10 上可能因语法或标准库变化失败，
  这类失败会在环境画像里显式标出，而不是计入「修复无效」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 时代锁文件目录（入库）。profile 名 == 锁文件名去掉 .txt。
LOCK_DIR = Path(__file__).resolve().parents[1] / "eval" / "envs"
# venv 默认落在仓库工作目录下，已被 .gitignore 排除；可用环境变量覆盖到共享盘。
ENV_ROOT_ENV = "REPLENS_ERA_ENVS"
DEFAULT_ENV_DIRNAME = ".miner_envs"
MODERN = "modern"
# 环境构建逻辑的版本号。改了「装什么 / 怎么装」就递增，让旧 venv 自动失效重建——
# 否则指纹不变会复用旧环境，旧的残留包（如 pyOpenSSL）会继续污染验证（2026-10-29 实测）。
BUILDER_VERSION = 2

# urllib3 2.0 移除、1.x 仍在的名称。测试代码直接导入它们说明时代是 1.x。
_REMOVED_URLLIB3_NAMES = ("SNIMissingWarning", "InsecurePlatformWarning")
_URLLIB3_TEXT = re.compile(r"""["'](urllib3[^"']*)["']""")
# 必须「包名 + 比较符 + 数字」，否则会把 setup.py 里的 ('pytest-args=', 'a', ...) 当依赖
# （2026-10-29 实测：pip 报 Invalid requirement: 'pytest-args='，整批验证全灭）。
_QUOTED_REQ = re.compile(
    r"""["']([A-Za-z0-9][A-Za-z0-9_.\-]*(?:\[[^\]]*\])?\s*(?:[<>]=?|==|!=|~=)\s*\d[^"']*)["']""")
_VERSION = re.compile(r"^(\d+(?:\.\d+)*)$")


class EraEnvError(RuntimeError):
    """时代环境无法构建（缺网络、解释器不兼容、锁定文件缺失）。"""


def env_root() -> Path:
    """时代环境的根目录：用户级缓存，可用环境变量覆盖。

    为什么不是放在仓库里（2026-10-29）：同一代环境是跨仓库共享的资产
    （requests / click 都是 src 布局 + 同一批测试插件），放进各自的仓库会重复
    建 N 份 venv，还会把几万个小文件塞进工作区。默认落到用户缓存目录。
    """
    override = os.environ.get(ENV_ROOT_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / DEFAULT_ENV_DIRNAME
    return Path.home() / ".cache" / DEFAULT_ENV_DIRNAME


def _git_show(repo, rev: str, path: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), "show", rev + ":" + path],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    return result.stdout if result.returncode == 0 else ""


def _version_tuple(text: str) -> Optional[Tuple[int, ...]]:
    match = _VERSION.match(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def urllib3_specifiers(texts: Iterable[str]) -> List[str]:
    """抽出所有 urllib3 需求串，如 urllib3>=1.21.1,<1.27。"""
    specs: List[str] = []
    for text in texts:
        for match in _URLLIB3_TEXT.finditer(text or ""):
            spec = match.group(1).strip()
            if spec not in specs:
                specs.append(spec)
    return specs


def urllib3_upper_bound(specs: Sequence[str]) -> Optional[Tuple[int, ...]]:
    """取最紧的上界；无上界返回 None。

    <1.27 / <=1.26 / ==1.26.20 / ~=1.26 都给出上界；>=1.21.1 不给。
    """
    best: Optional[Tuple[int, ...]] = None
    for spec in specs:
        for part in spec.split(","):
            # 允许传入完整需求串（urllib3~=1.26）或裸说明符（<1.27）。
            part = re.sub(r"^\s*urllib3\s*", "", part.strip(), flags=re.IGNORECASE)
            match = re.match(r"^(<=|<|==|~=|>=|>)\s*(\d[\d.]*)$", part)
            if not match:
                continue
            operator, raw = match.groups()
            version = _version_tuple(raw)
            if version is None:
                continue
            if operator == "~=":
                # ~=1.26 等价于 >=1.26,<2；~=1.26.20 等价于 >=1.26.20,<1.27
                version = version[:-1] + (version[-1] + 1,) if len(version) > 1 else (version[0] + 1,)
            elif operator not in ("<", "<=", "=="):
                continue
            if best is None or version < best:
                best = version
    return best


def runtime_specifiers(setup_py: str) -> List[str]:
    """从 setup.py 里取可直接安装的需求串。

    只取「名字+比较符」形式的纯包需求，跳过 -e .、URL、环境标记等；
    这类条目交给 pip 反而容易在旧提交上炸掉。

    不按 install_requires 切块（2026-10-29 实测踩到）：requests 等仓库写成
    "requires = [...]" 再 "install_requires=requires"，按关键字切块只能拿到变量名，
    结果运行期依赖一个都没装，验证时 import charset_normalizer 直接失败。
    """
    block = setup_py or ""
    # 两种写法都要支持：install_requires=[...] 直接写在 setup() 里，
    # 以及 requires=[...] + install_requires=requires（requests 等旧仓库用这种）。
    indirect = re.search(r"install_requiress*=s*([A-Za-z_][A-Za-z0-9_]*)", block)
    if indirect:
        name = indirect.group(1)
        literal = re.search(r"^" + name + r"s*=s*\[(.*?)\]", block,
                            re.DOTALL | re.MULTILINE)
        if literal:
            block = literal.group(1)
    else:
        cut = block.find("extras_require")
        if cut > 0:
            block = block[:cut]
    specs: List[str] = []
    for match in _QUOTED_REQ.finditer(block):
        spec = match.group(1).strip()
        if ";" in spec or "://" in spec or spec.startswith("-"):
            continue
        if spec not in specs:
            specs.append(spec)
    return specs


def legacy_test_imports(tests_init: str) -> bool:
    """测试代码是否未加守卫地导入 urllib3 1.x 独有名称。

    新版 requests 用 try/except ImportError 兜住了，那类提交不该被判成旧时代——
    否则会白建一个环境，还可能掩盖真正的失败。
    """
    text = tests_init or ""
    if "except ImportError" in text:
        return False
    return any(name in text for name in _REMOVED_URLLIB3_NAMES)


def select_profile(repo, parent: str) -> Tuple[str, str]:
    """返回 (profile, 依据)。profile 为 modern 或锁文件名（不含 .txt）。"""
    setup_py = _git_show(repo, parent, "setup.py")
    setup_cfg = _git_show(repo, parent, "setup.cfg")
    texts = [setup_py, setup_cfg]
    for name in ("requirements-dev.txt", "requirements.txt"):
        texts.append(_git_show(repo, parent, name))
    specs = urllib3_specifiers(texts)
    upper = urllib3_upper_bound(specs)
    if upper is not None and upper[0] < 2:
        return "urllib3-1", "父提交声明 urllib3<2（%s）" % ",".join(specs)
    tests_init = _git_show(repo, parent, "tests/__init__.py")
    if legacy_test_imports(tests_init):
        return "urllib3-1", "测试代码直接导入 urllib3 1.x 独有名称"
    return MODERN, "依赖未限制 urllib3<2，使用当前解释器"


def lock_path(profile: str) -> Path:
    return LOCK_DIR / (profile + ".txt")


def load_lock(profile: str) -> List[str]:
    path = lock_path(profile)
    if not path.exists():
        raise EraEnvError("缺少时代锁文件: " + str(path))
    requirements: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            requirements.append(line)
    return requirements


def current_tag() -> str:
    return "py%d%d" % sys.version_info[:2]


def interpreter_tag(python: Optional[str]) -> str:
    """目标解释器的 pyXY 标签；给不出就退回当前解释器。

    为什么需要（2026-10-29）：时代环境可能用**旧解释器**建（requests 2018 年的提交在
    py3.10 上只是运气好），目录名带 py 标签才能让同一 profile 的多个解释器版本共存。
    """
    if not python:
        return current_tag()
    try:
        result = subprocess.run([python, "-c",
                                 "import sys; print('%d%d' % sys.version_info[:2])"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return current_tag()
    tag = (result.stdout or "").strip()
    return "py" + tag if result.returncode == 0 and tag.isdigit() else current_tag()


def venv_dir(profile: str, tag: Optional[str] = None) -> Path:
    # 选择用哪一版解释器建的环境：显式 tag > 环境变量 REPLENS_ERA_PY_TAG > 当前解释器。
    # 环境变量是给「用旧解释器建好、但用当前解释器跑挖掘」的场景留的出口。
    chosen = tag or os.environ.get("REPLENS_ERA_PY_TAG") or current_tag()
    return env_root() / (profile + "-" + chosen)


def venv_python(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def python_for(profile: str, tag: Optional[str] = None) -> Optional[str]:
    """已建好的时代环境解释器；未建或已失配返回 None。"""
    if profile == MODERN:
        return sys.executable
    directory = venv_dir(profile, tag)
    marker = directory / "replens-era.json"
    executable = venv_python(directory)
    if marker.exists() and executable.exists():
        return str(executable)
    return None


def built_environments() -> List[dict]:
    """列出缓存里所有已构建的时代环境（跨解释器版本）。"""
    root = env_root()
    found: List[dict] = []
    for directory in sorted(root.glob("*-py*")):
        marker = directory / "replens-era.json"
        executable = venv_python(directory)
        if not executable.exists():
            continue
        profile, _, tag = directory.name.rpartition("-")
        record = {}
        if marker.exists():
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                record = {}
        found.append({"profile": profile, "tag": tag, "python": str(executable),
                      "builder": record.get("fingerprint") is not None,
                      "config": (record.get("packages") or {}).get("urllib3")})
    return found


def _fingerprint(profile: str, runtime: Sequence[str]) -> str:
    parts = ["builder=%d" % BUILDER_VERSION, "profile=" + profile]
    parts += load_lock(profile) + [""] + list(runtime)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _packages(python: str) -> Dict[str, str]:
    result = subprocess.run([python, "-m", "pip", "freeze"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return {}
    packages: Dict[str, str] = {}
    for line in (result.stdout or "").splitlines():
        if "==" in line:
            name, _, version = line.partition("==")
            packages[name.strip()] = version.strip()
    return packages


def recorded_fingerprint_mismatch(marker: Path, fingerprint: str) -> bool:
    """已存在的 venv 是否与本轮指纹不符（不符就要重建，不能叠加安装）。"""
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    return recorded.get("fingerprint") != fingerprint


def _package_name(requirement: str) -> str:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)", requirement.strip())
    return match.group(1).lower().replace("_", "-") if match else ""


def merge_requirements(lock: Sequence[str], runtime: Sequence[str]) -> List[str]:
    """锁文件优先：时代锁里已钉版本的包，不再采用该提交自己的版本区间。

    为什么必须这样（2026-10-29 实测）：requests 2018 年的 setup.py 写 urllib3<1.24，
    锁文件钉 1.26.20，两条一起交给 pip 会直接解析冲突；逐个候选各装一遍还会来回升降级。
    锁文件表达的是「这一代环境下跑得通的组合」，运行期依赖只补锁里没有的包。
    """
    pinned = {_package_name(item) for item in lock}
    merged = list(lock)
    for requirement in runtime:
        name = _package_name(requirement)
        if not name or name in pinned:
            continue
        merged.append(requirement)
        pinned.add(name)
    return merged


def _pip_install(python: str, requirements: Sequence[str], timeout: int) -> None:
    command = [python, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + list(requirements)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        raise EraEnvError("pip 安装失败（可能是离线）：" + " / ".join(tail))


def ensure(profile: str, runtime: Sequence[str] = (),
           base_python: Optional[str] = None, timeout: int = 900) -> dict:
    """确保 profile 对应的时代环境存在，返回可写入验证结果的描述。

    runtime 来自候选父提交的 install_requires——运行期依赖也属于时代，
    少了它们 requests 的 conftest 会因为 import charset_normalizer / chardet 失败。
    """
    if profile == MODERN:
        return {"profile": MODERN, "python": sys.executable, "created": False,
                "fingerprint": None, "packages": {}}

    directory = venv_dir(profile, interpreter_tag(base_python) if base_python else None)
    marker = directory / "replens-era.json"
    fingerprint = _fingerprint(profile, merge_requirements(load_lock(profile), runtime))
    executable = str(venv_python(directory))

    if marker.exists() and Path(executable).exists():
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            recorded = {}
        if recorded.get("fingerprint") == fingerprint:
            return {"profile": profile, "python": executable, "created": False,
                    "fingerprint": fingerprint, "packages": recorded.get("packages", {})}

    env_root().mkdir(parents=True, exist_ok=True)
    if marker.exists() and recorded_fingerprint_mismatch(marker, fingerprint):
        # 指纹变了说明锁或依赖集合变了：必须重建，不能在旧 venv 上叠加安装。
        # 2026-10-29 实测：旧 venv 里残留的 pyOpenSSL/cryptography 与新装版本不配，
        # 报 module 'lib' has no attribute 'GEN_EMAIL'，把一条好样本判成「修复后测试未通过」。
        shutil.rmtree(directory, ignore_errors=True)
    if not Path(executable).exists():
        creator = [base_python or sys.executable, "-m", "venv", str(directory)]
        created = subprocess.run(creator, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout)
        if created.returncode != 0:
            raise EraEnvError("创建 venv 失败: " + (created.stderr or "").strip()[-300:])

    _pip_install(executable, merge_requirements(load_lock(profile), runtime), timeout)
    packages = _packages(executable)
    record = {"profile": profile, "fingerprint": fingerprint, "runtime": list(runtime),
              "lock_sha256": hashlib.sha256(lock_path(profile).read_bytes()).hexdigest(),
              "python": executable, "packages": packages,
              "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())}
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"profile": profile, "python": executable, "created": True,
            "fingerprint": fingerprint, "packages": packages}


def reset(profile: str, tag: Optional[str] = None) -> bool:
    """删除某个时代环境，下次 ensure() 重建。给手工排查用。"""
    directory = venv_dir(profile, tag)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return True


def describe(environment: dict) -> dict:
    """把 ensure() 的结果压成可写进数据集或验证结果的短记录。"""
    packages = environment.get("packages") or {}
    return {"profile": environment.get("profile", MODERN),
            "python": environment.get("python"),
            "python_version": sys.version.split()[0],
            "urllib3": packages.get("urllib3"),
            "pytest": packages.get("pytest")}
