"""时代环境契约：环境选择必须由候选自己的依赖声明决定。

存在原因（2026-10-29）：
  requests 11 条候选里 9 条在单一解释器下必然失败——历史测试代码
  导入 urllib3 1.x 的 SNIMissingWarning，2.0 已移除，conftest 直接导入失败，
  pytest 退出码 4。要把这类候选判成「可用」，必须按时代准备环境。

  这份测试锁定三个判断，避免以后又退回「装个依赖就完事」：
  1. 上界解析：urllib3>=1.21.1,<1.27 / ~=1.26 / <3 各得出什么；
  2. 运行期依赖抽取：install_requires 里哪些能直接交给 pip；
  3. 画像选择：有守卫的 try/except 导入不算旧时代（否则会白建环境并掩盖真因）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replens import era_env  # noqa: E402


class SpecifierTests(unittest.TestCase):
    def test_extracts_urllib3_requirement_strings(self):
        setup = (
            "    install_requires=[\n"
            "        'urllib3>=1.21.1,<1.27',\n"
            "        'certifi>=2017.4.17',\n"
            "    ],\n"
        )
        self.assertEqual(era_env.urllib3_specifiers([setup]), ["urllib3>=1.21.1,<1.27"])

    def test_upper_bound_from_range(self):
        self.assertEqual(era_env.urllib3_upper_bound(["urllib3>=1.21.1,<1.27"]), (1, 27))

    def test_upper_bound_accepts_compatible_release(self):
        self.assertEqual(era_env.urllib3_upper_bound(["urllib3~=1.26"]), (1, 27))

    def test_no_upper_bound_when_only_lower(self):
        self.assertIsNone(era_env.urllib3_upper_bound(["urllib3>=1.21.1"]))
        self.assertEqual(era_env.urllib3_upper_bound(["urllib3>=1.21.1,<3"]), (3,))

    def test_tightest_bound_wins(self):
        self.assertEqual(
            era_env.urllib3_upper_bound(["urllib3>=1.21.1,<1.26,!=1.25.0"]), (1, 26))


class RuntimeSpecifierTests(unittest.TestCase):
    def test_skips_extras_and_markers(self):
        setup = (
            "setup(\n"
            "    install_requires=[\n"
            "        'urllib3>=1.21.1,<1.27',\n"
            "        'chardet>=3.0.2,<3.1.0',\n"
            "    ],\n"
            "    extras_require={'socks': ['PySocks>=1.5.6']},\n"
            "    python_requires='>=2.7',\n"
            ")\n"
        )
        self.assertEqual(era_env.runtime_specifiers(setup),
                         ["urllib3>=1.21.1,<1.27", "chardet>=3.0.2,<3.1.0"])

    def test_reads_variable_style_requires(self):
        """install_requires=requires 的写法也必须能取到依赖。"""
        setup = (
            "requires = [\n"
            "    'urllib3>=1.21.1,<1.27',\n"
            "    'chardet>=3.0.2,<3.1.0',\n"
            "]\n"
            "setup(install_requires=requires)\n"
        )
        self.assertEqual(era_env.runtime_specifiers(setup),
                         ["urllib3>=1.21.1,<1.27", "chardet>=3.0.2,<3.1.0"])

    def test_ignores_option_strings(self):
        """setup.py 里的命令行选项（pytest-args=）不是依赖。"""
        setup = "user_options = [('pytest-args=', 'a', 'Arguments')]\n"
        self.assertEqual(era_env.runtime_specifiers(setup), [])

    def test_drops_environment_markers(self):
        setup = "install_requires=['cryptography<40.0.0; python_version <= \"3.7\"']\n"
        self.assertEqual(era_env.runtime_specifiers(setup), [])


class LegacyImportTests(unittest.TestCase):
    def test_unguarded_import_means_legacy(self):
        text = "from urllib3.exceptions import SNIMissingWarning\n"
        self.assertTrue(era_env.legacy_test_imports(text))

    def test_guarded_import_is_not_legacy(self):
        text = (
            "try:\n"
            "    from urllib3.exceptions import SNIMissingWarning\n"
            "except ImportError:\n"
            "    SNIMissingWarning = None\n"
        )
        self.assertFalse(era_env.legacy_test_imports(text))


def _make_repo(directory, files):
    repo = str(directory)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    for name, content in files.items():
        path = Path(repo) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-qm", "init"], check=True)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    return repo, head


class ProfileSelectionTests(unittest.TestCase):
    def test_old_range_selects_urllib3_one(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, parent = _make_repo(directory, {
                "setup.py": "setup(install_requires=['urllib3>=1.21.1,<1.27'])\n",
                "tests/__init__.py": "",
            })
            profile, why = era_env.select_profile(repo, parent)
            self.assertEqual(profile, "urllib3-1")
            self.assertIn("urllib3<2", why)

    def test_relaxed_range_stays_modern(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, parent = _make_repo(directory, {
                "setup.py": "setup(install_requires=['urllib3>=1.21.1,<3'])\n",
                "tests/__init__.py": "",
            })
            self.assertEqual(era_env.select_profile(repo, parent)[0], era_env.MODERN)

    def test_guarded_test_import_does_not_force_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, parent = _make_repo(directory, {
                "setup.py": "setup(install_requires=['urllib3>=1.21.1,<3'])\n",
                "tests/__init__.py": (
                    "try:\n"
                    "    from urllib3.exceptions import SNIMissingWarning\n"
                    "except ImportError:\n"
                    "    SNIMissingWarning = None\n"),
            })
            self.assertEqual(era_env.select_profile(repo, parent)[0], era_env.MODERN)


class EnvironmentLayoutTests(unittest.TestCase):
    def test_modern_profile_uses_current_interpreter(self):
        environment = era_env.ensure(era_env.MODERN)
        self.assertEqual(environment["python"], sys.executable)
        self.assertFalse(environment["created"])

    def test_python_for_returns_none_when_not_built(self):
        old = os.environ.get(era_env.ENV_ROOT_ENV)
        with tempfile.TemporaryDirectory() as directory:
            os.environ[era_env.ENV_ROOT_ENV] = directory
            try:
                self.assertIsNone(era_env.python_for("urllib3-1"))
            finally:
                if old is None:
                    os.environ.pop(era_env.ENV_ROOT_ENV, None)
                else:
                    os.environ[era_env.ENV_ROOT_ENV] = old

    def test_env_root_honours_override(self):
        old = os.environ.get(era_env.ENV_ROOT_ENV)
        os.environ[era_env.ENV_ROOT_ENV] = tempfile.gettempdir()
        try:
            self.assertEqual(era_env.env_root(), Path(tempfile.gettempdir()))
        finally:
            if old is None:
                os.environ.pop(era_env.ENV_ROOT_ENV, None)
            else:
                os.environ[era_env.ENV_ROOT_ENV] = old

    def test_py_tag_override_selects_other_environment(self):
        """旧解释器建的环境要能被当前解释器复用：靠 py 标签区分目录。"""
        old = os.environ.get("REPLENS_ERA_PY_TAG")
        os.environ["REPLENS_ERA_PY_TAG"] = "py38"
        try:
            self.assertEqual(era_env.venv_dir("urllib3-1").name, "urllib3-1-py38")
        finally:
            if old is None:
                os.environ.pop("REPLENS_ERA_PY_TAG", None)
            else:
                os.environ["REPLENS_ERA_PY_TAG"] = old

    def test_interpreter_tag_falls_back_when_python_missing(self):
        self.assertEqual(era_env.interpreter_tag("/definitely/not/a/python"),
                         era_env.current_tag())
        self.assertEqual(era_env.interpreter_tag(None), era_env.current_tag())

    def test_stale_environment_is_detected(self):
        """指纹不符必须重建：旧 venv 里残留的包会把好样本判成失败。"""
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "replens-era.json"
            marker.write_text(json.dumps({"fingerprint": "old"}), encoding="utf-8")
            self.assertTrue(era_env.recorded_fingerprint_mismatch(marker, "new"))
            marker.write_text(json.dumps({"fingerprint": "new"}), encoding="utf-8")
            self.assertFalse(era_env.recorded_fingerprint_mismatch(marker, "new"))
            self.assertTrue(era_env.recorded_fingerprint_mismatch(marker, "newer"))

    def test_builder_version_invalidates_old_fingerprints(self):
        """构建逻辑改了必须让旧环境失效，否则残留包会继续污染。"""
        self.assertGreaterEqual(era_env.BUILDER_VERSION, 2)
        first = era_env._fingerprint("urllib3-1", [])
        self.assertEqual(first, era_env._fingerprint("urllib3-1", []))

    def test_merge_requirements_lets_lock_win(self):
        """锁文件已钉的包不再采用提交自带的版本区间，否则 pip 解析冲突。"""
        merged = era_env.merge_requirements(["urllib3==1.26.20", "pytest==7.4.4"],
                                            ["urllib3>=1.21.1,<1.24", "chardet>=3.0.2"])
        self.assertEqual(merged, ["urllib3==1.26.20", "pytest==7.4.4", "chardet>=3.0.2"])

    def test_lock_file_ships_with_repository(self):
        """时代锁必须入库：没有它，环境构建无法复现。"""
        self.assertTrue(era_env.lock_path("urllib3-1").exists())
        self.assertIn("urllib3==1.26.20", era_env.load_lock("urllib3-1"))


if __name__ == "__main__":
    unittest.main()
