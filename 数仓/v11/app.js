/* ============================================================
   v11 核心层（读库版）
   数据一律来自本地数仓 /api/*；前端只做展示聚合，不做科目判断
   ============================================================ */
'use strict';

// 本地起服务时同源访问（任意端口）；直接双击文件时回落到 8770
const API = location.protocol.startsWith('http') ? '' : 'http://127.0.0.1:8770';

const S = {
  meta: null,
  snap: null,
  entity: '珀研',
  period: 'YTD',
  tab: 'g-over',
  flowStep: 1,
  planStep: 1,
  batch: { entity: '', period: '', file: '', sha: '', id: null, status: '' },  // 流水申请
  planBatch: { entity: '', period: '', rows: [], file: '', issues: [], id: null, applicant: '' },
  flowFilter: 'un',        // 人工识别筛选：un 未识别 / id 已识别 / all 全部
  restrKind: '',           // 受限资金卡片：按类别筛选（空=全部）
  pick: {},                // 人工识别页面暂存的科目选择
  sel: new Set(),
  editId: null,
  drillAct: '', drillCat: '', drillSrc: '', drillDir2: 'both', drillMonth: 'ALL', drillAmt: 0,
  vaGrain: 'month',
  err: ''
};

const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const yuan = n => (n == null || isNaN(n)) ? '—' : Math.round(n).toLocaleString('zh-CN');
const yuan2 = n => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function big(n) {
  if (n == null || isNaN(n)) return '—';
  const a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (n / 1e4).toFixed(a >= 1e6 ? 0 : 1) + '万';
  return Math.round(n).toLocaleString('zh-CN');
}
function pctTxt(v, d = 1) { return v == null || isNaN(v) ? '—' : (v > 0 ? '+' : '') + v.toFixed(d) + '%'; }
/* 环比：▲红=增，▼绿=减，上月为 0 显示 — */
function mom(cur, prev) {
  if (prev == null || !isFinite(prev) || prev === 0 || !isFinite(cur)) return { txt: '—', cls: '' };
  const r = (cur - prev) / Math.abs(prev) * 100;
  if (Math.abs(r) >= 1000) return { txt: '变动 ' + (cur - prev >= 0 ? '+' : '−') + big(Math.abs(cur - prev)) + '元（上期基数小）', cls: '', v: r };
  return { txt: (r >= 0 ? '▲ ' : '▼ ') + Math.abs(r).toFixed(1) + '%', cls: r >= 0 ? 'up' : 'down', v: r };
}
const momHtml = (cur, prev) => { const m = mom(cur, prev); return `<span class="s ${m.cls}">${m.txt}</span>`; };

function toast(msg, ms) {
  let el = $('#toast');
  if (!el) { el = document.createElement('div'); el.id = 'toast'; el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add('on');
  clearTimeout(el._t); el._t = setTimeout(() => el.classList.remove('on'), ms || 2800);
}

/* ---------- API ---------- */
async function api(path, opts) {
  let r;
  try {
    r = await fetch(API + path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  } catch (e) {
    // fetch 抛 TypeError：服务没启动 / 跨域预检被拦 / 网络中断
    throw new Error('本地数仓服务未响应（' + API + '）。请双击项目根目录「打开看板v11.bat」启动服务后重试');
  }
  const txt = await r.text();
  let j; try { j = JSON.parse(txt); } catch (e) { throw new Error('返回非 JSON：' + txt.slice(0, 120)); }
  if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
const post = (p, body) => api(p, { method: 'POST', body: JSON.stringify(body) });

async function loadMeta() { S.meta = await api('/api/meta'); }
async function loadSnap() {
  S.snap = await api('/api/snapshot?entity=' + encodeURIComponent(S.entity) + '&period=' + encodeURIComponent(S.period));
  return S.snap;
}
async function reload() { await loadSnap(); render(); }

/* ---------- 数据访问 ---------- */
const E = () => (S.meta ? S.meta.entities : []);
const ENT = s => E().find(e => e.short === s) || {};
const accounts = () => (S.meta ? S.meta.accounts : []);
const SUBMAP = () => (S.meta ? S.meta.subjectMap : []);
const smByCode = c => SUBMAP().find(x => x.code === c) || null;
const rows = () => (S.snap ? S.snap.rows : []);
const plans = () => (S.snap ? S.snap.plans : []);
const fxOf = ccy => (S.snap && S.snap.fx ? S.snap.fx[ccy + '/CNY'] : null);

const cny = r => (r.amt_cny == null ? 0 : r.amt_cny);
function agg(list) {
  let inflow = 0, outflow = 0;
  list.forEach(r => { const v = cny(r); if (r.direction === '收') inflow += v; else outflow += v; });
  return { inflow, outflow, net: inflow - outflow, cnt: list.length };
}
const grp = (list, fn) => list.reduce((m, r) => { const k = fn(r); (m[k] = m[k] || []).push(r); return m; }, {});

/* 期间：由后端给出 start/end，前端只做显示 */
const PERIOD_LABEL = p => ({
  CUR: '当月', L3M: '近 3 个月', YTD: '累计（年初至今）', ALL: '全部期间',
  Q1: '第一季度', Q2: '第二季度', Q3: '第三季度', Q4: '第四季度'
}[p] || (p || '').replace('-', ' 年 ') + ' 月');
function periodOptions(cur) {
  const months = (S.snap ? S.snap.allMonths : []).slice().reverse();
  const y = (S.snap?.months?.at(-1) || months[0] || '').slice(0, 4);
  return `<optgroup label="快捷口径">
    <option value="CUR"${cur === 'CUR' ? ' selected' : ''}>当月</option>
    <option value="L3M"${cur === 'L3M' ? ' selected' : ''}>近 3 个月</option>
    <option value="YTD"${cur === 'YTD' ? ' selected' : ''}>累计（年初至今）</option>
    <option value="ALL"${cur === 'ALL' ? ' selected' : ''}>全部期间</option>
  </optgroup>
  <optgroup label="按季度">${['Q1', 'Q2', 'Q3', 'Q4'].map(q => `<option value="${q}"${cur === q ? ' selected' : ''}>${y} 年 ${q}</option>`).join('')}</optgroup>
  <optgroup label="按月">${months.map(m => `<option value="${m}"${cur === m ? ' selected' : ''}>${m.replace('-', ' 年 ')} 月</option>`).join('')}</optgroup>`;
}
function entityOptions(cur, allLabel) {
  return (allLabel ? `<option value="ALL"${cur === 'ALL' ? ' selected' : ''}>${allLabel}</option>` : '')
    + E().map(e => `<option value="${esc(e.short)}"${cur === e.short ? ' selected' : ''}>${esc(e.short)}${e.pending ? '（待补登）' : ''}</option>`).join('');
}
const statusChip = st => st === 'confirmed' ? '<span class="chip matched">已确认</span>'
  : st === 'draft' ? '<span class="chip">草稿</span>' : '<span class="chip pending">待确认</span>';

/* 内部往来：标识一律取映射表下发字段（is_internal），前端不按 l1 文字判断 */
const isInt = r => !!(r && r.is_internal);
const L1_LIST = ['经营活动', '投资活动', '筹资活动', '内部往来'];
const L1_SHORT = { 经营活动: '经营', 投资活动: '投资', 筹资活动: '筹资', 内部往来: '内部往来' };
const l1Of = r => { const s = smByCode(r && r.code); return s ? s.l1 : ''; };
const identified = r => !!(r && (r.code || '').trim());
/* 二级科目去重：映射表里 31 个编码按 l1/l2 收成 20 项（其他支出这类细分编码只保留一条通用项） */
function SUB_OPTS(l1) {
  const seen = new Set(), out = [];
  SUBMAP().filter(s => s.l1 === l1).forEach(s => {
    if (seen.has(s.l2)) return;
    seen.add(s.l2); out.push(s);
  });
  return out;
}
/* 写库回执统一时间戳 HH:MM:SS */
const stamp = () => new Date().toLocaleTimeString('zh-CN', { hour12: false });
const opName = () => localStorage.getItem('v11.op') || '操作员';

/* ---------- 期间聚合（展示用） ---------- */
function monthsInSnap() {
  const [a, b] = [S.snap.start, S.snap.end];
  if (!a) return [];
  return (S.snap.allMonths || []).filter(m => m >= a && m <= b);
}
function rowsByMonth(m) { return rows().filter(r => r.txn_date.slice(0, 7) === m); }
/* 月末可用余额（按账户取月末最后一条余额，折人民币） */
function balancesAt(monthEnd) {
  const map = new Map();
  rows().concat().forEach(r => {
    const date = r.balance_date || r.txn_date;
    if (r.balance == null || date.slice(0, 7) > monthEnd.slice(0, 7)) return;
    const k = r.entity + '|' + r.bank + '|' + (r.account || '');
    const old = map.get(k);
    if (!old || (date + r.id) > ((old.balance_date || old.txn_date) + old.id)) map.set(k, r);
  });
  let total = 0, missing = [];
  const seen = new Set();
  map.forEach((r, k) => {
    const ccy = ENT(r.entity).ccy || 'CNY';
    if (ccy === 'CNY') total += r.balance;
    else { const f = fxOf(ccy); if (f) total += r.balance * f.rate; else missing.push(r.entity); }
    seen.add(r.entity);
  });
  accounts().forEach(a => { if (!seen.has(a.entity) && !E().find(e => e.short === a.entity && e.pending)) missing.push(a.entity); });
  return { total, missing: Array.from(new Set(missing)) };
}
function accountNumber(value) {
  const number = String(value ?? '').trim();
  return /^(?:[（(]?(?:待补登|待补录|待核对|未登记|未提供)[）)]?|[-—–]+)$/.test(number) ? '' : number;
}
const accountKey = r => JSON.stringify([r.entity, r.bank || '', accountNumber(r.account)]);
function newerBalance(row, previous) {
  if (!previous) return true;
  const date = row.balance_date || row.txn_date, oldDate = previous.balance_date || previous.txn_date;
  if (date !== oldDate) return date > oldDate;
  const time = row.txn_time || '', oldTime = previous.txn_time || '';
  return time === oldTime ? Number(row.id) > Number(previous.id) : time > oldTime;
}

function accountRows() {
  const last = new Map();
  // 受限资金自动识别结果（按 主体|银行|账号 汇总，单位人民币）
  const autoRestr = new Map();
  ((((S.snap || S.full) || {}).restricted || {}).accounts || []).forEach(a => {
    const k = accountKey(a);
    autoRestr.set(k, (autoRestr.get(k) || 0) + (a.amt || 0));
  });
  ((((S.full || S.snap) || {}).rows) || []).forEach(r => {
    if (r.balance == null) return;
    const balanceMonth = (r.balance_date || r.txn_date || '').slice(0, 7);
    if (S.snap && S.snap.end && balanceMonth > S.snap.end) return;
    const k = accountKey(r);
    if (newerBalance(r, last.get(k))) last.set(k, r);
  });
  const out = [];
  const master = new Map();
  accounts().forEach(a => {
    const key = accountKey(a), previous = master.get(key);
    // 待补登和空号是同一银行的未识别记录；补录仍指向原台账键。
    if (!previous || (!previous.restricted && a.restricted)) master.set(key, a);
  });
  const keys = new Set([...last.keys(), ...master.keys()]);
  keys.forEach(k => {
    const [e, b, ac] = JSON.parse(k);
    const l = last.get(k), meta = master.get(k) || {};
    const ccy = ENT(e).ccy || 'CNY', fx = ccy === 'CNY' ? 1 : (fxOf(ccy) ? fxOf(ccy).rate : null);
    const bal = l ? l.balance : null;
    const auto = autoRestr.get(k) || 0;                    // 自动识别受限（人民币）
    const autoLocal = fx ? auto / fx : auto;                // 折回账户币种
    const manual = meta.restricted || 0;                    // 人工补录（账户币种）
    // 可用：先扣自动识别；该账户没有自动识别结果时才扣人工补录，避免同一笔重复扣减
    const avail = bal == null ? null : bal - autoLocal - (auto > 0 ? 0 : manual);
    out.push({
      entity: e, bank: b, account: ac, ccy, fx, masterAccount: meta.account == null ? null : meta.account,
      balance: bal, date: l ? (l.balance_date || l.txn_date) : '',
      restricted: manual,
      autoRestricted: auto,
      available: avail,
      cnyBalance: (bal != null && fx) ? bal * fx : null,
      cnyAvail: (avail != null && fx) ? avail * fx : null,
      basis: l ? '逐笔余额' : '无余额'
    });
  });
  return out.sort((a, b) => E().findIndex(x => x.short === a.entity) - E().findIndex(x => x.short === b.entity));
}
function safeLine(e) {
  const v = (S.meta.safeLines || {})[e] || 0;
  if (v) return v;
  const r = rows().filter(x => x.entity === e && x.direction === '支');
  const ms = new Set(r.map(x => x.txn_date.slice(0, 7)));
  const avg = ms.size ? r.reduce((s, x) => s + cny(x), 0) / ms.size : 0;
  return Math.round(avg * 0.5 / 10000) * 10000;
}
const isSafeSet = e => !!(S.meta.safeLines || {})[e];

/* ---------- 文件解析（只做字段识别，不做科目判断） ---------- */
const FLOW_ALIAS = {
  date: ['交易日期', '交易日', '记账日期', '入账日期', '业务日期', '日期', '交易时间'],
  time: ['交易时间', '时间'],
  dir: ['收支方向', '借贷标志', '借贷', '收付标志', '方向', '交易方向'],
  amt: ['交易金额', '发生额', '借贷金额', '金额'],
  amtIn: ['贷方金额', '收入金额', '存入金额', '贷方发生额'],
  amtOut: ['借方金额', '支出金额', '支取金额', '借方发生额'],
  bal: ['余额', '账户余额', '交易后余额'],
  balDate: ['余额日期'],
  bank: ['银行'],
  cp: ['对方户名', '收(付)方名称', '收付方名称', '对方名称', '对方单位', '对方单位名称', '对手方名称',
       '对方账户名称', '交易对手', '付款方名称', '收款方名称', '对方户名全称'],
  cpa: ['对方账号', '收(付)方账号', '收付方账号', '对方帐号', '对方账户', '收(付)方账户', '收付方账户', '对方卡号'],
  memo: ['摘要', '用途', '交易摘要', '备注', '附言', '交易类型'],
  acct: ['本方账号', '账号', '银行账号', '账户']
};
function decodeText(buf) {
  let s = new TextDecoder('utf-8').decode(buf);
  if ((s.match(/\uFFFD/g) || []).length > 2) { try { s = new TextDecoder('gbk').decode(buf); } catch (e) { } }
  return s.replace(/^\uFEFF/, '');
}
function parseCsv(text) {
  const out = []; let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; } else cell += ch; }
    else if (ch === '"') q = true;
    else if (ch === ',' || ch === '\t') { row.push(cell); cell = ''; }
    else if (ch === '\n') { row.push(cell); out.push(row); row = []; cell = ''; }
    else if (ch !== '\r') cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); out.push(row); }
  return out;
}
async function unzip(buf) {
  const dv = new DataView(buf), u8 = new Uint8Array(buf);
  let eocd = -1;
  for (let i = u8.length - 22; i >= Math.max(0, u8.length - 66000); i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error('不是有效的 xlsx（ZIP）文件');
  const count = dv.getUint16(eocd + 10, true), cd = dv.getUint32(eocd + 16, true);
  const files = new Map(); let p = cd;
  for (let i = 0; i < count; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) break;
    const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true);
    const nlen = dv.getUint16(p + 28, true), elen = dv.getUint16(p + 30, true), clen = dv.getUint16(p + 32, true);
    const lho = dv.getUint32(p + 42, true);
    const name = new TextDecoder('utf-8').decode(u8.subarray(p + 46, p + 46 + nlen));
    const lnlen = dv.getUint16(lho + 26, true), lelen = dv.getUint16(lho + 28, true);
    const raw = u8.subarray(lho + 30 + lnlen + lelen, lho + 30 + lnlen + lelen + csize);
    files.set(name, { method, raw });
    p += 46 + nlen + elen + clen;
  }
  for (const [k, v] of files) {
    if (v.method === 0) continue;
    const st = new Blob([v.raw]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
    v.data = new Uint8Array(await new Response(st).arrayBuffer());
  }
  const out = new Map();
  files.forEach((v, k) => out.set(k, v.method === 0 ? v.raw : v.data));
  return out;
}
const colIdx = ref => {
  const m = String(ref).match(/^([A-Z]+)/); if (!m) return 0;
  let n = 0; for (const ch of m[1]) n = n * 26 + (ch.charCodeAt(0) - 64); return n - 1;
};
function sheetRows(xml, shared) {
  const out = []; const re = /<row[^>]*>([\s\S]*?)<\/row>/g; let m;
  while ((m = re.exec(xml))) {
    const cells = []; const cre = /<c\s+([^>]*?)(\/>|>([\s\S]*?)<\/c>)/g; let cm;
    while ((cm = cre.exec(m[1]))) {
      const attrs = cm[1], inner = cm[3] || '';
      const ref = (attrs.match(/r="([A-Z]+\d+)"/) || [])[1] || '';
      const t = (attrs.match(/t="([^"]+)"/) || [])[1] || '';
      let v = '';
      if (t === 'inlineStr') { const is = inner.match(/<t[^>]*>([\s\S]*?)<\/t>/); v = is ? is[1] : ''; }
      else { const vm = inner.match(/<v>([\s\S]*?)<\/v>/); v = vm ? vm[1] : ''; if (t === 's') v = shared[Number(v)] || ''; }
      v = String(v).replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"');
      const i = ref ? colIdx(ref) : cells.length;
      while (cells.length < i) cells.push('');
      cells[i] = v;
    }
    out.push(cells);
  }
  return out;
}
async function readXlsx(buf) {
  const files = await unzip(buf);
  const dec = u8 => new TextDecoder('utf-8').decode(u8);
  const shared = [];
  if (files.get('xl/sharedStrings.xml')) {
    const re = /<si>([\s\S]*?)<\/si>/g; let m; const xml = dec(files.get('xl/sharedStrings.xml'));
    while ((m = re.exec(xml))) shared.push(Array.from(m[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)).map(x => x[1]).join('').replace(/&amp;/g, '&').replace(/&lt;/g, '<'));
  }
  let target = 'xl/worksheets/sheet1.xml', name = 'Sheet1';
  if (files.get('xl/workbook.xml')) {
    const wb = dec(files.get('xl/workbook.xml'));
    const sm = wb.match(/<sheet[^>]*name="([^"]+)"[^>]*r:id="rId(\d+)"/);
    if (sm) {
      name = sm[1];
      const rels = files.get('xl/_rels/workbook.xml.rels');
      if (rels) { const rm = dec(rels).match(new RegExp('Id="rId' + sm[2] + '"[^>]*Target="([^"]+)"')); if (rm) target = rm[1].startsWith('/') ? rm[1].slice(1) : 'xl/' + rm[1].replace(/^\.\//, ''); }
    }
  }
  const f = files.get(target) || files.get('xl/worksheets/sheet1.xml');
  if (!f) throw new Error('未找到工作表');
  return { name, rows: sheetRows(dec(f), shared) };
}
async function readFileRows(file) {
  const buf = await file.arrayBuffer();
  const u8 = new Uint8Array(buf.slice(0, 4));
  const isZip = u8[0] === 0x50 && u8[1] === 0x4B;              // xlsx / xlsm
  const isOle = u8[0] === 0xD0 && u8[1] === 0xCF;              // .xls (BIFF8)
  if (/\.pdf$/i.test(file.name) || (u8[0] === 0x25 && u8[1] === 0x50 && u8[2] === 0x44 && u8[3] === 0x46)) {
    const r = await api('/api/parse-pdf', { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf });
    if (r.error) throw new Error(r.error);
    r.rows.pdfInfo = { owner: r.owner, currency: r.currency, warnings: r.warnings || [] };
    return r.rows;
  }
  if (isZip) return (await readXlsx(buf)).rows;
  const head = new TextDecoder('utf-8', { fatal: false }).decode(new Uint8Array(buf.slice(0, 400))).toLowerCase();
  if (isOle || /\.xls$/i.test(file.name) || head.includes('<html') || head.includes('<table')) {
    // 旧版 .xls / 假 xls（HTML 表格）：交给本地服务端用 xlrd 解析
    // 文件名走查询串：不产生自定义请求头，避免 CORS 预检被拦（Failed to fetch）
    const r = await api('/api/parse-file?name=' + encodeURIComponent(file.name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: buf
    });
    if (r.error) throw new Error(r.error);
    return r.rows;
  }
  return parseCsv(decodeText(buf));
}
function normDate(v) {
  if (v == null || v === '') return '';
  if (typeof v === 'number' || /^\d{5}$/.test(String(v))) {
    const n = Number(v);
    if (n > 20000 && n < 60000) return new Date(Date.UTC(1899, 11, 30) + n * 86400000).toISOString().slice(0, 10);
  }
  let s = String(v).trim().replace(/[年./]/g, '-').replace(/日/g, '').replace(/-+/g, '-');
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (m) return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
  m = String(v).match(/^(\d{4})(\d{2})(\d{2})$/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : '';
}
const num = v => {
  if (v == null) return NaN;
  if (typeof v === 'number') return v;
  let s = String(v).replace(/[,，\s￥¥$元]/g, '');
  if (!s) return NaN;
  let neg = false;
  if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
  const n = parseFloat(s);
  return isNaN(n) ? NaN : (neg ? -n : n);
};
function findHeader(rowsArr, minHit) {
  const keys = Object.values(FLOW_ALIAS);
  for (let i = 0; i < Math.min(rowsArr.length, 30); i++) {
    const txt = (rowsArr[i] || []).map(c => String(c).replace(/\s|　/g, ''));
    let hit = 0;
    keys.forEach(list => { if (list.some(a => txt.includes(a))) hit++; });
    if (hit >= (minHit || 3)) return i;
  }
  return -1;
}
/* 表头里这些列不是「本方账号」：招行/工行对账单都带 收(付)方账号、对方账号 */
const HEAD_NEG = { acct: ['对方', '收付方', '母子', '对手方', '付款方', '收款方'] };
const noBrk = c => String(c).replace(/\s|　/g, '').replace(/[()（）[\]【】]/g, '');
function mapHeader(h) {
  const t = h.map(c => String(c).replace(/\s|　/g, '')), u = h.map(noBrk), m = {};
  Object.entries(FLOW_ALIAS).forEach(([k, list]) => {
    const neg = HEAD_NEG[k] || [];
    const fine = i => i >= 0 && !neg.some(n => u[i].includes(n));
    const pick = (arr, aliases, exact) => {
      for (const a of aliases) {
        const i = arr.findIndex(x => exact ? x === a : x.includes(a));
        if (fine(i)) return i;
      }
      return -1;
    };
    // 先按原样、再按去括号（收(付)方名称 → 收付方名称）比一遍，最后才做包含匹配
    let i = pick(t, list, true);
    if (i < 0) i = pick(u, list.map(noBrk), true);
    if (i < 0) i = pick(t, list, false);
    if (i < 0) i = pick(u, list.map(noBrk), false);
    m[k] = i;
  });
  return m;
}
const cellAt = (r, i) => (i == null || i < 0 || r[i] == null) ? '' : r[i];

/* 解析成"原始行"，科目交给服务端规则 */
function parseFlowFile(rowsArr, fileName, entity) {
  const hi = findHeader(rowsArr, 3);
  if (hi < 0) return { error: '表头识别失败：未找到含「交易日期 / 金额 / 摘要」等列名的表头行' };
  const m = mapHeader(rowsArr[hi]);
  const missing = [];
  if (m.date < 0) missing.push('交易日期');
  if (m.amt < 0 && (m.amtIn < 0 || m.amtOut < 0)) missing.push('金额（或借方/贷方金额）');
  if (m.memo < 0) missing.push('摘要/备注');
  if (missing.length) return { error: '缺列：' + missing.join('、') + '（请按模板字段映射后重传）' };
  const out = [], dupInFile = [];
  const seen = new Map();
  for (let i = hi + 1; i < rowsArr.length; i++) {
    const r = rowsArr[i]; if (!r || !r.length) continue;
    const d = normDate(cellAt(r, m.date));
    let amt = num(cellAt(r, m.amt)), dirRaw = String(cellAt(r, m.dir)).trim();
    if (m.amtIn >= 0 || m.amtOut >= 0) {
      const ain = num(cellAt(r, m.amtIn)), aout = num(cellAt(r, m.amtOut));
      if (!isNaN(ain) && ain) { amt = ain; dirRaw = dirRaw || '收'; }
      else if (!isNaN(aout) && aout) { amt = aout; dirRaw = dirRaw || '支'; }
    }
    if (!d || isNaN(amt) || !amt) continue;
    let dir = '';
    if (/收|贷|流入|^C$|CR/i.test(dirRaw) && !/支|借/.test(dirRaw)) dir = '收';
    else if (/支|借|流出|^D$|DR/i.test(dirRaw)) dir = '支';
    if (!dir) dir = amt < 0 ? '支' : '收';
    const cp = String(cellAt(r, m.cp)).trim(), memo = String(cellAt(r, m.memo)).trim();
    const bal = m.bal >= 0 ? num(cellAt(r, m.bal)) : NaN;
    const key = [d, dir, Math.abs(amt), memo.slice(0, 40), cp.slice(0, 40)].join('|');
    if (seen.has(key)) dupInFile.push({ row: i + 1, first: seen.get(key), date: d, amount: Math.abs(amt), cp, memo });
    else seen.set(key, i + 1);
    out.push({
      date: d, time: (/\d{1,2}:\d{2}(?::\d{2})?/.exec(String(cellAt(r, m.time))) || [''])[0], dir,
      amount: Math.abs(amt), balance: isNaN(bal) ? null : bal,
      balance_date: normDate(cellAt(r, m.balDate)) || '',
      cp, cpa: String(cellAt(r, m.cpa)), memo,
      entity, bank: String(cellAt(r, m.bank)), account: String(cellAt(r, m.acct)), src: fileName, src_row: i + 1
    });
  }
  return { rows: out, headerIdx: hi, dupInFile };
}

/* ---------- 导出 ---------- */
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = u8 => { let c = 0xFFFFFFFF; for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
function zipStore(files) {
  const enc = new TextEncoder(), chunks = [], cd = []; let off = 0;
  files.forEach(f => {
    const nm = enc.encode(f.name), data = f.data, crc = crc32(data);
    const lh = new Uint8Array(30 + nm.length), dv = new DataView(lh.buffer);
    dv.setUint32(0, 0x04034b50, true); dv.setUint16(4, 20, true); dv.setUint16(6, 0x0800, true);
    dv.setUint32(14, crc, true); dv.setUint32(18, data.length, true); dv.setUint32(22, data.length, true);
    dv.setUint16(26, nm.length, true); lh.set(nm, 30);
    chunks.push(lh, data);
    const ch = new Uint8Array(46 + nm.length), cv = new DataView(ch.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x0800, true);
    cv.setUint32(16, crc, true); cv.setUint32(20, data.length, true); cv.setUint32(24, data.length, true);
    cv.setUint16(28, nm.length, true); cv.setUint32(42, off, true); ch.set(nm, 46);
    cd.push(ch); off += lh.length + data.length;
  });
  const cdSize = cd.reduce((s, c) => s + c.length, 0);
  const end = new Uint8Array(22), ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
  ev.setUint32(12, cdSize, true); ev.setUint32(16, off, true);
  return new Blob([...chunks, ...cd, end], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
}
const xesc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const colName = i => { let s = ''; i++; while (i > 0) { const r = (i - 1) % 26; s = String.fromCharCode(65 + r) + s; i = Math.floor((i - 1) / 26); } return s; };
const sheetXml = rowsArr => `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rowsArr.map((r, ri) => `<row r="${ri + 1}">` + r.map((v, ci) => {
  if (v == null || v === '') return '';
  const ref = colName(ci) + (ri + 1);
  const n = typeof v === 'number' || (/^-?\d+(\.\d+)?$/.test(String(v)) && !/^0\d/.test(String(v)));
  return n ? `<c r="${ref}"><v>${v}</v></c>` : `<c r="${ref}" t="inlineStr"><is><t xml:space="preserve">${xesc(v)}</t></is></c>`;
}).join('') + '</row>').join('')}</sheetData></worksheet>`;
function buildXlsx(sheets) {
  const enc = new TextEncoder(), files = [];
  files.push({ name: '[Content_Types].xml', data: enc.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((s, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join('')}</Types>`) });
  files.push({ name: '_rels/.rels', data: enc.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`) });
  files.push({ name: 'xl/workbook.xml', data: enc.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s, i) => `<sheet name="${xesc(s.name || ('Sheet' + (i + 1)))}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join('')}</sheets></workbook>`) });
  files.push({ name: 'xl/_rels/workbook.xml.rels', data: enc.encode(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((s, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join('')}</Relationships>`) });
  sheets.forEach((s, i) => files.push({ name: `xl/worksheets/sheet${i + 1}.xml`, data: enc.encode(sheetXml(s.rows)) }));
  return zipStore(files);
}
function download(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1200);
}
function downloadCsv(rowsArr, name) {
  const csv = rowsArr.map(r => r.map(v => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }).join(',')).join('\r\n');
  download(new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' }), name);
}
