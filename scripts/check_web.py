"""Web 冒烟：检查首页、静态资源及三个只读 API。

依赖：fastapi、httpx、anyio、typing_extensions>=4.16.0（见 constraints.txt）。
退出码：0 通过；1 失败。
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:
        print(f"无法导入 fastapi.testclient: {type(exc).__name__}: {exc}")
        print("修复建议：pip install -c constraints.txt -e .[web,test]")
        return 1

    try:
        from web.app import app
    except Exception as exc:
        print(f"无法导入 web.app: {type(exc).__name__}: {exc}")
        return 1

    print(f"路由数: {len(app.routes)}")
    client = TestClient(app)
    paths = ("/", "/static/app.js", "/static/style.css",
             "/api/env", "/api/repos", "/api/datasets")
    failed = []
    for path in paths:
        response = client.get(path)
        print(f"GET {path} -> {response.status_code}")
        if response.status_code != 200:
            failed.append(path)
        elif path == "/" and "RepoLens-OSS" not in response.text:
            failed.append(path + " (首页内容不匹配)")
        elif path.startswith("/static/") and not response.content:
            failed.append(path + " (静态资源为空)")

    if failed:
        print(f"冒烟失败，非 200 端点: {failed}")
        return 1

    print("Web 冒烟通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
