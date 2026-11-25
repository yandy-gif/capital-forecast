# -*- coding: utf-8 -*-
"""server11.py —— v11 本地数仓服务（标准库，无第三方依赖）

用法：
  .venv/Scripts/python.exe 数仓/v11/server11.py          # 默认 127.0.0.1:8770
  打开 数仓/v11/open_v11.bat 可一键起服务并打开看板

接口：
  GET  /api/meta                         主数据 + 字典 + 汇率 + 批次 + 计数 + 聚合时间
  GET  /api/snapshot?entity=&period=     看板统一取数（行级 + 余额 + 计划 + 汇率）
  GET  /api/pending?entity=&period=      待确认池
  GET  /api/health
  POST /api/batch                        建申请批次（公司+期间+文件名+指纹）
  POST /api/import                       逐笔落库（submit=false 存草稿）
  POST /api/confirm                      人工确认（更新状态+反写规则+留痕）
  POST /api/edit                         单条修改（置待确认，留痕）
  POST /api/plan                         计划上传（approve=false 为草稿）
  POST /api/plan/approve                 计划审批入库
  POST /api/fx                           汇率维护（人工，生效日期）
  POST /api/safe                         安全线维护
  POST /api/restricted                   受限资金维护
  GET  /                                 直接返回 v11 单文件看板
"""
import os
import sys
import json
import hashlib
import argparse
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import db11  # noqa: E402
import xls_read  # noqa: E402

HTML = os.path.join(ROOT, "生态资金计划系统v11.html")
PLAN_TEMPLATE = os.path.join(ROOT, "资金计划表_模板.xlsx")   # 模板直接下发源文件，保持原格式


class Handler(SimpleHTTPRequestHandler):
    server_version = "v11/1.0"

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---------- 公共 ----------
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        # 预检会带 Access-Control-Request-Headers，直接回声，避免自定义头（如 X-File-Name）被判不合规
        req = self.headers.get("Access-Control-Request-Headers")
        self.send_header("Access-Control-Allow-Headers", req or "Content-Type,X-File-Name")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/health":
                return self._json({"ok": True, "db": db11.DB_PATH, "html": os.path.exists(HTML)})
            if u.path == "/api/meta":
                return self._json(db11.meta())
            if u.path == "/api/snapshot":
                return self._json(db11.snapshot(q.get("entity", "ALL"), q.get("period", "YTD")))
            if u.path == "/api/batch-rows":
                return self._json({"rows": db11.batch_rows(int(q["batchId"]))})
            if u.path == "/api/plan-template":
                if not os.path.exists(PLAN_TEMPLATE):
                    return self._json({"error": "模板文件不存在：资金计划表_模板.xlsx"}, 404)
                with open(PLAN_TEMPLATE, "rb") as fh:
                    data = fh.read()
                name = "资金计划表_模板.xlsx"
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition",
                                 "attachment; filename=plan_template.xlsx; filename*=UTF-8''" + quote(name))
                self.send_header("Content-Length", str(len(data)))
                self._cors()
                self.end_headers()
                self.wfile.write(data)
                return
            if u.path == "/api/flow/batch":
                return self._json({"batch": db11.batch_status(int(q["batchId"]))})
            if u.path == "/api/plan-batch":
                return self._json(db11.plan_batch_rows(int(q["batchId"])))
            if u.path == "/api/pending":
                return self._json({"rows": db11.pending_flows(q.get("entity", "ALL"), q.get("period", "ALL"))})
            if u.path in ("/", "/index.html"):
                if not os.path.exists(HTML):
                    return self._json({"error": "看板文件不存在，请先运行 数仓/build_v11.py"}, 500)
                with open(HTML, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._json({"error": "not found"}, 404)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            return self._json({"error": str(ex)}, 500)

    # ---------- POST ----------
    def do_POST(self):
        u = urlparse(self.path)
        # 二进制文件解析（.xls / 假 xls）走原始 body，不走 JSON
        if u.path == "/api/parse-file":
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 60 * 1024 * 1024:
                return self._json({"error": "文件为空或超过 60MB"}, 400)
            raw = self.rfile.read(n)
            # 文件名优先从查询串取（不触发预检），兼容旧版自定义头
            fname = (parse_qs(u.query).get("name") or [""])[0] or unquote(self.headers.get("X-File-Name") or "")
            fname = unquote(fname)
            try:
                return self._json(xls_read.parse_any(raw, fname))
            except Exception as ex:
                return self._json({"error": "解析失败：%s" % ex}, 400)
        b = self._body()
        try:
            if u.path == "/api/batch":
                row, dup = db11.create_batch(
                    b["entity"], b["period"], b.get("fileName", ""),
                    b.get("sha") or hashlib.sha256(json.dumps(b, sort_keys=True).encode()).hexdigest()[:16],
                    b.get("kind", "flow"), b.get("operator", "操作员"))
                return self._json({"batch": row, "duplicate": dup})
            if u.path == "/api/import":
                return self._json(db11.import_rows(
                    int(b["batchId"]), b.get("rows", []),
                    b.get("operator", "操作员"), bool(b.get("submit", True))))
            if u.path == "/api/confirm":
                return self._json(db11.confirm_flows(
                    b.get("ids", []), b["code"], b.get("operator", "操作员")))
            if u.path == "/api/flow/save":
                # batchId 允许为空（没有打开具体批次时按记录自身批次归集），不要因 int(None) 直接 500
                return self._json(db11.save_flow_assignments(
                    int(b.get("batchId") or 0), b.get("items", []), b.get("operator", "操作员")))
            if u.path == "/api/flow/confirm-batch":
                return self._json(db11.confirm_batch(int(b["batchId"]), b.get("operator", "操作员")))
            if u.path == "/api/flow/change":
                return self._json(db11.flow_change(int(b["batchId"]), b.get("operator", "操作员")))
            if u.path == "/api/flow/batch":
                return self._json({"batch": db11.batch_status(int(b["batchId"]))})
            if u.path == "/api/edit":
                return self._json(db11.edit_flow(
                    int(b["id"]), b.get("patch", {}), b.get("operator", "操作员"), b.get("reason", "")))
            if u.path == "/api/plan":
                return self._json(db11.plan_import(
                    b["entity"], b["period"], b.get("fileName", ""), b.get("rows", []),
                    b.get("operator", "操作员"), bool(b.get("approve", False))))
            if u.path == "/api/plan/rows":
                return self._json(db11.plan_update_rows(
                    int(b["batchId"]), b.get("rows", []), b.get("operator", "操作员")))
            if u.path == "/api/plan/submit":
                return self._json(db11.plan_submit(int(b["batchId"]), b.get("operator", "操作员")))
            if u.path == "/api/plan/withdraw":
                return self._json(db11.plan_withdraw(
                    int(b["batchId"]), b.get("operator", "操作员"), b.get("reason", "撤回修改")))
            if u.path == "/api/plan/reject":
                return self._json(db11.plan_reject(
                    int(b["batchId"]), b.get("reason", ""), b.get("operator", "审批人")))
            if u.path == "/api/plan/change":
                return self._json(db11.plan_change(int(b["batchId"]), b.get("operator", "操作员")))
            if u.path == "/api/plan/approve":
                return self._json(db11.plan_approve(int(b["batchId"]), b.get("operator", "审批人")))
            if u.path == "/api/fx":
                return self._json(db11.fx_set(b["pair"], b["rate"], b["effectiveDate"], b.get("operator", "操作员")))
            if u.path == "/api/safe":
                c = db11.conn()
                c.execute("INSERT INTO entity_rule(entity,safe_line,updated_at) VALUES(?,?,datetime('now','localtime'))"
                          " ON CONFLICT(entity) DO UPDATE SET safe_line=excluded.safe_line, updated_at=excluded.updated_at",
                          (b["entity"], float(b.get("safeLine", 0))))
                c.commit(); c.close()
                return self._json({"ok": True})
            if u.path == "/api/reset-data":
                if b.get("confirm") != "RESET_DATA":
                    return self._json({"error": "需要 confirm=RESET_DATA"}, 400)
                return self._json(db11.reset_business_data())
            if u.path == "/api/restricted-rule":
                return self._json(db11.restricted_rule_add(b.get("kind", ""), b.get("keyword", "")))
            if u.path == "/api/restricted":
                c = db11.conn()
                c.execute("UPDATE dim_account SET restricted=? WHERE entity=? AND bank=? AND account=?",
                          (float(b.get("amount", 0)), b["entity"], b["bank"], b.get("account", "")))
                c.commit(); c.close()
                db11.bump_agg()
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as ex:
            import traceback
            traceback.print_exc()
            return self._json({"error": str(ex)}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("V11_PORT", 8770)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reset", action="store_true", help="重建数仓并重新导入种子（会清空本库）")
    a = ap.parse_args()
    db11.init_db(reset=a.reset)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("v11 数仓服务已启动: http://%s:%d  (db=%s)" % (a.host, a.port, db11.DB_PATH))
    print("看板: http://%s:%d/   接口: /api/meta /api/snapshot" % (a.host, a.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
