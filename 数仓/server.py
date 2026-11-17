"""本地数仓服务：托管看板 + 提供数据接口

启动
  python server.py
然后浏览器打开  http://127.0.0.1:8765

原有接口（流水 / 计划 / 人工标签 / 规则）
  GET  /api/health          健康检查
  GET  /api/rows            看板数据（原始流水行 + 计划 + 人工标签）
  POST /api/import          {kind:"flow|plan", fileName, rows:[...]}
  POST /api/manual          {manual:{uniqKey:{l1,l2,cf}}}
  POST /api/clear           {kind:"flow|plan|manual"}
  GET  /api/export          下载数仓 Excel

生态多主体资金计划接口（模板下载 / 上传审批 / 参数 / 桥接 / 预实）
  GET  /api/eco                 一次拿全：模板注册、科目字典、主体台账、汇率、参数、账期、桥接、提报清单
  GET  /api/template?id=TPL-05  下载《生态多主体月度资金预算与资金计划表模板》（计数 +1）
  POST /api/upload-plan         上传资金计划表（body=文件原始字节，头 X-File-Name / X-Period / X-Actor / X-Submit）
                                → 解析「预算明细」+ 跑 G1/G2/G3 三道闸门 + 建提报单
  GET  /api/submissions         提报清单（含状态）
  GET  /api/submission?id=1     单条明细 + 审批留痕
  POST /api/submission-action   {id, action:"提交|复核通过|批准|退回|发起变更|核销", actor, role, reason}
  GET  /api/locked?period=&entity=  已锁定版本的计划明细（预实比对只认这一层）
  POST /api/params              {params:{id:value}} 或 {params:[{id,grp,label,value,...}], actor}
  POST /api/terms               {rows:[{customer,klass,term_days,note}], actor}
  POST /api/bridge              {rows:[{id,direction,l1,l2,subject,cashflow,handle,priority}]}
  POST /api/match-link          {links:[{item_id,period,entity,subject,flow_key,amount,basis,confirmer}]}

看板本身不含数据：启动时从 /api/rows 与 /api/eco 拉数，计算仍在前端那套规则引擎里，
所以数仓与看板的口径永远一致。
"""
import os
import sys
import json
import mimetypes
import shutil
import tempfile
import threading
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote, quote, unquote_to_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db
import sqlite3
import db_eco

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)                     # 项目根目录
UPLOAD_DIR = os.path.join(BASE, "uploads")
PORT = int(os.environ.get("WH_PORT", "8765"))
# 重置、备份与正常读写互斥，防止半清空或旧请求在重置期间落库。
DATA_LOCK = threading.RLock()


def _pick_html():
    env = os.environ.get("WH_HTML")
    if env:
        return env
    for name in ("生态资金计划系统v10.html", "珀研Q4资金预测看板v9.html"):
        if os.path.exists(os.path.join(ROOT, name)):
            return name
    return "珀研Q4资金预测看板v9.html"


HTML_NAME = _pick_html()

# 提报科目 → 看板标准计划科目（与前端 PLAN_MAP 保持一致，锁定时写 dwd_plan 用）
PLAN_SUBJECT_MAP = {
    "销售回款(商品/渠道分销)": "KA收入", "销售回款（商品/渠道分销）": "KA收入",
    "服务与咨询费收入": "KA收入", "内容分成及广告收入": "KA收入",
    "平台返点与佣金收入": "KA收入", "退税款及政府补贴": "KA收入", "其他经营流入": "KA收入",
    "人力薪酬及社保公积金": "部门费用-工资",
    "租金物业及水电": "房租水电",
    "采购货款与生产成本": "采购货款",
    "研发投入(材料/委外)": "部门费用-其他", "研发投入（材料/委外）": "部门费用-其他",
    "市场推广及渠道佣金": "部门费用-市场部非工资",
    "物流仓储及履约": "部门费用-其他",
    "差旅及业务招待": "部门费用-其他",
    "办公及行政开支": "部门费用-其他",
    "其他经营流出": "部门费用-其他",
    "税费及附加": "税费-增值税及附加", "税金及附加": "税费-增值税及附加",
    "支付利息": "税费-增值税及附加",
}


def map_plan_subject(s):
    s = str(s or "").strip()
    if s in PLAN_SUBJECT_MAP:
        return PLAN_SUBJECT_MAP[s]
    if "税" in s:
        return "税费-增值税及附加"
    if any(k in s for k in ("薪", "工资", "社保", "人力")):
        return "部门费用-工资"
    if any(k in s for k in ("租", "物业", "水电")):
        return "房租水电"
    if any(k in s for k in ("采购", "货款", "生产")):
        return "采购货款"
    if any(k in s for k in ("推广", "营销", "渠道佣金", "市场")):
        return "部门费用-市场部非工资"
    if any(k in s for k in ("收入", "回款", "退款", "补贴", "返点")):
        return "KA收入"
    return s


def sync_locked_plan():
    """把「已锁定」的提报单写进 dwd_plan —— 计划审批通过后，看板才有计划侧可比。"""
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    subs = [dict(r) for r in conn.execute(
        "SELECT * FROM plan_submission WHERE status IN ('已锁定','已核销') ORDER BY id")]
    db.clear("plan")
    total = 0
    for s in subs:
        period = str(s.get("period") or "")
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM plan_item WHERE sub_id=? ORDER BY seq", (s["id"],))]
        rows = [{
            "月份": period,
            "收支方向": it.get("direction"),
            "现金流量分类": it.get("cat"),
            "科目": map_plan_subject(it.get("subject")),
            "来源表": "计划提报#%s" % s["id"],
            "金额": it.get("amt_cny") or it.get("amt_orig") or 0,
            "含税标记": "",
            "备注": it.get("note") or "",
        } for it in items]
        if rows:
            total += db.save_plan(rows, "提报单#%s" % s["id"])["rows"]
    conn.close()
    return total


# 数仓模式启动脚本：页面加载完从数仓拉数
BOOT = r"""
<script>
(async function(){
  var badge=document.getElementById("whBadge");
  try {
    var health=await fetch("/api/health",{cache:"no-store"});
    if(!health.ok) throw new Error("数仓未连接");
    WH.on=true;
    var d=await window.WH_REFRESH();
    if(!d) throw new Error("数仓取数失败");
    var rules=await fetch("/api/rules",{cache:"no-store"}).then(r=>r.json());
    await WH.restoreRules(rules.payload ? JSON.parse(rules.payload) : null);
    window.WH_RECOMPUTE();
    await flushImports();
    if(badge){badge.style.display="inline-flex";badge.title="数据来自本地数仓："+d.flow.length+" 笔流水 / "+d.plan.length+" 行计划";}
    goHash();
  }catch(e){WH.on=false;if(badge){badge.style.display="inline-flex";badge.textContent="数仓未连接";} }
  function goHash(){
    var h=(location.hash||"").replace("#","");
    if(/^p[0-8]$/.test(h) && typeof showTab==="function")showTab(h);
  }
  window.addEventListener("hashchange",goHash);
})();
</script>
"""



class Handler(BaseHTTPRequestHandler):
    server_version = "PryanWH/2.0"
    protocol_version = "HTTP/1.1"

    # ---------- 帮助方法 ----------
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _hdr(self, name):
        """HTTP 头里的中文：浏览器用 encodeURIComponent(UTF-8)，个别脚本/curl 会发 GBK 字节，
        这里 percent 解码后依次按 utf-8 / gbk 解，避免文件名变乱码。"""
        v = self.headers.get(name) or ""
        if not v:
            return ""
        try:
            raw = v.encode("latin-1", "ignore")      # 还原头部的原始字节
        except Exception:
            raw = v.encode("utf-8", "ignore")
        if b"%" in raw:                              # percent 编码的再解一层
            try:
                raw = unquote_to_bytes(raw)
            except Exception:
                pass
        for enc in ("utf-8", "gbk"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        return v

    def _q(self):
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type,X-File-Name,X-Period,X-Actor,X-Submit,X-Reset-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        with DATA_LOCK:
            return self.get_routes()

    def get_routes(self):
        path = urlparse(self.path).path
        q = self._q()

        if path in ("/", "/index.html"):
            return self.serve_html()
        if path == "/api/health":
            return self._json({"ok": True, "db": db.DB_PATH, "html": HTML_NAME,
                               "stats": db.stats(), "eco": db_eco.eco_stats()})

        # ---- 原有 ----
        if path == "/api/rows":
            try:
                tagged = db.load_tagged()
            except Exception:
                tagged = []
            return self._json({
                "flow": db.load_flow(True),
                "tagged": tagged,
                "resetToken": db.get_meta("reset_token", ""),
                "plan": db.load_plan(),
                "manual": db.load_manual(),
                "balances": db.monthly_balances(),
                "items": [{"name": b["file_name"], "ok": True, "batch": b["batch"],
                           "kind": "plan" if b.get("kind") == "plan" else "flow",
                           "rows": b["rows"], "fields": 0, "sheet": "数仓导入", "enc": ""}
                          for b in db.stats()["batches"] if b["file_name"] and (b.get("rows") or 0) > 0],
                "stats": db.stats(),
            })
        if path == "/api/requests":
            return self._json({"rows": db.list_requests(q.get("kind") or None)})
        if path == "/api/flow-edits":
            fid = q.get("id") or q.get("flow_id")
            return self._json({"rows": db.flow_edits(int(fid) if fid else None)})
        if path == "/api/rules":
            r = db.load_rule()
            return self._json(r or {"payload": None, "updated_at": None})
        if path == "/db":
            return self.serve_db()
        if path == "/api/export":
            return self.serve_export()

        # ---- 生态多主体 ----
        if path == "/api/eco":
            return self._json(self.eco_bundle())
        if path == "/api/template":
            return self.serve_template(q.get("id") or "TPL-05")
        if path == "/api/uploaded":
            return self.serve_uploaded(q.get("name") or "", q.get("inline") == "1")
        if path == "/api/submissions":
            return self._json({"ok": True, "submissions": db_eco.submission_list()})
        if path == "/api/submission":
            d = db_eco.submission_get(int(q.get("id") or 0))
            return self._json({"ok": bool(d), "submission": d})
        if path == "/api/locked":
            return self._json({"ok": True,
                               "items": db_eco.locked_items(q.get("period"), q.get("entity"))})
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        with DATA_LOCK:
            token = db.get_meta('reset_token', '')
            if token and self.headers.get('X-Reset-Token', '') != token:
                self.close_connection = True
                return self._json({'ok': False, 'error': '测试数据已重置，请刷新页面后重新操作'}, 409)
            return self.post_routes()

    def post_routes(self):
        path = urlparse(self.path).path

        if path == '/api/reset':
            origin = self.headers.get('Origin')
            site = self.headers.get('Sec-Fetch-Site')
            if (origin and origin != 'http://' + self.headers.get('Host', '')) or site not in (None, 'same-origin', 'none'):
                self.close_connection = True
                return self._json({'ok': False, 'error': '请从当前项目页面发起重置'}, 403)
            body = self._body()
            if body.get('confirm') != 'RESET_ALL':
                return self._json({'ok': False, 'error': '需要明确确认重置全部测试数据'}, 400)
            from reset_data import reset_all
            try:
                return self._json(reset_all(UPLOAD_DIR, body.get('browserState') or {}))
            except Exception as exc:
                return self._json({'ok': False, 'error': '重置失败，原数据已保留：' + str(exc)}, 500)

        # ---- 原有 ----
        if path == "/api/import":
            body = self._body()
            kind = body.get("kind", "flow")
            name = body.get("fileName") or "上传文件"
            rows = body.get("rows") or []
            if not rows:
                return self._json({"ok": False, "error": "没有数据行"}, 400)
            res = db.save_plan(rows, name) if kind == "plan" else db.save_flow(rows, name)
            try:
                db.add_request("plan" if kind == "plan" else "flow", name, res.get("batch", ""),
                               res.get("rows", 0) + res.get("updated", 0), "已完成",
                               body.get("actor") or "", "导入" + ("（覆盖同文件同序号的行）" if res.get("updated") else ""))
            except Exception:
                pass
            return self._json({"ok": True, **res})
        if path == "/api/tagged":
            body = self._body()
            rows = body.get("rows") or []
            if not rows:
                return self._json({"ok": False, "error": "没有数据行"}, 400)
            res = db.save_tagged(rows)
            return self._json({"ok": True, **res})
        if path == "/api/requests":
            body = self._body()
            act = body.get("action") or "add"
            if act == "add":
                rid = db.add_request(body.get("kind") or "edit", body.get("title") or "修改存档流水",
                                     body.get("batch") or "", body.get("rows") or 0,
                                     body.get("status") or "已完成", body.get("actor") or "",
                                     body.get("note") or "")
                return self._json({"ok": True, "id": rid})
            if act == "status":
                return self._json(db.set_request_status(int(body.get("id") or 0),
                                                        body.get("status") or "已确认",
                                                        body.get("actor") or ""))
            return self._json({"ok": False, "error": "未知动作"}, 400)
        if path == "/api/flow-edit":
            body = self._body()
            fid = body.get("id") or body.get("flow_id")
            if not fid:
                return self._json({"ok": False, "error": "缺少流水行 id（请先点「从数仓重新取数」）"}, 400)
            res = db.edit_flow(int(fid), body.get("patch") or {},
                               actor=body.get("actor") or "", reason=body.get("reason") or "",
                               label=body.get("label") or None)
            return self._json(res, 200 if res.get("ok") else 400)
        if path == "/api/manual":
            body = self._body()
            try:
                db.save_manual(body.get("manual") or {},
                               merge=bool(body.get("merge", True)),
                               remove=body.get("remove") or None)
            except (ValueError, TypeError) as exc:
                return self._json({"ok": False, "error": str(exc)}, 400)
            return self._json({"ok": True, "count": len(db.load_manual()),
                               "saved": len(body.get("manual") or {})})
        if path == "/api/rules":
            body = self._body()
            db.save_rule(body.get("payload") or "[]")
            return self._json({"ok": True})
        if path == "/api/clear":
            body = self._body()
            db.clear(body.get("kind") or "flow")
            return self._json({"ok": True})

        # ---- 生态多主体 ----
        if path == "/api/upload-plan":
            return self.upload_plan()
        if path == "/api/submission-action":
            return self.submission_action()
        if path == "/api/params":
            body = self._body()
            db_eco.params_save(body.get("params") or {}, body.get("actor") or "")
            return self._json({"ok": True, "params": db_eco.params_load()})
        if path == "/api/terms":
            body = self._body()
            db_eco.terms_save(body.get("rows") or [], body.get("actor") or "")
            return self._json({"ok": True, "terms": db_eco.terms_load()})
        if path == "/api/bridge":
            body = self._body()
            db_eco.bridge_save(body.get("rows") or [])
            return self._json({"ok": True, "bridge": db_eco.bridge_load()})
        if path == "/api/match-link":
            body = self._body()
            n = db_eco.match_link_save(body.get("links") or [])
            return self._json({"ok": True, "saved": n})
        self._raw()                       # 排空请求体，避免 HTTP/1.1 长连接错位
        return self._send(404, b"not found", "text/plain; charset=utf-8")

    # ---------- 生态多主体：业务处理 ----------
    def eco_bundle(self):
        d = db_eco.dict_all()
        return {
            "ok": True,
            "templates": db_eco.tpl_list(),
            "subjects": d["subjects"],
            "entities": d["entities"],
            "fx": d["fx"],
            "params": db_eco.params_load(),
            "terms": db_eco.terms_load(),
            "bridge": db_eco.bridge_load(),
            "submissions": db_eco.submission_list(),
            "statusFlow": db_eco.STATUS_FLOW,
            "actionRole": db_eco.ACTION_ROLE,
            "actionGate": db_eco.ACTION_GATE,
            "stats": db_eco.eco_stats(),
        }

    def serve_uploaded(self, name, inline=False):
        """取回上传的原件（供「看文档」）：只允许 uploads 目录内的文件，防目录穿越"""
        base = os.path.basename(name or "")
        if not base:
            return self._json({"ok": False, "error": "缺少文件名"}, 400)
        hits = []
        if os.path.isdir(UPLOAD_DIR):
            hits = [f for f in os.listdir(UPLOAD_DIR) if f == base or f.endswith("_" + base)]
        if not hits:
            return self._json({"ok": False, "error": "原件不存在：%s" % base}, 404)
        p = os.path.join(UPLOAD_DIR, sorted(hits)[-1])
        data = open(p, "rb").read()
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        disp = ("inline" if inline else "attachment") + "; filename*=UTF-8''%s" % quote(base)
        return self._send(200, data, ctype, {"Content-Disposition": disp})

    def serve_template(self, tpl_id):
        t = db_eco.tpl_hit(tpl_id)
        if not t:
            return self._json({"ok": False, "error": "模板未注册：%s" % tpl_id}, 404)
        rel = t.get("file_path") or ""
        p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            p = os.path.join(ROOT, os.path.basename(rel))
        if not os.path.isfile(p):
            if tpl_id in ("TPL-05", "TPL-06"):
                from template_export import plan_template, flow_template
                data = plan_template() if tpl_id == "TPL-05" else flow_template()
                name = "%s_%s.xlsx" % (t["name"], t.get("version") or "v1")
                return self._send(200, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name)})
            return self._json({"ok": False, "error": "模板文件不存在：%s" % rel}, 404)
        # 文件名带版本号，回收时靠它判断该用哪套校验规则
        stem, ext = os.path.splitext(os.path.basename(p))
        fname = "%s_%s%s" % (stem, t.get("version") or "v1", ext)
        data = open(p, "rb").read()
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        disp = "attachment; filename*=UTF-8''%s" % quote(fname)
        return self._send(200, data, ctype, {"Content-Disposition": disp})

    def upload_plan(self):
        """body = xlsx 原始字节；解析 → 三道闸门 → 建提报单（草稿或直接提交待审）"""
        raw = self._raw()
        if not raw:
            return self._json({"ok": False, "error": "没有收到文件内容"}, 400)
        name = self._hdr("X-File-Name") or "上传的资金计划表.xlsx"
        period = self._hdr("X-Period")
        actor = self._hdr("X-Actor")
        auto_submit = self.headers.get("X-Submit") in ("1", "true", "True")

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = "".join(c for c in os.path.basename(name) if c not in '\\/:*?"<>|')
        save_path = os.path.join(UPLOAD_DIR, "%s_%s" % (stamp, safe))
        with open(save_path, "wb") as f:
            f.write(raw)

        try:
            import plan_parse
        except Exception as e:
            return self._json({"ok": False, "error": "解析模块不可用（需要 openpyxl）：%s" % e}, 500)

        params = db_eco.params_map()
        try:
            res = plan_parse.parse_plan(save_path, period=period or None, params=params)
        except Exception as e:
            return self._json({"ok": False, "error": "解析失败：%s" % e, "file": safe}, 500)
        if not res.get("ok"):
            # 不是「已拆解的计划表」→ 按提报源表拆解（skill：销售/部门费用/人力 → by 月资金计划，C-01~C-09）
            try:
                import claim_parse
                cres = claim_parse.parse_claim(save_path, period=period or None,
                                               dicts=db_eco.dict_all())
            except Exception as e:
                cres = {"ok": False, "error": "拆解失败：%s" % e}
            if cres.get("ok"):
                return self.create_from_claim(cres, safe, actor or "系统", auto_submit)
            return self._json({"ok": False, "error": res.get("error"),
                               "claim_error": cres.get("error"),
                               "sheets": res.get("sheets"), "warnings": res.get("warnings"),
                               "saved_as": os.path.relpath(save_path, ROOT)}, 400)

        # 上传件里若带新版科目字典/主体台账，同步刷新（模板升版即字典升版）
        dd = res.get("dicts") or {}
        if dd.get("subjects") or dd.get("entities"):
            # 只合并、不覆盖：上传件可能只带局部字典（或测试用的小字典），
            # 整表覆盖会把集团统一口径洗掉
            db_eco.dict_merge(subjects=[dict(x, tpl_ver="v1.0") for x in (dd.get("subjects") or [])] or None,
                              entities=dd.get("entities") or None)
        if dd.get("fx"):
            db_eco.dict_replace(fx=dd["fx"])
            from seed_eco import ensure_fx_params
            ensure_fx_params()

        period = res["period"] or datetime.now().strftime("%Y-%m")
        entity = res["entity"]
        version = db_eco.next_version(period, entity)
        gates = res["gates"]
        # G3 补充：同期间同主体已有锁定版本 → 必须走变更，不能二次锁定
        locked_same = [s for s in db_eco.submission_list(500)
                       if s["period"] == period and s["entity"] == entity
                       and s["status"] in ("已锁定", "已核销")]
        if locked_same:
            gates["G3"]["issues"].append({
                "type": "已有锁定版本", "row": None,
                "detail": "同期间同主体已存在锁定版本 #%d，本次将作为 v%d，批准前需先对旧版发起变更"
                          % (locked_same[0]["id"], version)})
            gates["G3"]["pass"] = False

        tpl = db_eco.tpl_get("TPL-05") or {}
        sub_id = db_eco.submission_create(
            {"entity": entity, "period": period, "version": version, "file_name": safe,
             "tpl_ver": tpl.get("version"), "rule_ver": tpl.get("rule_ver"),
             "status": "草稿", "inflow": res["inflow"], "outflow": res["outflow"],
             "net": res["net"], "gates": gates},
            res["items"], actor=actor or "系统")

        try:
            apn = db_eco.save_appendix(sub_id, period, entity, version, res.get("appendix") or [])
            if apn:
                msg_ap = "，附页 %d 条" % apn
            else:
                msg_ap = ""
        except Exception:
            msg_ap = ""
        msg = "已创建提报单 #%d（草稿）%s" % (sub_id, msg_ap)
        if auto_submit and gates["G1"]["pass"]:
            ok, m, st = db_eco.submission_action(sub_id, "提交", actor=actor or "提报人",
                                                 role="提报人", reason="上传即提交",
                                                 gates={"G1": gates["G1"]})
            msg = "已创建并提交（#%d，%s）" % (sub_id, m) if ok else msg + "；提交失败：" + m
        elif auto_submit:
            msg = "已创建提报单 #%d，但 G1 未通过（%d 处错误），需修正后再提交" % (
                sub_id, gates["G1"]["error"])

        return self._json({
            "ok": True, "sub_id": sub_id, "message": msg,
            "period": period, "entity": entity, "version": version,
            "rows": len(res["items"]), "inflow": res["inflow"], "outflow": res["outflow"],
            "net": res["net"], "gates": gates, "warnings": res.get("warnings") or [],
            "sheets": res.get("sheets"), "skipped": res.get("skipped") or [],
            "items": res["items"][:500],
            "saved_as": os.path.relpath(save_path, ROOT),
            "submission": db_eco.submission_get(sub_id),
        })

    def submission_action(self):
        body = self._body()
        sid = int(body.get("id") or 0)
        action = body.get("action") or ""
        actor = body.get("actor") or ""
        role = body.get("role") or db_eco.ACTION_ROLE.get(action, "")
        reason = body.get("reason") or ""
        if not actor:
            return self._json({"ok": False, "error": "必须填写操作人（审批要留痕）"}, 400)
        sub = db_eco.submission_get(sid)
        if not sub:
            return self._json({"ok": False, "error": "提报单不存在"}, 404)

        gate_key = db_eco.ACTION_GATE.get(action)
        force = bool(body.get("force"))
        gates = {}
        # 闸门未过不允许推进：G1 看必填/科目/金额，G2 看重复与字典外语汇，G3 看勾稽与二次锁定
        # 确实要放行，必须显式带 force=true，会在留痕里记下是谁强制推的
        if gate_key:
            g = (sub.get("gates") or {}).get(gate_key) or {}
            if not g:
                g = {"pass": True, "desc": "无历史校验记录，按当前明细放行"}
            if g.get("pass") is False:
                if not force:
                    detail = "；".join(
                        str(i.get("detail") or "、".join(i.get("issues") or []) or i.get("type") or "")
                        for i in (g.get("issues") or [])[:3])
                    return self._json({
                        "ok": False, "gate": gate_key, "issues": g.get("issues"),
                        "error": "%s 未通过：%s。修正后重传，或确认无误时带 force=true 强制推进（会留痕）"
                                 % (gate_key, detail)}, 409)
                g = dict(g, forced=True, force_by=actor)
                reason = (reason + "｜").strip("｜") + "%s 未过强制放行" % gate_key
            gates[gate_key] = g

        ok, msg, status = db_eco.submission_action(sid, action, actor=actor, role=role,
                                                   reason=reason, gates=gates)
        if not ok:
            return self._json({"ok": False, "error": msg}, 409)
        # 计划「锁定」即入数仓：看板的计划侧完全由 dwd_plan 决定
        plan_rows = None
        try:
            if status == "已锁定":
                plan_rows = sync_locked_plan()
        except Exception as e:
            plan_rows = "sync_error:%s" % e
        try:
            sub = db_eco.submission_get(sid) or {}
            db.add_request("plan", sub.get("file_name") or "资金计划表",
                           "%s / %s" % (sub.get("entity") or "", sub.get("period") or ""),
                           sub.get("rows") or 0, "已完成", body.get("actor") or "",
                           "资金计划上传（%s）" % (sub.get("status") or status))
        except Exception:
            pass
        return self._json({"ok": True, "message": msg, "status": status,
                           "plan_rows": plan_rows,
                           "submission": db_eco.submission_get(sid)})


    # ---------- 提报源表拆解结果 → 逐期间建提报单 ----------
    def create_from_claim(self, cres, file_name, actor, auto_submit):
        made = []
        rules = cres.get("rules") or {}
        for pd in cres["periods"]:
            items = pd["items"]
            err_rows = [i for i in items if str(i.get("flag")).startswith("error")]
            seen, dups = {}, []
            for i in items:
                k = (i["direction"], i["subject"], round(float(i.get("amt_cny") or 0), 2))
                if k in seen:
                    dups.append({"type": "重复行", "detail": "%s / %s 金额重复" % (i["direction"], i["subject"])})
                else:
                    seen[k] = i
            gates = {
                "G1": {"pass": not err_rows, "error": len(err_rows), "warn": 0,
                       "issues": [{"row": i.get("src_row"), "subject": i.get("subject"),
                                   "issues": [i.get("flag")]} for i in err_rows][:60],
                       "desc": "科目命中字典（C-09）/ 金额为正 / 回款可由件数×售价×扣率算出（C-01）"},
                "G2": {"pass": not dups, "issues": dups,
                       "desc": "按 客户（流入）/ 科目（流出）汇总无重复行，附页可逐条追溯"},
                "G3": {"pass": True,
                       "issues": [{"type": "勾稽校验", "row": None,
                                   "detail": "流入 {:,.2f} − 流出 {:,.2f} = 净额 {:,.2f}（明细 {} 行）".format(
                                       pd["inflow"], pd["outflow"], pd["net"], len(items))}],
                       "desc": "合计勾稽 / 同期间不得二次锁定"},
                "claim": {"rules": rules, "pending": len(cres.get("pending") or []),
                          "excluded": len(cres.get("excluded") or []),
                          "warnings": cres.get("warnings") or [],
                          "appendix_sales": len(cres.get("appendix_sales") or []),
                          "appendix_expense": len(cres.get("appendix_expense") or [])},
            }
            version = db_eco.next_version(pd["period"], cres["entity"])
            sub_id = db_eco.submission_create(
                {"entity": cres["entity"], "period": pd["period"], "version": version,
                 "file_name": file_name, "tpl_ver": "源表拆解", "rule_ver": "v1",
                 "status": "草稿", "inflow": pd["inflow"], "outflow": pd["outflow"],
                 "net": pd["net"], "gates": gates},
                items, actor=actor)
            st = "草稿"
            if auto_submit and gates["G1"]["pass"]:
                ok, _m, st = db_eco.submission_action(sub_id, "提交", actor=actor, role="提报人",
                                                      reason="源表拆解后提交", gates={"G1": gates["G1"]})
                if not ok:
                    st = "草稿"
            try:
                db_eco.save_appendix(sub_id, pd["period"], cres["entity"], version,
                                     (cres.get("appendix_sales") or []) + (cres.get("appendix_expense") or []))
            except Exception:
                pass
            made.append({"sub_id": sub_id, "period": pd["period"], "version": version, "status": st,
                         "rows": len(items), "inflow": pd["inflow"], "outflow": pd["outflow"],
                         "net": pd["net"], "gates": gates})
        try:
            db.add_request("plan", file_name or "资金计划表",
                           "%s / %s" % (cres["entity"], ",".join(m["period"] for m in made)),
                           sum(m["rows"] for m in made), "已完成", actor or "",
                           "资金计划导入（%d 张提报单）" % len(made))
        except Exception:
            pass
        return self._json({
            "ok": True, "kind": "claim", "message": "已按 C-01~C-09 拆解为 %d 个期间，共 %d 张提报单" % (
                len(made), len(made)),
            "entity": cres["entity"], "submissions": made,
            "rules": rules, "warnings": cres.get("warnings") or [],
            "pending": cres.get("pending") or [], "excluded": (cres.get("excluded") or [])[:50],
            "appendix_sales": (cres.get("appendix_sales") or [])[:50],
            "appendix_expense": (cres.get("appendix_expense") or [])[:50],
            "sheets": cres.get("sheets"), "saved_as": os.path.relpath(
                os.path.join(UPLOAD_DIR, file_name), ROOT) if file_name else "",
        })

    # ---------- 静态 ----------
    def serve_html(self):
        p = os.path.join(ROOT, HTML_NAME)
        if not os.path.exists(p):
            return self._send(404, ("找不到看板文件：" + HTML_NAME).encode("utf-8"),
                              "text/plain; charset=utf-8")
        html = open(p, encoding="utf-8").read()
        if "</body>" in html:
            html = html.replace("</body>", BOOT + "</body>", 1)
        return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def serve_db(self):
        """数仓浏览页：表清单 + 数据预览，点开就能看，不用装客户端。"""
        import html as _h
        q = self._q()
        tab = q.get("t") or ""
        try:
            limit = max(1, min(500, int(q.get("limit") or 100)))
        except Exception:
            limit = 100
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {}
        for t in tabs:
            try:
                counts[t] = conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0]
            except Exception:
                counts[t] = -1
        if tab not in tabs:
            tab = "dwd_flow_tagged" if "dwd_flow_tagged" in tabs else (tabs[0] if tabs else "")
        cols, rows = [], []
        if tab:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % tab)]
            rows = [dict(r) for r in conn.execute("SELECT * FROM %s LIMIT %d" % (tab, limit))]
        conn.close()

        nav = "".join(
            '<a class="t%s" href="/db?t=%s"><b>%s</b><i>%s</i></a>' % (
                " on" if t == tab else "", t, t, counts.get(t, 0))
            for t in tabs)
        head = "".join("<th>%s</th>" % _h.escape(c) for c in cols)
        body = ""
        for r in rows:
            tds = []
            for c in cols:
                v = r.get(c)
                s = "" if v is None else str(v)
                if len(s) > 90:
                    s = s[:90] + "…"
                tds.append("<td>%s</td>" % _h.escape(s))
            body += "<tr>" + "".join(tds) + "</tr>"
        page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>本地数仓 · warehouse.db</title>
<style>
body{margin:0;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#F6F7F9;color:#1F2937}
header{background:#1F3864;color:#fff;padding:14px 20px}
header h1{margin:0;font-size:16px;font-weight:500}
header p{margin:4px 0 0;font-size:12px;opacity:.8}
.wrap{display:flex;gap:14px;padding:14px}
nav{width:250px;flex:0 0 250px;background:#fff;border:1px solid #E5E7EB;border-radius:8px;padding:8px;height:fit-content}
nav a{display:flex;justify-content:space-between;align-items:center;padding:6px 9px;border-radius:6px;color:#374151;text-decoration:none;font-size:12.5px}
nav a:hover{background:#F3F4F6}
nav a.on{background:#E6F1FB;color:#0C447C}
nav a b{font-weight:500}
nav a i{font-style:normal;font-size:11px;color:#9CA3AF}
main{flex:1;min-width:0;background:#fff;border:1px solid #E5E7EB;border-radius:8px;padding:12px}
main h2{margin:0 0 8px;font-size:14px;font-weight:500}
main p{margin:0 0 10px;font-size:12px;color:#6B7280}
table{border-collapse:collapse;width:100%%;font-size:12px}
th,td{border-bottom:1px solid #F0F1F3;padding:5px 8px;text-align:left;white-space:nowrap;max-width:320px;overflow:hidden;text-overflow:ellipsis}
th{background:#FAFBFC;color:#4B5563;position:sticky;top:0}
tr:hover td{background:#FAFBFC}
.scroll{overflow:auto;max-height:calc(100vh - 190px)}
a.dl{font-size:12px;color:#185FA5}
</style></head><body>
<header><h1>本地数仓 · warehouse.db</h1>
<p>SQLite 单文件数据库　·　%(path)s</p></header>
<div class="wrap"><nav>%(nav)s</nav>
<main><h2>%(tab)s　<span style="font-size:12px;color:#6B7280">%(n)s 行 · 预览前 %(lim)s 行</span></h2>
<p>%(cols)s 个字段　·　<a class="dl" href="/api/rows" target="_blank">/api/rows</a>　<a class="dl" href="/api/health" target="_blank">/api/health</a></p>
<div class="scroll"><table><thead><tr>%(head)s</tr></thead><tbody>%(body)s</tbody></table></div>
</main></div></body></html>""" % {
            "path": _h.escape(db.DB_PATH), "nav": nav, "tab": _h.escape(tab),
            "n": counts.get(tab, 0), "lim": limit, "cols": len(cols), "head": head,
            "body": body or '<tr><td colspan="%d" style="color:#9CA3AF">（空表）</td></tr>' % max(1, len(cols))
        }
        return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def serve_export(self):
        import etl
        tmp = os.path.join(tempfile.gettempdir(), "资金数仓导出.xlsx")
        etl.cmd_export(tmp)
        data = open(tmp, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type",
                         mimetypes.guess_type(tmp)[0] or "application/octet-stream")
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''%s" % quote("资金数仓导出.xlsx"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    from seed_eco import seed_defaults
    seed_defaults()
    s = db.stats()
    e = db_eco.eco_stats()
    print("=" * 62)
    print("  珀研本地数仓 · 生态多主体资金计划")
    print("=" * 62)
    print("  数据库：%s" % db.DB_PATH)
    print("  流水 %s 笔 / 旧口径计划 %s 行 / 人工标签 %s 条"
          % (s["flow_rows"], s["plan_rows"], s["manual_rows"]))
    print("  提报单 %s 份（%s）/ 模板 %s 个 / 参数 %s 项 / 桥接 %s 条"
          % (e["submissions"], "、".join("%s×%s" % kv for kv in e["by_status"].items()) or "空",
             e["templates"], e["params"], e["bridge"]))
    print("  看板：%s" % HTML_NAME)
    print("  打开：http://127.0.0.1:%d" % PORT)
    print("  停止：Ctrl + C")
    print("=" * 62)
    ThreadingHTTPServer.allow_reuse_address = False
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
