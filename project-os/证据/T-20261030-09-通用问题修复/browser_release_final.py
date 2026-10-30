"""Real browser check on a renamed local repository, outside the scenario catalog."""
from pathlib import Path
import json,hashlib
from playwright.sync_api import sync_playwright
OUT=Path(__file__).resolve().parent
REPO=OUT/'custom-browser-inputs/repo'
BASE='http://127.0.0.1:8000'
def save(name,data):(OUT/'browser-release-final'/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    context=browser.new_context(viewport={'width':1440,'height':1000},record_har_path=str(OUT/'browser-release-final/final-workbench.har'),record_har_url_filter=BASE+'/**')
    context.tracing.start(screenshots=True,snapshots=True,sources=False)
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(BASE+'/',wait_until='networkidle');page.wait_for_function("document.querySelector('#scenarioSelect').options.length===5")
    page.locator('#modelSelect').select_option('qwen35-9b-unc:latest')
    page.locator('#repoSelect').fill(str(REPO));page.locator('#repoSelect').dispatch_event('change')
    page.locator('[data-view="run"]').click()
    page.locator('#runIssue').fill('把输入序列按给定容量分组，最后一组即使没有装满也不能丢掉元素。空序列返回空列表，容量小于等于0时应该报ValueError。')
    page.locator('details:has(#runExamples) > summary').click()
    page.locator('#runExamples').fill(json.dumps([{'args':[[11,12,13],2],'expected':[[11,12],[13]]}]))
    page.locator('#runTestCmd').fill('python -m pytest tests')
    page.locator('#runAcceptanceSuite').fill(str(OUT/'custom-browser-inputs/acceptance.json'))
    before=hashlib.sha256((REPO/'stream_ops.py').read_bytes()).hexdigest()
    with page.expect_response(lambda r:r.url==BASE+'/api/run',timeout=600000) as pending:
        page.locator('#btnRun').click()
        disabled=page.locator('#btnRun').is_disabled()
    response=pending.value;payload=response.json();save('custom-run-response.json',payload)
    page.wait_for_function("!document.querySelector('#runResult .loading')")
    page.screenshot(path=str(OUT/'browser-release-final/custom-run.png'))
    result={'http_status':response.status,'status':payload.get('status'),'model':payload.get('report',{}).get('model'),
            'button_disabled_while_running':disabled,'scenario_selection':page.locator('#scenarioSelect').input_value(),
            'test_source':payload.get('report',{}).get('task_tests',{}).get('kind'),
            'explicit_example':{'args':[[11,12,13],2],'expected':[[11,12],[13]]},
            'source_unchanged':before==hashlib.sha256((REPO/'stream_ops.py').read_bytes()).hexdigest(),'errors':errors}
    if payload.get('provenance'):
        with page.expect_response(lambda r:r.url==BASE+'/api/verify',timeout=180000) as pending:
            page.locator('#btnVerify').click()
        verified=pending.value.json();save('custom-replay-response.json',verified)
        page.wait_for_function("!document.querySelector('#verifyResult .loading')")
        result['replay_status']=verified.get('replay_status');result['repair_status']=verified.get('repair_status')
        with page.expect_download() as pending:page.get_by_role('button',name='下载 Markdown 报告').click()
        pending.value.save_as(OUT/'browser-release-final/downloaded-custom-report.md')
        if payload.get('report',{}).get('patch_diff'):
            with page.expect_download() as patch_download:page.get_by_role('button',name='下载补丁',exact=True).click()
            target=OUT/'browser-release-final/downloaded-candidate.patch'
            patch_download.value.save_as(target)
            result['downloaded_patch_matches_report']=target.read_text(encoding='utf8')==payload['report']['patch_diff']
        page.screenshot(path=str(OUT/'browser-release-final/custom-replay.png'))
    save('custom-browser-summary.json',result)
    context.tracing.stop(path=str(OUT/'browser-release-final/final-trace.zip'));context.close();browser.close()
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert not errors and disabled and result['source_unchanged'],result
    assert result.get('downloaded_patch_matches_report'),result
    assert result.get('test_source')=='user_behavioral_examples',result
    assert result['status']=='VERIFIED' and result.get('replay_status')=='PASS',result
