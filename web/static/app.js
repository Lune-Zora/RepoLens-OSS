/* RepoLens-OSS 前端逻辑（零构建 vanilla JS）。 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { repo: "", repos: [], datasets: [] };

const esc = (s) => (s == null ? "" : String(s))
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

async function post(url, body) {
  const ids = {"/api/run":"btnRun", "/api/plan":"btnAnalyze", "/api/eval":"btnEval", "/api/mine":"btnMine", "/api/compliance":"btnCompliance"};
  const button = $(ids[url]);
  if (button) button.disabled = true;
  try {
    const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body || {})});
    const data = await r.json();
    if (!r.ok && !data.error) data.error = "请求失败，HTTP " + r.status;
    return data;
  } finally { if (button) button.disabled = false; }
}

async function loadModelsAndTasks() {
  try {
    const models = await fetch('/api/models').then(r=>r.json());
    $('modelSelect').innerHTML = (models.models || []).map(m=>'<option value="'+esc(m)+'">'+esc(m)+'</option>').join('');
    if ((models.models || []).includes(models.current)) $('modelSelect').value=models.current;
    if (models.error) toast('模型列表：'+models.error, 6000);
  } catch(e) { toast('无法读取模型列表：'+e.message,6000); }
  try {
    const entries=await fetch('/api/scenarios').then(r=>r.json());
    state.scenarios=entries;
    $("scenarioSelect").innerHTML='<option value="">自定义仓库 / 问题</option>';
    entries.forEach(e=>{const o=document.createElement('option');o.value=e.id;o.textContent=e.name+'（构造任务）';$('scenarioSelect').appendChild(o);});
  } catch(e) { toast('独立任务列表加载失败：'+e.message); }
}
$('scenarioSelect').addEventListener('change',()=>{
  const scenario=(state.scenarios || []).find(x=>x.id===$('scenarioSelect').value);
  if (!scenario) return;
  state.repo=scenario.repo; $('repoSelect').value=state.repo;
  $('analyzeIssue').value=$('runIssue').value=scenario.issue;
  $('runTestCmd').value=scenario.test_cmd || ''; $('runExamples').value='';
  $('runAcceptanceSuite').value=scenario.acceptance_suite;
  $('analyzeResult').innerHTML=$('runResult').innerHTML=''; window._lastReport=null;
  switchView('run'); toast('已载入独立构造任务；源码没有修复提示，验收在仓库之外。',5000);
});
function selectedModel() { return $('modelSelect').value; }
function renderUnderstanding(u) {
  if(!u) return '';
  return '<div class="card"><div class="card-head"><h2>问题理解与仓库范围</h2></div><div class="card-body">'+
    '<p class="muted">以下为模型推断，请结合源码复核；最终结论以实际测试和独立验收为准。</p><p><b>理解：</b>'+esc(u.summary)+'</p><p><b>预期行为：</b>'+esc(u.expected_behavior || '尚需补充')+'</p>'+
    '<p><b>判断依据：</b>'+esc(u.explanation)+'</p><p class="muted">模型：'+esc(u.model || '未使用')+
    '；目录：'+esc(u.catalog_shown)+'/'+esc(u.catalog_total)+'；检索方式：'+esc(u.retrieval_mode)+'</p>'+
    '<details><summary>检索表达与实际目标</summary><pre class="code">'+esc(JSON.stringify({query:u.search_query,targets:u.targets,functions:u.repository_functions},null,2))+'</pre></details>'+
    (u.error?'<p class="note error">'+esc(u.error)+'</p>':'')+'</div></div>';
}

function toast(msg, ms = 2600) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), ms);
}

function loading(html) {
  return '<div class="loading"><span class="spinner"></span>' + esc(html) + "</div>";
}

function setBtn(btn, busy, text) {
  btn.disabled = busy;
  if (text !== undefined) btn.textContent = text;
}

/* ---------- 视图切换 ---------- */
document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => switchView(b.dataset.view));
});
document.querySelectorAll(".quick[data-goto]").forEach((q) => {
  q.addEventListener("click", () => switchView(q.dataset.goto));
});
function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
}

/* ---------- 仓库选择 ---------- */
async function loadRepos() {
  try {
    state.repos = await fetch("/api/repos").then((r) => r.json());
    const dl = $("repoList");
    dl.innerHTML = "";
    state.repos.forEach((r) => {
      const o = document.createElement("option");
      o.value = r.path;       // 提交用绝对路径
      o.label = r.relative;   // 下拉显示用相对路径
      dl.appendChild(o);
    });
    if (!state.repo) {
      const demo = state.repos.find((r) => r.relative.includes("demo_repo"));
      state.repo = (demo || state.repos[0] || {}).path || "";
    }
    $("repoSelect").value = state.repo;
  } catch (e) {
    toast("仓库列表加载失败");
  }
}
// 选择建议或手输路径后更新（支持任意本地路径）
$("repoSelect").addEventListener("change", (e) => { state.repo = e.target.value.trim(); });
$("repoRefresh").addEventListener("click", () => { loadRepos().then(() => toast("仓库列表已刷新")); });

// 导入远程链接（GitHub / http / ssh）
$("btnClone").addEventListener("click", doClone);
async function doClone() {
  const url = $("repoUrl").value.trim();
  if (!url) { toast("请先粘贴仓库链接"); return; }
  setBtn($("btnClone"), true, "克隆中…");
  try {
    const r = await post("/api/clone", { url });
    if (r.error) { toast(r.error); return; }
    state.repo = r.path;
    $("repoSelect").value = r.path;
    $("repoUrl").value = "";
    await loadRepos();
    toast(r.existed ? "目录已存在，已选中" : "克隆成功，已选中 " + r.relative);
  } finally {
    setBtn($("btnClone"), false, "导入链接");
  }
}

/* ---------- 本地目录浏览（智能选择仓库） ---------- */
const fsState = { path: "", parent: null, current: "" };

$("btnBrowse").addEventListener("click", () => {
  $("fsModal").style.display = "flex";
  $("fsSelect").disabled = true;
  loadFs("");
});
$("fsClose").addEventListener("click", closeFs);
$("fsCancel").addEventListener("click", closeFs);
$("fsUp").addEventListener("click", () => {
  if (fsState.parent) loadFs(fsState.parent);
  else loadFs("");
});
$("fsSelect").addEventListener("click", () => {
  if (fsState.current) {
    state.repo = fsState.current;
    $("repoSelect").value = fsState.current;
    closeFs();
    toast("已选择目录，可点「导入仓库」查看元信息");
  }
});
function closeFs() { $("fsModal").style.display = "none"; }

async function loadFs(path) {
  const list = $("fsList");
  list.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const r = await post("/api/fs/list", { path });
    if (r.error) { list.innerHTML = '<div class="note error">' + esc(r.error) + "</div>"; return; }
    fsState.path = r.path;
    fsState.parent = r.parent;
    fsState.current = r.path;
    $("fsPath").textContent = r.path || "（磁盘列表）";
    $("fsSelect").disabled = !r.path; // 盘符根列表时不能选择
    if (!r.entries || !r.entries.length) {
      list.innerHTML = '<div class="empty">此目录下没有子目录。</div>';
      return;
    }
    list.innerHTML = r.entries.map((e) =>
      '<button class="fs-entry" data-path="' + esc(e.path) + '">' +
      '<span class="fs-ico">' + (e.is_repo ? "📦" : "📁") + "</span>" +
      '<span class="fs-name">' + esc(e.name) +
      (e.is_repo ? ' <span class="badge info">仓库</span>' : "") + "</span></button>").join("");
    list.querySelectorAll(".fs-entry").forEach((b) => {
      b.addEventListener("click", () => loadFs(b.dataset.path));
    });
  } catch (e) {
    list.innerHTML = '<div class="note error">' + esc(e.message) + "</div>";
  }
}

async function loadDatasets() {
  try {
    state.datasets = await fetch("/api/datasets").then((r) => r.json());
    const sel = $("evalDataset");
    sel.innerHTML = "";
    state.datasets.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.name;
      o.textContent = d.name;
      sel.appendChild(o);
    });
    const demo = state.datasets.find((d) => d.name === "dataset.json");
    if (demo) sel.value = demo.name;
  } catch (e) { /* ignore */ }
}

/* ---------- 环境状态 ---------- */
async function loadEnv() {
  try {
    const env = await fetch("/api/env").then((r) => r.json());
    const pill = $("envPill");
    const llm = env.llm.configured
      ? "LLM：已配置 " + (env.llm.model || "（自动选择模型）") + "（运行结果见任务记录）"
      : "LLM：未配置（严格模式需先配置模型）";
    const emb = env.embedding.remote ? "向量：远程" : (env.embedding.local ? "向量：依赖已安装，权重未验证" : "向量：未启用（回退 BM25）");
    pill.textContent = "Python " + env.python + "\n" + llm + "\n" + emb;

    $("envCards").innerHTML = [
      { k: "Python", v: env.python, cls: "ok" },
      { k: "LLM 配置", v: env.llm.configured ? "已配置；结果见任务" : "未配置", cls: "warn" },
      { k: "语义向量", v: env.embedding.remote ? "远程" : (env.embedding.local ? "依赖已安装" : "未安装"), cls: "warn" },
      { k: "修复验证", v: "真实测试执行", cls: "info" },
    ].map((c) => '<div class="stat"><div class="k">' + esc(c.k) + '</div><div class="v ' + c.cls + '">' + esc(c.v) + "</div></div>").join("");
  } catch (e) { /* ignore */ }
}

/* ---------- 元信息 ---------- */
$("btnInit").addEventListener("click", doInit);
async function doInit() {
  if (!state.repo) { toast("请先选择仓库"); return; }
  setBtn($("btnInit"), true, "导入中…");
  try {
    const m = await post("/api/init", { repo: state.repo });
    if (m.error) { toast(m.error); return; }
    $("metaRepoPath").textContent = m.path;
    $("metaInfo").innerHTML = renderMeta(m);
    toast("仓库导入完成");
  } finally {
    setBtn($("btnInit"), false, "导入仓库");
  }
}

function renderMeta(m) {
  const kv = [
    ["名称", m.name],
    ["许可证", m.license + (m.license_confident ? "（高置信）" : "（低置信/未知）") + "  [" + (m.license_file || "未找到") + "]"],
    ["依赖文件", (m.dependency_files || []).join(", ") || "无"],
    ["测试框架", m.test_framework + "　测试目录：" + ((m.test_dirs || []).join(", ") || "无")],
    ["Python 文件数", (m.python_files || []).length],
  ];
  return '<table><tbody>' + kv.map(([k, v]) =>
    '<tr><th style="width:150px">' + esc(k) + "</th><td>" + esc(v) + "</td></tr>").join("") + "</tbody></table>";
}

$("btnIndex").addEventListener("click", doIndex);
async function doIndex() {
  if (!state.repo) { toast("请先选择仓库"); return; }
  setBtn($("btnIndex"), true, "索引中…");
  try {
    const r = await post("/api/index", { repo: state.repo });
    if (r.error) { toast(r.error); return; }
    toast("索引完成：" + r.symbols + " 个符号（FTS5 " + (r.fts_available ? "可用" : "降级 LIKE") + "）");
  } finally {
    setBtn($("btnIndex"), false, "构建索引");
  }
}

/* ---------- 定位 / 计划 ---------- */
$("btnAnalyze").addEventListener("click", doAnalyze);
async function doAnalyze() {
  const issue = $("analyzeIssue").value.trim();
  if (!issue) { toast("请输入 Issue 描述"); return; }
  const out = $("analyzeResult");
  out.innerHTML = loading("检索定位中…");
  const body = { repo: state.repo, issue, top_k: +$("analyzeTopK").value || 5, hybrid: false, model: selectedModel() };
  try {
    const r = await post("/api/plan", body);
    if (r.error) { out.innerHTML = '<div class="note error">' + esc(r.error) + "</div>"; return; }
    out.innerHTML = renderUnderstanding(r.understanding) + renderEvidences(r.evidences, r.insufficient_evidence) + renderPlan(r.plan, r.insufficient_evidence);
  } catch (e) {
    out.innerHTML = '<div class="note error">请求失败：' + esc(e.message) + "</div>";
  }
}

function renderEvidences(evs, insufficient) {
  if (insufficient) return '<div class="note">当前仓库证据不足，无法确认该结论（系统诚实拒答）。</div>';
  if (!evs || !evs.length) return '<div class="empty">无证据。</div>';
  let h = '<div class="section-title">代码定位证据</div><div class="card"><table><thead><tr>' +
    "<th>文件:行号</th><th>符号</th><th>类型</th><th>相关度（非概率）</th><th>来源</th><th>判断依据</th></tr></thead><tbody>";
  evs.forEach((e) => {
    const srcBadge = { keyword_match: "info", hybrid_rrf: "info", call_related: "neutral", semantic_only: "warn" }[e.source] || "neutral";
    h += "<tr>" +
      '<td class="mono">' + esc(e.ref) + "</td>" +
      '<td><b>' + esc(e.name) + "</b>" + (e.signature ? '<div class="muted mono">' + esc(e.signature) + "</div>" : "") + "</td>" +
      "<td>" + esc(e.type) + "</td>" +
      (e.source === 'intent_target' ? '<td>模型选择，未标定</td>' : '<td><div class="conf"><div class="bar"><i style="width:' + Math.round((e.confidence || 0) * 100) + '%"></i></div><span class="num">' + esc(e.confidence) + "</span></div></td>") +
      '<td><span class="badge ' + srcBadge + '">' + esc(e.source) + "</span></td>" +
      "<td>" + esc(e.reason) + "</td></tr>";
    if (e.code) {
      h += '<tr><td colspan="6"><details class="accordion"><summary>查看代码片段</summary><pre class="code">' + esc(e.code) + "</pre></details></td></tr>";
    }
  });
  return h + "</tbody></table></div>";
}

function renderPlan(plan, insufficient) {
  if (!plan) return "";
  if (insufficient) return "";
  const gb = plan.generated_by === "llm"
    ? '<span class="badge info">LLM 生成</span>' : '<span class="badge neutral">规则生成</span>';
  return '<div class="section-title">修改计划 ' + gb + "</div>" +
    '<div class="card"><div class="card-body">' +
    '<h4>根因分析</h4><p>' + esc(plan.root_cause || "—") + "</p>" +
    '<h4>影响范围 / 修改文件</h4><p>' + esc((plan.affected_scope || []).join("、") || "未知") + "</p>" +
    '<h4>修改步骤</h4><ol class="steps-list">' + (plan.steps || []).map((s) => "<li>" + esc(s) + "</li>").join("") + "</ol>" +
    '<h4>风险提示</h4><ul class="steps-list">' + (plan.risks || []).map((s) => "<li>" + esc(s) + "</li>").join("") + "</ul>" +
    "</div></div>";
}

/* ---------- 完整闭环 ---------- */
$("btnRun").addEventListener("click", doRun);
async function doRun() {
  const issue = $("runIssue").value.trim();
  if (!issue) { toast("请输入 Issue 描述"); return; }
  let examples=[];
  try { examples=$('runExamples').value.trim()?JSON.parse($('runExamples').value):[]; if(!Array.isArray(examples))throw new Error('必须是数组'); }
  catch(e){toast('行为样例JSON无效：'+e.message,5000);return;}
  const out = $("runResult");
  out.innerHTML = loading("正在理解问题、生成测试并验证补丁…可能持续数分钟，请等待结果");
  const body = {
    repo: state.repo, issue, behavior_examples: examples,
    top_k: +$("runTopK").value || 5,
    hybrid: false, model: selectedModel(),
    test_cmd: $("runTestCmd").value.trim(),
    acceptance_suite: $("runAcceptanceSuite").value.trim(),
    timeout: +$("runTimeout").value || 120,
    auto_patch: $("runAutoPatch").checked,
    no_provenance: $("runNoProv").checked,
  };
  try {
    const r = await post("/api/run", body);
    if (r.error) {
      out.innerHTML = '<div class="note error">运行失败：' + esc(r.error) + "</div>" +
        (r.traceback ? '<details class="accordion"><summary>堆栈</summary><pre class="code">' + esc(r.traceback) + "</pre></details>" : "");
      return;
    }
    out.innerHTML = renderRun(r);
  } catch (e) {
    out.innerHTML = '<div class="note error">请求失败：' + esc(e.message) + "</div>";
  }
}

function renderRun(r) {
  window._lastReport = r;
  const rep = r.report || {};
  const status = rep.status || "UNVERIFIED";
  const labels = {VERIFIED:'测试范围内独立验收通过',UNVERIFIED:'尚未获独立验收确认',NO_PATCH:'尚未选出可接受补丁',FAILED:'测试未通过',INVALID:'测试执行无效'};
  let h = '<div class="note' + (status === 'VERIFIED' ? ' success' : ['FAILED','INVALID'].includes(status) ? ' error' : '') + '"><strong>' + esc(labels[status] || status) + '（' + esc(status) + '）</strong>' +
    '<ul>' + (rep.reasons || []).map((reason) => '<li>' + esc(reason) + '</li>').join('') + '</ul></div>';
  h += '<p class="muted">'+esc(rep.verification_scope || '结论仅针对给定测试范围')+'</p>';
  h += '<p class="muted mono">本次目标仓库：'+esc(rep.repo || '')+'</p>';
  h += renderUnderstanding(rep.understanding);
  if(rep.blocked_reason) h += '<p class="note error">'+esc(rep.blocked_reason)+'</p>';
  if(rep.task_tests && rep.task_tests.cases) h += '<div class="card"><div class="card-body"><h3>当前问题的冻结行为用例</h3><p>'+(rep.task_tests.kind==='user_behavioral_examples'?'由用户明确提供并保持原样':'由模型提出并审核')+'，补丁生成前冻结，共 '+rep.task_tests.cases.length+' 项；这些用例不能替代独立验收。</p><details><summary>查看用例与预期</summary><pre class="code">'+esc(JSON.stringify(rep.task_tests.cases,null,2))+'</pre></details></div></div>';
  if(rep.validation && rep.validation.preexisting_failures && rep.validation.preexisting_failures.length) h += '<p class="note">仍有其他既有失败：'+rep.validation.preexisting_failures.map(esc).join('；')+'</p>';
  h += renderProvenance(r);

  if (rep.insufficient_evidence) {
    h += '<div class="note">证据不足，无法确认该结论。</div>';
    return h;
  }

  // 补丁说明
  if (rep.patch_note) h += '<div class="note">候选筛选记录：' + esc(rep.patch_note) + "</div>";

  h += renderEvidences(rep.evidences, false) + renderPlan(rep.plan, false);

  // 测试验证
  h += '<div class="section-title">测试验证</div><div class="card"><div class="card-body">';
  h += '<p class="muted mono">测试命令：' + esc(rep.test_command) + "</p>";
  h += '<h4>修复前</h4>' + renderTestResults(rep.before);
  if (rep.after && rep.after.length) h += "<h4>修复后</h4>" + renderTestResults(rep.after);
  else h += '<p class="muted">（未应用补丁，无修复后结果）</p>';
  h += "</div></div>";

  // 执行证据
  if (rep.execution_hints && rep.execution_hints.length) {
    h += '<div class="section-title">执行证据（失败轨迹 → 证伪式重检索）</div><div class="card"><table><thead><tr><th>帧位置</th><th>函数</th><th>权重</th></tr></thead><tbody>' +
      rep.execution_hints.map((x) => '<tr><td class="mono">' + esc(x.ref) + "</td><td>" + esc(x.frame_func) + "</td><td>" + esc(x.weight) + "</td></tr>").join("") +
      "</tbody></table></div>";
  }

  // 翻转预测
  if (rep.flip_prediction) {
    const fp = rep.flip_prediction;
    h += '<div class="section-title">测试翻转统计（补丁测试后计算）</div><div class="card"><div class="card-body">' +
      "<p>静态关联候选：" + esc((fp.predicted || []).join(", ") || "无") + "</p>" +
      "<p>实际翻转：" + esc((fp.actual_flips || []).join(", ") || "无") + "</p>" +
      "<p>命中：" + esc((fp.hit || []).join(", ") || "无") + "</p>" +
      '<p>precision=' + esc(fp.precision) + "，recall=" + esc(fp.recall) + "</p>" +
      "</div></div>";
  }

  // 补丁 diff
  if (rep.patch_diff) {
    h += '<div class="section-title">补丁内容（diff）</div><div class="card"><div class="card-body">' +
      '<details class="accordion" open><summary>展开 diff</summary><pre class="code">' + esc(rep.patch_diff) + "</pre></details></div></div>";
  }

  // 回归测试验证
  if (rep.regression_before || rep.regression_after) {
    h += '<div class="section-title">自动生成的辅助回归（含冒烟检查，不作为独立验收）</div><div class="card"><div class="card-body">' +
      '<p class="muted mono">' + esc(rep.regression_test_cmd) + "</p>" +
      "<h4>修复前</h4>" + renderTestResults(rep.regression_before) +
      "<h4>修复后</h4>" + renderTestResults(rep.regression_after) + "</div></div>";
  }

  if (rep.acceptance_test_cmd) {
    h += '<div class="section-title">冻结的独立行为验收</div><div class="card"><div class="card-body">' +
      '<p class="muted mono">' + esc(rep.acceptance_test_cmd) + '</p>' +
      '<h4>修复前</h4>' + renderTestResults(rep.acceptance_before) +
      '<h4>修复后</h4>' + renderTestResults(rep.acceptance_after) + '</div></div>';
  }

  // 生成的回归测试
  if (rep.generated_test) {
    h += '<div class="section-title">' + (['model_behavioral_cases','user_behavioral_examples'].includes(rep.generated_test_kind) ? '本次任务测试（执行器在临时副本加载）' : '建议新增的回归测试') + '</div><div class="card"><div class="card-body">' +
      '<details class="accordion"><summary>查看代码</summary><pre class="code">' + esc(rep.generated_test) + "</pre></details></div></div>";
  }

  return h;
}

function renderProvenance(r) {
  const p = r.provenance_status || (r.report || {}).provenance || {};
  let h = '<div class="card"><div class="card-body"><p>证据包生成时快照：' + esc(p.status || 'UNKNOWN') +
    '；材料核验：' + esc(p.materials_status || 'NOT_CHECKED') +
    '；重放一致性：' + esc(p.replay_status || 'NOT_RUN') + '</p>';
  if (p.reasons && p.reasons.length) h += '<p class="note error">' + p.reasons.map(esc).join('；') + '</p>';
  if (r.markdown) h += '<button class="btn primary" onclick="downloadReport()">下载 Markdown 报告</button> ';
  if ((r.report || {}).patch_diff) h += '<button class="btn" onclick="downloadPatch()">下载补丁</button> ';
  h += '<p class="muted">验证在临时副本完成，原仓库未修改。请审阅diff与测试范围后再应用下载的补丁。</p>';
  if (r.provenance) {
    h += '<button id="btnVerify" class="btn" onclick="verifyLastRun()">重放证据包</button>' +
      '<p class="muted mono">' + esc(r.provenance) + '</p>';
  }
  h += '<p class="muted">重放 PASS 表示结果与记录一致；修复结论 VERIFIED 才表示通过独立验收。</p>' +
    '<div id="verifyResult"></div></div></div>';
  return h;
}

async function verifyLastRun() {
  const r = window._lastReport;
  if (!r || !r.provenance) return;
  const out = $('verifyResult');
  const btn = $('btnVerify');
  btn.disabled = true;
  out.innerHTML = loading('正在核验材料并重放主测试、辅助检查和独立验收…');
  try {
    const v = await post('/api/verify', {
      repo: r.report.repo, provenance: r.provenance,
    });
    if (v.error) throw new Error(v.error);
    out.innerHTML = '<table><thead><tr><th>材料核验</th><th>重放一致性</th><th>修复结论</th></tr></thead>' +
      '<tbody><tr><td>' + esc(v.materials_status) + '</td><td>' + esc(v.replay_status) +
      '</td><td>' + esc(v.repair_status) + '</td></tr></tbody></table>' +
      '<ul>' + (v.reasons || []).map((reason) => '<li>' + esc(reason) + '</li>').join('') + '</ul>' +
      '<details class="accordion"><summary>重放报告</summary><pre class="code">' + esc(v.markdown || '') + '</pre></details>';
  } catch (e) {
    out.innerHTML = '<div class="note error">重放失败：' + esc(e.message) + '</div>';
  } finally {
    btn.disabled = false;
  }
}

function renderTestResults(rs) {
  if (!rs || !rs.length) return '<p class="empty">无结果。</p>';
  return rs.map((r) => {
    const badge = r.timed_out
      ? '<span class="badge warn">超时</span>'
      : (!r.valid ? '<span class="badge warn">无效</span>' :
        (r.passed ? '<span class="badge pass">通过</span>' : '<span class="badge fail">未通过</span>'));
    let extra = !r.valid ? '<p class="muted">' + esc(r.invalid_reason || '缺少有效结果') + '</p>' : '';
    if (!r.passed && !r.timed_out) {
      const lines = extractFailLines(r);
      if (lines.length) extra += '<div class="fail-lines">' + lines.map(esc).join("<br>") + "</div>";
    }
    if (r.tests && Object.keys(r.tests).length) extra += '<details class="accordion"><summary>逐项测试结果</summary><pre class="code">' + esc(JSON.stringify(r.tests, null, 2)) + '</pre></details>';
    return '<div class="test-line">' + badge +
      '<span class="cmd">' + esc(r.command) + "</span>" +
      '<span class="muted">exit ' + esc(r.returncode) + "</span></div>" + extra;
  }).join("");
}

function extractFailLines(r) {
  const markers = ["FAILED", "AssertionError", "=== FAILURES ===", "FAIL:", "ERROR:", "tests failed", "short test summary"];
  const text = (r.stdout || "") + "\n" + (r.stderr || "");
  const out = [];
  for (const ln of text.split("\n")) {
    const s = ln.trim();
    if (s && markers.some((m) => ln.includes(m))) out.push(s);
    if (out.length >= 8) break;
  }
  return out;
}

function downloadReport() {
  const r = window._lastReport;
  if (!r || !r.markdown) return;
  const blob = new Blob([r.markdown], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "replens_report.md";
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadPatch() {
  const diff = ((window._lastReport || {}).report || {}).patch_diff;
  if (!diff) return;
  const link=document.createElement('a');
  link.href=URL.createObjectURL(new Blob([diff],{type:'text/plain;charset=utf-8'}));
  link.download='replens_candidate.patch';link.click();URL.revokeObjectURL(link.href);
}

/* ---------- 评测 ---------- */
$("btnEval").addEventListener("click", doEval);
async function doEval() {
  const out = $("evalResult");
  out.innerHTML = loading("评测运行中…");
  try {
    const r = await post("/api/eval", {
      repo: state.repo, dataset: $("evalDatasetPath").value.trim() || $("evalDataset").value,
      top_k: Number($("evalTopK").value), hybrid: $("evalHybrid").checked,
      candidate_limit: Number($("evalCandidateLimit").value),
      input_manifest: $("evalInputManifest").value.trim(), sample_id: $("evalSampleId").value.trim(),
    });
    if (r.error) { out.innerHTML = '<div class="note error">' + esc(r.error) + "</div>"; return; }
    out.innerHTML = renderEval(r);
  } catch (e) {
    out.innerHTML = '<div class="note error">请求失败：' + esc(e.message) + "</div>";
  }
}

function renderEval(r) {
  const k = r.top_k;
  const c = r.counts || {};
  const metrics = [
    ["hit@1（去重文件）", r["hit@1"], c.hit1, r.total_positive],
    ["hit@" + k + "（去重文件）", r["hit@" + k], c.hitk, r.total_positive],
    ["func_name@" + k + "（短名）", r["func_name@" + k], c.func, r.total_func_eval],
    ["symbol_start@" + k + "（起始行±3）", r["symbol_start@" + k], c.line, r.total_line_eval],
    ["mrr@" + k + "（文件，截断）", r["mrr@" + k], null, null],
    ["拒答率", r["refusal_rate"], c.refused, r.total_noise],
  ];
  let h = '<div class="section-title">评测指标 <span class="muted">top-' + esc(r.top_k) + " · " + esc(r.total_positive) + " 正向 + " + esc(r.total_noise) + " 干扰</span></div>";
  h += '<div class="grid metric-grid">' + metrics.map(([name, value, num, den]) =>
    '<div class="stat"><div class="k">' + esc(name) + '</div><div class="v">' + (value == null ? "—" : esc(value)) +
    '</div>' + (den == null ? '' : '<div class="muted">' + esc(num) + '/' + esc(den) + '</div>') + '</div>').join('') + '</div>';
  const audit = r.input_audit || {};
  h += '<p class="muted">输入模式：' + esc(audit.mode || 'unknown') + '；候选预算：' + esc(r.candidate_limit) +
    '；暂不支持限定名的样本：' + esc(r.total_qualified_unsupported || 0) + '</p>';
  if (audit.warning) h += '<div class="note">' + esc(audit.warning) + '</div>';

  if (r.details && r.details.length) {
    h += '<div class="section-title">逐条明细</div><div class="card"><table><thead><tr><th>#</th><th>类型</th><th>期望</th><th>去重文件 Top-' + esc(k) + '</th><th>符号 Top-' + esc(k) + '</th></tr></thead><tbody>';
    r.details.forEach((d) => {
      const isNoise = d.type === "noise";
      h += "<tr>" +
        "<td>" + esc(d.id) + "</td>" +
        '<td><span class="badge ' + (isNoise ? "warn" : "info") + '">' + esc(d.type) + "</span></td>" +
        '<td class="mono">' + (d.expected || []).map(esc).join('<br>') + "</td>" +
        '<td class="mono">' + (d.ranked_files || []).map(esc).join('<br>') + '</td>' +
        '<td class="mono">' + (d.returned || []).map(esc).join('<br>') + "</td></tr>";
    });
    h += "</tbody></table></div>";
  }
  return h;
}

/* ---------- 合规 ---------- */
$("btnCompliance").addEventListener("click", doCompliance);
async function doCompliance() {
  const out = $("complianceResult");
  out.innerHTML = loading("生成披露报告中…");
  try {
    const r = await post("/api/compliance", { repo: state.repo });
    if (r.error) { out.innerHTML = '<div class="note error">' + esc(r.error) + "</div>"; return; }
    out.innerHTML = renderCompliance(r);
  } catch (e) {
    out.innerHTML = '<div class="note error">请求失败：' + esc(e.message) + "</div>";
  }
}

function renderCompliance(r) {
  let h = '<div class="note">识别到 ' + esc((r.resources || []).length) + " 项第三方资源。报告已写入：<span class='mono'>" + esc(r.report) + "</span> 与 <span class='mono'>" + esc(r.official_checklist) + "</span></div>";
  h += '<div class="card"><table><thead><tr><th>资源名称</th><th>版本/来源</th><th>许可证</th><th>使用场景</th><th>合规状态</th></tr></thead><tbody>';
  (r.resources || []).forEach((x) => {
    h += "<tr><td><b>" + esc(x.name) + "</b></td><td>" + esc(x.version_source) + "</td><td>" + esc(x.license) + "</td><td>" + esc(x.usage) + "</td><td><span class='badge " + (x.status === '已核验' ? 'pass' : x.status === '待核验' ? 'warn' : 'neutral') + "'>" + esc(x.status) + "</span></td></tr>";
  });
  return h + "</tbody></table></div>";
}

/* ---------- 挖掘 ---------- */
$("btnMine").addEventListener("click", doMine);
async function doMine() {
  const out = $("mineResult");
  out.innerHTML = loading("挖掘修复提交中…（可能较慢）");
  try {
    const r = await post("/api/mine", {
      repo: state.repo, max: +$("mineMax").value || 20,
      validate: $("mineValidate").checked, timeout: +$("mineTimeout").value || 120,
    });
    if (r.error) { out.innerHTML = '<div class="note error">' + esc(r.error) + "</div>"; return; }
    out.innerHTML = renderMine(r);
  } catch (e) {
    out.innerHTML = '<div class="note error">请求失败：' + esc(e.message) + "</div>";
  }
}

function renderMine(r) {
  let h = '<div class="note">挖掘到 <b>' + esc(r.mined) + "</b> 条候选样本；验证通过 <b>" + esc(r.validated_ok) + '</b> 条。<br>补丁目录：<span class="mono">' + esc(r.patches_dir) + "</span>" + (r.output ? '<br>数据集：<span class="mono">' + esc(r.output) + "</span>" : "") + "</div>";
  if (r.items && r.items.length) {
    h += '<div class="card"><table><thead><tr><th>#</th><th>Issue</th><th>期望定位</th><th>验证</th></tr></thead><tbody>';
    r.items.forEach((it) => {
      const v = it.validation;
      const badge = !v ? '<span class="badge neutral">未验证</span>'
        : (v.ok ? '<span class="badge pass">PASS</span>' : '<span class="badge fail">FAIL</span>');
      h += "<tr><td>" + esc(it.id) + "</td><td>" + esc(it.issue) + '</td><td class="mono">' + esc((it.expected || []).join("<br>")) + "</td><td>" + badge + "</td></tr>";
    });
    h += "</tbody></table></div>";
  }
  return h;
}

/* ---------- 初始化 ---------- */
(async function init() {
  await loadModelsAndTasks();
  await loadRepos();
  await loadDatasets();
  await loadEnv();
})();
