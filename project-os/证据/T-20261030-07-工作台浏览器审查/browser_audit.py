"""Local browser review evidence; this is not a security or bulk model benchmark."""
from pathlib import Path
import json
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000"
results = {"page_errors": [], "console_errors": [], "responses": [], "steps": [], "layouts": []}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def step(name, action):
    start = time.time()
    try:
        value = action()
        item = {"name": name, "result": value, "seconds": round(time.time() - start, 3)}
    except Exception as exc:
        item = {"name": name, "error": str(exc), "seconds": round(time.time() - start, 3)}
    results["steps"].append(item)
    save("browser-results.json", results)
    print(json.dumps(item, ensure_ascii=False), flush=True)


with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="msedge", headless=True)
    context = browser.new_context(viewport={"width": 1366, "height": 768},
                                  record_har_path=str(OUT / "workbench.har"),
                                  record_har_url_filter="http://127.0.0.1:8000/**")
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    page = context.new_page()
    page.on("pageerror", lambda e: results["page_errors"].append(str(e)))
    page.on("console", lambda m: results["console_errors"].append(m.text) if m.type == "error" else None)
    page.on("response", lambda r: results["responses"].append({"url": r.url, "status": r.status})
            if r.url.startswith(BASE) else None)
    page.goto(BASE + "/", wait_until="networkidle")
    page.wait_for_function("document.querySelector('#envPill').textContent.includes('qwen2.5-coder:1.5b')")

    for view in ["overview", "analyze", "run", "eval", "compliance", "mine"]:
        page.locator(f'.nav-item[data-view="{view}"]').click()
        page.screenshot(path=str(OUT / f"desktop-{view}.png"))
        results["steps"].append({"name": "navigation-" + view,
                                 "title": page.locator(f"#view-{view} .view-title").inner_text()})

    def select_repo(path):
        page.locator("#repoSelect").fill(str(path))
        page.locator("#repoSelect").dispatch_event("change")

    def click_api(button, endpoint, output, timeout=30000):
        with page.expect_response(lambda r: r.url == BASE + endpoint, timeout=timeout) as pending:
            page.locator(button).click()
        response = pending.value
        payload = response.json()
        save(output, payload)
        return {"http_status": response.status, "payload_file": output, "error": payload.get("error")}

    def browse():
        response = click_api("#btnBrowse", "/api/fs/list", "directory-response.json")
        page.locator("#fsModal").wait_for(state="visible")
        response["visible_entries"] = page.locator(".fs-entry").count()
        page.locator("#fsCancel").click()
        return response

    step("directory-picker", browse)
    select_repo(ROOT / "examples/demo_repo")
    step("import-demo", lambda: click_api("#btnInit", "/api/init", "init-response.json"))
    step("index-demo", lambda: click_api("#btnIndex", "/api/index", "index-response.json"))

    page.locator('[data-view="eval"]').click()
    results["dataset_options"] = page.locator("#evalDataset option").evaluate_all("els => els.map(e=>e.value)")
    page.locator("#evalDataset").select_option("dataset.json")
    step("evaluate-demo", lambda: click_api("#btnEval", "/api/eval", "eval-response.json"))
    page.locator("#evalResult .metric-grid").wait_for()
    page.screenshot(path=str(OUT / "eval-result.png"))
    page.locator("#evalDataset").select_option("dataset.schema.json")
    step("listed-schema-selected", lambda: click_api("#btnEval", "/api/eval", "schema-eval-response.json"))
    page.screenshot(path=str(OUT / "invalid-listed-dataset.png"))
    page.locator("#evalDataset").select_option("dataset.json")

    page.locator('[data-view="compliance"]').click()
    select_repo(ROOT)
    step("project-compliance", lambda: click_api("#btnCompliance", "/api/compliance", "compliance-response.json"))
    page.wait_for_function("document.querySelector('#complianceResult').textContent.includes('49')")
    results["compliance_badges"] = page.locator("#complianceResult .badge").evaluate_all(
        "els => els.map(e => ({text:e.textContent,classes:e.className}))")
    page.screenshot(path=str(OUT / "compliance-result.png"))
    select_repo(ROOT / "examples/demo_repo")

    page.locator('[data-view="run"]').click()
    results["run_defaults"] = {"acceptance_suite": page.locator("#runAcceptanceSuite").input_value(),
                                "description": page.locator("#view-run .view-desc").inner_text()}
    page.locator("#runIssue").fill("date string with surrounding whitespace fails to parse")
    page.locator("#runTestCmd").fill("python -m pytest tests/test_parser.py")
    page.locator("#runAcceptanceSuite").fill(str(ROOT / "examples/acceptance/demo.json"))

    def run():
        with page.expect_response(lambda r: r.url == BASE + "/api/run", timeout=240000) as pending:
            page.locator("#btnRun").click()
            results["run_button_disabled_while_pending"] = page.locator("#btnRun").is_disabled()
        response = pending.value
        payload = response.json()
        save("run-response.json", payload)
        page.wait_for_function("!document.querySelector('#runResult .loading')")
        page.screenshot(path=str(OUT / "run-result.png"))
        return {"http_status": response.status, "status": payload.get("status"),
                "error": payload.get("error"), "patch_note": payload.get("report", {}).get("patch_note"),
                "provenance": payload.get("provenance"), "payload_file": "run-response.json"}

    step("full-model-demo-with-acceptance", run)
    if (OUT / "run-response.json").exists():
        payload = json.loads((OUT / "run-response.json").read_text(encoding="utf-8"))
        if payload.get("provenance"):
            step("replay-demo", lambda: click_api("#btnVerify", "/api/verify", "verify-response.json", 120000))
            page.wait_for_function("!document.querySelector('#verifyResult .loading')")
            results["verify_display"] = page.locator("#verifyResult").inner_text()
            results["run_section_titles"] = page.locator("#runResult .section-title").all_inner_texts()
            page.screenshot(path=str(OUT / "replay-result.png"))

            def download():
                with page.expect_download() as pending:
                    page.get_by_role("button", name="下载 Markdown 报告").click()
                downloaded = pending.value
                downloaded.save_as(OUT / "downloaded-report.md")
                return {"filename": downloaded.suggested_filename,
                        "bytes": (OUT / "downloaded-report.md").stat().st_size}
            step("download-report", download)

    for width, height in [(1440, 900), (1366, 768), (1024, 768), (390, 844)]:
        page.set_viewport_size({"width": width, "height": height})
        for view in ["overview", "analyze", "run", "eval", "compliance", "mine"]:
            page.locator(f'.nav-item[data-view="{view}"]').click()
            layout = page.evaluate("""() => ({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
                mainWidth:document.querySelector('.main').getBoundingClientRect().width,
                contentWidth:document.querySelector('.content').clientWidth,
                contentScrollWidth:document.querySelector('.content').scrollWidth,
                offscreenControls:[...document.querySelectorAll('.topbar button, .topbar input, .view.active button, .view.active input, .view.active select')]
                .filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&(r.right>innerWidth+1||r.left<0)})
                .map(e=>({id:e.id,right:Math.round(e.getBoundingClientRect().right)}))})""")
            results["layouts"].append({"width": width, "view": view, **layout})
        if width in (1024, 390):
            page.locator('[data-view="overview"]').click()
            page.screenshot(path=str(OUT / f"overview-width-{width}.png"))

    save("browser-results.json", results)
    context.tracing.stop(path=str(OUT / "browser-trace.zip"))
    context.close()
    browser.close()
    print("Browser review complete", flush=True)
