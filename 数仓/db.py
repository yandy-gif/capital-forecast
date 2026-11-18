"""本地数仓 · SQLite 基础层（零第三方依赖，仅用标准库 sqlite3）

设计原则
- ods 存原始行，永不覆盖，可回溯
- dwd_manual 按业务键存放人工审核结果，跨批次、跨文件都生效
- 看板不直接读表，而是通过 server.py 的 /api/rows 拿「原始行 + 人工标签」，
  计算仍由前端那套规则引擎完成，保证看板与数仓口径完全一致
"""
import os
import sqlite3
import json
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "warehouse.db")
SCHEMA = os.path.join(BASE, "schema.sql")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)   # autocommit：避免连接被遗忘时长期占写锁
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")        # 读写不互斥
        conn.execute("PRAGMA busy_timeout=30000")      # 锁住就等，不直接抛错
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def init():
    conn = connect()
    with open(SCHEMA, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def new_batch():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def set_meta(k, v):
    conn = connect()
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    conn.commit()
    conn.close()


def get_meta(k, default=None):
    conn = connect()
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    conn.close()
    return row["v"] if row else default


def clear(kind):
    """清空某一类数据（flow 清 ods_flow，plan 清 dwd_plan）"""
    conn = connect()
    if kind == "flow":
        conn.execute("DELETE FROM ods_flow")
        conn.execute("UPDATE meta SET v='' WHERE k='active_batch_flow'")
    elif kind == "plan":
        conn.execute("DELETE FROM dwd_plan")
        conn.execute("UPDATE meta SET v='' WHERE k='active_batch_plan'")
    elif kind == "manual":
        conn.execute("DELETE FROM dwd_manual")
    conn.commit()
    conn.close()


def _flow_num(x):
    try:
        return round(abs(float(str(x).replace(",", "").replace("¥", "").replace("￥", "").strip())), 2)
    except Exception:
        return None


def _flow_dir(v):
    """借/贷、收/支、收入/支出 归一，避免同一笔因表述不同被当成两条"""
    t = str(v or "").strip()
    i = any(k in t for k in ("收", "贷", "进", "入账", "存入", "转入", "流入"))
    o = any(k in t for k in ("支", "借", "出账", "付出", "转出", "付款", "流出"))
    if i and not o:
        return "收"
    if o and not i:
        return "支"
    return t


def _bal_str(v):
    """余额归一化：把 715000 / 715000.0 / 715,000 视为同一个值，避免不同来路（csv 与 xlsx）判不出重复。"""
    if v is None or str(v).strip() == "":
        return ""
    return _amt_str(v)


def flow_key(r):
    """去重键，按可靠性降级：
    1) 银行流水号（唯一，最可靠）
    2) 交易时间（到秒）+ 方向 + 金额 —— 同日同额的真实多笔时间不同，不会被误合并
    3) 余额 + 方向 + 金额 —— 同一账户余额逐笔变化
    4) 日期+方向+金额+对方+摘要 —— 最后兜底（可能误合并同日同额同摘要的多笔手续费）
    余额与金额统一转成数值再拼键，避免 "2000" 与 "2000.0" 被当成两笔。
    """
    d = str(r.get("交易日期") or r.get("日期") or "").strip()
    di = _flow_dir(r.get("收支方向") or r.get("方向"))
    amt = _flow_num(r.get("金额"))
    sn = str(r.get("流水号") or "").strip()
    if sn:
        return ("S", sn)
    ts = str(r.get("交易时间") or "").strip()
    bal0 = _bal_str(r.get("余额"))
    if ts:
        return ("T", d, ts, di, amt, bal0)
    bal = _bal_str(r.get("余额"))
    if bal:
        return ("B", d, bal, di, amt)
    return (
        "K", d, di, amt,
        str(r.get("对方户名") or r.get("对方单位") or "").strip(),
        str(r.get("摘要") or r.get("用途") or "").strip()[:50],
    )


def save_flow(rows, file_name="上传文件", batch=None):
    """rows 为看板标准字段的字典列表：月份/交易日期/收支方向/金额/对方户名/摘要/收支科目(一级)/明细科目(二级)/现金流量分类
    入库前按业务键与库内已有流水比对，重复的不再插入，避免反复上传同一份文件把金额翻倍。"""
    batch = batch or new_batch()
    conn = connect()
    # 同一文件重导会走 UPDATE ... edited_at=...，全新库必须先补上这两列，否则报 no such column
    _ensure_flow_edit_schema(conn)
    exist = set()
    for r in conn.execute("SELECT row_json FROM ods_flow"):
        try:
            exist.add(flow_key(json.loads(r["row_json"])))
        except Exception:
            pass
    # 同一文件之前导过的行（按 file_name + row_no 定位）→ 重导时原地覆盖，不新增一行
    same = {}
    for x in conn.execute("SELECT id, row_no, row_json FROM ods_flow WHERE file_name=?", (file_name,)):
        same[x["row_no"]] = (x["id"], x["row_json"] or "{}")
    n = 0
    upd = 0
    skipped = 0
    for i, r in enumerate(rows, 1):
        k = flow_key(r)
        hit = same.get(i)
        if hit:
            rid, old_json = hit
            # 内容没变就是「重复上传同一份文件」：不写库、不计入 updated，
            # 这样调用方能把整批判定为 duplicated 而不留下申请记录。
            try:
                unchanged = json.loads(old_json) == r
            except Exception:
                unchanged = False
            if unchanged:
                skipped += 1
                exist.add(k)
                continue
            conn.execute("UPDATE ods_flow SET batch=?, row_json=?, edited_at=NULL, edited_by=NULL WHERE id=?",
                         (batch, json.dumps(r, ensure_ascii=False), rid))
            upd += 1
            exist.add(k)
            continue
        if k in exist:
            skipped += 1
            continue
        exist.add(k)
        conn.execute(
            "INSERT INTO ods_flow(batch,file_name,row_no,row_json) VALUES(?,?,?,?)",
            (batch, file_name, i, json.dumps(r, ensure_ascii=False)),
        )
        n += 1
    conn.execute("INSERT INTO import_log(kind,file_name,rows,batch) VALUES('flow',?,?,?)", (file_name, n + upd, batch))
    conn.commit()
    conn.close()
    set_meta("active_batch_flow", batch)
    return {"batch": batch, "rows": n, "updated": upd, "skipped": skipped}


# ---------------- 申请记录（导入 / 修改台账） ----------------
def _ensure_request_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS import_request(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,                -- flow 流水 / plan 资金计划 / edit 修改存档
        title TEXT, batch TEXT,
        rows INTEGER DEFAULT 0,
        status TEXT DEFAULT '已完成',   -- 进行中 / 已完成 / 已确认 / 已作废
        actor TEXT, note TEXT,
        created_at TEXT, updated_at TEXT)""")


def add_request(kind, title, batch="", rows=0, status="已完成", actor="", note=""):
    conn = connect()
    _ensure_request_schema(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute("""INSERT INTO import_request(kind,title,batch,rows,status,actor,note,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?)""",
                       (kind, title or "", batch or "", int(rows or 0), status or "已完成",
                        actor or "本地用户", note or "", now, now))
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def list_requests(kind=None, limit=100):
    conn = connect()
    _ensure_request_schema(conn)
    if kind:
        rs = conn.execute("SELECT * FROM import_request WHERE kind=? ORDER BY id DESC LIMIT ?",
                          (kind, limit)).fetchall()
    else:
        rs = conn.execute("SELECT * FROM import_request ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    out = []
    for r in rs:
        d = dict(r)
        d["status"] = d.get("status") or "已完成"
        out.append(d)
    return out


def set_request_status(rid, status, actor=""):
    conn = connect()
    _ensure_request_schema(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = conn.execute("UPDATE import_request SET status=?, updated_at=?, actor=COALESCE(NULLIF(?, ''), actor) WHERE id=?",
                     (status, now, actor or "", rid)).rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(n), "id": rid, "status": status}


# ---------------- 原始流水修改（备注/户名/金额/方向/日期） ----------------
FLOW_EDIT_FIELDS = ["月份", "交易日期", "收支方向", "金额", "对方户名", "摘要",
                    "收支科目(一级)", "明细科目(二级)", "现金流量分类"]


def _ensure_flow_edit_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS flow_edit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        flow_id INTEGER, batch TEXT, file_name TEXT,
        field TEXT, old_val TEXT, new_val TEXT,
        actor TEXT, reason TEXT, created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_flow_edit_flow ON flow_edit_log(flow_id)")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ods_flow)")]
    for c in ("edited_at", "edited_by"):
        if c not in cols:
            conn.execute("ALTER TABLE ods_flow ADD COLUMN %s TEXT" % c)


def _amt_str(v):
    """与前端 manualKey 对齐：整数不带小数点，其余保留两位"""
    try:
        f = _flow_num(v)
        if f is None:
            return str(v or "")
    except Exception:
        return str(v or "")
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return ("%.2f" % round(f, 2)).rstrip("0").rstrip(".")


def _mkey(d, amt, cp):
    return "%s|%s|%s" % (str(d or ""), _amt_str(amt), str(cp or ""))


def edit_flow(flow_id, patch, actor="", reason="", label=None):
    """原始字段、标签迁移和留痕在同一事务内保存。"""
    conn = connect()
    try:
        _ensure_flow_edit_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        r = conn.execute("SELECT * FROM ods_flow WHERE id=?", (flow_id,)).fetchone()
        if not r:
            return {"ok": False, "error": "流水不存在，请刷新后重试"}
        row = json.loads(r["row_json"] or "{}")
        old_row = dict(row)
        changed = []
        for k, v in (patch or {}).items():
            if k not in FLOW_EDIT_FIELDS:
                continue
            nv = "" if v is None else str(v).strip()
            if k == "金额":
                import math
                try:
                    amount = float(nv.replace(",", "").replace("¥", "").replace("￥", "").strip())
                except (TypeError, ValueError):
                    return {"ok": False, "error": "金额必须是有效数字"}
                if not math.isfinite(amount) or amount <= 0:
                    return {"ok": False, "error": "金额必须大于零，收支由方向字段指定"}
            if k == "收支方向" and nv not in ("收", "支", "收入", "支出", "流入", "流出", "借", "贷"):
                return {"ok": False, "error": "收支方向请选择收或支"}
            ov = str(row.get(k, "") or "")
            if ov != nv:
                changed.append((k, ov, nv)); row[k] = nv
        lbl = None
        if isinstance(label, dict) and label:
            lbl = {k: str(label.get(k) or "").strip() for k in ("l1", "l2", "cf")}
        if not changed and lbl is None:
            return {"ok": False, "error": "没有改动"}
        import re
        m = re.match(r"(\d{4})[-/.年](\d{1,2})", str(row.get("交易日期", "")))
        if m:
            row["月份"] = "%s.%02d" % (m.group(1), int(m.group(2)))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if changed:
            conn.execute("UPDATE ods_flow SET row_json=?,edited_at=?,edited_by=? WHERE id=?",
                         (json.dumps(row,ensure_ascii=False),now,actor or "本地用户",flow_id))
            conn.executemany("INSERT INTO flow_edit_log(flow_id,batch,file_name,field,old_val,new_val,actor,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                             [(flow_id,r["batch"],r["file_name"],k,ov,nv,actor or "本地用户",reason or "",now) for k,ov,nv in changed])
        old_key = _mkey(old_row.get("交易日期"),old_row.get("金额"),old_row.get("对方户名"))
        new_key = _mkey(row.get("交易日期"),row.get("金额"),row.get("对方户名"))
        old_label = conn.execute("SELECT * FROM dwd_manual WHERE uniq_key=?",(old_key,)).fetchone()
        if lbl is None and old_label and old_key != new_key:
            lbl = {"l1":old_label["l1"],"l2":old_label["l2"],"cf":old_label["cashflow"]}
        if lbl is not None:
            conn.execute("""INSERT INTO dwd_manual(uniq_key,trade_date,amount,counterparty,l1,l2,cashflow,confirmer,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uniq_key) DO UPDATE SET
                            l1=excluded.l1,l2=excluded.l2,cashflow=excluded.cashflow,
                            confirmer=excluded.confirmer,updated_at=excluded.updated_at""",
                         (new_key,row.get("交易日期"),_flow_num(row.get("金额")),row.get("对方户名"),lbl["l1"],lbl["l2"],lbl["cf"],actor,now))
            if old_key != new_key:
                conn.execute("DELETE FROM dwd_manual WHERE uniq_key=?",(old_key,))
        conn.commit()
        return {"ok":True,"id":flow_id,"row":row,"changed":[{"field":k,"old":ov,"new":nv} for k,ov,nv in changed],
                "label":lbl,"manual":load_manual(),"moved_label":lbl is not None,
                "old_key":old_key,"new_key":new_key}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def flow_edits(flow_id=None, limit=200):
    conn = connect()
    _ensure_flow_edit_schema(conn)
    if flow_id:
        rs = conn.execute("SELECT * FROM flow_edit_log WHERE flow_id=? ORDER BY id DESC LIMIT ?",
                          (flow_id, limit)).fetchall()
    else:
        rs = conn.execute("SELECT * FROM flow_edit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(x) for x in rs]


def save_plan(rows, file_name="上传文件", batch=None):
    batch = batch or new_batch()
    conn = connect()
    n = 0
    for r in rows:
        conn.execute(
            """INSERT INTO dwd_plan(batch,file_name,month,direction,cashflow,subject,source,amount,tax_flag,note,row_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (batch, file_name,
             r.get("月份") or r.get("month"), r.get("收支方向") or r.get("dir"),
             r.get("现金流量分类") or r.get("cat"), r.get("科目") or r.get("subj"),
             r.get("来源表") or r.get("src"), float(r.get("金额") or r.get("计划金额") or r.get("amt") or 0),
             r.get("含税标记") or r.get("tax"), r.get("备注") or r.get("note"),
             json.dumps(r, ensure_ascii=False)),
        )
        n += 1
    conn.execute("INSERT INTO import_log(kind,file_name,rows,batch) VALUES('plan',?,?,?)", (file_name, n, batch))
    conn.commit()
    conn.close()
    set_meta("active_batch_plan", batch)
    return {"batch": batch, "rows": n}


def load_flow(with_ids=False):
    """读原始流水。同一份文件被重复上传时只取最新批次，避免金额翻倍；
    不同文件（不同账户/不同银行）照常合并。"""
    conn = connect()
    # 每个 file_name 只保留 batch 最大的一批
    keep = {}
    # 以 ods_flow 里实际存在的批次为准（import_log 可能与数据不一致）
    for r in conn.execute("SELECT DISTINCT batch, file_name FROM ods_flow"):
        fn = (r["file_name"] or "").strip() or ("__batch__" + str(r["batch"]))
        b = str(r["batch"] or "")
        if b and (fn not in keep or b > keep[fn]):
            keep[fn] = b
    batches = sorted(set(keep.values()))
    if not batches:
        conn.close()
        return []
    ph = ",".join("?" * len(batches))
    rs = conn.execute("SELECT id, batch, row_json FROM ods_flow WHERE batch IN (" + ph + ") ORDER BY id", batches).fetchall()
    conn.close()
    if with_ids:
        out = []
        for r in rs:
            o = json.loads(r["row_json"])
            o["__id"] = r["id"]
            o["__batch"] = r["batch"]
            out.append(o)
        return out
    return [json.loads(r["row_json"]) for r in rs]


def load_plan():
    conn = connect()
    rs = conn.execute("SELECT * FROM dwd_plan ORDER BY month, direction, subject").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def save_manual(manual, merge=True, remove=None):
    """manual: {uniqKey: {l1,l2,cf}}
    merge=True（默认）：按 key 覆盖写入，不动其他已有标注，避免旧页面把别人的确认冲掉
    merge=False：全量替换；remove=[keys] 可显式删除若干条"""
    import math
    if not isinstance(manual, dict):
        raise ValueError("人工标注必须为对象")
    values = []
    for k, v in (manual or {}).items():
        if not isinstance(v, dict):
            raise ValueError("人工标注内容无效")
        parts = str(k).split("|", 2)
        d = parts[0] if len(parts) > 0 else ""
        amt = float(parts[1]) if len(parts) > 1 and parts[1] else None
        cp = parts[2] if len(parts) > 2 else ""
        if len(parts) != 3 or amt is None or not math.isfinite(amt):
            raise ValueError("人工标注的日期、金额或户名键无效")
        values.append((k,d,amt,cp,v.get("l1"),v.get("l2"),v.get("cf")))
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not merge:
            conn.execute("DELETE FROM dwd_manual")
        for k in (remove or []):
            conn.execute("DELETE FROM dwd_manual WHERE uniq_key=?", (k,))
        conn.executemany(
            """INSERT INTO dwd_manual(uniq_key,trade_date,amount,counterparty,l1,l2,cashflow)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(uniq_key) DO UPDATE SET
               trade_date=excluded.trade_date,amount=excluded.amount,counterparty=excluded.counterparty,
               l1=excluded.l1,l2=excluded.l2,cashflow=excluded.cashflow,
               updated_at=datetime('now','localtime')""", values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_manual():
    conn = connect()
    rs = conn.execute("SELECT * FROM dwd_manual").fetchall()
    conn.close()
    return {r["uniq_key"]: {"l1": r["l1"], "l2": r["l2"], "cf": r["cashflow"]} for r in rs}


def save_rule(payload):
    """规则集落库（前端导出的 JSON 字符串），单行 id='current' 全量覆盖"""
    conn = connect()
    conn.execute(
        """INSERT INTO dwd_rule(id,payload,updated_at) VALUES('current',?,datetime('now','localtime'))
           ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=datetime('now','localtime')""",
        (payload,))
    conn.commit()
    conn.close()


def load_rule():
    conn = connect()
    row = conn.execute("SELECT payload,updated_at FROM dwd_rule WHERE id='current'").fetchone()
    conn.close()
    return {"payload": row["payload"], "updated_at": row["updated_at"]} if row else None


def stats():
    conn = connect()
    out = {
        "flow_rows": conn.execute("SELECT COUNT(*) c FROM ods_flow").fetchone()["c"],
        "plan_rows": conn.execute("SELECT COUNT(*) c FROM dwd_plan").fetchone()["c"],
        "manual_rows": conn.execute("SELECT COUNT(*) c FROM dwd_manual").fetchone()["c"],
        "batches": [dict(r) for r in conn.execute(
            "SELECT batch, kind, file_name, rows, created_at FROM import_log ORDER BY id DESC LIMIT 10").fetchall()],
        "active_flow": get_meta("active_batch_flow", ""),
        "active_plan": get_meta("active_batch_plan", ""),
    }
    conn.close()
    return out


if __name__ == "__main__":
    init()
    print("数仓已初始化：", DB_PATH)
    print(json.dumps(stats(), ensure_ascii=False, indent=1))


# ---------------- 已打标流水（处理好的结果层） ----------------
_TAGGED_COLS = ["month","entity","account","trade_date","trade_time","direction","amount","balance",
                "counterparty","memo","l1","l2","cashflow","cat","activity","actual_flag",
                "party_type","related","tag_src","src_file","confirmer"]


def save_tagged(rows, batch=None):
    """覆盖式写入打标结果：每次确认都整表替换，保证与看板一致。
    rows 为字典列表，键同 _TAGGED_COLS。"""
    batch = batch or new_batch()
    conn = connect()
    conn.execute("""CREATE TABLE IF NOT EXISTS dwd_flow_tagged (
        id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL, month TEXT, entity TEXT, account TEXT,
        trade_date TEXT, trade_time TEXT, direction TEXT, amount REAL, balance REAL,
        counterparty TEXT, memo TEXT, l1 TEXT, l2 TEXT, cashflow TEXT, cat TEXT, activity TEXT,
        actual_flag TEXT, party_type TEXT, related TEXT, tag_src TEXT, src_file TEXT, confirmer TEXT,
        tagged_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")
    conn.execute("DELETE FROM dwd_flow_tagged")
    n = 0
    for r in rows:
        vals = [r.get(c) for c in _TAGGED_COLS]
        if vals[5] not in ("收", "支"):          # 方向认不出的行不入结果层
            continue
        conn.execute(
            "INSERT INTO dwd_flow_tagged(batch," + ",".join(_TAGGED_COLS) + ") VALUES(" +
            ",".join(["?"] * (len(_TAGGED_COLS) + 1)) + ")",
            [batch] + vals)
        n += 1
    conn.execute("INSERT INTO import_log(kind,file_name,rows,batch) VALUES('tagged',?,?,?)",
                 ("已打标流水", n, batch))
    conn.commit()
    conn.close()
    set_meta("active_batch_tagged", batch)
    return {"batch": batch, "rows": n}


def ensure_tagged_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dwd_flow_tagged (
        id INTEGER PRIMARY KEY AUTOINCREMENT, batch TEXT NOT NULL, month TEXT, entity TEXT, account TEXT,
        trade_date TEXT, trade_time TEXT, direction TEXT, amount REAL, balance REAL,
        counterparty TEXT, memo TEXT, l1 TEXT, l2 TEXT, cashflow TEXT, cat TEXT, activity TEXT,
        actual_flag TEXT, party_type TEXT, related TEXT, tag_src TEXT, src_file TEXT, confirmer TEXT,
        tagged_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))""")


def load_tagged():
    """读已打标流水；表为空时返回 []，调用方回退到 ods_flow。"""
    conn = connect()
    try:
        ensure_tagged_table(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM dwd_flow_tagged ORDER BY trade_date, id")]
    except Exception:
        rows = []
    conn.close()
    return rows

def _norm_acct(s):
    """账户明细列 / 文件名 → 银行简称；认不出就取前 12 个字符"""
    t = str(s or "").replace("\u3000", " ").strip()
    if not t:
        return "默认"
    banks = [("工行", ("工行", "工商银行")), ("招行", ("招行", "招商银行")),
             ("建行", ("建行", "建设银行")), ("中行", ("中行", "中国银行")),
             ("农行", ("农行", "农业银行")), ("交行", ("交行", "交通银行")),
             ("宁波银行", ("宁波银行",)), ("杭州银行", ("杭州银行",)),
             ("华夏银行", ("华夏银行", "华夏")), ("浦发银行", ("浦发",)),
             ("民生银行", ("民生银行", "民生")), ("兴业银行", ("兴业银行", "兴业")),
             ("平安银行", ("平安银行",)), ("北京银行", ("北京银行",)),
             ("上海银行", ("上海银行",)), ("广发银行", ("广发",)),
             ("邮储银行", ("邮储", "邮政储蓄"))]
    for name, keys in banks:
        for k in keys:
            if k in t:
                return name
    return t.split(" ")[0][:12]


def monthly_balances():
    """按「账户 × 月份」取该月最后一条流水余额，并汇总每月合计（各账户最后已知余额相加）"""
    import re as _re
    conn = connect()
    rows = conn.execute("SELECT row_json FROM ods_flow").fetchall()
    conn.close()
    last = {}
    months = {}
    for r in rows:
        try:
            d = json.loads(r["row_json"])
        except Exception:
            continue
        raw = d.get("余额")
        if raw in (None, ""):
            continue
        try:
            b = abs(float(str(raw).replace(",", "").replace("￥", "").replace("¥", "").strip()))
        except Exception:
            continue
        dt = str(d.get("交易日期") or d.get("日期") or "")
        m = _re.search(r"(20\d{2})[-/.年](\d{1,2})", dt)
        if not m:
            continue
        ym = "%s-%02d" % (m.group(1), int(m.group(2)))
        acct = _norm_acct(d.get("_acct") or d.get("账户") or d.get("账号") or "默认")
        key = dt + " " + str(d.get("交易时间") or "")
        k = (acct, ym)
        if k not in last or key >= last[k][0]:
            last[k] = (key, b)
        months.setdefault(ym, set()).add(acct)

    # 逐月推进：某账户该月无记录则沿用上次余额
    known = {}
    by_month = {}
    for ym in sorted(months):
        for acct in list(known):
            pass
        for (acct, m2), (key, b) in last.items():
            if m2 == ym:
                known[acct] = b
        row = {a: round(v, 2) for a, v in known.items()}
        row["_total"] = round(sum(row.values()), 2)
        by_month[ym] = row
    return {"byMonth": by_month, "accounts": sorted({a for a, _ in last})}
