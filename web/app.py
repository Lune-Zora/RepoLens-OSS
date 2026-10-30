"""RepoLens-OSS Web 层（FastAPI 可选依赖）。

把核心包 replens/ 的完整能力（init / index / search / plan / run / eval /
compliance / mine）暴露为 JSON API，并托管一个零构建（vanilla JS）的可视化前端。

安装：pip install -e .[web]
运行：uvicorn web.app:app --reload
打开：http://127.0.0.1:8000
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import traceback
import json
import urllib.request
from threading import Lock
from pathlib import Path
from typing import List, Optional

# 保证 `import replens` 可用（无论从哪个目录启动 uvicorn）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from replens import __version__
from replens.compliance import build_disclosure
from replens.issue_understanding import understand_and_retrieve, model_client
from replens.model_settings import using_model
from replens.eval_runner import run_eval
from replens.execution_evidence import parse_traceback_frames
from replens.executor import render_patch_as_diff, run_test_suite
from replens.indexer import build_index
from replens.miner import mine_repo
from replens.patchgen import auto_patch_loop
from replens.planner import build_planner
from replens.provenance import build_provenance, verify_provenance
from replens.repo import import_repo
from replens.report import build_report, render_markdown, render_verification_markdown
from replens.retriever import Evidence, retrieve
from replens.retriever_hybrid import build_embedder, retrieve_hybrid
from replens.testgen import generate_regression_test

app = FastAPI(title="RepoLens-OSS", version=__version__, description="开源贡献 Issue 定位 / 补丁生成 / 测试验证工作台")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_REPO_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".git", "README.md", "README.rst")


def _is_repo_dir(path: str) -> bool:
    return any(os.path.exists(os.path.join(path, m)) for m in _REPO_MARKERS)


def _system_proxy() -> Optional[str]:
    """读取系统代理（Windows 注册表 → 环境变量），供 git clone 自动走代理。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
        try:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        finally:
            winreg.CloseKey(key)
        if enable and server:
            if "=" in str(server):
                parts = dict(p.split("=", 1) for p in str(server).split(";") if "=" in p)
                addr = parts.get("https") or parts.get("http") or str(server)
            else:
                addr = str(server)
            if addr and not addr.startswith("http"):
                addr = "http://" + addr
            return addr
    except Exception:
        pass
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def _list_drives() -> List[dict]:
    import string
    drives = []
    for letter in string.ascii_uppercase:
        d = letter + ":\\"
        if os.path.exists(d):
            drives.append({"name": d, "path": d, "is_dir": True, "is_repo": False})
    return drives


# ---------------------------------------------------------------------------
# 资源发现
# ---------------------------------------------------------------------------

def _list_repos() -> List[dict]:
    """扫描内置演示仓库与 repos/ 工作区，供前端下拉选择。"""
    root = _PROJECT_ROOT
    repos: List[dict] = []
    seen: set = set()

    def add(path: str) -> None:
        ap = os.path.abspath(path)
        if ap in seen or not os.path.isdir(ap) or not _is_repo_dir(ap):
            return
        seen.add(ap)
        repos.append({
            "path": ap,
            "relative": os.path.relpath(ap, root),
            "name": os.path.basename(ap.rstrip("/\\")) or ap,
        })

    for sub in ("examples", "repos"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isdir(p) and not name.startswith("."):
                    collection=os.path.join(p,"repositories")
                    if os.path.isfile(os.path.join(p,"catalog.json")) and os.path.isdir(collection):
                        for child in sorted(os.listdir(collection)):
                            add(os.path.join(collection,child))
                    else:
                        add(p)

    # 项目根目录自身（replens 的 compliance 等针对当前项目）
    if os.path.isfile(os.path.join(root, "pyproject.toml")):
        add(root)

    repos.sort(key=lambda r: (0 if "demo_repo" in r["relative"] else 1, r["relative"]))
    return repos


def _list_datasets() -> List[dict]:
    d = os.path.join(_PROJECT_ROOT, "eval", "datasets")
    out: List[dict] = []
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                try:
                    from replens.eval_metrics import load_dataset
                    load_dataset(os.path.join(d,name))
                except (ValueError, OSError, TypeError):
                    continue
                out.append({"name": name, "path": os.path.join(d, name)})
    return out


def _env_status() -> dict:
    return {
        "python": sys.version.split()[0],
        "llm": {
            "base_url": os.environ.get("REPLENS_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "",
            "model": os.environ.get("REPLENS_LLM_MODEL") or "",
            "configured": bool(os.environ.get("REPLENS_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")),
            "strict": os.environ.get("REPLENS_REQUIRE_LLM", "").lower() in {"1","true","yes"},
        },
        "embedding": {
            "remote": bool(
                (os.environ.get("REPLENS_EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
                and (os.environ.get("REPLENS_EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY"))),
            "local": bool(importlib.util.find_spec("fastembed")
                          or importlib.util.find_spec("sentence_transformers")),
        },
    }


# ---------------------------------------------------------------------------
# 检索辅助
# ---------------------------------------------------------------------------

def _do_retrieve(issue: str, db, top_k: int, hybrid: bool, execution_hints=None):
    """按需选择混合检索；embedding 不可用时回退关键字检索。"""
    if hybrid:
        emb = build_embedder()
        if emb is not None:
            return retrieve_hybrid(issue, db, top_k=top_k, embedder=emb,
                                   execution_hints=execution_hints)
        return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)
    return retrieve(issue, db, top_k=top_k, execution_hints=execution_hints)


def _default_test_command(meta) -> str:
    if meta.test_framework == "pytest":
        return "pytest"
    if meta.test_framework == "unittest":
        return "python -m unittest"
    return "pytest"


def _evidences_to_json(evidences: List[Evidence]) -> List[dict]:
    return [{
        "ref": e.symbol.ref,
        "type": e.symbol.type,
        "name": e.symbol.name,
        "confidence": e.confidence,
        "reason": e.reason,
        "source": e.source,
        "signature": e.symbol.signature,
        "code": e.symbol.code,
    } for e in evidences]


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class InitRequest(BaseModel):
    repo: str


class IndexRequest(BaseModel):
    repo: str
    force: bool = False


class SearchRequest(BaseModel):
    repo: str
    issue: str
    top_k: int = 5
    hybrid: bool = False


class PlanRequest(BaseModel):
    model: str = ""
    repo: str
    issue: str
    top_k: int = 5
    hybrid: bool = False


class RunRequest(BaseModel):
    behavior_examples: list = Field(default_factory=list)
    model: str = ""
    repo: str
    issue: str
    top_k: int = 5
    hybrid: bool = False
    test_cmd: str = ""
    auto_patch: bool = True
    patch: str = ""
    timeout: int = 120
    force_reindex: bool = False
    no_provenance: bool = False
    acceptance_suite: str = ""


class EvalRequest(BaseModel):
    repo: str
    dataset: str
    top_k: int = 5
    hybrid: bool = False
    candidate_limit: int = 100
    input_manifest: str = ""
    sample_id: str = ""


class VerifyRequest(BaseModel):
    repo: str
    provenance: str
    test_cmd: str = ''
    timeout: Optional[int] = Field(default=None, gt=0)


class ComplianceRequest(BaseModel):
    repo: str


class MineRequest(BaseModel):
    repo: str
    max: int = 20
    run_validation: bool = Field(default=False, validation_alias="validate")
    timeout: int = 120
    output: str = ""
    # auto=按候选父提交的依赖声明选时代环境；历史仓库（如 requests）必须开，否则整批候选死在
    # urllib3 版本不匹配上。默认关闭：构建 venv 需要联网，Web 端不应意外触发。
    era_env: str = Field(default="", validation_alias="era_env")


class CloneRequest(BaseModel):
    url: str


class FsListRequest(BaseModel):
    path: str = ""


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


# ---------------------------------------------------------------------------
# 元信息 API
# ---------------------------------------------------------------------------

@app.get("/api/models")
def api_models():
    client = model_client('model_list')
    if client is None:
        return {"models": [], "current": "", "error": "未配置模型服务"}
    try:
        request=urllib.request.Request(client.base_url+'/models')
        if client.api_key: request.add_header('Authorization','Bearer '+client.api_key)
        with urllib.request.urlopen(request, timeout=5) as response: data=json.loads(response.read())
        return {"models":[x['id'] for x in data.get('data',[]) if isinstance(x,dict) and isinstance(x.get('id'),str)],
                "current":client.model}
    except Exception as exc:
        return JSONResponse({"models":[],"current":client.model,"error":str(exc)},status_code=503)


@app.get("/api/scenarios")
def api_scenarios():
    manifest=Path(_PROJECT_ROOT)/'examples/independent_tasks/catalog.json'
    if not manifest.exists(): return []
    entries=json.loads(manifest.read_text(encoding='utf8'))
    return [dict(e,repo=str(manifest.parent/e['repo']),acceptance_suite=str(manifest.parent/e['acceptance_suite'])) for e in entries]


@app.get("/api/env")
def api_env():
    return JSONResponse(_env_status())


@app.get("/api/repos")
def api_repos():
    return JSONResponse(_list_repos())


@app.get("/api/datasets")
def api_datasets():
    return JSONResponse(_list_datasets())


# ---------------------------------------------------------------------------
# 功能 API
# ---------------------------------------------------------------------------

@app.post("/api/init")
def api_init(req: InitRequest):
    try:
        meta = import_repo(req.repo)
        return JSONResponse(meta.to_dict())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/index")
def api_index(req: IndexRequest):
    try:
        meta = import_repo(req.repo)
        db = build_index(meta, force=req.force)
        n = db.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        fts = db.fts_available
        db.close()
        return JSONResponse({
            "symbols": n,
            "fts_available": fts,
            "index_path": str(Path(req.repo) / ".replens" / "index.db"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/search")
def api_search(req: SearchRequest):
    try:
        meta = import_repo(req.repo)
        db = build_index(meta)
        try:
            evidences, insufficient = _do_retrieve(req.issue, db, req.top_k, req.hybrid)
            return JSONResponse({
                "insufficient_evidence": insufficient,
                "evidences": _evidences_to_json(evidences),
            })
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/plan")
def api_plan(req: PlanRequest):
    try:
        meta = import_repo(req.repo)
        db = build_index(meta)
        try:
            with using_model(req.model):
                evidences, insufficient, understanding = understand_and_retrieve(req.issue, db, req.top_k, repo_root=req.repo)
                planner = build_planner()
                plan = planner.generate(req.issue, evidences)
            return JSONResponse({
                "insufficient_evidence": insufficient,
                "evidences": _evidences_to_json(evidences),
                "plan": plan.to_dict(),
                "understanding": understanding.to_dict(),
            })
        finally:
            db.close()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


_repair_lock = Lock()

@app.post("/api/run")
def api_run(req: RunRequest):
    if not _repair_lock.acquire(blocking=False):
        return JSONResponse({"error":"已有修复任务正在运行，请等待完成后再提交。"}, status_code=409)
    try:
        # 与 CLI 共用 replens.runner.run_repair 编排，保证两入口行为一致
        from replens.runner import RepairRequest, run_repair
        runner_req = RepairRequest(
            repo=req.repo,
            issue=req.issue,
            top_k=req.top_k,
            hybrid=req.hybrid,
            test_cmd=req.test_cmd or "",
            auto_patch=req.auto_patch,
            patch=req.patch or "",
            timeout=req.timeout,
            force_reindex=req.force_reindex,
            extra_files={},
            acceptance_suite=req.acceptance_suite,
            model=getattr(req,"model", ""),
            behavior_examples=getattr(req,"behavior_examples", []),
        )

        result = run_repair(runner_req, build_provenance=None if req.no_provenance else build_provenance)
        return JSONResponse({
            "report": result.report,
            "status": result.status,
            "reasons": result.reasons,
            "markdown": result.markdown,
            "provenance": result.provenance_dir,
            "provenance_status": result.provenance_status,
        })
    except Exception as e:
        import traceback as _tb
        return JSONResponse({"error": str(e), "traceback": _tb.format_exc()}, status_code=500)
    finally:
        _repair_lock.release()


@app.post('/api/verify')
def api_verify(req: VerifyRequest):
    result = verify_provenance(req.repo, req.provenance, req.test_cmd, timeout=req.timeout)
    return JSONResponse({**result, 'markdown': render_verification_markdown(result)})


@app.post("/api/eval")
def api_eval(req: EvalRequest):
    try:
        dataset_path = Path(req.dataset)
        if not dataset_path.is_absolute():
            dataset_root = Path(_PROJECT_ROOT) / "eval"
            if len(dataset_path.parts) == 1:
                dataset_root = dataset_root / "datasets"
            req.dataset = str(dataset_root / dataset_path)
        result = run_eval(req.repo, req.dataset, top_k=req.top_k, hybrid=req.hybrid,
                          candidate_limit=req.candidate_limit, input_manifest=req.input_manifest,
                          sample_id=req.sample_id)
        return JSONResponse(result)
    except (ValueError, OSError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e), "traceback": traceback.format_exc()}, status_code=500)


@app.post("/api/compliance")
def api_compliance(req: ComplianceRequest):
    try:
        result = build_disclosure(req.repo)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/mine")
def api_mine(req: MineRequest):
    try:
        result = mine_repo(req.repo, max_commits=req.max, validate=req.run_validation,
                           timeout=req.timeout, output=req.output or None,
                           era_env=req.era_env or None)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e), "traceback": traceback.format_exc()}, status_code=500)


@app.post("/api/clone")
def api_clone(req: CloneRequest):
    """把远程仓库（GitHub / http / ssh）克隆到本地 repos/ 目录，返回本地路径。

    安全约束：
    - 仅接受 http(s)/git/ssh 协议或 git@ 形式的地址；
    - 仓库名从 URL 末段提取并做字符白名单过滤，杜绝路径注入。
    """
    try:
        url = req.url.strip()
        if not url:
            return JSONResponse({"error": "仓库地址为空"}, status_code=400)
        if not (re.match(r"^(https?|git|ssh)://", url) or url.startswith("git@")):
            return JSONResponse({"error": "仅支持 http(s)/git/ssh 协议或 git@ 形式的仓库地址"}, status_code=400)

        name = url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        name = re.sub(r"[^\w\-.]+", "_", name) or "repo"

        dest = os.path.join(_PROJECT_ROOT, "repos", name)
        if os.path.exists(dest):
            return JSONResponse({
                "path": dest,
                "relative": os.path.relpath(dest, _PROJECT_ROOT),
                "name": name,
                "existed": True,
                "note": "目录已存在，直接复用",
            })

        # 浅克隆（--depth 1）加速；自动走系统代理（若检测到）；需要完整 git 历史时再 git fetch --unshallow
        cmd = ["git"]
        proxy = _system_proxy()
        if proxy:
            cmd += ["-c", "http.proxy=" + proxy, "-c", "https.proxy=" + proxy]
        cmd += ["clone", "--depth", "1", url, dest]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-800:]
            return JSONResponse({"error": "克隆失败：" + detail}, status_code=500)

        return JSONResponse({
            "path": dest,
            "relative": os.path.relpath(dest, _PROJECT_ROOT),
            "name": name,
            "existed": False,
        })
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "克隆超时（>600s）"}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/fs/list")
def api_fs_list(req: FsListRequest):
    """浏览本地目录：返回子目录列表（标记哪些是仓库），供前端目录选择器使用。

    - path 为空：返回全部盘符（Windows）。
    - path 为目录：返回其直接子目录 + 父目录 + 是否仓库标记。
    """
    try:
        path = (req.path or "").strip()

        if not path:
            return JSONResponse({"path": "", "parent": None,
                                 "entries": _list_drives(), "is_root": True})

        p = os.path.abspath(path)
        if not os.path.isdir(p):
            return JSONResponse({"error": "不是有效目录: " + p}, status_code=400)

        entries: List[dict] = []
        try:
            names = sorted(os.listdir(p))
        except PermissionError:
            return JSONResponse({"error": "无权限访问: " + p}, status_code=403)
        except OSError as e:
            return JSONResponse({"error": "无法读取目录: " + str(e)}, status_code=400)

        for name in names:
            full = os.path.join(p, name)
            try:
                if os.path.isdir(full) and not name.startswith("."):
                    entries.append({"name": name, "path": full, "is_dir": True,
                                    "is_repo": _is_repo_dir(full)})
            except OSError:
                continue

        parent = os.path.dirname(p)
        if parent == p or not os.path.isdir(parent):
            parent = None

        return JSONResponse({"path": p, "parent": parent, "entries": entries,
                             "is_root": False})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# 静态资源（放在所有路由之后挂载）
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
