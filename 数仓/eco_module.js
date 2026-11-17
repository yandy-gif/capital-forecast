/* =====================================================================
   生态多主体资金计划模块（看板 v10 新增）
   面板：p6 计划提报与审批 / p7 业务参数中心 / p8 预实比对·生态
   依赖：看板主脚本的 showTab / DATA / S；数据全部来自本地数仓 /api/eco
   原则：数仓只存，计算在这里跑（与主看板同一套口径）；闸门不过不许推进，
        强制放行必须留痕；无法识别的进「未匹配清单」，不猜、不篡改。
   ===================================================================== */
(function () {
"use strict";

var ECO = window.ECO = {
  on: false, data: null, locked: [], tab: "p6",
  sel: { sub: null, period: "", entity: "", win: "滚动3月" }, planStep: 1,
  lastUpload: null,
  PANES: { p6: 1, p7: 1, p8: 1 }
};

/* 接口地址自适应：
   - 通过 http(s) 打开看板 → 同源相对路径；
   - 双击 HTML（file://）打开 → 显式指向本地数仓服务，否则 /api/... 会被解析成本地文件路径而"点不开"。 */
var API = (location.protocol === "http:" || location.protocol === "https:") ? "" : "http://127.0.0.1:8765";
var FILE_MODE = API !== "";
var LOCAL_TPL = API + "/api/template?id=TPL-05";
var LOCAL_FLOW = API + "/api/template?id=TPL-06";

/* 模块〇里 v9 旧按钮「下载流水模板/下载计划模板」原先只生成 CSV 样例，
   这里克隆节点（丢掉旧监听器）后改指向注册模板的 xlsx 下载接口 */
function rewireLegacyTplButtons() {
  [["tpl1", "TPL-06", "银行流水导入模板", LOCAL_FLOW],
   ["tpl2", "TPL-05", "生态多主体资金计划模板", LOCAL_TPL]].forEach(function (cfg) {
    var b = document.getElementById(cfg[0]);
    if (!b) return;
    var n = b.cloneNode(true);
    b.parentNode.replaceChild(n, b);
    n.textContent = "下载" + cfg[2] + "(xlsx)";
    n.title = "从本地数仓下载注册模板（带版本号）";
    n.addEventListener("click", function (ev) {
      ev.preventDefault();
      window.location.href = API + "/api/template?id=" + cfg[1];
    });
  });
}

/* 模块〇里 v9 的「下载计划模板/下载流水模板」按钮（运行时为 data-tpl）原先只生成 CSV 样例，
   这里在捕获阶段拦截（抢在 v9 的委托监听之前），改指向注册模板的 xlsx 下载接口 */
var TPL_BTN = { plan: ["TPL-05", "生态多主体资金计划模板", LOCAL_TPL],
  flow: ["TPL-06", "银行流水导入模板", LOCAL_FLOW] };
function rewireTplLabels() {
  Object.keys(TPL_BTN).forEach(function (k) {
    $$('[data-tpl="' + k + '"]').forEach(function (b) {
      if (b._eco) return; b._eco = 1;
      b.textContent = "下载" + TPL_BTN[k][1] + "(xlsx)";
      b.title = "从本地数仓下载注册模板（文件名带版本号）";
    });
  });
}
document.addEventListener("click", function (ev) {
  var b = ev.target.closest("[data-tpl]");
  if (!b || !TPL_BTN[b.dataset.tpl]) return;
  ev.stopPropagation(); ev.preventDefault();
  var cfg = TPL_BTN[b.dataset.tpl];
  toast("正在从数仓下载 " + cfg[1] + " …");
  window.location.href = API + "/api/template?id=" + cfg[0];
}, true);

/* ---------- 小工具 ---------- */
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
  });
}
function num(v) { var n = parseFloat(v); return isFinite(n) ? n : 0; }
function fmt(n) { return (n < 0 ? "−" : "") + Math.abs(Math.round(n)).toLocaleString("en-US"); }
function wan(n) { return (n / 10000).toFixed(1).replace("-", "−"); }
function pct(n) { return isFinite(n) ? (n * 100).toFixed(1) + "%" : "—"; }
function P(key, def) {
  var p = ((ECO.data || {}).params || []).filter(function (x) { return x.id === key; })[0];
  return p ? num(p.value) : def;
}
function PS(key, def) {
  var p = ((ECO.data || {}).params || []).filter(function (x) { return x.id === key; })[0];
  return p ? p.value : def;
}
function api(path, opts) {
  return fetch(API + path, opts || {}).then(function (r) { return r.json(); });
}
function post(path, body) {
  return fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) }).then(async function (r) {
      var d=await r.json();
      if(!r.ok || !d || d.ok===false) return {ok:false,error:(d&&d.error)||"保存失败",gate:d&&d.gate};
      return d;
    }).catch(function(e){toast("保存失败，内容仍保留，请重试",true);return {ok:false,error:e.message};});
}
function toast(msg, bad) {
  var t = $("#ecoToast");
  if (!t) { t = document.createElement("div"); t.id = "ecoToast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:99;max-width:520px;padding:10px 14px;" +
    "border-radius:8px;font-size:12.5px;color:#fff;background:" + (bad ? "#A5352A" : "#1F6F4A") +
    ";box-shadow:0 6px 20px rgba(0,0,0,.25)";
  clearTimeout(t._h); t._h = setTimeout(function () { t.style.display = "none"; }, 5200);
  t.style.display = "block";
}
var ST_COLOR = { "草稿": "#8A8F98", "待审": "#B7791F", "待批": "#B7791F", "已锁定": "#1F6F4A",
  "已核销": "#4A5568", "已作废": "#9CA3AF", "退回": "#A5352A", "变更中": "#7C3AED" };
function stPill(st) {
  return '<span class="eco-pill" style="background:' + (ST_COLOR[st] || "#666") + '22;color:' +
    (ST_COLOR[st] || "#666") + ';border:1px solid ' + (ST_COLOR[st] || "#666") + '55">' + esc(st) + "</span>";
}
function judgeColor(j) {
  return j === "符合" ? "#1F6F4A" : j === "关注" ? "#B7791F" : j === "预警" ? "#A5352A" : "#6B7280";
}

/* ---------- 样式 ---------- */
var CSS = [
  ".eco-pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}",
  ".eco-k{font-size:11px;color:#6B7280}",
  ".eco-tbl{width:100%;border-collapse:collapse;font-size:12px}",
  ".eco-tbl th{background:#F2F5FA;text-align:left;padding:6px 7px;font-size:11.5px;color:#374151;border-bottom:1px solid #E3E7EE;white-space:nowrap}",
  ".eco-tbl td{padding:5px 7px;border-bottom:1px solid #EEF1F6;vertical-align:top}",
  ".eco-tbl tr.err td{background:#FDECEA}",
  ".eco-tbl tr.warn td{background:#FEF7E6}",
  ".eco-tbl tr.okc td{background:#F0F9F4}",
  ".eco-in{width:100%;box-sizing:border-box;border:1px solid #E3E7EE;border-radius:4px;padding:4px 6px;font-size:12px;font-family:inherit;background:#FCFDFF}",
  ".eco-in:focus{outline:none;border-color:#1F3864;background:#fff}",
  ".eco-gate{border:1px solid #E3E7EE;border-radius:8px;padding:8px 10px;margin-bottom:8px}",
  ".eco-gate b{font-size:12.5px}",
  ".eco-gate .eco-k{display:block;margin-top:2px}",
  ".eco-btn{border:1px solid #1F3864;background:#1F3864;color:#fff;border-radius:6px;padding:5px 12px;font-size:12px;cursor:pointer;font-family:inherit}",
  ".eco-btn.gh{background:#fff;color:#1F3864}",
  ".eco-btn.dg{background:#fff;color:#A5352A;border-color:#E4B7B2}",
  ".eco-btn:disabled{opacity:.45;cursor:not-allowed}",
  ".eco-dz{border:2px dashed #C9D2E0;border-radius:10px;padding:16px;text-align:center;color:#4B5563;font-size:12.5px;cursor:pointer;background:#FBFCFE}",
  ".eco-dz.on{border-color:#1F3864;background:#F2F6FC}",
  ".eco-grid{display:grid;gap:12px}",
  ".eco-g2{grid-template-columns:1fr 1fr}",
  ".eco-g3{grid-template-columns:repeat(3,1fr)}",
  "@media(max-width:1180px){.eco-g2,.eco-g3{grid-template-columns:1fr}}",
  ".eco-kv{display:flex;justify-content:space-between;gap:10px;padding:4px 0;border-bottom:1px dashed #E8ECF2;font-size:12px}",
  ".eco-kv b{font-variant-numeric:tabular-nums}",
  ".eco-note{font-size:11px;color:#6B7280;line-height:1.7;margin-top:6px}",
  ".eco-hl{background:#FFF7E0;border:1px solid #F0DFA8;border-radius:8px;padding:8px 10px;font-size:12px}",
  ".eco-scroll{max-height:420px;overflow:auto}",
  ".eco-mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}",
  "nav button b{display:none !important}",
  "nav button .eco-dot{display:none}",
  ".eco-navbrand{display:none}",
  ".eco-hd{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}",
  ".eco-hd h3{margin:0}",
  "#p6 .card,#p7 .card,#p8 .card{padding:16px 18px}",
  "#p6 .eco-tbl td,#p6 .eco-tbl th{padding:6px 8px}",
  "#p6 .eco-dz{padding:20px 12px}",
  "#p6 .eco-note{font-size:10.8px;line-height:1.6}",
  "#p6 .eco-ell{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
  ".eco-flow{display:flex;align-items:center;gap:6px;margin:0 0 14px;flex-wrap:wrap}",
  ".eco-flowstep{display:flex;align-items:center;gap:8px;border:1px solid #E3E8F0;background:#fff;",
  "  border-radius:20px;padding:6px 14px;font-size:12.5px;color:#48566B;cursor:pointer;font-family:inherit}",
  ".eco-flowstep b{width:18px;height:18px;border-radius:50%;background:#E9EEF6;color:#48566B;font-size:11px;",
  "  display:flex;align-items:center;justify-content:center;font-weight:700}",
  ".eco-flowstep:hover{border-color:#B9C6DA}",
  ".eco-flowstep.on{border-color:#1F3864;color:#1F3864;font-weight:700;background:#F7FAFF}",
  ".eco-flowstep.on b{background:#1F3864;color:#fff}",
  ".eco-flowstep.done b{background:#1F6F4A;color:#fff}",
  ".eco-flowstep:disabled{opacity:.45;cursor:not-allowed}",
  ".eco-flowline{flex:0 0 24px;height:1px;background:#E3E8F0}",
  ".eco-statusbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;position:sticky;bottom:8px;",
  "  background:#F7F9FC;border:1px solid #E1E7F0;border-radius:10px;padding:10px 14px;",
  "  box-shadow:0 -2px 10px rgba(16,24,40,.05);font-size:12px;margin-top:6px}",
  ".eco-pagenav{display:flex;align-items:center;gap:8px;margin:-4px 0 14px;padding:8px 12px;",
  "  background:#F7F9FC;border:1px solid #E6EAF2;border-radius:8px;font-size:12px;color:#6B7280}",
  ".eco-pagenav .eco-pn-cur{font-weight:700;color:#1F3864}",
  ".eco-pagenav button{border:1px solid #D5DDEA;background:#fff;color:#1F3864;border-radius:6px;",
  "  padding:4px 12px;font-size:12px;cursor:pointer;font-family:inherit}",
  ".eco-pagenav button:hover{border-color:#1F3864}",
  ".eco-pagenav button.eco-pn-next{background:#1F3864;color:#fff;border-color:#1F3864;font-weight:600}",
  ".eco-navgrp{font-size:10.5px;color:#8A8F98;letter-spacing:.1em;font-weight:700;margin:12px 8px 4px;display:none}",
  "@media(min-width:1000px){",
  "  nav{position:fixed;left:0;top:0;bottom:0;width:192px;z-index:60;background:#F6F8FB;",
  "    border-right:1px solid #E4E9F1;border-bottom:none;box-shadow:none;overflow-y:auto;padding:18px 12px}",
  "  nav .wrap{display:flex;flex-direction:column;gap:1px;padding:0;overflow:visible;max-width:none}",
  "  nav button{width:100%;justify-content:flex-start;text-align:left;height:36px;padding:0 12px;",
  "    border-radius:8px;border-bottom:none;font-size:12.8px;font-weight:500;color:#48566B;",
  "    display:flex;align-items:center;position:relative;",
  "    transition:background .15s ease,color .15s ease}",
  "  nav button:hover{background:#E9EEF6;color:#1F3864}",
  "  nav button.on{background:#fff;color:#1F3864;font-weight:700;",
  "    box-shadow:0 1px 2px rgba(16,24,40,.06),inset 0 0 0 1px #E1E7F0}",
  "  nav button.on:before{content:\"\";position:absolute;left:0;top:9px;bottom:9px;width:3px;",
  "    border-radius:0 3px 3px 0;background:#1F3864}",
  "  .eco-navgrp{display:block;font-size:10px;color:#98A2B3;letter-spacing:.14em;font-weight:700;",
  "    margin:16px 10px 6px}",
  "  .eco-navbrand{display:flex;align-items:baseline;gap:6px;font-size:13px;font-weight:800;",
  "    color:#1F3864;padding:0 10px 14px;letter-spacing:.02em}",
  "  .eco-navbrand span{font-size:10px;font-weight:600;color:#98A2B3}",
  "  header{margin-left:192px}",
  "  body>div.wrap{max-width:none;margin:0 0 0 192px;padding:0 28px 56px}",
  "}",
  "@media(max-width:999px){ .eco-navgrp{display:none} }"
].join("\n");

/* ---------- 加载 ---------- */
ECO.load = function (then) {
  return Promise.all([
    fetch(API + "/api/eco", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
    fetch(API + "/api/locked", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
  ]).then(function (rs) {
    if (!rs[0] || !rs[0].ok) {
      if (!ECO._retried) { ECO._retried = 1; setTimeout(function () { ECO.load(); }, 1500); return; }
      ECO.on = false; ECO.render(ECO.tab); return;
    }
    ECO._retried = 0; ECO.on = true; ECO.data = rs[0];
    ECO.locked = (rs[1] && rs[1].items) || [];
    applyLockedPlan();
    clearPlanIfEmpty();
    syncDataNotice(ECO.tab);
    /* 默认期间：优先「有已锁定计划」的最新一期，预实比对才有计划侧可比 */
    var lp = {}; ECO.locked.forEach(function (i) { lp[i.period] = 1; });
    var lps = Object.keys(lp).sort();
    if (lps.length) ECO.sel.period = lps[lps.length - 1];
    else if (!ECO.sel.period) {
      var ps2 = (rs[0].stats && rs[0].stats.periods) || [];
      ECO.sel.period = ps2[ps2.length - 1] || "";
    }
    ECO.render(ECO.tab);
    if (then) then();
  });
};

/* ---------- 注入导航与面板 ---------- */
function inject() {
  var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
  var tabs = $("#tabs");
  if (tabs && !$("#tabs").querySelector('[data-t="p6"]')) {
    tabs.insertAdjacentHTML("beforeend",
      '<button data-t="p6"><b>六</b>资金计划</button>' +
      '<button data-t="p7"><b>七</b>业务参数中心</button>' +
      '<button data-t="p8"><b>八</b>预实比对·生态</button>');
  }
  var wrap = ($("#p0") || {}).parentNode;   /* 主内容容器；注意 header 里也有一个 .wrap，不能用 querySelector(".wrap") */
  var footer = wrap ? wrap.querySelector("footer") : null;
  if (wrap && !$("#p6")) {
    var html =
      '<section class="pane" id="p6"><div class="pane-title"><h2>模块六 · 资金计划</h2>' +
      '<span>模板下载 → 上传计划 → 看文档 → 人工审核通过 → 锁定入库，之后才进管理视角</span></div>' +
      '<div id="ecoP6"></div></section>' +
      '<section class="pane" id="p7"><div class="pane-title"><h2>模块七 · 业务参数中心</h2>' +
      '<span>汇率 / 账期 / 回款 / 水位 / 阈值都可调，改完全看板与预实联动</span></div><div id="ecoP7"></div></section>' +
      '<section class="pane" id="p8"><div class="pane-title"><h2>模块八 · 预实比对（生态）</h2>' +
      '<span>已锁定的资金计划 × 银行流水，按 客户×科目×账期 三维比对</span></div><div id="ecoP8"></div></section>';
    if (footer) footer.insertAdjacentHTML("beforebegin", html);
    else wrap.insertAdjacentHTML("beforeend", html);
  }
}

/* ---------- 包装主脚本 ---------- */
function hook() {
  var _showTab = window.showTab;
  window.showTab = function (id) {
    _showTab(id);
    ECO.tab = id;
    if (ECO.PANES[id]) ECO.render(id);
    syncDataNotice(id);
    syncLayout();
    try { injectPageNav(id); } catch (e) { }
    if (id === "p0") setTimeout(function () { syncLayout(); rewireTplLabels(); }, 0);
    if (id === "p1") setTimeout(gateP1, 0);
  };
  /* 导入完成就自动打标（不依赖首屏 BOOT，也不依赖同步时机） */
  if (typeof window.commitFlow === "function") {
    var _cf = window.commitFlow;
    window.commitFlow = function (rows, meta, items, dup) {
      var r = _cf.apply(this, arguments);
      try {
        var src = (typeof S !== "undefined" && S && S.raw && S.raw.flow && S.raw.flow.rows) || rows || [];
        if (src && src.length && typeof applyFlow === "function") {
          window.__ecoTagging = 1;
          applyFlow(src, "all");
          window.__ecoTagging = 0;
          if (typeof S !== "undefined" && S) S.applied = true;
        }
        /* 导入完成 → 上传区回到可再次导入的初始状态 */
        try {
          Array.prototype.slice.call(document.querySelectorAll('#p0 input[type="file"]'))
            .forEach(function (x) { x.value = ""; });
          var dz = document.getElementById("ecoDz");
          if (dz) dz.classList.remove("on");
          toast("已导入 " + (src && src.length ? src.length : 0) + " 笔并完成打标，可以继续导入下一份");
        } catch (e3) { }
        syncLayout();
      } catch (e) { window.__ecoTagging = 0; }
      return r;
    };
  }
  if (typeof window.applyFlow === "function") {
    var _af = window.applyFlow;
    window.applyFlow = function (rows, mode) {
      try { presetActMonths(rows); } catch (e0) { }
      var r = _af(rows, mode);
      try { presetForecastMonths(); } catch (e1) { }
      if (ECO.tab === "p8") ECO.render("p8");
      return r;
    };
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-eco]");
    if (b) ecoClick(b);
  });
  document.addEventListener("change", function (e) {
    var s = e.target.closest("[data-ecosel]");
    if (s) ecoSelect(s);
  });
}

ECO.render = function (id) {
  if (!ECO.on) { renderOffline(id); return; }
  if (id === "p6") renderP6();
  if (id === "p7") renderP7();
  if (id === "p8") renderP8();
};

function renderOffline(id) {
  var box = $("#" + ({ p6: "ecoP6", p7: "ecoP7", p8: "ecoP8" }[id] || "ecoP6"));
  if (!box) return;
  var cards = '<div class="eco-grid eco-g3" style="margin-top:10px">' + [
    ["TPL-05", "生态多主体月度资金预算与资金计划表模板", "v1.0"],
    ["TPL-06", "银行流水导入模板", "v1.0"],
    ["TPL-07", "资金计划规则与配置包", "v1"],
    ["TPL-08", "by 月资金计划表（样例）", "v1.0"]
  ].map(function (t) {
    return '<div style="border:1px solid #E3E7EE;border-radius:8px;padding:10px">' +
      '<div style="font-size:12.5px;font-weight:700">' + esc(t[1]) + '</div>' +
      '<div class="eco-k" style="margin:4px 0 8px">' + t[0] + ' · ' + t[2] + '</div>' +
      '<a class="eco-btn" style="text-decoration:none;display:inline-block" href="' + API +
      '/api/template?id=' + t[0] + '">下载 ' + t[2] + '</a></div>';
  }).join("") + '</div>';
  box.innerHTML = '<div style="margin-bottom:8px"><button class="eco-btn" data-eco="reconnect">重新连接</button> ' +
    '<button class="eco-btn gh" data-eco="openhost">打开服务版看板</button></div>' +
    '<div class="eco-hl">连不上本地数仓服务（接口请求失败），提报/审批/参数/预实暂不可用；' +
    '<b>模板下载按钮仍可直接点</b>——能下载说明服务其实活着，只是刚才那一次请求失败。<br>' +
    '启动服务：双击 <b>数仓/启动数仓.bat</b>，看到页面右上角「● 本地数仓」即恢复；' +
    '建议用 <b>http://127.0.0.1:8765</b> 打开看板（双击 HTML 文件时上传/审批会受限）。' +
    '<br>服务启动后可下载按当前配置生成的空白模板。</div>' + cards;
}

/* =====================================================================
   p6 · 计划提报与审批
   ===================================================================== */
function renderP6() {
  var d = ECO.data, box = $("#ecoP6"); if (!box) return;
  var subs = d.submissions || [];
  if (!ECO.sel.sub && subs.length) ECO.sel.sub = subs[0].id;
  var sel = subs.filter(function (s) { return s.id === ECO.sel.sub; })[0] || null;
  var lockedN = (ECO.locked || []).length;
  var step = ECO.planStep || 1;
  if (step === 3 && !lockedN) step = ECO.planStep = 2;      /* 没有已通过的计划，不去第 3 步 */

  var html = flowBar(step, subs.length, lockedN);
  if (step === 1) html += p6Upload(subs);
  else if (step === 2) html += p6Approve(subs, sel);
  else html += p6Board(lockedN);

  box.innerHTML = html + '<div class="card" style="margin-top:14px">'
    + '<h3>申请记录 <span>资金计划导入与审批台账</span></h3><div id="reqBox2"></div></div>';
  bindP6();
  injectManualSave();
  if (window.renderReqBoxes) setTimeout(window.renderReqBoxes, 0);

  if (step === 2 && sel && (!sel.items || !sel.logs)) {
    fetch(API + "/api/submission?id=" + sel.id, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res || !res.ok || !res.submission) return;
        var arr = (ECO.data.submissions || []);
        for (var i = 0; i < arr.length; i++) if (arr[i].id === res.submission.id) arr[i] = res.submission;
        if (ECO.tab === "p6" && (ECO.planStep || 1) === 2) renderP6();
      }).catch(function () { });
  }
}

/* 顶部三步：可点击切换 */
function flowBar(step, subN, lockedN) {
  function cls(n) {
    if (step === n) return "on";
    if (n === 1 && subN) return "done";
    if (n === 2 && lockedN) return "done";
    if (n === 3 && lockedN) return "";
    return "";
  }
  var dis3 = lockedN ? "" : ' disabled title="审批通过后才能进入看板"';
  return '<div class="eco-flow">' +
    '<button class="eco-flowstep ' + cls(1) + '" data-eco="planstep" data-s="1"><b>1</b>上传资金计划表</button>' +
    '<i class="eco-flowline"></i>' +
    '<button class="eco-flowstep ' + cls(2) + '" data-eco="planstep" data-s="2"><b>2</b>审批' +
    (subN ? '（' + subN + '）' : '') + '</button>' +
    '<i class="eco-flowline"></i>' +
    '<button class="eco-flowstep ' + cls(3) + '" data-eco="planstep" data-s="3"' + dis3 + '><b>3</b>进入看板</button>' +
    '</div>';
}

/* ① 上传 */
function p6Upload(subs) {
  var h = '<div class="card" style="margin-bottom:14px">' +
    '<div class="eco-hd"><h3>上传资金计划表</h3>' +
    '<a class="eco-btn gh" style="text-decoration:none;display:inline-block;white-space:nowrap" href="' + API +
    '/api/template?id=TPL-05">⬇ 下载模板 v1.0</a></div>' +
    '<div class="eco-grid eco-g2" style="margin-bottom:10px">' +
    '<label class="eco-k">预算期间 <span style="color:#A5352A">*</span>' +
    '<input class="eco-in" id="ecoPeriod" placeholder="如 2026-11" value="' +
    esc($("#ecoPeriod") ? $("#ecoPeriod").value : "") + '"></label>' +
    '<label class="eco-k">操作人 <span style="color:#A5352A">*</span>' +
    '<input class="eco-in" id="ecoActor" placeholder="姓名" value="' +
    esc(($("#ecoActor") && $("#ecoActor").value) || localStorage.getItem("eco.actor") || "") + '"></label>' +
    '</div>' +
    '<div class="eco-dz" id="ecoDz"><b>拖入或点击上传</b><br>' +
    '<span class="eco-k">计划表（预算明细）或提报源表（销售 / 部门费用 / 人力）；两项必填后才可上传</span></div>' +
    '<input type="file" id="ecoFile" accept=".xlsx,.xls" style="display:none">' +
    '<div id="ecoUpRes" style="margin-top:10px"></div>' +
    '<div class="eco-note">上传即把原件存到 数仓/uploads/ 并写入明细；校验结果会直接显示在下方（异常逐条标红）。</div>' +
    '</div>';

  h += '<div class="card"><div class="eco-hd"><h3>本期间已上传</h3>' +
    '<button class="eco-btn" data-eco="planstep" data-s="2"' + (subs.length ? "" : " disabled") +
    ' style="font-size:12.5px">下一步：审批 →</button></div>';
  if (!subs.length) {
    h += '<div class="eco-k">还没有提报单。下载模板填写后上传，或直接上传提报源表。</div>';
  } else {
    h += '<table class="eco-tbl"><thead><tr><th>#</th><th>期间</th><th>版本</th><th>状态</th>' +
      '<th>行数</th><th>净额</th><th>文件</th></tr></thead><tbody>';
    subs.slice(0, 6).forEach(function (x) {
      h += '<tr><td>' + x.id + '</td><td><b>' + esc(x.period) + '</b></td><td>v' + x.version + '</td>' +
        '<td>' + stPill(x.status) + '</td><td>' + x.item_count + '</td><td>' + fmt(x.net) + '</td>' +
        '<td class="eco-k eco-ell" title="' + esc(x.file_name || "") + '">' + esc(x.file_name || "") + '</td></tr>';
    });
    h += '</tbody></table>';
  }
  return h + '</div>';
}

/* ② 审批：清单 + 明细 + 意见 + 底部状态 */
function p6Approve(subs, sel) {
  var h = '<div class="card" style="margin-bottom:14px"><div class="eco-hd"><h3>提报清单</h3>' +
    '<button class="eco-btn gh" data-eco="reload">刷新</button></div>' +
    '<div class="eco-scroll" style="max-height:220px"><table class="eco-tbl"><thead><tr>' +
    '<th>#</th><th>期间</th><th>主体</th><th>版本</th><th>状态</th><th>行数</th>' +
    '<th>流入(CNY)</th><th>流出(CNY)</th><th>净额</th><th>文件</th></tr></thead><tbody>';
  if (!subs.length) h += '<tr><td colspan="10" class="eco-k">还没有提报单，请先在第 1 步上传。</td></tr>';
  subs.forEach(function (x) {
    h += '<tr data-eco="pick" data-id="' + x.id + '" style="cursor:pointer"' +
      (sel && sel.id === x.id ? ' class="okc"' : "") + '><td>' + x.id + '</td><td><b>' + esc(x.period) +
      '</b></td><td>' + esc(x.entity) + '</td><td>v' + x.version + '</td><td>' + stPill(x.status) + '</td>' +
      '<td>' + x.item_count + '</td><td>' + fmt(x.inflow) + '</td><td>' + fmt(x.outflow) + '</td>' +
      '<td><b>' + fmt(x.net) + '</b></td><td class="eco-k eco-ell" title="' + esc(x.file_name || "") + '">' +
      esc(x.file_name || "") + '</td></tr>';
  });
  h += '</tbody></table></div></div>';

  h += '<div class="card" style="margin-bottom:14px"><div class="eco-hd"><h3>审批意见</h3>' +
    (sel ? '<span class="eco-k">#' + sel.id + ' · ' + esc(sel.period) + ' · ' + esc(sel.entity) + '</span>' : "") +
    '</div>' + approveHtml(sel) + '</div>';

  if (sel) {
    h += '<div class="card" style="margin-bottom:14px"><div class="eco-hd"><h3>明细行（文档可读版）</h3>' +
      '<span class="eco-k">' + (sel.file_name ? '<a href="' + API + '/api/uploaded?name=' +
        encodeURIComponent(sel.file_name) + '" style="color:#1F3864">打开原件</a>' : '') + '</span></div>' +
      '<div class="eco-scroll" style="max-height:320px"><table class="eco-tbl"><thead><tr>' +
      '<th>#</th><th>方向</th><th>科目明细</th><th>原币</th><th>折CNY</th><th>责任人</th><th>备注</th></tr></thead><tbody>';
    (sel.items || []).forEach(function (it) {
      var fl = String(it.flag || "ok");
      var cls2 = fl.indexOf("error") === 0 ? "err" : fl.indexOf("warn") === 0 ? "warn" : "";
      h += '<tr class="' + cls2 + '"><td>' + it.seq + '</td><td>' + esc(it.direction) + '</td>' +
        '<td>' + esc(it.subject) + '</td><td>' + fmt(it.amt_orig) + '</td><td><b>' + fmt(it.amt_cny) +
        '</b></td><td>' + esc(it.owner || "-") + '</td>' +
        '<td class="eco-k eco-ell" title="' + esc(it.note || "") + '">' + esc(it.note || "") + '</td></tr>';
    });
    h += '</tbody></table></div></div>';
    h += logsHtml(sel);
  }

  /* 底部状态条 */
  var st = sel ? sel.status : "无单据";
  var acts = sel ? Object.keys((ECO.data.statusFlow || {})[st] || {}) : [];
  h += '<div class="eco-statusbar">' +
    '<span>当前状态：' + (sel ? stPill(st) : '<span class="eco-pill" style="background:#EEF1F5;color:#5B6B85">无单据</span>') + '</span>' +
    '<span class="eco-k">可执行：' + (acts.length ? acts.join(" / ") : "—") + '</span>' +
    (sel ? '<span class="eco-k">行数 ' + (sel.item_count || 0) + ' · 净额 ' + fmt(sel.net) + '</span>' : "") +
    '<span style="flex:1"></span>' +
    (sel && (st === "已锁定" || st === "已核销")
      ? '<button class="eco-btn" data-eco="planstep" data-s="3">下一步：进入看板 →</button>' : "") +
    '</div>';
  return h;
}

/* ③ 进入看板 */
function p6Board(lockedN) {
  var ps = {}, rows = 0, inflow = 0, outflow = 0;
  (ECO.locked || []).forEach(function (i) {
    ps[i.period] = 1; rows++;
    if (i.direction === "流入") inflow += Number(i.amt_cny) || 0;
    else outflow += Number(i.amt_cny) || 0;
  });
  var per = Object.keys(ps).sort();
  return '<div class="card"><div class="eco-hd"><h3>计划已就绪</h3>' +
    '<button class="eco-btn" data-eco="goboard" style="font-size:12.5px">打开管理视角 →</button></div>' +
    '<div class="eco-grid eco-g3" style="margin-bottom:10px">' +
    '<div class="eco-kv"><span>已通过期间</span><b>' + (per.length ? per.join("、") : "—") + '</b></div>' +
    '<div class="eco-kv"><span>计划行数</span><b>' + rows + ' 行</b></div>' +
    '<div class="eco-kv"><span>合计（流入 / 流出）</span><b>' + fmt(inflow) + ' / ' + fmt(outflow) + '</b></div>' +
    '</div>' +
    '<div class="eco-note">管理视角、预实比对只认「审批通过（已锁定）」的版本；' +
    '要改金额回第 2 步重新上传变更版本即可。' +
    (lockedN ? "" : " 当前没有已通过的计划。") + '</div></div>';
}

/* ---------- 流水匹配情况（已匹配 / 系统未匹配） ---------- */
function flowStats() {
  var rows = flowRows(), ok = [], no = [];
  rows.forEach(function (r) { (r.k1 && r.k1 !== "未分类" ? ok : no).push(r); });
  return { all: rows, ok: ok, no: no };
}
function flowMatchHtml() {
  var st = flowStats(), f = ECO.flowFilter || "all";
  var list = f === "ok" ? st.ok : f === "no" ? st.no : st.all;
  var h = '<div class="card" style="margin-bottom:14px"><div class="eco-hd"><h3>流水匹配情况</h3>' +
    '<span class="eco-k">用于核对计划与流水口径：未匹配的多为摘要无法识别，需补规则</span></div>' +
    '<div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">' +
    '<button class="eco-btn ' + (f === "all" ? "" : "gh") + '" data-eco="flowfilter" data-f="all" style="padding:4px 12px;font-size:11.5px">全部 ' + st.all.length + '</button>' +
    '<button class="eco-btn ' + (f === "ok" ? "" : "gh") + '" data-eco="flowfilter" data-f="ok" style="padding:4px 12px;font-size:11.5px">已匹配 ' + st.ok.length + '</button>' +
    '<button class="eco-btn ' + (f === "no" ? "" : "gh") + '" data-eco="flowfilter" data-f="no" style="padding:4px 12px;font-size:11.5px">系统未匹配 ' + st.no.length + '</button>' +
    '</div><div class="eco-scroll" style="max-height:300px"><table class="eco-tbl"><thead><tr>' +
    '<th>月份</th><th>日期</th><th>方向</th><th>金额</th><th>对方户名</th><th>摘要</th><th>一级科目</th><th>二级科目</th><th>状态</th>' +
    '</tr></thead><tbody>';
  if (!list.length) h += '<tr><td colspan="9" class="eco-k">没有符合条件的流水。</td></tr>';
  list.slice(0, 300).forEach(function (r) {
    var okr = r.k1 && r.k1 !== "未分类";
    h += '<tr' + (okr ? '' : ' class="warn"') + '><td>' + esc(r.m) + '</td><td class="eco-k">' + esc(r.d) + '</td>' +
      '<td>' + esc(r.dir) + '</td><td>' + fmt(r.amt) + '</td><td class="eco-ell" title="' + esc(r.cp) + '">' + esc(r.cp) +
      '</td><td class="eco-k eco-ell" title="' + esc(r.memo) + '">' + esc(r.memo) + '</td><td>' + esc(r.k1 || "-") +
      '</td><td>' + esc(r.k2 || "-") + '</td><td>' + (okr ? '<span class="eco-pill" style="background:#F0F9F4;color:#1F6F4A">已匹配</span>'
        : '<span class="eco-pill" style="background:#FDECEA;color:#A5352A">未匹配</span>') + '</td></tr>';
  });
  h += '</tbody></table></div></div>';
  return h;
}

function logsHtml(sel) {
  var h = '<div class="card"><h3>审批留痕</h3><div class="eco-scroll" style="max-height:240px">' +
    '<table class="eco-tbl"><thead><tr><th>时间</th><th>动作</th><th>从</th><th>到</th><th>操作人</th><th>角色</th><th>原因 / 说明</th></tr></thead><tbody>';
  (sel.logs || []).forEach(function (l) {
    h += '<tr><td class="eco-k">' + esc(l.created_at) + '</td><td><b>' + esc(l.action) + '</b></td><td>' +
      esc(l.from_status || "—") + '</td><td>' + stPill(l.to_status) + '</td><td>' + esc(l.actor || "-") + '</td>' +
      '<td class="eco-k">' + esc(l.role || "-") + '</td><td class="eco-k eco-ell" title="' + esc(l.reason || "") + '">' +
      esc(l.reason || "") + '</td></tr>';
  });
  if (!(sel.logs || []).length) h += '<tr><td colspan="7" class="eco-k">暂无记录</td></tr>';
  return h + '</tbody></table></div></div>';
}

/* ---------- 校验结果：一行结论 + 红字列表 ---------- */
function gateHtml(gates) {
  if (!gates) return '<div class="eco-k">上传后这里显示校验结果。</div>';
  var issues = [];
  ["G1", "G2", "G3"].forEach(function (g) {
    var gg = gates[g] || {};
    (gg.issues || []).forEach(function (i) {
      var txt = (i.detail || (i.issues || []).join("；") || "");
      if (!txt) return;
      issues.push({ g: g, bad: gg.pass === false, row: i.row, txt: txt });
    });
  });
  var bads = issues.filter(function (x) { return x.bad; });
  var h = "";
  if (!bads.length) {
    h += '<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;color:#1F6F4A">' +
      '<b>✓ 校验通过</b><span class="eco-k">可以提交</span></div>';
  } else {
    h += '<div style="display:flex;align-items:center;gap:8px;font-size:12.5px;color:#A5352A;font-weight:700">' +
      '✗ 有 ' + bads.length + ' 项需要处理<span class="eco-k" style="font-weight:400">（可存草稿，修好后重传）</span></div>' +
      '<ul style="margin:8px 0 0 18px;padding:0;color:#A5352A;font-size:12px;line-height:1.9">';
    bads.slice(0, 20).forEach(function (x) {
      h += '<li>' + (x.row ? "第 " + x.row + " 行：" : "") + esc(x.txt) + '</li>';
    });
    if (bads.length > 20) h += '<li>… 共 ' + bads.length + ' 项</li>';
    h += '</ul>';
  }
  var tips = issues.filter(function (x) { return !x.bad; });
  if (tips.length) {
    h += '<div class="eco-note" style="margin-top:8px">提示 ' + tips.length + ' 条：' +
      esc(tips.slice(0, 3).map(function (x) { return x.txt; }).join("；")) + (tips.length > 3 ? " …" : "") + '</div>';
  }
  return h;
}

/* ---------- 审批节点横轴 ---------- */
var NODES = [["草稿", "草稿"], ["待审", "已提交"], ["已锁定", "审批通过"], ["已核销", "核销"]];
function nodeIndex(status) {
  for (var i = 0; i < NODES.length; i++) if (NODES[i][0] === status) return i;
  return status === "退回" ? 0 : 0;
}
function stepperHtml(sel) {
  var cur = nodeIndex(sel.status);
  var logs = sel.logs || [];
  function hit(action) {
    var r = null;
    logs.forEach(function (l) { if (l.action === action) r = l; });
    return r;
  }
  var h = '<div style="display:flex;align-items:flex-start;gap:0;margin:6px 0 16px;overflow-x:auto">';
  NODES.forEach(function (n, i) {
    var done = i < cur || (i === cur && (sel.status === "已锁定" || sel.status === "已核销"));
    var active = i === cur;
    var lg = hit({ "待审": "提交", "已锁定": "批准", "已核销": "核销" }[n[0]]);
    var color = done ? "#1F6F4A" : active ? "#1F3864" : "#CBD5E1";
    h += '<div style="flex:1;min-width:120px;text-align:center;position:relative">';
    if (i > 0) {
      h += '<div style="position:absolute;left:0;right:50%;top:11px;height:2px;background:' +
        (i <= cur ? "#1F6F4A" : "#E2E8F0") + '"></div>';
    }
    if (i < NODES.length - 1) {
      h += '<div style="position:absolute;left:50%;right:0;top:11px;height:2px;background:' +
        (i < cur ? "#1F6F4A" : "#E2E8F0") + '"></div>';
    }
    h += '<div style="position:relative;width:22px;height:22px;border-radius:50%;margin:0 auto;background:' +
      color + ';color:#fff;font-size:11px;line-height:22px;font-weight:700">' + (done ? "✓" : i + 1) + '</div>' +
      '<div style="font-size:12px;margin-top:6px;font-weight:' + (active ? "700" : "500") + ';color:' +
      (active ? "#1F3864" : "#48566B") + '">' + n[1] + '</div>' +
      '<div class="eco-k" style="font-size:10.5px;margin-top:2px">' +
      (done && lg ? esc(String(lg.created_at || "").slice(5, 16)) + '<br>' + esc(lg.actor || "") : "&nbsp;") + '</div>' +
      '</div>';
  });
  h += '</div>';
  if (sel.status === "退回") {
    h += '<div class="eco-hl" style="background:#FDECEA;border-color:#E4B7B2;margin-bottom:10px">' +
      '已退回：' + esc((sel.reject_reason || "")) + '　修正后重新提交即可。</div>';
  }
  return h;
}

function approveHtml(sel) {
  if (!sel) return '<div class="eco-k">选择上面任一行，这里显示审批节点与操作。</div>';
  var st = sel.status;
  var h = stepperHtml(sel);
  h += '<div class="eco-grid eco-g3" style="align-items:end;margin-bottom:10px">' +
    '<label class="eco-k">操作人<input class="eco-in" id="ecoApActor" placeholder="姓名" value="' +
    esc(localStorage.getItem("eco.actor") || "") + '"></label>' +
    '<label class="eco-k">意见 / 退回原因（退回必填）<input class="eco-in" id="ecoApReason" placeholder="一句话说明"></label>' +
    '<label class="eco-k" style="padding-bottom:4px"><input type="checkbox" id="ecoForce"> 校验异常也推进（留痕）</label>' +
    '</div><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">';

  if (st === "草稿" || st === "退回") {
    h += '<button class="eco-btn" data-eco="submitonly" style="font-size:12.5px;padding:7px 18px">提交审批</button>';
  }
  if (st === "待审") {
    h += '<button class="eco-btn" data-eco="act" data-action="批准" style="background:#1F6F4A;border-color:#1F6F4A;' +
      'font-size:12.5px;font-weight:700;padding:7px 18px">✓ 审批通过（锁定）</button>' +
      '<button class="eco-btn dg" data-eco="act" data-action="退回">退回修改</button>';
  }
  if (st === "已锁定") {
    h += '<button class="eco-btn gh" data-eco="act" data-action="核销">核销</button>' +
      '<button class="eco-btn" data-eco="goboard">下一步：进入看板 →</button>';
  }
  if (st === "已核销") {
    h += '<button class="eco-btn" data-eco="goboard">下一步：进入看板 →</button>';
  }
  h += '</div>';

  if (st === "草稿" || st === "退回") {
    h += '<div class="eco-note">提交后进入「待审」，由审批人点「审批通过」才锁定；不通过就退回，改完重新提交。</div>';
  } else if (st === "已锁定") {
    h += '<div class="eco-note">要改金额：<b>在上方重新上传变更后的计划表</b>，系统会自动生成新版本（v+1）；' +
      '新版本审批通过前，当前版本继续生效，通过后旧版自动作废，历史数据与留痕都保留。</div>';
  }
  return h;
}

function bindP6() {
  var dz = $("#ecoDz"), fi = $("#ecoFile");
  if (dz && fi && !dz._b) {
    dz._b = 1;
    dz.addEventListener("click", function () { fi.click(); });
    ["dragover", "dragenter"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.add("on"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      dz.addEventListener(ev, function (e) { e.preventDefault(); dz.classList.remove("on"); });
    });
    dz.addEventListener("drop", function (e) {
      var f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) upload(f);
    });
    fi.addEventListener("change", function () { if (fi.files[0]) upload(fi.files[0]); });
  }
}

function upload(file) {
  var period = (($("#ecoPeriod") || {}).value || "").trim();
  var actor = (($("#ecoActor") || {}).value || "").trim();
  if (!period || !actor) {
    toast("请先填写「预算期间」和「操作人」再上传", true);
    var t0 = !period ? $("#ecoPeriod") : $("#ecoActor");
    if (t0) { t0.focus(); t0.style.borderColor = "#A5352A"; }
    return;
  }
  if (!/^20\d{2}-(0[1-9]|1[0-2])$/.test(period)) {
    toast("预算期间格式应为 2026-11", true);
    var t1 = $("#ecoPeriod"); if (t1) { t1.focus(); t1.style.borderColor = "#A5352A"; }
    return;
  }
  var namedMonth=String(file.name||"").match(/(20\d{2})\s*[-_.\/年]\s*(\d{1,2})(?:月|\D|$)/);
  if(namedMonth){
    var expected=namedMonth[1]+"-"+String(+namedMonth[2]).padStart(2,"0");
    if(expected!==period && !confirm("文件名中的月份是 "+expected+"，填写的预算期间是 "+period+"。同期间同主体的新计划批准后会替代原有效版本。仍按 "+period+" 上传吗？")) return;
  }
  localStorage.setItem("eco.actor", actor);
  var autoSub = false;                       /* 上传只存草稿，审核通过由人来点 */
  var res = $("#ecoUpRes");
  if (res) res.innerHTML = '<div class="eco-k">正在解析 ' + esc(file.name) + ' …</div>';
  fetch(API + "/api/upload-plan", {
    method: "POST", body: file,
    headers: {
      "Content-Type": "application/octet-stream",
      "X-File-Name": encodeURIComponent(file.name),
      "X-Period": encodeURIComponent(period),
      "X-Actor": encodeURIComponent(actor),
      "X-Submit": autoSub ? "1" : "0"
    }
  }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d.ok) {
      if (res) res.innerHTML = '<div class="eco-hl" style="background:#FDECEA;border-color:#E4B7B2">解析失败：' +
        esc(d.error || "") + '</div>';
      toast(d.error || "解析失败", true);
      return;
    }
    if (d.kind === "claim") {          /* 提报源表：一次拆出多个月，逐期间建单 */
      ECO.lastUpload = d;
      var first = (d.submissions || [])[0];
      if (first) ECO.sel.sub = first.sub_id;
      if (res) res.innerHTML = claimResultHtml(d);
      var gb0 = $("#ecoGateBox");
      if (gb0) gb0.innerHTML = gateHtml(first ? first.gates : null);
      toast(d.message);
      ECO.load();
      return;
    }
    ECO.lastUpload = d;
    ECO.sel.sub = d.sub_id;
    if (res) {
      res.innerHTML = '<div class="eco-hl"><b>' + esc(d.message) + '</b><br>' +
        '<span class="eco-k">期间 ' + esc(d.period) + ' · 主体 ' + esc(d.entity) + ' · v' + d.version +
        ' · ' + d.rows + ' 行 · 流入 ' + fmt(d.inflow) + ' · 流出 ' + fmt(d.outflow) + ' · 净额 ' + fmt(d.net) + '</span>' +
        ((d.warnings || []).length ? '<br><span class="eco-k">提醒：' + esc((d.warnings || []).join("；")) + '</span>' : "") +
        ((d.skipped || []).length ? '<br><span class="eco-k">跳过 ' + d.skipped.length + ' 行：' +
          esc((d.skipped || []).map(function (s) { return "第" + s.row + "行 " + s.subject; }).join("；")) + '</span>' : "") +
        '</div>';
    }
    var gb = $("#ecoGateBox"); if (gb) gb.innerHTML = gateHtml(d.gates);
    toast(d.message);
    ECO.load();
  }).catch(function (e) {
    if (res) res.innerHTML = '<div class="eco-hl" style="background:#FDECEA;border-color:#E4B7B2">上传失败：' + esc(e) + '</div>';
  });
}


/* ---------- 提报源表拆解的展示 ---------- */
function claimResultHtml(d) {
  var h = '<div class="eco-hl"><b>' + esc(d.message) + '</b><br>' +
    '<span class="eco-k">主体 ' + esc(d.entity) + ' · 规则版本 v1（C-01~C-09）· 原件 ' + esc(d.saved_as || "") + '</span></div>';
  /* 规则命中 */
  var R = d.rules || {}, RMAP = {
    "C-01": "回款＝件数×售价×扣率（不读源表收入金额列）",
    "C-02": "客户分类＝KA 才进主表，非 KA 剔除",
    "C-03": "扣率空且中心化 → 按主数据扣率补算",
    "C-04": "扣率空且非中心化 → 待补清单",
    "C-05": "部门费用类型 → 计划科目",
    "C-06": "人力预算按说明实质归类",
    "C-07": "人力预算缺工资社保 → 不补数、标注缺口"
  };
  h += '<div style="margin:8px 0 4px" class="eco-k">规则命中</div><div class="eco-grid eco-g3">';
  Object.keys(RMAP).forEach(function (k) {
    h += '<div class="eco-kv"><span>' + k + ' ' + RMAP[k] + '</span><b>' + (R[k] || 0) + '</b></div>';
  });
  h += '</div>';
  /* 期间表 */
  h += '<div style="margin:10px 0 4px" class="eco-k">拆解出的期间（选中一张可在下方审批）</div>' +
    '<table class="eco-tbl"><thead><tr><th></th><th>期间</th><th>提报单</th><th>版本</th><th>状态</th>' +
    '<th>行数</th><th>流入</th><th>流出</th><th>净额</th><th>G1</th><th>G2</th></tr></thead><tbody>';
  (d.submissions || []).forEach(function (x) {
    var g1 = (x.gates || {}).G1 || {}, g2 = (x.gates || {}).G2 || {};
    h += '<tr><td><input type="radio" name="ecoclaim" data-ecosel="sub" value="' + x.sub_id + '"' +
      (ECO.sel.sub === x.sub_id ? " checked" : "") + '></td><td><b>' + esc(x.period) + '</b></td>' +
      '<td>' + x.sub_id + '</td><td>v' + x.version + '</td><td>' + stPill(x.status) + '</td>' +
      '<td>' + x.rows + '</td><td>' + fmt(x.inflow) + '</td><td>' + fmt(x.outflow) + '</td>' +
      '<td><b>' + fmt(x.net) + '</b></td>' +
      '<td>' + (g1.pass ? "✓" : "✗") + '</td><td>' + (g2.pass ? "✓" : "✗") + '</td></tr>';
  });
  h += '</tbody></table>';
  /* 附页与清单 */
  h += '<div class="eco-grid eco-g3" style="margin-top:8px">' +
    '<div class="eco-kv"><span>附页1 销售回款明细</span><b>' + (d.appendix_sales || []).length + ' 条</b></div>' +
    '<div class="eco-kv"><span>附页2 费用申报明细</span><b>' + (d.appendix_expense || []).length + ' 条</b></div>' +
    '<div class="eco-kv"><span>待补清单（C-04）</span><b>' + (d.pending || []).length + ' 条</b></div>' +
    '<div class="eco-kv"><span>已剔除（非 KA，C-02）</span><b>' + (d.excluded || []).length + ' 条</b></div>' +
    '</div>';
  if ((d.pending || []).length) {
    h += '<div class="eco-note">待补：' + esc((d.pending || []).slice(0, 6).map(function (p) {
      return p["客户"] + "（" + (p["客户分类"] || "未标注") + "）";
    }).join("；")) + ((d.pending || []).length > 6 ? " …" : "") + '</div>';
  }
  ((d.warnings || [])).forEach(function (w) {
    h += '<div class="eco-note" style="color:#A5352A">⚠ ' + esc(w) + '</div>';
  });
  h += '<div class="eco-note">拆解口径：流入按客户 1 行、流出按科目 1 行，明细全部进附页可追溯；' +
    '非 KA 回款只披露不进合计；扣率缺失的不猜、进待补清单。确认无误后在下方逐期间提交审批。</div>';
  return h;
}



/* 把「已审核通过」的资金计划灌进管理视角（DATA.plan），并补齐 新科目 → 收支分类 映射。
   这样「审核通过 → 进管理视角」看到的是刚批的计划，而不是旧的 dwd_plan 样例数据。 */
var ECO2CAT = {
  "销售回款（商品/渠道分销）": "销售收款",
  "服务与咨询费收入": "销售收款",
  "内容分成及广告收入": "销售收款",
  "平台返点与佣金收入": "销售收款",
  "退税款及政府补贴": "销售收款",
  "其他经营流入": "销售收款",
  "采购货款与生产成本": "采购付款",
  "人力薪酬及社保公积金": "人力费",
  "市场推广及渠道佣金": "市场费",
  "物流仓储及履约": "市场费",
  "差旅及业务招待": "市场费",
  "专业服务费（审计/法务/财税）": "市场费",
  "IT与软件订阅": "市场费",
  "租金物业及水电": "市场费",
  "其他经营流出": "市场费",
  "税费缴纳": "税费"
};

function applyLockedPlan() {
  if (!ECO.on || !ECO.locked || !ECO.locked.length) return false;
  if (typeof DATA === "undefined" || !DATA) return false;
  var rows = ECO.locked.map(function (i) {
    var subj = i.subject || "";
    var cat = ECO2CAT[subj] || "其他";
    try { if (typeof PLAN2CAT !== "undefined" && PLAN2CAT) PLAN2CAT[subj] = cat; } catch (e) { }
    return { month: i.period, dir: i.direction, cat: "经营活动", subj: subj,
      src: "资金计划 #" + i.sub_id + " v" + i.version, amt: Number(i.amt_cny) || 0,
      tax: "含税", note: i.note || "" };
  });
  DATA.plan = rows;

  try { if (typeof S !== "undefined" && S.raw) S.raw.plan = { rows: rows, meta: {}, items: [], dup: 0, name: "资金计划（已审核）" }; } catch (e) { }
  return true;
}

/* =====================================================================
   界面拆分：流水接入（p0）与 资金计划（p6）各管一摊
   - p0 只留银行流水相关（计划卡片、参数卡片、计划上传区都挪走）
   - 计划未审核通过时，管理视角顶部拦一条提示，引导先审核
   ===================================================================== */

/* =====================================================================
   打标规则：只留一个精简入口
   - 规则库整块 UI 收起来；需要时下载 CSV
   - 「补充规则」用一句话或几个字段描述，实时试算命中了多少笔，确认后写入规则库并重跑打标
   ===================================================================== */
function ruleRows() {
  try { return flowRows(); } catch (e) { return []; }
}
function injectRuleBox() {
  var p0 = document.getElementById("p0");
  if (!p0 || document.getElementById("ecoRuleBox")) return;
  var d = document.createElement("div");
  d.id = "ecoRuleBox";
  d.className = "card";
  d.style.marginTop = "14px";
  d.innerHTML = '<div class="eco-hd"><h3>打标规则 <span>流水按规则自动归类；命中不了的进「未匹配」，可在这里补一条</span></h3>' +
    '<div style="display:flex;gap:8px">' +
    '<button class="eco-btn gh" data-eco="ruledownload">⬇ 下载规则</button>' +
    '<button class="eco-btn" data-eco="ruleopen">＋ 补充规则</button></div></div>' +
    '<div class="eco-k" id="ecoMatchStat" style="margin:-2px 0 6px">已匹配 — 笔 / 未匹配 — 笔</div>' +
    '<div id="ecoRulePanel" style="display:none;margin-top:6px">' +
    '<label class="eco-k">一句话描述（可选，自动填下面）' +
    '<input class="eco-in" id="ecoRsent" placeholder=\'摘要含"货款"的收 → 主营业务收入/线下货款\'></label>' +
    '<div style="display:flex;gap:8px;margin-top:8px"><button class="eco-btn gh" data-eco="ruleparse" style="padding:4px 10px;font-size:11.5px">解析这句话</button>' +
    '<span class="eco-k" id="ecoRpreview" style="align-self:center">试算：命中 0 笔</span></div>' +
    '<div class="eco-grid eco-g3" style="margin-top:10px">' +
    '<label class="eco-k">对方户名含<input class="eco-in" id="ecoRcp" placeholder="如 美丽谷"></label>' +
    '<label class="eco-k">摘要含<input class="eco-in" id="ecoRmemo" placeholder="如 货款"></label>' +
    '<label class="eco-k">方向<select class="eco-in" id="ecoRdir"><option value="">不限</option><option value="收">收</option><option value="支">支</option></select></label>' +
    '<label class="eco-k">一级科目<input class="eco-in" id="ecoRl1" placeholder="如 主营业务成本"></label>' +
    '<label class="eco-k">二级科目<input class="eco-in" id="ecoRl2" placeholder="如 采购货款"></label>' +
    '<label class="eco-k">现金流量分类<select class="eco-in" id="ecoRcf"><option>经营活动</option><option>投资活动</option><option>筹资活动</option><option>不列入现金流量</option></select></label>' +
    '</div><div style="margin-top:10px;display:flex;gap:8px">' +
    '<button class="eco-btn" data-eco="ruleapply">写入规则库并按新规则重跑</button>' +
    '<button class="eco-btn gh" data-eco="ruleclose">收起</button></div>' +
    '<div class="eco-note">写入后立刻对现有流水重跑打标，回到「已匹配 / 未匹配」就能看到变化；规则同时存本地与数仓。</div>' +
    '</div>';
  p0.appendChild(d);
  ["ecoRcp", "ecoRmemo", "ecoRl1", "ecoRl2"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener("input", rulePreview);
  });
  var dd = document.getElementById("ecoRdir"), cc = document.getElementById("ecoRcf");
  if (dd) dd.addEventListener("change", rulePreview);
  if (cc) cc.addEventListener("change", rulePreview);
}
function ruleDraft() {
  var g = function (id) { var e = document.getElementById(id); return e ? String(e.value || "").trim() : ""; };
  return { cp: g("ecoRcp"), memo: g("ecoRmemo"), dir: g("ecoRdir"), l1: g("ecoRl1"), l2: g("ecoRl2"), cf: g("ecoRcf") || "经营活动" };
}
function rulePreview() {
  var d = ruleDraft(), hit = 0, amt = 0;
  if (!d.cp && !d.memo) {
    var el0 = document.getElementById("ecoRpreview");
    if (el0) el0.textContent = "试算：填「户名含」或「摘要含」后显示命中笔数";
    return;
  }
  ruleRows().forEach(function (r) {
    if (d.dir && r.dir !== d.dir) return;
    if (d.cp && String(r.cp || "").indexOf(d.cp) < 0) return;
    if (d.memo && String(r.memo || "").indexOf(d.memo) < 0) return;
    hit++; amt += r.amt;
  });
  var el = document.getElementById("ecoRpreview");
  if (el) el.innerHTML = '试算：命中 <b>' + hit + '</b> 笔，合计 <b>' + fmt(amt) + '</b> 元';
}
function ruleParse() {
  var t = (document.getElementById("ecoRsent") || {}).value || "";
  t = String(t).trim();
  if (!t) return;
  var qs = [], m, re = /["“'']([^"”'']+)["”'']/g;
  while ((m = re.exec(t))) qs.push(m[1]);
  var arrow = t.split(/→|->|=>/);
  if (arrow[1]) {
    var parts = arrow[1].split(/[\/、]/).map(function (x) { return x.trim(); }).filter(Boolean);
    if (parts[0]) document.getElementById("ecoRl1").value = parts[0];
    if (parts[1]) document.getElementById("ecoRl2").value = parts[1];
  }
  if (qs.length) {
    if (/户名|对方/.test(t)) {
      document.getElementById("ecoRcp").value = qs[0];
      if (qs[1]) document.getElementById("ecoRmemo").value = qs[1];
    } else {
      document.getElementById("ecoRmemo").value = qs[0];
    }
  }
  if (/收|进账|入账/.test(t)) document.getElementById("ecoRdir").value = "收";
  if (/支|出账|付款/.test(t)) document.getElementById("ecoRdir").value = "支";
  rulePreview();
}
async function ruleApply() {
  var d = ruleDraft();
  if (!d.cp && !d.memo) { toast("至少填「户名含」或「摘要含」", true); return; }
  if (!d.l1) { toast("请填一级科目", true); return; }
  var mx = 0;
  (S.rules || []).forEach(function (r) { var m = /^R-(\d+)$/.exec(String(r.id || "")); if (m) mx = Math.max(mx, +m[1]); });
  var nr = { id: "R-" + ("0" + (mx + 1)).slice(-2), cp: d.cp, memo: d.memo, l1: d.l1, l2: d.l2, cf: d.cf };
  S.rules.push(nr);
  var saved=await saveRulesLS();
  try {
    if (S.raw && S.raw.flow && S.raw.flow.rows && typeof applyFlow === "function") {
      applyFlow(S.raw.flow.rows, "all");
    }
  } catch (e) { }
  toast((saved.ok ? "已保存规则 " : "规则草稿已保留，等待重试 ") + nr.id + "：" + [d.cp, d.memo].filter(Boolean).join(" / ") + " → " + d.l1 + (d.l2 ? "/" + d.l2 : ""),!saved.ok);
  rulePreview();
  if (ECO.tab === "p6") renderP6();
}
function ruleDownload() {
  try {
    var txt = (typeof ruleCSV === "function") ? ruleCSV() : JSON.stringify(S.rules || [], null, 1);
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob(["\ufeff" + txt], { type: "text/csv;charset=utf-8" }));
    a.download = "打标规则.csv";
    a.click();
  } catch (e) { toast("导出失败：" + e, true); }
}


/* =====================================================================
   页面切换：主流程导航条
   流程：流水接入 → 资金计划 → 管理视角 → 下钻分析 → 收支明细 → 预实分析 → 输出报表
   业务参数中心 / 预实比对·生态 属于配置与工具，只从左侧栏进，不进主流程
   ===================================================================== */
var FLOW = [["p0", "流水接入"], ["p6", "资金计划"], ["p1", "管理视角"],
            ["p2", "下钻分析"], ["p3", "收支明细"], ["p4", "预实分析"], ["p5", "输出报表"]];

function injectPageNav(id) {
  var old = document.getElementById("ecoPageNav");
  if (old) old.parentNode.removeChild(old);
  var idx = -1;
  for (var i = 0; i < FLOW.length; i++) if (FLOW[i][0] === id) idx = i;
  if (idx < 0) return;                       /* 配置/工具页不加导航条 */
  var pane = document.getElementById(id);
  if (!pane) return;
  var title = pane.querySelector(".pane-title");
  if (!title) return;
  var prev = FLOW[idx - 1], next = FLOW[idx + 1];
  var d = document.createElement("div");
  d.id = "ecoPageNav";
  d.className = "eco-pagenav";
  d.innerHTML = '<span class="eco-pn-cur">第 ' + (idx + 1) + ' / ' + FLOW.length + ' 步 · ' + FLOW[idx][1] + '</span>' +
    '<span style="flex:1"></span>' +
    (prev ? '<button data-eco="pnav" data-to="' + prev[0] + '">← ' + prev[1] + '</button>' : "") +
    (next ? '<button class="eco-pn-next" data-eco="pnav" data-to="' + next[0] + '">' + next[1] + ' →</button>' : "");
  title.parentNode.insertBefore(d, title.nextSibling);
}

function syncLayout() {
  try {
    var t0 = $('#tabs [data-t="p0"]');
    if (t0 && t0.textContent.indexOf("流水接入") < 0) t0.innerHTML = '<b>〇</b>流水接入';   /* 幂等：避免触发观察器自循环 */
    groupTabs();
    if (FILE_MODE) fileModeBar();
    ["src2", "src3"].forEach(function (id) {          /* ② 资金计划 / ③ 业务参数 两张卡 */
      var n = document.getElementById(id);
      var c = n && n.closest ? n.closest(".card") : null;
      if (c) c.style.display = "none";
    });
    var d2 = document.getElementById("dz2");           /* 上传资金计划的拖拽区 */
    if (d2) d2.style.display = "none";
    var tp = document.querySelector('#p0 [data-tpl="plan"]');
    if (tp) tp.style.display = "none";                 /* 计划模板按钮移到了模块六 */
    var h2t = document.querySelector("#p0 .pane-title h2");
    if (h2t && h2t.textContent.indexOf("流水接入") < 0) h2t.textContent = "流水接入";
    var h0 = document.querySelector("#p0 .pane-title span");
    if (h0 && h0.textContent.indexOf("流水") < 0) h0.textContent = "上传银行流水、跑规则打标、确认未识别的流水；资金计划另有独立入口";
    $$('#p0 [data-go]').forEach(function (b) {
      if (b.textContent.indexOf("应用规则") >= 0) {
        b.textContent = "下一步：人工审核 →";
        b.setAttribute("data-go", "3");   /* 规则库已收起，直接进人工审核 */
      }
    });
    /* 步骤条连续编号：隐藏了「应用规则」「业务参数」后，仍显示 1 / 2 / 3 */
    var vis = $$('#p0 [data-step]').filter(function (b) { return b.style.display !== "none"; });
    vis.sort(function (x, y) { return (+x.dataset.step) - (+y.dataset.step); });
    vis.forEach(function (b, i) {
      var bb = b.querySelector("b"), t = String(i + 1);
      if (bb && bb.textContent !== t) bb.textContent = t;
    });
    /* 没有流水时「人工审核」不可点：先导入流水（或从申请记录去修改）才开放 */
    var hasFlow = ((typeof DATA !== "undefined" && DATA && DATA.actMeta && DATA.actMeta.rows) || 0) > 0
      || ((typeof S !== "undefined" && S && S.raw && S.raw.flow && S.raw.flow.rows) || []).length > 0;
    var st3 = document.querySelector('#p0 [data-step="3"]');
    if (st3) {
      if (!hasFlow) {
        st3.title = "请先导入银行流水（或从申请记录点「去修改」）后再人工审核";
        st3.style.opacity = ".42";
        st3.style.pointerEvents = "none";
        st3.style.filter = "grayscale(1)";
        if (typeof S !== "undefined" && S && S.step === 3) {
          S.step = 1;
          try { renderSteps(); renderStepBody(); } catch (e3) { }
        }
      } else {
        st3.title = "";
        st3.style.opacity = "";
        st3.style.pointerEvents = "";
        st3.style.filter = "";
      }
    }
    /* 手动导入不会走首屏 BOOT，这里兜底：有流水但还没打标就立刻应用规则 */
    /* 注意：v9 的 S 是顶层 const，不在 window 上，必须用裸标识符 */
    var fr = (typeof S !== "undefined" && S && S.raw && S.raw.flow && S.raw.flow.rows) || null;
    var actN = (typeof DATA !== "undefined" && DATA && DATA.actAll) ? DATA.actAll.length : 0;
    if (fr && !actN && !window.__ecoTagging) {
      window.__ecoTagging = 1;
      try { applyFlow(fr, "all"); } catch (e0) { }
      window.__ecoTagging = 0;
    }
    /* 打标结果直接挂在「导入银行流水」卡片里，下面再点「下一步：人工审核」 */
    var cardImp = null;
    $$('#p0 .card').forEach(function (c) {
      if (!cardImp && String(c.textContent || "").indexOf("导入银行流水") >= 0) cardImp = c;
    });
    if (cardImp) {
      var st = document.getElementById("ecoTagStat");
      if (!st) { st = document.createElement("div"); st.id = "ecoTagStat"; }
      st.className = "eco-k";
      st.style.cssText = "margin:10px 0 0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;" +
        "padding-top:8px;border-top:1px dashed #E8ECF2";
      var fs2 = (typeof flowStats === "function") ? flowStats() : { all: [], ok: [], no: [] };
      var tot = fs2.all.length, okn = fs2.ok.length, non = fs2.no.length;
      var msg = tot
        ? ('✓ 已自动应用打标规则：共 ' + tot + ' 笔 · 命中 ' + okn + ' 笔（' +
           (tot ? Math.round(okn / tot * 100) : 0) + '%）· 未识别 ' + non + ' 笔')
        : '尚未导入流水：导入后会自动应用打标规则';
      var hh = '<span>' + msg + '</span>' +
        (tot ? '<button class="eco-btn gh" data-eco="rerunrules" style="padding:2px 9px;font-size:11px">重新应用</button>' : '');
      if (st._sig !== hh) { st.innerHTML = hh; st._sig = hh; }
      if (st.parentNode !== cardImp) cardImp.appendChild(st);
    }
    /* v9 的 step3 用 S.applied 判断「是否已打标」，手动导入后必须置位，否则一直提示「还没有应用规则」 */
    if (typeof S !== "undefined" && S) {
      var actN2 = (typeof DATA !== "undefined" && DATA && DATA.actAll) ? DATA.actAll.length : 0;
      if (actN2 > 0 && S.applied !== true) {
        S.applied = true;
        if (S.step === 3 && typeof renderStepBody === "function") setTimeout(function () { renderStepBody(); }, 0);
      }
    }
    /* step3（人工审核）里 v9 内置的黄色提示引用了已删除的步骤，这里按真实打标状态接管 */
    var warnEl = null;
    $$('#p0 div').forEach(function (el) {
      if (warnEl || el.children.length > 3) return;
      var tx = String(el.textContent || "");
      if (tx.indexOf("还没有应用规则") >= 0 || tx.indexOf("尚未打标") >= 0) warnEl = el;
    });
    if (warnEl) {
      var taggedN = (typeof DATA !== "undefined" && DATA && DATA.actAll) ? DATA.actAll.length : 0;
      if (taggedN > 0) {
        warnEl.style.display = "none";
      } else {
        warnEl.style.display = "";
        var wtx = "尚未打标：导入流水时会自动应用规则；若刚清空过，请重新上传流水（或点上方的「重新应用」）。";
        if (warnEl.textContent.trim() !== wtx) warnEl.textContent = wtx;
      }
    }
    injectManualSave();
    var mst = document.getElementById("ecoMatchStat");
    if (mst && typeof flowStats === "function") {
      var fs = flowStats();
      var txt = "已匹配 " + fs.ok.length + " 笔 / 未匹配 " + fs.no.length + " 笔（点「人工审核」逐条确认未匹配）";
      if (mst.textContent !== txt) mst.textContent = txt;
    }
    var h = document.getElementById("dzhint");
    if (h && !h._eco) {
      h._eco = 1;
      h.innerHTML = "本页只管银行流水。资金计划请到「六 · 资金计划」上传、看文档并审核通过。";
    }
    /* 「导入资金计划」独立卡片 */
    $$("#p0 .card").forEach(function (c) {
      var t = (c.querySelector("h3") || {}).textContent || "";
      if (t.indexOf("导入资金计划") >= 0 && t.indexOf("导入银行流水") < 0) c.style.display = "none";
    });
    /* 步骤条：「应用规则」「业务参数」都收起来（规则走下面的精简入口，参数在配置里） */
    var st4 = document.querySelector('#p0 [data-step="4"]');
    if (st4) st4.style.display = "none";
    var st2 = document.querySelector('#p0 [data-step="2"]');
    if (st2) st2.style.display = "none";
    if (window.S && S.step === 2) { S.step = 1; if (typeof renderSteps === "function") renderSteps(); if (typeof renderStepBody === "function") renderStepBody(); }
    injectRuleBox();
    /* 上传结果表里的「资金计划」行 */
    $$("#p0 table tr").forEach(function (tr) {
      var f = tr.cells && tr.cells[0];
      if (f && f.textContent.trim() === "资金计划") tr.style.display = "none";
    });
  } catch (e) { }
}


/* 左侧导航按板块分组：数据（流水接入、资金计划）/ 分析（管理视角…输出报表）/ 配置（参数、预实生态） */
function groupTabs() {
  var t = document.getElementById("tabs");
  if (!t || t._grp) return;
  t._grp = 1;
  var groups = [["数据", ["p0", "p6"]], ["分析", ["p1", "p2", "p3", "p4", "p5"]],
                ["配置", ["p7", "p8"]]];
  var btns = {};
  $$("button", t).forEach(function (b) { if (b.dataset && b.dataset.t) btns[b.dataset.t] = b; });
  var used = {};
  if (!t.querySelector(".eco-navbrand")) {
    var br = document.createElement("div");
    br.className = "eco-navbrand";
    br.innerHTML = "资金计划<span>v10</span>";
    t.insertBefore(br, t.firstChild);
  }
  groups.forEach(function (g, gi) {
    var hd = document.createElement("div");
    hd.className = "eco-navgrp";
    hd.textContent = g[0];
    t.appendChild(hd);
    g[1].forEach(function (id) {
      var b = btns[id];
      if (!b) return;
      b.classList.add("eco-g" + (gi + 1));
      if (!b.querySelector(".eco-dot")) {
        var dot = document.createElement("i");
        dot.className = "eco-dot";
        b.insertBefore(dot, b.firstChild);
      }
      t.appendChild(b);                                    /* appendChild 即移动，不丢事件 */
      used[id] = 1;
    });
  });
  Object.keys(btns).forEach(function (id) { if (!used[id]) t.appendChild(btns[id]); });
}

/* 双击 HTML（file://）打开时：浏览器会拦接口请求，给一键入口切到服务版 */
function fileModeBar() {
  if (document.getElementById("ecoFileBar")) return;
  if (!/^file:/.test(location.protocol)) return;
  var d = document.createElement("div");
  d.id = "ecoFileBar";
  d.style.cssText = "position:fixed;left:50%;transform:translateX(-50%);bottom:16px;z-index:999;" +
    "background:#FFF7E0;border:1px solid #F0DFA8;border-radius:8px;padding:8px 14px;font-size:12px;" +
    "color:#7C4A03;box-shadow:0 6px 18px rgba(0,0,0,.14);max-width:min(760px,92vw)";
  d.innerHTML = '当前是<b>双击打开的本地文件（file://）</b>，浏览器会拦掉上传/审批需要的接口。' +
    ' <button class="eco-btn" style="padding:3px 10px;font-size:11.5px" data-eco="openhost">打开服务版看板 →</button>' +
    ' <button class="eco-btn gh" style="padding:2px 8px;font-size:11.5px" data-eco="closetip">×</button>';
  document.body.appendChild(d);
}


/* 数仓已连接但没有任何「已锁定」计划时：把管理视角的计划侧清零，
   避免页面内置的演示计划（v9 硬编码）冒充真实计划。 */
function clearPlanIfEmpty() {
  if (!ECO.on) return;                 /* 离线时保留内置演示，至少页面不空 */
  if (ECO.locked && ECO.locked.length) return;
  try {
    if (typeof DATA !== "undefined" && DATA) DATA.plan = [];
    if (typeof S !== "undefined" && S.raw) S.raw.plan = { rows: [], meta: {}, items: [], dup: 0, name: "（无已审核计划）" };
  } catch (e) { }
  if (typeof renderP1 === "function" && (ECO.tab === "p1" || document.querySelector("#p1.on"))) {
    setTimeout(renderP1, 0);
  }
}

function gateP1() {
  var p1 = document.getElementById("p1");
  if (!p1) return;
  var old = document.getElementById("ecoGate1");
  if (old) old.parentNode.removeChild(old);
  if (!ECO.on) return;
  if (ECO.locked && ECO.locked.length) {              /* 已审核通过 → 放行，但标注口径来源 */
    applyLockedPlan();
    var ps = {}, lk = ECO.locked;
    lk.forEach(function (i) { ps[i.period] = 1; });
    var pers = Object.keys(ps).sort();
    var t2 = p1.querySelector(".pane-title");
    var ok = document.createElement("div");
    ok.id = "ecoGate1";
    ok.style.cssText = "background:#F0F9F4;border:1px solid #BFE3CE;border-left:4px solid #1F6F4A;" +
      "border-radius:8px;padding:8px 14px;margin-bottom:14px;font-size:12px;color:#14532D";
    ok.innerHTML = '<b>计划侧已接入审核通过的资金计划</b>　期间：' + pers.join("、") + "（共 " +
      lk.length + " 行，版本取最新已锁定）· 科目为新口径，旧的 dwd_plan 样例数据已不再参与本视角。" +
      ' <button class="eco-btn gh" style="margin-left:8px;padding:2px 9px;font-size:11px" data-tab="p6">查看/审批资金计划 →</button>';
    if (t2 && t2.nextSibling) p1.insertBefore(ok, t2.nextSibling);
    else p1.insertBefore(ok, p1.firstChild);
    if (typeof renderP1 === "function" && ECO.tab === "p1") setTimeout(renderP1, 0);
    return;
  }
  var t = p1.querySelector(".pane-title");
  var div = document.createElement("div");
  div.id = "ecoGate1";
  div.style.cssText = "background:#FFF7E0;border:1px solid #F0DFA8;border-left:4px solid #B7791F;" +
    "border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12.5px;color:#7C4A03";
  div.innerHTML = '<b>资金计划尚未审核通过</b>　当前管理视角只按银行流水口径看，计划侧为空。' +
    '请先到「六 · 资金计划」上传计划表、核对文档、点「审核通过」。' +
    ' <button class="eco-btn" style="margin-left:8px;padding:3px 10px;font-size:11.5px" data-tab="p6">去资金计划 →</button>';
  if (t && t.nextSibling) p1.insertBefore(div, t.nextSibling);
  else p1.insertBefore(div, p1.firstChild);
}

/* 一键审核通过：按当前状态把 提交→复核→批准 串起来，每步都留痕 */
function approveAll() {
  var actor = ($("#ecoApActor") || {}).value || "";
  if (!actor) { toast("请先填操作人（审核要留痕）", true); return; }
  localStorage.setItem("eco.actor", actor);
  var force = !!($("#ecoForce") || {}).checked;
  var sub = (ECO.data.submissions || []).filter(function (x) { return x.id === ECO.sel.sub; })[0];
  if (!sub) { toast("先在清单里选一张提报单", true); return; }
  var chain = [];
  if (sub.status === "草稿" || sub.status === "退回") chain = ["提交", "批准"];
  else if (sub.status === "待审") chain = ["批准"];
  else if (sub.status === "待批") chain = ["批准"];
  else { toast("当前状态「" + sub.status + "」不需要审核通过", true); return; }
  var i = 0;
  toast("正在审核通过 #" + sub.id + "（" + sub.period + "）…");
  (function step() {
    if (i >= chain.length) {
      toast("已审核通过并锁定 #" + sub.id + "（" + sub.period + "）");
      ECO.load(function () { if (ECO.tab === "p6") renderP6(); });
      return;
    }
    var a = chain[i++];
    post("/api/submission-action", { id: sub.id, action: a, actor: actor, force: force,
      reason: "人工审核通过（一键）" }).then(function (d) {
      if (!d.ok) { toast(a + " 未通过：" + (d.error || ""), true); return; }
      step();
    }).catch(function () { toast("网络异常，审核中断", true); });
  })();
}


/* 保存按钮与自动保存共用一条链路，只在服务器确认后显示成功。 */
function manualCount(){ return Object.keys(S.manual||{}).length; }
function hhmmss(ts){ return new Date(ts||Date.now()).toLocaleTimeString("zh-CN",{hour12:false}); }
function persistManual(silent){
  if(S.mMode) {
    saveManualDraft();
    if(!silent) return saveSelected();
    mStat("修改草稿已保留，点「保存所选」写入数仓","#B7791F");
    return Promise.resolve({ok:false,draft:true});
  }
  return flushManual("save").then(function(d){
    if(!silent) toast(d.ok ? "确认结果已保存到数仓" : "尚未写入数仓，修改已保留待重试",!d.ok);
    return d;
  });
}
/* 在人工确认页注入「保存已确认结果」按钮（页面重渲染后自动补回） */
function injectManualSave() {
  var exp = document.getElementById("mExp");
  if (!exp || document.getElementById("ecoSaveBtn")) return;
  var btn = document.createElement("button");
  btn.id = "ecoSaveBtn";
  btn.className = "btn ghost";
  btn.textContent = "保存进度（可中断）";
  btn.title = "随时点一下就把当前已确认的标签存到本地与数仓，关掉浏览器回来可以接着做";
  btn.style.marginLeft = "6px";
  btn.addEventListener("click", function () { persistManual(false); });
  exp.parentNode.appendChild(btn);

  var st = document.createElement("span");
  st.id = "ecoSaveStat";
  st.className = "eco-k";
  st.style.marginLeft = "8px";
  var snap = null;
  try { snap = JSON.parse(localStorage.getItem("eco.saveSnap") || "null"); } catch (e2) { }
  st.textContent = "已确认 " + manualCount() + " 笔 · " +
    (snap && snap.ts ? ("上次保存 " + hhmmss(snap.ts) + "（" + (snap.done || 0) + " 笔）") : "变更会自动保存");
  exp.parentNode.appendChild(st);

  /* 全部确认后的按钮：原指向已隐藏的「业务参数」步，改为直接进看板 */
  var fb = document.querySelector('#mFooter [data-go="4"]');
  if (fb) { fb.setAttribute("data-go", "5"); fb.textContent = "全部确认并进入看板 →"; }
}

/* ---------- 点击 / 选择 分发 ---------- */
function ecoClick(b) {
  var k = b.dataset.eco;
  if (k === "pnav") { showTab(b.dataset.to); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
  if (k === "planstep") {
    var sN = +b.dataset.s;
    if (sN === 3 && !(ECO.locked || []).length) { toast("还没有审批通过的资金计划", true); return; }
    ECO.planStep = sN;
    renderP6();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (k === "flowgo") {
    var tg = b.dataset.target;
    if (tg === "board") {
      if ((ECO.locked || []).length) { showTab("p1"); }
      else { toast("还没有审批通过的资金计划", true); }
      return;
    }
    var el2 = tg === "up" ? document.querySelector("#ecoP6 .card") : document.getElementById("ecoApprove");
    if (el2) el2.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (k === "pick") { ECO.sel.sub = +b.dataset.id; renderP6(); return; }
  if (k === "reload") { ECO.load(); return; }
  if (k === "act") {
    var action = b.dataset.action;
    var actor = ($("#ecoApActor") || {}).value || "";
    var role = ($("#ecoApRole") || {}).value || "";
    var reason = ($("#ecoApReason") || {}).value || "";
    var force = ($("#ecoForce") || {}).checked;
    if (actor) localStorage.setItem("eco.actor", actor);
    if (!actor) { toast("请先填操作人（审批要留痕）", true); return; }
    post("/api/submission-action", { id: ECO.sel.sub, action: action, actor: actor, role: role,
      reason: reason, force: force }).then(function (d) {
      if (!d.ok) {
        toast((d.gate ? d.gate + " 未过：" : "") + (d.error || "操作失败"), true);
        return;
      }
      toast("#" + ECO.sel.sub + " " + d.message);
      ECO.load();
    });
    return;
  }
  if (k === "openhost") { location.href = "http://127.0.0.1:8765/"; return; }
  if (k === "closetip") { if (b.parentNode) b.parentNode.parentNode.removeChild(b.parentNode); return; }
  if (k === "reconnect") { ECO._retried = 0; toast("正在重新连接本地数仓…"); ECO.load(); return; }
  if (k === "lockpass") { approveAll(); return; }
  if (k === "submitonly") {
    var a0 = ($("#ecoApActor") || {}).value || "";
    if (!a0) { toast("请先填操作人", true); return; }
    localStorage.setItem("eco.actor", a0);
    post("/api/submission-action", { id: ECO.sel.sub, action: "提交", actor: a0,
      force: !!($("#ecoForce") || {}).checked, reason: "提交待审" }).then(function (r) {
      toast(r.ok ? "已提交，等待复核" : (r.error || "提交失败"), !r.ok);
      if (r.ok) ECO.load();
    });
    return;
  }
  if (k === "goboard") { showTab("p1"); return; }
  if (k === "flowfilter") { ECO.flowFilter = b.dataset.f; renderP6(); return; }
  if (k === "rerunrules") {
    try {
      if (!(S.raw && S.raw.flow && S.raw.flow.rows)) { toast("还没有导入流水", true); return; }
      applyFlow(S.raw.flow.rows, "all");
      syncLayout();
      toast("已按当前规则重新生成标签");
    } catch (e2) { toast("重跑失败：" + e2, true); }
    return;
  }
  if (k === "ruleopen") { var rp = document.getElementById("ecoRulePanel"); if (rp) rp.style.display = "block"; rulePreview(); return; }
  if (k === "ruleclose") { var rc = document.getElementById("ecoRulePanel"); if (rc) rc.style.display = "none"; return; }
  if (k === "ruleparse") { ruleParse(); return; }
  if (k === "ruleapply") { ruleApply(); return; }
  if (k === "ruledownload") { ruleDownload(); return; }
  if (k === "approvepass") { approveAll(); return; }
  if (k === "saveparams") { saveParams(); return; }
  if (k === "saveterms") { saveTerms(); return; }
  if (k === "pvexport") { exportPV(); return; }
}
function ecoSelect(s) {
  var k = s.dataset.ecosel;
  if (k === "sub") { ECO.sel.sub = +s.value; renderP6(); return; }
  if (k === "concl") {
    ECO.conclMonth = s.value;
    if (typeof renderStepBody === "function") renderStepBody();
    return;
  }
  if (k === "period") { ECO.sel.period = s.value; renderP8(); return; }
  if (k === "entity") { ECO.sel.entity = s.value; renderP8(); return; }
  if (k === "win") { ECO.sel.win = s.value; renderP8(); return; }
}

/* =====================================================================
   p7 · 业务参数中心
   ===================================================================== */
function monthAdd(m, n) {
  var p = String(m).split("-"); var y = +p[0], mo = +p[1] + n;
  y += Math.floor((mo - 1) / 12); mo = ((mo - 1) % 12 + 12) % 12 + 1;
  return y + "-" + (mo < 10 ? "0" : "") + mo;
}
function monthDiff(a, b) {
  var pa = String(a).split("-"), pb = String(b).split("-");
  return (+pb[0] - +pa[0]) * 12 + (+pb[1] - +pa[1]);
}

var configDraft={params:{},terms:{}};
try { var cd=JSON.parse(localStorage.getItem("eco.configDraft")||"null");if(cd)configDraft={params:cd.params||{},terms:cd.terms||{}}; }catch(e){}
function storeConfigDraft(){localStorage.setItem("eco.configDraft",JSON.stringify(configDraft));}
function captureConfigDraft(ev){
  var el=ev.target;
  if(el.matches && el.matches("[data-pid]")) configDraft.params[el.dataset.pid]=el.value;
  else if(el.matches && el.matches("[data-term]")) {
    var t=(ECO.data.terms||[])[+el.dataset.term];if(t)configDraft.terms[t.customer]=el.value;
  }else return;
  storeConfigDraft();
  var msg=$("#configSaveState");if(msg)msg.textContent="修改草稿已保留，点击对应保存按钮后生效";
}
document.addEventListener("input",captureConfigDraft);
document.addEventListener("change",captureConfigDraft);
function renderP7() {
  var d = ECO.data, box = $("#ecoP7"); if (!box) return;
  var groups = {};
  (d.params || []).forEach(function (p) { (groups[p.grp] = groups[p.grp] || []).push(p); });
  var ORDER = ["汇率", "账期", "回款", "水位", "阈值", "口径", "预测"];
  var keys = ORDER.filter(function (k) { return groups[k]; })
    .concat(Object.keys(groups).filter(function (k) { return ORDER.indexOf(k) < 0; }));

  var html = '<div id="configSaveState" class="eco-note">' + (Object.keys(configDraft.params).length||Object.keys(configDraft.terms).length ? '有未提交的修改草稿，保存后生效' : '已加载数仓中保存的配置') + '</div><div class="card" style="margin-bottom:14px"><h3>可调整业务参数 <span>改完点「保存参数」，全看板与预实比对联动</span>' +
    ' <button class="eco-btn" data-eco="saveparams">保存参数</button></h3>' +
    '<div class="eco-grid eco-g2">';
  keys.forEach(function (g) {
    html += '<div style="border:1px solid #E3E7EE;border-radius:8px;padding:10px">' +
      '<div style="font-size:12.5px;font-weight:700;margin-bottom:6px">' + esc(g) + '</div>';
    groups[g].forEach(function (p) {
      html += '<div class="eco-kv"><span>' + esc(p.label) +
        (p.scope ? ' <span class="eco-pill" style="background:#EEF2F8;color:#4A5568">' + esc(p.scope) + '</span>' : "") +
        (p.unit ? ' <span class="eco-k">(' + esc(p.unit) + ')</span>' : "") +
        '<br><span class="eco-k">' + esc(p.note || "") + '</span></span>' +
        '<b style="min-width:110px"><input class="eco-in" data-pid="' + esc(p.id) + '" value="' + esc(Object.prototype.hasOwnProperty.call(configDraft.params,p.id)?configDraft.params[p.id]:p.value) + '"' +
        (p.locked ? ' title="系统内置阈值，谨慎修改"' : "") + '></b></div>';
    });
    html += '</div>';
  });
  html += '</div><div class="eco-note">这些参数就是「业务给的预测假设」：账期决定回款什么时候到账、回款比例决定当月能收回多少、' +
    '安全线决定什么时候报警、差异阈值决定预实比对怎么标色。改这里不改脚本，规则表化。</div></div>';

  /* 客户账期 */
  html += '<div class="card" style="margin-bottom:14px"><h3>客户账期 <span>预实比对的窗口口径，逐户可改</span>' +
    ' <button class="eco-btn gh" data-eco="saveterms">保存账期</button></h3>' +
    '<div class="eco-scroll"><table class="eco-tbl"><thead><tr><th>客户</th><th>分类</th><th>账期(天)</th><th>说明</th><th>最近修改</th></tr></thead><tbody>';
  (d.terms || []).forEach(function (t, i) {
    html += '<tr><td>' + esc(t.customer) + '</td><td>' + esc(t.klass) + '</td>' +
      '<td style="width:90px"><input class="eco-in" data-term="' + i + '" value="' + esc(Object.prototype.hasOwnProperty.call(configDraft.terms,t.customer)?configDraft.terms[t.customer]:t.term_days) + '"></td>' +
      '<td class="eco-k">' + esc(t.note || "") + '</td><td class="eco-k">' + esc(t.updated_by || "") + ' ' +
      esc(String(t.updated_at || "").slice(0, 10)) + '</td></tr>';
  });
  html += '</tbody></table></div></div>';

  /* 科目桥接 */
  html += '<div class="card" style="margin-bottom:14px"><h3>科目桥接 <span>流水「方向+一级+二级」→ 计划科目明细，未命中进挂起清单</span></h3>' +
    '<div class="eco-scroll"><table class="eco-tbl"><thead><tr><th>ID</th><th>方向</th><th>流水一级</th><th>流水二级</th>' +
    '<th>→ 计划科目明细</th><th>收支分类</th><th>处理</th><th>优先级</th><th>状态</th><th></th></tr></thead><tbody>';
  (d.bridge || []).forEach(function (b) {
    html += '<tr' + (b.status === "停用" ? ' style="opacity:.5"' : "") + '><td class="eco-mono">' + esc(b.id) + '</td><td>' +
      esc(b.direction) + '</td><td>' + esc(b.l1) + '</td><td>' + esc(b.l2) + '</td><td><b>' + esc(b.subject) + '</b></td><td>' +
      esc(b.cashflow) + '</td><td>' + esc(b.handle) + '</td><td>' + b.priority + '</td><td>' + esc(b.status) + '</td>' +
      '<td><button class="eco-btn gh" data-eco="bgtoggle" data-id="' + esc(b.id) + '" style="padding:2px 8px;font-size:11px">' +
      (b.status === "停用" ? "启用" : "停用") + '</button></td></tr>';
  });
  html += '</tbody></table></div><div class="eco-note">新增三元组：在预实比对里对挂起的流水点「补规则」，会写回这张表（规则库迭代的唯一来源）。</div></div>';

  /* 参数驱动的滚动预测 */
  html += '<div class="card"><h3>参数驱动的现金流预测 <span>已锁定计划 × 账期/回款参数 → 未来各月头寸</span></h3>' +
    forecastHtml() + '</div>';
  box.innerHTML = html;
}

function forecastHtml() {
  var horizon = Math.max(1, Math.round(P("fc.horizon", 3)));
  var ratio = P("collect.month_ratio", 1);
  var lag = Math.round(P("term.purchase_lag", 0));
  var defTerm = Math.round(P("term.default", 30));
  var safe = P("safe.line", 2000000);
  var terms = {}; (ECO.data.terms || []).forEach(function (t) { terms[t.customer] = t.term_days; });

  /* 起始月：最晚的已锁定期间 */
  var periods = {}; ECO.locked.forEach(function (i) { periods[i.period] = 1; });
  var ps = Object.keys(periods).sort();
  if (!ps.length) return '<div class="eco-k">还没有已锁定的资金计划。先在模块六走完审批并批准锁定，预测才有输入。</div>';
  var start = ps[ps.length - 1];
  var months = []; for (var i = 0; i < horizon; i++) months.push(monthAdd(start, i));

  var inflow = {}, outflow = {};
  months.forEach(function (m) { inflow[m] = 0; outflow[m] = 0; });
  ECO.locked.forEach(function (it) {
    var amt = num(it.amt_cny);
    if (!amt) return;
    var base = it.period;
    if (it.direction === "流入") {
      var cust = (String(it.note || "").match(/KA客户：([^｜|]+)/) || [])[1];
      var term = cust && terms[cust.trim()] != null ? terms[cust.trim()] : defTerm;
      var now = amt * ratio;
      var rest = amt - now;
      var shift = Math.max(0, Math.round(term / 30));
      add(months, inflow, base, now);
      if (rest) add(months, inflow, monthAdd(base, shift || 1), rest);
    } else {
      var isPur = /采购货款/.test(it.subject || "");
      add(months, outflow, monthAdd(base, isPur ? Math.round(lag / 30) : 0), amt);
    }
  });

  var h = '<table class="eco-tbl"><thead><tr><th>月份</th><th>预测流入</th><th>预测流出</th><th>净现金流</th>' +
    '<th>累计净流</th><th>预测期末余额</th><th>与安全线(' + wan(safe) + '万)关系</th></tr></thead><tbody>';
  var previous=monthAdd(start,-1);
  var openingMonth=((window.__WH_BAL||{}).byMonth||{})[previous];
  var opening=openingMonth && openingMonth._total!=null ? Number(openingMonth._total) : null;
  var cum = 0;
  months.forEach(function (m) {
    var net = inflow[m] - outflow[m]; cum += net;
    h += '<tr><td><b>' + m + '</b></td><td>' + fmt(inflow[m]) + '</td><td>' + fmt(outflow[m]) + '</td>' +
      '<td style="color:' + (net >= 0 ? "#1F6F4A" : "#A5352A") + '"><b>' + fmt(net) + '</b></td>' +
      '<td>' + fmt(cum) + '</td><td>' + (opening==null?'—':fmt(opening+cum)) + '</td><td>' +
      (opening==null ? '<span class="eco-pill">待补期初余额，暂不判断</span>' :
       opening+cum < safe ? '<span class="eco-pill" style="background:#FDECEA;color:#A5352A">低于安全线，缺口 '+fmt(safe-opening-cum)+'</span>' :
       '<span class="eco-pill" style="background:#F0F9F4;color:#1F6F4A">达到安全线</span>') + '</td></tr>';

  });
  h += '</tbody></table><div class="eco-note">口径：流入按「当月回款比例 ' + (ratio * 100).toFixed(0) +
    '%」当月到账，其余按客户账期后移；采购付款按账期 ' + lag + ' 天后移。' +
    '期末余额＝'+previous+' 月末实际余额＋累计预测净流；缺少该月余额时不输出安全结论。</div>';
  return h;
}
function add(months, bag, m, v) { if (months.indexOf(m) >= 0) bag[m] += v; }

var configSaving={params:false,terms:false};
async function saveParams(){
  if(configSaving.params)return {ok:false};
  var vals=Object.assign({},configDraft.params);
  if(!Object.keys(vals).length)return {ok:true};
  configSaving.params=true;
  try{
    var d=await post("/api/params",{params:vals,actor:localStorage.getItem("eco.actor")||""});
    if(!d.ok){toast(d.error||"保存失败，草稿已保留",true);return d;}
    Object.keys(vals).forEach(function(k){if(configDraft.params[k]===vals[k])delete configDraft.params[k];});
    storeConfigDraft();toast("参数已保存到数仓");await ECO.load();return d;
  }finally{configSaving.params=false;}
}
async function saveTerms(){
  if(configSaving.terms)return {ok:false};
  var sent=Object.assign({},configDraft.terms),rows=[];
  Object.keys(sent).forEach(function(customer){
    var t=(ECO.data.terms||[]).filter(function(x){return x.customer===customer;})[0];
    if(t)rows.push({customer:customer,klass:t.klass,term_days:Number(sent[customer]),note:t.note});
  });
  if(rows.some(function(t){return !Number.isInteger(t.term_days)||t.term_days<0||String(sent[t.customer]).trim()==="";})){
    toast("账期必须是大于或等于 0 的整数天数",true);return {ok:false};
  }
  if(!rows.length)return {ok:true};
  configSaving.terms=true;
  try{
    var d=await post("/api/terms",{rows:rows,actor:localStorage.getItem("eco.actor")||""});
    if(!d.ok){toast(d.error||"保存失败，草稿已保留",true);return d;}
    Object.keys(sent).forEach(function(k){if(configDraft.terms[k]===sent[k])delete configDraft.terms[k];});
    storeConfigDraft();toast("客户账期已保存到数仓");await ECO.load();return d;
  }finally{configSaving.terms=false;}
}

/* =====================================================================
   p8 · 预实比对（生态）
   ===================================================================== */
function flowRows() {
  /* 注意：v9 的 DATA 是顶层 const（词法全局），不是 window 属性，必须用裸标识符 + typeof 守卫 */
  var src = (typeof DATA !== "undefined" && DATA && DATA.actAll) || [];
  return src.map(function (r) {
    return { m: String(r[0] || "").replace(/\./g, "-"), d: r[1], dir: r[2], amt: num(r[3]),
      cp: r[4] || "", memo: r[5] || "", k1: r[6] || "", k2: r[7] || "", cat: r[8] || "", cf: r[9] || "" };
  });
}
function tokens(s) {
  return String(s || "").split(/[\/、,，]/).map(function (x) { return x.trim(); })
    .filter(function (x) { return x && x !== "（任意）" && x !== "任意" && x !== "—"; });
}
function hitList(pat, val) {
  var ts = tokens(pat);
  if (!ts.length) return true;
  if (!val) return false;
  return ts.some(function (t) { return val.indexOf(t) >= 0 || t.indexOf(val) >= 0; });
}
function bridgeHit(row) {
  var bs = (ECO.data.bridge || []).filter(function (b) { return b.status !== "停用"; });
  for (var i = 0; i < bs.length; i++) {
    var b = bs[i];
    if (!(b.direction === row.dir || b.direction === "收/支" || b.direction === "全")) continue;
    if (!hitList(b.l1, row.k1)) continue;
    if (!hitList(b.l2, row.k2)) continue;
    return b;
  }
  return null;
}
function inWindow(planMonth, actMonth, mode, termDays) {
  if (mode === "同期") return actMonth === planMonth;
  if (mode === "滚动3月") { var dd = monthDiff(planMonth, actMonth); return dd >= -1 && dd <= 1; }
  var shift = Math.max(0, Math.round((termDays || 0) / 30));
  var dd2 = monthDiff(planMonth, actMonth);
  return dd2 >= 0 && dd2 <= Math.max(1, shift);
}

function computePV() {
  var mode = ECO.sel.win;
  var period = ECO.sel.period;
  var entity = ECO.sel.entity;
  var defTerm = Math.round(P("term.default", 30));
  var terms = {}; (ECO.data.terms || []).forEach(function (t) { terms[t.customer] = t.term_days; });

  /* 计划侧：只认已锁定 */
  var plan = ECO.locked.filter(function (i) {
    return (!period || i.period === period) && (!entity || entity === "全部" || i.entity === entity);
  });
  var planIn = {}, planOut = {}, custRows = [];
  plan.forEach(function (i) {
    var s = i.subject; if (!s) return;
    var neg = /冲减|退款/.test(String(i.note || "")) ? -1 : 1;
    if (i.direction === "流入") planIn[s] = (planIn[s] || 0) + num(i.amt_cny) * neg;
    else planOut[s] = (planOut[s] || 0) + num(i.amt_cny) * neg;
    var cust = (String(i.note || "").match(/KA客户：([^｜|]+)/) || [])[1];
    if (cust) custRows.push(cust.trim());
  });

  /* 实际侧：流水经桥接映射 */
  var rows = flowRows();
  var actIn = {}, actOut = {}, unmatched = [], sidelined = [], matchedAmt = 0, opAmt = 0, tagged = 0;
  var cpSet = {}; rows.forEach(function (r) { if (r.cp) cpSet[r.cp] = 1; });
  rows.forEach(function (r) {
    if (r.k1 && r.k1 !== "未分类") tagged++;
    if (r.cf === "经营活动") opAmt += Math.abs(r.amt);
    var b = bridgeHit(r);
    if (!b) { unmatched.push(r); return; }
    if (b.handle === "剔除" || b.handle === "单列") { sidelined.push({ r: r, b: b }); return; }
    matchedAmt += Math.abs(r.amt);
    /* 窗口：只有落在计划月窗口内的流水才计入该期预实 */
    var term = defTerm;
    var okWin = (function () {
      if (!period) return true;
      return inWindow(period, r.m, mode, term);
    })();
    if (!okWin) return;
    var s = b.subject;
    var sign = b.handle === "冲减（负数）" ? -1 : 1;
    if (r.dir === "收") actIn[s] = (actIn[s] || 0) + r.amt * sign;
    else actOut[s] = (actOut[s] || 0) + r.amt * sign;
  });

  /* 对比行 */
  var okAbs = P("th.var_ok_abs", 10000), okR = P("th.var_ok", 0.05), warnR = P("th.var_warn", 0.2);
  var subs = {};
  Object.keys(planIn).forEach(function (s) { subs[s] = 1; });
  Object.keys(planOut).forEach(function (s) { subs[s] = 1; });
  Object.keys(actIn).forEach(function (s) { subs[s] = 1; });
  Object.keys(actOut).forEach(function (s) { subs[s] = 1; });
  var cmp = [];
  Object.keys(subs).forEach(function (s) {
    var pi = planIn[s] || 0, po = planOut[s] || 0, ai = actIn[s] || 0, ao = actOut[s] || 0;
    var plan = pi - po, act = ai - ao;
    var diff = act - plan;
    var rate = plan !== 0 ? diff / Math.abs(plan) : (act !== 0 ? Infinity : 0);
    var judge;
    if (plan === 0 && act !== 0) judge = "流水有·计划无";
    else if (plan !== 0 && act === 0) judge = "计划有·流水无";
    else if (plan * act < 0) judge = "方向相反";
    else if (Math.abs(diff) <= okAbs || Math.abs(rate) <= okR) judge = "符合";
    else if (Math.abs(rate) <= warnR) judge = "关注";
    else judge = "预警";
    cmp.push({ subject: s, plan: plan, act: act, diff: diff, rate: rate, judge: judge });
  });
  cmp.sort(function (a, b) { return Math.abs(b.diff) - Math.abs(a.diff); });

  /* 体检 */
  var custHitN = custRows.filter(function (c) { return cpSet[c]; }).length;
  var health = [
    { k: "流水打标率", v: rows.length ? tagged / rows.length : 0, th: P("th.tag_rate", 0.8) },
    { k: "经营金额映射率", v: opAmt ? matchedAmt / opAmt : 0, th: P("th.map_rate", 0.9) },
    { k: "客户命中率", v: custRows.length ? custHitN / custRows.length : 0, th: P("th.cust_hit", 0.7) },
    { k: "未匹配笔数", v: unmatched.length, th: null, raw: true }
  ];
  return { cmp: cmp, unmatched: unmatched, sidelined: sidelined, health: health,
    planN: plan.length, flowN: rows.length, period: period, mode: mode };
}

function renderP8() {
  var box = $("#ecoP8"); if (!box) return;
  var periods = {}; ECO.locked.forEach(function (i) { periods[i.period] = 1; });
  flowRows().forEach(function (r) { periods[r.m] = 1; });
  var ps = Object.keys(periods).sort();
  /* 下拉与计算必须同一口径：当前期间不在候选里就落到最新一期 */
  if (ps.length && ps.indexOf(ECO.sel.period) < 0) ECO.sel.period = ps[ps.length - 1];
  var ents = {}; ECO.locked.forEach(function (i) { ents[i.entity] = 1; });

  var pv = computePV();
  var html = '<div class="card" style="margin-bottom:14px"><div class="filters" style="margin:0">' +
    '<select class="eco-in" style="width:130px" data-ecosel="period">' +
    ps.map(function (m) { return '<option' + (m === ECO.sel.period ? " selected" : "") + '>' + m + '</option>'; }).join("") +
    '</select>' +
    '<select class="eco-in" style="width:220px" data-ecosel="entity"><option>全部</option>' +
    Object.keys(ents).map(function (e) { return '<option' + (e === ECO.sel.entity ? " selected" : "") + '>' + esc(e) + '</option>'; }).join("") +
    '</select>' +
    '<select class="eco-in" style="width:130px" data-ecosel="win">' +
    ["滚动3月", "同期", "账期"].map(function (w) { return '<option' + (w === ECO.sel.win ? " selected" : "") + '>' + w + '</option>'; }).join("") +
    '</select>' +
    '<button class="eco-btn gh" data-eco="pvexport">导出 CSV</button>' +
    '<span class="eco-k">已锁定计划 ' + pv.planN + ' 行 × 流水 ' + pv.flowN + ' 笔 · 窗口口径：' + esc(pv.mode) + '</span>' +
    '</div></div>';

  /* 体检 */
  html += '<div class="eco-grid eco-g3" style="margin-bottom:14px">';
  pv.health.forEach(function (h) {
    var bad = h.th != null && h.v < h.th;
    html += '<div class="card" style="margin:0"><h3>' + esc(h.k) + '</h3>' +
      '<div style="font-size:22px;font-weight:800;color:' + (h.raw ? "#374151" : bad ? "#A5352A" : "#1F6F4A") + '">' +
      (h.raw ? h.v : pct(h.v)) + '</div>' +
      (h.th != null ? '<div class="eco-k">阈值 ' + pct(h.th) + (bad ? " · 未达标" : " · 达标") + '</div>' : '<div class="eco-k">进下方未匹配清单</div>') +
      '</div>';
  });
  html += '</div>';

  /* 对比表 */
  html += '<div class="card" style="margin-bottom:14px"><h3>预实对比 <span>主体×期间×科目明细 · 差异 = 实际 − 计划</span></h3>' +
    '<div class="eco-scroll"><table class="eco-tbl"><thead><tr><th>科目明细</th><th>计划(CNY)</th><th>实际(CNY)</th>' +
    '<th>差异</th><th>差异率</th><th>判定</th></tr></thead><tbody>';
  if (!pv.cmp.length) html += '<tr><td colspan="6" class="eco-k">该期间没有已锁定计划或没有流水。先在模块六批准锁定，或导入流水。</td></tr>';
  pv.cmp.forEach(function (c) {
    html += '<tr><td><b>' + esc(c.subject) + '</b></td><td>' + fmt(c.plan) + '</td><td>' + fmt(c.act) + '</td>' +
      '<td style="color:' + (c.diff >= 0 ? "#1F6F4A" : "#A5352A") + '">' + fmt(c.diff) + '</td><td>' +
      (isFinite(c.rate) ? pct(c.rate) : "—") + '</td>' +
      '<td><span class="eco-pill" style="background:' + judgeColor(c.judge) + '18;color:' + judgeColor(c.judge) +
      ';border:1px solid ' + judgeColor(c.judge) + '55">' + esc(c.judge) + '</span></td></tr>';
  });
  html += '</tbody></table></div>' +
    '<div class="eco-note">判定阈值：符合 ≤ ±' + pct(P("th.var_ok", 0.05)) + ' 或绝对差 ≤ ' + fmt(P("th.var_ok_abs", 10000)) +
    '；关注 ≤ ' + pct(P("th.var_warn", 0.2)) + '；超过即预警进复盘。「计划有·流水无」查漏收/跨期，「流水有·计划无」是下期预算的输入。</div></div>';

  /* 未匹配 + 单列 */
  html += '<div class="eco-grid eco-g2">' +
    '<div class="card"><h3>未匹配清单 <span>挂起，不进合计；补规则或补计划的唯一来源</span></h3>' +
    '<div class="eco-scroll"><table class="eco-tbl"><thead><tr><th>月份</th><th>日期</th><th>方向</th><th>金额</th><th>对方户名</th><th>摘要</th></tr></thead><tbody>';
  if (!pv.unmatched.length) html += '<tr><td colspan="6" class="eco-k">全部命中，无挂起。</td></tr>';
  pv.unmatched.slice(0, 200).forEach(function (r) {
    html += '<tr><td>' + esc(r.m) + '</td><td class="eco-k">' + esc(r.d) + '</td><td>' + esc(r.dir) + '</td><td>' +
      fmt(r.amt) + '</td><td>' + esc(r.cp) + '</td><td class="eco-k">' + esc(r.memo) + '</td></tr>';
  });
  html += '</tbody></table></div></div>' +
    '<div class="card"><h3>剔除 / 单列 <span>内部划转、理财、借款保证金：不参与经营预实</span></h3>' +
    '<div class="eco-scroll"><table class="eco-tbl"><thead><tr><th>月份</th><th>方向</th><th>金额</th><th>对方户名</th><th>处理</th><th>去向</th></tr></thead><tbody>';
  if (!pv.sidelined.length) html += '<tr><td colspan="6" class="eco-k">本期无剔除/单列流水。</td></tr>';
  pv.sidelined.slice(0, 200).forEach(function (x) {
    html += '<tr><td>' + esc(x.r.m) + '</td><td>' + esc(x.r.dir) + '</td><td>' + fmt(x.r.amt) + '</td><td>' +
      esc(x.r.cp) + '</td><td>' + esc(x.b.handle) + '</td><td class="eco-k">' + esc(x.b.subject) + '</td></tr>';
  });
  html += '</tbody></table></div></div></div>';
  box.innerHTML = html;
  ECO._pv = pv;
}

function exportPV() {
  var pv = ECO._pv; if (!pv) return;
  var lines = [["科目明细", "计划(CNY)", "实际(CNY)", "差异", "差异率", "判定"]];
  pv.cmp.forEach(function (c) {
    lines.push([c.subject, Math.round(c.plan), Math.round(c.act), Math.round(c.diff),
      isFinite(c.rate) ? (c.rate * 100).toFixed(1) + "%" : "", c.judge]);
  });
  lines.push([]);
  lines.push(["未匹配清单"]);
  lines.push(["月份", "日期", "方向", "金额", "对方户名", "摘要"]);
  pv.unmatched.forEach(function (r) { lines.push([r.m, r.d, r.dir, r.amt, r.cp, r.memo]); });
  var csv = "\uFEFF" + lines.map(function (l) {
    return l.map(function (v) { return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"'; }).join(",");
  }).join("\n");
  var a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = "预实比对_" + (ECO.sel.period || "all") + "_" + ECO.sel.win + ".csv";
  a.click();
}

/* 桥接启停 */
document.addEventListener("click", function (e) {
  var b = e.target.closest('[data-eco="bgtoggle"]');
  if (!b) return;
  var id = b.dataset.id;
  var row = (ECO.data.bridge || []).filter(function (x) { return x.id === id; })[0];
  if (!row) return;
  row.status = row.status === "停用" ? "启用" : "停用";
  post("/api/bridge", { rows: [row] }).then(function (d) {
    toast("桥接 " + id + " → " + row.status);
    ECO.load();
  });
});

/* ---------- 启动 ---------- */
/* 脚本位于 body 末尾：#tabs / #p0 已存在，立即装配，保证早于服务端注入的 BOOT 脚本，
   这样 BOOT 里的 applyFlow / showTab 调用都会走本模块的包装，p8 能拿到打标后的流水 */

/* =====================================================================
   流水接入最后一步：资金结论按「实际导入的月份」显示
   v9 原版固定写 Q4 / 10-12 月，与只导入 7 月的事实不符
   ===================================================================== */
function ecoStep5() {
  var inM = (typeof DATA !== "undefined" && DATA.actIn) || {};
  var outM = (typeof DATA !== "undefined" && DATA.actOut) || {};
  var netM = (typeof DATA !== "undefined" && DATA.actNet) || {};
  var months = Object.keys(inM).concat(Object.keys(outM))
    .filter(function (v, i, a) { return a.indexOf(v) === i; }).sort();
  var pick = (ECO.conclMonth && months.indexOf(ECO.conclMonth) >= 0)
    ? ECO.conclMonth
    : (months.length ? months[months.length - 1] : null);
  var m = pick;
  var bal = null, balNote = "";
  if (m && typeof DATA !== "undefined" && DATA.balHist && typeof ACTM !== "undefined") {
    var i = ACTM.indexOf(m);
    if (i >= 0 && DATA.balHist[i] != null) bal = DATA.balHist[i];
  }
  if (bal == null && typeof DATA !== "undefined" && DATA.balLatest) {
    bal = DATA.balLatest.bal; balNote = DATA.balLatest.month.replace(".", "-") + " 月末";
  }
  var rowsN = (typeof DATA !== "undefined" && DATA.actMeta && DATA.actMeta.rows) || 0;
  var doneN = Object.keys((typeof S !== "undefined" && S.manual) || {}).filter(function (k) {
    return S.manual[k];
  }).length;
  var planN = (typeof DATA !== "undefined" && DATA.plan) ? DATA.plan.length : 0;
  var per = m ? m.replace(".", "-") : "—";

  return '<div class="eco-grid eco-g3">' +
    '<div class="card"><h3>数据就绪</h3>' +
      '<div class="eco-kv"><span>银行流水</span><b>' + rowsN + ' 笔</b></div>' +
      '<div class="eco-kv"><span>已人工确认</span><b>' + doneN + ' 笔</b></div>' +
      '<div class="eco-kv"><span>资金计划</span><b>' + planN + ' 行</b></div>' +
      '<div class="eco-kv"><span>流水覆盖月份</span><b>' + (months.length ? months.map(function (x) { return x.replace(".", "-"); }).join("、") : "—") + '</b></div>' +
    '</div>' +
    '<div class="card"><div class="eco-hd"><h3>资金结论 · ' + per + '</h3>' +
      '<select class="eco-in" style="width:118px" data-ecosel="concl">' +
      months.map(function (x) {
        return '<option value="' + x + '"' + (x === pick ? " selected" : "") + '>' +
          x.replace(".", "-") + '</option>';
      }).join("") + '</select></div>' +
      '<div class="eco-kv"><span>' + per + ' 流入</span><b class="red">' + fmt(inM[m] || 0) + '</b></div>' +
      '<div class="eco-kv"><span>' + per + ' 流出</span><b class="green">' + fmt(outM[m] || 0) + '</b></div>' +
      '<div class="eco-kv"><span>当月净额</span><b>' + fmt(netM[m] || 0) + '</b></div>' +
      '<div class="eco-kv"><span>月末余额</span><b>' + (bal == null ? "—" : fmt(bal)) +
        (balNote ? ' <span class="eco-k">(' + esc(balNote) + ')</span>' : "") + '</b></div>' +
    '</div>' +
    '<div class="card"><h3>下一步</h3>' +
      '<div class="eco-note" style="margin-top:0">确认完标签后进看板；资金计划在「六 · 资金计划」上传与审批。</div>' +
      '<div style="margin-top:12px;display:flex;flex-direction:column;gap:8px">' +
      '<button class="eco-btn" data-tab="p1">进入看板 →</button>' +
      '<button class="eco-btn gh" data-eco="planstep" data-s="1">去上传资金计划 →</button></div></div>' +
    '</div>';
}

if (typeof window.step5 === "function") { window.step5 = ecoStep5; }


/* =====================================================================
   唯一数据源原则：关掉 v9 内置的演示数据（306 笔流水 / 7-9 月汇总 / 演示计划）
   启动时清空，等 BOOT 用 /api/rows 的真实数据填回来
   ===================================================================== */

/* =====================================================================
   月份跟随数据：只导入 7 月 → 实际月份只有 7 月，本月=7 月、下月=8 月
   ===================================================================== */
function monthAdd2(ym, n) {
  var p = String(ym).split(".");
  var y = +p[0], mo = +p[1] + n;
  y += Math.floor((mo - 1) / 12); mo = ((mo - 1) % 12 + 12) % 12 + 1;
  return y + "." + (mo < 10 ? "0" : "") + mo;
}

function presetActMonths(rows) {
  try {
    var set = {};
    (rows || []).forEach(function (r) {
      var mo = String(r["月份"] || "").trim();
      if (!/\d{4}[.\-]\d{1,2}/.test(mo)) {
        var d = String(r["交易日期"] || r["日期"] || "");
        var mm = d.match(/(\d{4})[-/.年](\d{1,2})/);
        mo = mm ? (mm[1] + "." + (+mm[2] < 10 ? "0" : "") + (+mm[2])) : "";
      }
      mo = mo.replace("-", ".");
      if (/^\d{4}\.\d{2}$/.test(mo)) set[mo] = 1;
    });
    var ms = Object.keys(set).sort().slice(-3);
    if (!ms.length) return;
    ACTM.length = 0;
    ms.forEach(function (x) { ACTM.push(x); });
    Object.keys(ACTL).forEach(function (k) { delete ACTL[k]; });
    ms.forEach(function (x) { ACTL[x] = parseInt(x.split(".")[1], 10) + "月"; });
  } catch (e) { }
}

function presetForecastMonths() {
  try {
    var last = (typeof ACTM !== "undefined" && ACTM.length) ? ACTM[ACTM.length - 1] : null;
    if (!last) return;
    var ms = [monthAdd2(last, 1), monthAdd2(last, 2), monthAdd2(last, 3)];
    MONTHS.length = 0;
    ms.forEach(function (x) { MONTHS.push(x.replace(".", "-")); });
    Object.keys(MLAB).forEach(function (k) { delete MLAB[k]; });
    MONTHS.forEach(function (x) { MLAB[x] = parseInt(x.split("-")[1], 10) + "月"; });
  } catch (e) { }
}


/* 服务端算好的余额（/api/rows.balances）：每月各账户月末余额 + 合计 */
function applyServerBalances() {
  try {
    var b = window.__WH_BAL;
    if (!b || !b.byMonth) return false;
    var ms = Object.keys(b.byMonth).sort();
    if (!ms.length) return false;
    DATA.balByMonth = b.byMonth;
    var lastM = ms[ms.length - 1], o2 = b.byMonth[lastM] || {};
    var accts = Object.keys(o2).filter(function (k) { return k.charAt(0) !== "_"; });
    DATA.balLatest = {
      month: lastM.replace("-", "."), bal: o2._total, accts: accts, lag: [],
      detail: accts.map(function (a) { return { acct: a, bal: o2[a], month: lastM }; })
    };
    if (typeof S !== "undefined" && S) S.cash = o2._total;
    alignBalHist();          /* 月份以流水为准（ACTM），余额按月对齐填进去 */
    return true;
  } catch (e) { return false; }
}

/* 余额按月对齐到当前实际月份（ACTM 由流水决定，余额只负责填数） */
function alignBalHist() {
  try {
    var b = DATA.balByMonth;
    if (!b) return;
    DATA.balHist = (typeof ACTM !== "undefined" ? ACTM : []).map(function (x) {
      var o = b[String(x).replace(".", "-")];
      return (o && o._total != null) ? o._total : null;
    });
  } catch (e) { }
}

/* 人工确认/存档修改后：用当前内存（=数仓）数据重跑打标并重画分析页面 */
window.WH_RECOMPUTE = function () {
  try {
    if (typeof S === "undefined" || !S) return false;
    var rows = (S.raw && S.raw.flow && S.raw.flow.rows) || [];
    if (!rows.length) return false;
    applyFlow(rows, "all");
    try {
      if (typeof ECO !== "undefined" && ECO && ECO.tab) {
        var tb = ECO.tab;
        if (tb === "p1" || tb === "p2" || tb === "p3" || tb === "p4" || tb === "p5") {
          var fn = window["render" + tb.toUpperCase()];
          if (typeof fn === "function") fn();
        }
      }
    } catch (e1) { }
    return true;
  } catch (e) { return false; }
};

/* 导入完成后整体刷新：流水 / 人工标注 / 余额 / 月份 全部按数仓重算 */
window.WH_REFRESH = function () {
  var base = (typeof API !== "undefined" && API) ? API : "";
  return fetch(base + "/api/rows", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (d) {
    if (!d || !d.flow) return null;
    window.__WH_BAL = d.balances || { byMonth: {}, accounts: [] };
    window.__WH_MANUAL = d.manual || {};
    window.__WH_STATS = d.stats || {};
    try {
      if (typeof S === "undefined" || !S) return d;
      S.raw.flow = S.raw.flow || {};
      S.raw.flow.rows = d.flow;
      S.raw.flow.name = "本地数仓";
      S.raw.flow.meta = d.flowMeta || {};
      S.raw.flow.items = (d.items || []).filter(x=>x.kind==="flow" && d.flow.some(r=>r.__batch===x.batch));
      S.raw.fileLog = d.items || [];
      S.applied = true;
      /* 本批 = 最近一次导入的批次（stats.active_flow），人工审核默认只看它 */
      try {
        if (d.stats && d.stats.active_flow) S.batchId = d.stats.active_flow;
      } catch (e0) { }
      restoreManual(d.manual || {});
      try { if (typeof saveManual === "function") saveManual(); } catch (e0) { }
      try { presetActMonths(d.flow); presetForecastMonths(); } catch (e1) { }
      applyServerBalances();
      S.applyInfo=applyFlow(d.flow, "all");
      try { renderSteps(); renderStepBody(); } catch (e2) { }
      try {
        if (ECO.tab === "p1" && typeof renderP1 === "function") renderP1();
        if (ECO.tab === "p2" && typeof renderP2 === "function") renderP2();
        if (ECO.tab === "p3" && typeof renderP3 === "function") renderP3();
      } catch (e3) { }
      try {
        toast("已按数仓刷新：" + d.flow.length + " 笔流水 · 月份 " +
          (ACTM.length ? ACTM.join("、") : "—"));
      } catch (e4) { }
    } catch (e) { }
    return d;
  }).catch(function () { return null; });
};

function stripBuiltin() {
  try {
    if (typeof DATA === "undefined" || !DATA) return;
    DATA.actAll = [];
    DATA.actIn = {}; DATA.actOut = {}; DATA.actNet = {};
    DATA.actCat = {}; DATA.actCatCnt = {}; DATA.actInCat = {}; DATA.actOutCat = {};
    DATA.unmatched = []; DATA.actBig = [];
    DATA.actMeta = { rows: 0, uncls: 0, unclsAmt: 0 };
    DATA.actInTotal = 0; DATA.actOutTotal = 0;
    DATA.balHist = [null, null, null];
    DATA.balLatest = null;
    DATA.cov = { total: 0, 可映射: 0, 部分可映射: 0, 无对应: 0, tagged: 0 };
    DATA.plan = [];
  } catch (e) { }
  try {
    if (typeof S !== "undefined" && S) {
      S.cash = 0;
      S.applied = false;
      S.raw.flow = null;
      S.raw.fileLog = [];
    }
  } catch (e2) { }
}

/* 页面顶部提示条（同一个 id 只留一条） */
function noticeBar(paneId, html, color) {
  var pane = document.getElementById(paneId);
  if (!pane) return;
  var old = pane.querySelector(".eco-notice");
  if (old) old.parentNode.removeChild(old);
  if (!html) return;
  var d = document.createElement("div");
  d.className = "eco-notice";
  d.style.cssText = "background:#FFF7E0;border:1px solid #F0DFA8;border-left:4px solid #B7791F;" +
    "border-radius:8px;padding:8px 12px;margin:0 0 12px;font-size:12px;color:#7C4A03";
  d.innerHTML = html;
  var t = pane.querySelector(".pane-title");
  if (t && t.nextSibling) pane.insertBefore(d, t.nextSibling);
  else pane.insertBefore(d, pane.firstChild);
}

function syncDataNotice(id) {
  if (!ECO.on) {
    noticeBar(id, '未连接本地数仓：本页不展示任何演示数据。请用 <b>http://127.0.0.1:8765</b> 打开' +
      '（双击 数仓/启动数仓.bat），或点右上角「打开服务版看板」。');
    return;
  }
  if (id === "p5" && (!(typeof DATA !== "undefined" && DATA.plan && DATA.plan.length))) {
    noticeBar(id, '当前没有「审批通过」的资金计划，报表的数字为 0；请到「六 · 资金计划」上传并审批。');
    return;
  }
  noticeBar(id, "");
}


/* 收支明细：若没有资金计划而流水有数据，自动切到流水视图（否则默认空表） */
if (typeof window.renderP3 === "function") {
  var _ecoR3 = window.renderP3;
  window.renderP3 = function () {
    try {
      var hasPlan = (typeof DATA !== "undefined" && DATA.plan && DATA.plan.length);
      var hasAct = (typeof DATA !== "undefined" && DATA.actAll && DATA.actAll.length);
      if (!hasPlan && hasAct && (typeof S !== "undefined") && S.dSrc === "plan") {
        S.dSrc = "act";
        Array.prototype.slice.call(document.querySelectorAll("[data-src]")).forEach(function (b) {
          b.classList.toggle("on", b.dataset.src === "act");
        });
      }
    } catch (e) { }
    return _ecoR3.apply(this, arguments);
  };
}

function boot() {
  inject(); hook(); rewireLegacyTplButtons(); rewireTplLabels(); stripBuiltin();
  if (window.MutationObserver) { new MutationObserver(function () { rewireTplLabels(); syncLayout(); }).observe(document.body, { childList: true, subtree: true }); }
  ECO.load();
  /* file:// 打开时服务端不会注入 BOOT 脚本，这里自行处理 hash，保证双击也能直达模块 */
  var h0 = (location.hash || "").replace("#", "");
  if (/^p[0-8]$/.test(h0) && typeof window.showTab === "function") setTimeout(function () { window.showTab(h0); }, 30);
  /* 深链：?step=3 直接进入人工审核步（也方便排查该步的状态判断） */
  var q = String(location.search || "");
  var mPlan = /[?&]plan=([1-3])/.exec(q);
  if (mPlan) { ECO.planStep = +mPlan[1]; setTimeout(function () { if (ECO.on && ECO.tab === "p6") renderP6(); }, 900); }
  var mStep = /[?&]step=([1-5])/.exec(q);
  if (mStep) {
    setTimeout(function () {
      try {
        if (typeof S === "undefined" || typeof renderStepBody !== "function") return;
        S.step = +mStep[1];
        if (typeof renderSteps === "function") renderSteps();
        renderStepBody();
        syncLayout();
      } catch (e) { }
    }, 400);
  }
  /* 支持 ?step=3 直达某一步（便于验收与截图；不影响正常用法） */
  var mHi = (location.search.match(/[?&]hist=(\d+)/) || [])[1];
  if (mHi) setTimeout(function () {
    try { if (typeof showRowHist === "function") showRowHist(+mHi); } catch (e) { }
  }, 1800);
  var mArch = (location.search.match(/[?&]arch=(\d*)/) || [])[1];
  if (mArch !== undefined) setTimeout(function () {
    try {
      var chip = Array.prototype.slice.call(document.querySelectorAll("#steps [data-step]"))
        .filter(function (b) { return b.textContent.indexOf("导入流水") >= 0; })[0];
      if (chip) chip.click();
      setTimeout(function () {
        if (typeof openArch === "function") openArch();
        if (mArch) { try { S.archEdit = +mArch; renderArch(); } catch (e) { } }
      }, 500);
    } catch (e) { }
  }, 1500);
  if (/[?&]mode=1/.test(location.search)) setTimeout(function () {
    try { S.mMode = true; S.mSel = {}; renderStepBody(); } catch (e) { }
  }, 1400);
  var mStep = (location.search.match(/[?&]step=(\d)/) || [])[1];
  if (mStep) setTimeout(function () {
    try { S.step = +mStep; renderSteps(); renderStepBody(); } catch (e) { }
  }, 1200);

  setTimeout(function () {
    if (applyServerBalances() && typeof renderP1 === "function" && ECO.tab === "p1") renderP1();
    if (ECO.on) ECO.render(ECO.tab);
  }, 1500);
  setTimeout(function () { if (ECO.on) ECO.render(ECO.tab); }, 4000);
}
boot();
})();
