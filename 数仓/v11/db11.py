# -*- coding: utf-8 -*-
"""db11.py —— v11 数仓访问层（SQLite）

唯一写入路径：
  批次(申请) → 逐笔落库(flow_raw) → 规则匹配 → 待确认池 → 人工确认(状态+反写规则)
  → 聚合视图(v_flow_actual / v_month_agg) → 各看板取数

口径：看板实际数只认 flow_raw.status='confirmed'（规则已匹配 + 人工确认）；
      draft / pending 单独存放并过滤，绝不计入。
"""
import os
import re
import json
import sqlite3
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
# 允许用环境变量指向测试库，避免测试污染工作库
DB_PATH = os.environ.get("V11_DB") or os.path.join(HERE, "warehouse_v11.db")
SCHEMA = os.path.join(HERE, "schema.sql")
SEED = os.path.join(HERE, "seed.json")
DICT = os.path.join(HERE, "dict_default.json")
SKILL = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills",
                     "bank-flow-recognition", "scripts")

# 22 项 → 现金流量表项目（映射表 seed 用，之后一律读库）
CF_ITEM = {
    "A01": ("一、经营活动产生的现金流量", "销售商品、提供劳务收到的现金"),
    "A02": ("一、经营活动产生的现金流量", "销售商品、提供劳务收到的现金"),
    "A03": ("一、经营活动产生的现金流量", "销售商品、提供劳务收到的现金"),
    "A04": ("一、经营活动产生的现金流量", "销售商品、提供劳务收到的现金"),
    "A06": ("一、经营活动产生的现金流量", "收到的其他与经营活动有关的现金"),
    "A99": ("一、经营活动产生的现金流量", "收到的其他与经营活动有关的现金"),
    "A07": ("三、筹资活动产生的现金流量", "收到的其他与筹资活动有关的现金（含存款利息）"),
    "B01": ("一、经营活动产生的现金流量", "购买商品、接受劳务支付的现金"),
    "B02": ("一、经营活动产生的现金流量", "购买商品、接受劳务支付的现金"),
    "B03": ("一、经营活动产生的现金流量", "支付给职工以及为职工支付的现金"),
    "B04": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B05": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B06": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B07": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B08": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B09": ("一、经营活动产生的现金流量", "支付的各项税费"),
    "B10": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B11": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "B99": ("一、经营活动产生的现金流量", "支付其他与经营活动有关的现金"),
    "C01": ("二、投资活动产生的现金流量", "收回投资、取得投资收益收到的现金"),
    "C03": ("二、投资活动产生的现金流量", "收回投资、取得投资收益收到的现金"),
    "C99": ("二、投资活动产生的现金流量", "收回投资、取得投资收益收到的现金"),
    "D01": ("二、投资活动产生的现金流量", "投资支付的现金"),
    "D02": ("二、投资活动产生的现金流量", "购建固定资产、无形资产支付的现金"),
    "D03": ("二、投资活动产生的现金流量", "投资支付的现金"),
    "E01": ("三、筹资活动产生的现金流量", "取得借款、吸收投资收到的现金"),
    "E02": ("三、筹资活动产生的现金流量", "收到的其他与筹资活动有关的现金（含存款利息）"),
    "F01": ("三、筹资活动产生的现金流量", "偿还债务支付的现金"),
    "F02": ("三、筹资活动产生的现金流量", "分配股利、利润或偿付利息支付的现金"),
    "F03": ("三、筹资活动产生的现金流量", "分配股利、利润或偿付利息支付的现金"),
    "G01": ("四、集团内单位资金往来", "集团内单位资金划入/划出"),
}
DIR_OF = {c: ("收" if c[0] in "ACE" else "支" if c[0] in "BDF" else "双向") for c in CF_ITEM}

RELATED = ("珀莱雅", "美丽谷", "珀妍")
CONSULT_ENT = ("凯缇丝", "青澜子衿", "闻芳叙雨")
GROUP_KEYS = ("珀研", "内曜", "昕棉", "聆汐", "闻芳叙雨", "凯缇丝", "青澜子衿",
              "融盈", "众益信", "雅都乐", "YADOLE")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn():
    c = sqlite3.connect(DB_PATH, timeout=8)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------- 初始化
def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        for f in (DB_PATH, DB_PATH + "-wal", DB_PATH + "-shm"):
            if os.path.exists(f):
                os.remove(f)
    c = conn()
    with open(SCHEMA, encoding="utf-8") as fh:
        c.executescript(fh.read())
    _ensure_columns(c)
    c.commit()
    seed(c)
    _ensure_columns(c)      # 种子写入后回填内部往来标识
    _seed_restricted(c)
    c.commit()
    c.close()


# 已有库平滑升级：缺列就补，不动数据
_COLUMNS = {
    "subject_map": [("is_internal", "INTEGER DEFAULT 0"), ("group_elim", "INTEGER DEFAULT 0")],
    "import_batch": [("updated_at", "TEXT"), ("confirmed_at", "TEXT"), ("confirmed_by", "TEXT"),
                     ("parent_id", "INTEGER")],
    "plan_batch": [("version", "INTEGER DEFAULT 1"), ("parent_id", "INTEGER"),
                   ("updated_at", "TEXT"), ("submitted_at", "TEXT"), ("submitted_by", "TEXT"),
                   ("reject_reason", "TEXT")],
    "flow_raw": [("restricted_kind", "TEXT")],
}

# 受限识别：类别 → 关键词（摘要 / 对方户名命中即打标签，不改科目分类）
RESTRICTED_KW = {
    "保证金": ["保证金", "保函", "履约保证", "投标保证", "质量保证", "保证金存款"],
    "未到期定期存款": ["定期存款", "通知存款", "大额存单", "结构性存款"],
    "境外受监管": ["监管账户", "境外账户", "NRA", "OSA", "FTN", "离岸账户"],
    "质押存款": ["质押", "存单质押"],
    "冻结": ["冻结", "查封", "止付"],
    "公管资产": ["房改", "维修基金", "公积金", "共管", "公管"],
}


def _seed_restricted(c):
    """受限关键词规则：幂等补种，不覆盖人工新增"""
    for kind, kws in RESTRICTED_KW.items():
        for kw in kws:
            c.execute("INSERT OR IGNORE INTO restricted_rule(kind,keyword,enabled,source,hit_count,created_at)"
                      " VALUES(?,?,1,'skill',0,?)", (kind, kw, now()))


def _ensure_columns(c):
    for table, cols in _COLUMNS.items():
        have = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        for name, ddl in cols:
            if name not in have:
                try:
                    c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))
                except Exception:
                    pass
    # 内部往来标识：按 l1 回填（幂等）
    c.execute("UPDATE subject_map SET is_internal=1, group_elim=1 WHERE l1='内部往来'")
    c.execute("UPDATE subject_map SET is_internal=0, group_elim=0 WHERE l1<>'内部往来'")
    _migrate_plan_batch_unique(c)


PLAN_BATCH_DDL = """CREATE TABLE plan_batch (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  entity      TEXT NOT NULL,
  period      TEXT NOT NULL,
  file_name   TEXT,
  n_rows      INTEGER DEFAULT 0,
  inflow      REAL DEFAULT 0,
  outflow     REAL DEFAULT 0,
  version     INTEGER DEFAULT 1,
  parent_id   INTEGER,
  status      TEXT DEFAULT 'parsed',
  operator    TEXT,
  created_at  TEXT,
  updated_at  TEXT,
  submitted_at TEXT,
  submitted_by TEXT,
  approved_at TEXT,
  approved_by TEXT,
  reject_reason TEXT
)"""


def _migrate_plan_batch_unique(c):
    """旧库在 plan_batch 上有 UNIQUE(entity,period,file_name)，同一文件重传/变更版本会直接报错。
    这里重建表去掉该约束（数据原样搬），只保留 ix_plan_batch_scope。"""
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='plan_batch'").fetchone()
    if not row or "UNIQUE" not in (row[0] or "").upper():
        return False
    cols = [r[1] for r in c.execute("PRAGMA table_info(plan_batch)")]
    collist = ",".join(cols)
    c.commit()
    c.execute("PRAGMA foreign_keys=OFF")
    # legacy_alter_table：RENAME 不去改写 plan_item 的外键指向，重命名后仍指向 plan_batch
    c.execute("PRAGMA legacy_alter_table=ON")
    try:
        c.execute("ALTER TABLE plan_batch RENAME TO plan_batch_old")
        c.execute(PLAN_BATCH_DDL)
        c.execute("INSERT INTO plan_batch(%s) SELECT %s FROM plan_batch_old" % (collist, collist))
        c.execute("DROP TABLE plan_batch_old")
        c.execute("CREATE INDEX IF NOT EXISTS ix_plan_batch_scope ON plan_batch(entity, period, status)")
        c.commit()
    finally:
        c.execute("PRAGMA legacy_alter_table=OFF")
        c.execute("PRAGMA foreign_keys=ON")
    return True


def _batch_seqs(c):
    """批次序号：按创建顺序稳定编号（不显示自增 id，避免删库后从 #2 开始）。"""
    out = {}
    for i, r in enumerate(c.execute("SELECT id FROM import_batch ORDER BY id ASC"), 1):
        out[r["id"]] = i
    return out


def seed(c):
    cur = c.cursor()
    if cur.execute("SELECT COUNT(*) FROM dim_entity").fetchone()[0]:
        return
    # 空库来源：seed.json（本地种子）。仓库不附带业务数据时退化为「一个占位主体 +
    # 全量科目/规则/汇率」，保证首次启动即可用；有 seed.json 时行为完全不变。
    data = {"entities": [], "flows": [], "plan": []}
    if os.path.exists(SEED):
        with open(SEED, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        print("[db11] 未找到 %s，按空库启动（只灌科目/规则/汇率）" % os.path.basename(SEED))
        cur.execute("INSERT OR IGNORE INTO dim_entity(short,name,segment,region,ccy,core,pending)"
                    " VALUES('DEMO','示例主体（请按需改名）','经营主体','—','CNY','',0)")
        cur.execute("INSERT OR IGNORE INTO entity_rule(entity,safe_line,updated_at) VALUES('DEMO',0,?)",
                    (now(),))

    for e in data["entities"]:
        cur.execute("INSERT OR REPLACE INTO dim_entity(short,name,segment,region,ccy,core,pending) VALUES(?,?,?,?,?,?,?)",
                    (e["short"], e["name"], e.get("segment", ""), e.get("region", ""),
                     e.get("ccy", "CNY"), e.get("core", ""), 1 if e.get("pending") else 0))
        for a in e.get("accts", []) or []:
            cur.execute("INSERT OR IGNORE INTO dim_account(entity,bank,account,note) VALUES(?,?,?,?)",
                        (e["short"], a["bank"], a.get("acct", ""), a.get("note", "")))
        cur.execute("INSERT OR IGNORE INTO entity_rule(entity,safe_line,updated_at) VALUES(?,0,?)",
                    (e["short"], now()))

    # 分类映射表
    for code, (l1, l2, sub) in _codes().items():
        sec, item = CF_ITEM.get(code, ("", ""))
        cur.execute("""INSERT OR REPLACE INTO subject_map(code,l1,l2,sub_detail,direction,plan_subject,cf_section,cf_item,sort)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (code, l1, l2, sub, DIR_OF.get(code, "双向"), l2, sec, item,
                     int(code[1:]) if code[1:].isdigit() else 99))

    # 匹配规则：优先从 skill 的 classify.KW 复制
    rules = _skill_rules()
    for pri, dr, kw, code, scope in rules:
        cur.execute("""INSERT INTO match_rule(pri,direction,scope,keyword,code,enabled,source,hit_count,created_at)
                       VALUES(?,?,?,?,?,1,'skill',0,?)""", (pri, dr, scope, kw, code, now()))

    # 汇率（人工维护表，种子里给两条基线，页面可改）
    for pair, rate, eff, op in (("USD/CNY", 6.68, "2026-11-01", "样表汇率"),
                                ("HKD/CNY", 0.917, "2026-11-01", "样表汇率")):
        cur.execute("INSERT OR IGNORE INTO fx_rate(pair,rate,effective_date,operator,created_at) VALUES(?,?,?,?,?)",
                    (pair, rate, eff, op, now()))

    # 流水：种子按「已确认」入库，归到一个历史批次
    for e, p in sorted({(f["e"], f["p"]) for f in data["flows"]}):
        cur.execute("""INSERT INTO import_batch(entity,period,kind,file_name,file_sha,n_rows,n_confirmed,n_pending,status,operator,created_at)
                       VALUES(?,?,'flow','种子数据：流水识别与科目分类_2026.07-09_v2.xlsx',?,0,0,0,'closed','系统导入',?)""",
                    (e, p, "seed-%s-%s" % (e, p), now()))
        bid = cur.lastrowid
        for f in [x for x in data["flows"] if x["e"] == e and x["p"] == p]:
            code = _code_of(cur, f)
            sm = cur.execute("SELECT * FROM subject_map WHERE code=?", (code,)).fetchone() if code else None
            pending = f["st"] == "pending" or not code
            cur.execute("""INSERT OR IGNORE INTO flow_raw(batch_id,entity,bank,account,txn_date,txn_time,direction,amount,balance,
                            counterparty,cp_account,memo,code,l1,l2,sub_detail,cf_section,cf_item,matched_by,confidence,status,
                            src_file,src_row,biz_key,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (bid, e, f["b"], f["ac"], f["d"], f["t"], f["dir"], f["amt"], f["bal"],
                         f["cp"], f["cpa"], f["memo"], code,
                         sm["l1"] if sm else "", sm["l2"] if sm else "", sm["sub_detail"] if sm else "",
                         sm["cf_section"] if sm else "", sm["cf_item"] if sm else "",
                         "rule" if pending is False else "", f.get("basis", ""),
                         "pending" if pending else "confirmed",
                         f["src"], int(f["id"][1:]) if f["id"][1:].isdigit() else 0, _biz_key(f["e"], f["ac"], f["d"], f["dir"], f["amt"], f["memo"], f["cp"], f["id"]),
                         now(), now()))
        cur.execute("UPDATE import_batch SET n_rows=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=?), n_confirmed=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=? AND status='confirmed') WHERE id=?", (bid, bid, bid))

    # 计划：按 (主体,期间) 建已审批批次
    for e in sorted({p["e"] for p in data["plan"]}):
        rows = [p for p in data["plan"] if p["e"] == e]
        for per in sorted({p["p"] for p in rows}):
            sub = [p for p in rows if p["p"] == per]
            inflow = sum(p["amt"] for p in sub if p["dir"] == "流入")
            outflow = sum(p["amt"] for p in sub if p["dir"] == "流出")
            cur.execute("""INSERT INTO plan_batch(entity,period,file_name,n_rows,inflow,outflow,status,operator,created_at,approved_at,approved_by)
                           VALUES(?,?,?,?,?,?,'approved','系统导入',?,?,'系统导入')""",
                        (e, per, "种子计划_%s_%s" % (e, per), len(sub), inflow, outflow, now(), now()))
            pid = cur.lastrowid
            for p in sub:
                cur.execute("""INSERT INTO plan_item(batch_id,entity,period,direction,l1,l2,code,amount,note,status)
                               VALUES(?,?,?,?,?,?,?,?,?,'approved')""",
                            (pid, e, per, p["dir"], p["l1"], p["l2"], p.get("code", ""), p["amt"], p.get("note", "")))
    bump_agg(c)
    c.commit()


def _dict_default():
    """仓库内的科目/规则兜底字典（不含任何业务数据），skill 不存在时用它"""
    try:
        with open(DICT, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _codes():
    """从 skill 的 classify.CF + SUBNAME 取 31 编码，避免两处维护；无 skill 时用 dict_default.json"""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_classify", os.path.join(SKILL, "classify.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return {c: (l1, l2, getattr(m, "SUBNAME", {}).get(c, "")) for c, (l1, l2) in m.CF.items()}
    except Exception:
        codes = _dict_default().get("codes") or {}
        if codes:
            return {c: (v[0], v[1], v[2] if len(v) > 2 else "") for c, v in codes.items()}
        return {"A01": ("经营活动", "货款收入", "货款收入-线下渠道"), "B99": ("经营活动", "其他支出", "其他经营流出")}


def _skill_rules():
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("skill_classify2", os.path.join(SKILL, "classify.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return [(pri, dr, rx, code, scope) for pri, dr, rx, code, scope in m.KW]
    except Exception:
        rules = _dict_default().get("rules") or []
        if rules:
            return [tuple(r) for r in rules]
        return [(1.0, "*", "珀研|内曜|昕棉|聆汐", "G01", "cp")]


def _code_of(cur, f):
    if f.get("st") == "pending":
        return ""
    row = cur.execute("SELECT code FROM subject_map WHERE l1=? AND l2=?", (f.get("l1"), f.get("l2"))).fetchone()
    return row["code"] if row else ""


def _biz_key(entity, account, date, direction, amount, memo, cp, seq=0):
    # 含文件内行序：同一天两笔同额同对手的真实交易不会被并掉，重复导入仍能命中同一条
    return "|".join([str(entity), str(account or ""), str(date), str(direction),
                     ("%.2f" % float(amount or 0)), str(memo or "")[:40], str(cp or "")[:40], str(seq)])


def bump_agg(c=None):
    own = c is None
    c = c or conn()
    c.execute("INSERT INTO agg_meta(k,v) VALUES('agg_at',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (now(),))
    if own:
        c.commit()
        c.close()


# ---------------------------------------------------------------- 分类
def classify(c, dir_, cp, memo, entity, amount=0):
    """规则来自 match_rule 表（可人工反写），命中返回 (code, rule_id, 依据)"""
    text_cp = str(cp or "")
    text_all = re.sub(r"\s+", "", text_cp + str(memo or ""))
    text_up = text_all.upper()
    # 关联方回款按主体性质分流（这条是硬逻辑，不是关键词）
    if dir_ == "收" and any(k in text_all for k in RELATED):
        code = "A03" if entity in CONSULT_ENT else "A01"
        return code, None, "关联方回款，按主体业务性质归集"
    rows = c.execute("SELECT * FROM match_rule WHERE enabled=1 ORDER BY pri ASC, id ASC").fetchall()
    hit = None
    for r in rows:
        if r["direction"] not in ("*", dir_):
            continue
        # person 只能看户名本身，不能把摘要拼进来，否则 ^…{2,4}$ 永远不匹配
        target = text_cp if r["scope"] in ("cp", "person") else text_up
        if r["scope"] == "person" and not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", text_cp.strip()):
            continue
        try:
            if not re.search(r["keyword"], target, re.I if r["scope"] == "cp" else 0):
                continue
        except re.error:
            continue
        head = r["code"][0]
        if head in "ACE" and dir_ != "收":
            continue
        if head in "BDF" and dir_ != "支":
            continue
        if r["code"] == "G01" and not any(k in text_all for k in GROUP_KEYS):
            continue
        hit = r
        break
    if hit:
        c.execute("UPDATE match_rule SET hit_count=hit_count+1, last_hit=? WHERE id=?", (now(), hit["id"]))
        return hit["code"], hit["id"], "规则命中(p%s)" % hit["pri"]
    if abs(float(amount or 0)) <= 1:
        return ("A99" if dir_ == "收" else "B99"), None, "小额（≤1元）鉴权/验证款"
    return None, None, "规则未命中，留空待人工确认"


def restricted_kind_of(c, cp, memo, is_internal=0):
    """受限资金自动识别：摘要 + 对方户名命中 restricted_rule 关键词 → 返回受限类别。
    内部往来一律不打受限标签（两类标签不混用）；科目分类不受影响。"""
    if is_internal:
        return ""
    text = re.sub(r"\s+", "", str(cp or "") + str(memo or "")).upper()
    if not text:
        return ""
    for r in c.execute("SELECT id,kind,keyword FROM restricted_rule WHERE enabled=1 ORDER BY id"):
        kw = (r["keyword"] or "").upper().strip()
        if kw and kw in text:
            c.execute("UPDATE restricted_rule SET hit_count=hit_count+1, last_hit=? WHERE id=?", (now(), r["id"]))
            return r["kind"]
    return ""


def restricted_rule_add(kind, keyword, source="manual"):
    kind = (kind or "").strip()
    keyword = (keyword or "").strip()
    if not kind or not keyword:
        return {"error": "类别与关键词都不能为空"}
    c = conn()
    if c.execute("SELECT 1 FROM restricted_rule WHERE kind=? AND keyword=?", (kind, keyword)).fetchone():
        c.close()
        return {"error": "「%s」下已有关键词「%s」" % (kind, keyword)}
    c.execute("INSERT INTO restricted_rule(kind,keyword,enabled,source,hit_count,created_at) VALUES(?,?,1,?,0,?)",
              (kind, keyword, source, now()))
    c.commit()
    rules = [dict(r) for r in c.execute("SELECT * FROM restricted_rule ORDER BY kind, id")]
    c.close()
    bump_agg()
    return {"ok": True, "rules": rules}


# ---------------------------------------------------------------- 批次与流水
def create_batch(entity, period, file_name, sha, kind="flow", operator="操作员"):
    c = conn()
    row = c.execute("SELECT * FROM import_batch WHERE entity=? AND period=? AND file_sha=?",
                    (entity, period, sha)).fetchone()
    if row:
        c.close()
        return dict(row), True
    c.execute("""INSERT INTO import_batch(entity,period,kind,file_name,file_sha,n_rows,n_confirmed,n_pending,status,operator,created_at)
                 VALUES(?,?,?,?,?,0,0,0,'draft',?,?)""", (entity, period, kind, file_name, sha, operator, now()))
    bid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.commit()
    row = c.execute("SELECT * FROM import_batch WHERE id=?", (bid,)).fetchone()
    c.close()
    return dict(row), False


def _is_internal_subject(c, l1, l2):
    row = c.execute("SELECT is_internal FROM subject_map WHERE l1=? AND l2=?",
                    (l1 or "", l2 or "")).fetchone()
    return (row["is_internal"] if row else 0) or 0


def _repair_dup(c, old, r, key):
    """重复行不重复落库，但把这次新识别出的字段补回旧行：只补空值，不动已确认的科目。"""
    sets, vals = [], []
    cp = (r.get("cp") or "").strip()
    cpa = (r.get("cpa") or "").strip()
    fill_cp = bool(cp) and not (old["counterparty"] or "").strip()
    if fill_cp:
        sets.append("counterparty=?")
        vals.append(cp)
    if cpa and not (old["cp_account"] or "").strip():
        sets.append("cp_account=?")
        vals.append(cpa)
    if old["balance"] is None and r.get("balance") is not None:
        sets.append("balance=?")
        vals.append(r.get("balance"))
    # 旧版本把「收(付)方账号」当成「本方账号」落了库：这次本方账号识别为空、
    # 且旧值正好等于对方账号 → 清掉，避免账户表里出现一堆对方卡号
    acct = str(r.get("account") or "")
    clear_acct = bool(old["account"]) and not acct and old["account"] == cpa
    if clear_acct:
        sets.append("account=?")
        vals.append("")
    if not sets:
        return 0
    if fill_cp:
        # 对方户名变了会影响受限资金识别，就地重算标签（不动科目）
        cur = c.execute("SELECT l1,l2 FROM flow_raw WHERE id=?", (old["id"],)).fetchone()
        rk = restricted_kind_of(c, cp, r.get("memo", ""),
                                _is_internal_subject(c, cur["l1"] if cur else "", cur["l2"] if cur else ""))
        sets.append("restricted_kind=?")
        vals.append(rk)
    sets += ["biz_key=?", "updated_at=?"]
    vals += [key, now(), old["id"]]
    c.execute("UPDATE flow_raw SET %s WHERE id=?" % ", ".join(sets), vals)
    return 1


def import_rows(batch_id, rows, operator="操作员", submit=True):
    """逐笔落库。submit=True 时：规则命中的直接 confirmed，未命中进 pending；
    submit=False 时整批 draft（不进看板）。"""
    c = conn()
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (batch_id,)).fetchone()
    if not b:
        c.close()
        return {"error": "批次不存在"}
    inserted = pending = dup = repaired = 0
    skipped_period = 0
    skipped_months = {}
    dup_rows, errors = [], []
    sm_cache = {}
    accmap = {(x["entity"], x["account"] or ""): x["bank"] for x in c.execute("SELECT entity,account,bank FROM dim_account")}
    first_bank = {x["entity"]: x["bank"] for x in c.execute("SELECT entity,bank FROM dim_account GROUP BY entity")}
    # 仅按账号反查银行（跨主体时提示，不静默改主体）
    acc_any = {}
    for x in c.execute("SELECT entity,account,bank FROM dim_account"):
        digits = re.sub(r"\D", "", x["account"] or "")
        if digits:
            acc_any[digits] = (x["entity"], x["bank"])
    for i, r in enumerate(rows):
        try:
            entity = r.get("entity") or b["entity"]
            period = r.get("period") or b["period"]
            date = r.get("date")
            if not date or not period:
                errors.append("第 %d 行缺日期/期间" % (i + 1))
                continue
            # 只收批次所属期间的流水：文件里其他月份的行跳过（计入 skippedPeriod）
            month = str(date)[:7]
            if b["period"] and month != b["period"]:
                skipped_period += 1
                skipped_months[month] = skipped_months.get(month, 0) + 1
                continue
            amount = float(r.get("amount") or 0)
            dir_ = r.get("dir") or "收"
            key = _biz_key(entity, r.get("account", ""), date, dir_, amount, r.get("memo", ""), r.get("cp", ""), r.get("src_row") or (i + 1))
            old = c.execute("SELECT id,account,counterparty,cp_account,balance,biz_key FROM flow_raw WHERE biz_key=?",
                            (key,)).fetchone()
            if not old:
                # 表头识别升级（对方户名改从「收(付)方名称」取、本方账号不再误取对方账号）后，
                # 同一笔流水会算出不同的 biz_key：按业务字段再认一次，命中就补字段，不重复落库
                old = c.execute("""SELECT id,account,counterparty,cp_account,balance,biz_key FROM flow_raw
                                   WHERE entity=? AND txn_date=? AND direction=? AND abs(amount-?)<0.005
                                     AND substr(IFNULL(memo,''),1,40)=? AND IFNULL(src_row,0)=?
                                   LIMIT 1""",
                                (entity, date, dir_, amount, str(r.get("memo", "") or "")[:40],
                                 r.get("src_row") or (i + 1))).fetchone()
            if old:
                dup += 1
                dup_rows.append({"row": r.get("src_row") or i + 1, "date": date, "amount": amount,
                                 "cp": r.get("cp", ""), "memo": r.get("memo", "")})
                repaired += _repair_dup(c, old, r, key)
                continue
            code, rule_id, basis = classify(c, dir_, r.get("cp", ""), r.get("memo", ""), entity, amount)
            if code not in sm_cache:
                sm_cache[code] = c.execute("SELECT * FROM subject_map WHERE code=?", (code,)).fetchone() if code else None
            sm = sm_cache[code]
            # 受限标签：内部往来不参与识别（两类标签不混用）
            rk = restricted_kind_of(c, r.get("cp", ""), r.get("memo", ""),
                                    (sm["is_internal"] if sm else 0) or 0)
            status = "draft" if not submit else ("confirmed" if code else "pending")
            conf = "高" if code and rule_id else ("中" if code else "未知")
            bank = r.get("bank") or accmap.get((entity, r.get("account") or ""), "")
            if not bank and r.get("account"):
                hit = acc_any.get(re.sub(r"\D", "", str(r.get("account"))))
                if hit:
                    bank = hit[1]
                    if hit[0] != entity:
                        errors.append("第 %d 行账号属于 %s，与批次主体 %s 不一致，请核对" % (i + 1, hit[0], entity))
            bank = bank or first_bank.get(entity, "")
            c.execute("""INSERT INTO flow_raw(batch_id,entity,bank,account,txn_date,txn_time,direction,amount,balance,
                          counterparty,cp_account,memo,code,l1,l2,sub_detail,cf_section,cf_item,matched_by,rule_id,confidence,
                          restricted_kind,status,src_file,src_row,biz_key,created_at,updated_at)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (batch_id, entity, bank, r.get("account", ""), date, r.get("time", ""),
                       dir_, amount, r.get("balance"),
                       r.get("cp", ""), r.get("cpa", ""), r.get("memo", ""),
                       code or "", sm["l1"] if sm else "", sm["l2"] if sm else "", sm["sub_detail"] if sm else "",
                       sm["cf_section"] if sm else "", sm["cf_item"] if sm else "",
                       "rule" if code else "", rule_id, conf, rk, status,
                       r.get("src", b["file_name"]), r.get("src_row") or i + 1, key, now(), now()))
            inserted += 1
            if status == "pending":
                pending += 1
        except Exception as ex:
            errors.append("第 %d 行：%s" % (i + 1, ex))
    # 一笔都没进（全被判重/全失败）的批次不留空壳，避免批次列表里出现 0 行批次
    n_rows = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=?", (batch_id,)).fetchone()["n"]
    dropped = False
    if n_rows == 0:
        c.execute("DELETE FROM import_batch WHERE id=?", (batch_id,))
        dropped = True
    elif submit:
        # 导入即确认：整批都命中规则（无待确认）时批次直接置 confirmed，
        # 否则会卡在 parsed 且「全部确认」是灰的，没有任何按钮能把它置为已确认
        _refresh_batch(c, batch_id, allow_confirm=True, operator=operator)
    else:
        _refresh_batch(c, batch_id)
    bump_agg(c)
    c.commit()
    c.close()
    return {"inserted": inserted, "pending": pending, "dup": dup, "dupRows": dup_rows,
            "repaired": repaired,
            "errors": errors, "batchDropped": dropped,
            "skippedPeriod": skipped_period, "skippedMonths": skipped_months}


def _refresh_batch(c, bid, allow_confirm=False, operator=None):
    """重算批次行数/已确认/待确认，并把批次状态与记录状态对齐。
    allow_confirm=True：整批没有未确认记录时置 confirmed（「确认所选」把全批确认完的场景）；
    原本 confirmed 的批次新增了未确认记录 → 退回 parsed，不再声称已确认。"""
    if not bid:
        return None
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (bid,)).fetchone()
    if not b:
        return None
    n_rows = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=?", (bid,)).fetchone()["n"]
    n_conf = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=? AND status='confirmed'", (bid,)).fetchone()["n"]
    n_pend = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=? AND status='pending'", (bid,)).fetchone()["n"]
    left = n_rows - n_conf
    st, confirmed_at, confirmed_by = b["status"], b["confirmed_at"], b["confirmed_by"]
    if allow_confirm and left == 0 and n_rows > 0:
        st = "confirmed"
        confirmed_at = confirmed_at or now()
        confirmed_by = confirmed_by or operator
    elif b["status"] == "draft" and allow_confirm and n_rows > 0:
        st = "parsed"          # 导入完成即从草稿推进到可处理
    elif b["status"] == "confirmed" and left > 0:
        st = "parsed"
    c.execute("""UPDATE import_batch SET n_rows=?,n_confirmed=?,n_pending=?,status=?,updated_at=?,
                 confirmed_at=?,confirmed_by=? WHERE id=?""",
              (n_rows, n_conf, n_pend, st, now(), confirmed_at, confirmed_by, bid))
    # 变更批次确认生效：父批次及其记录退出看板实际数，历史保留
    if st == "confirmed":
        _supersede_parent(c, b)
    return st


def _supersede_parent(c, b):
    """变更批次确认生效 → 父批次及其记录退出看板（历史保留）。幂等。"""
    if not b["parent_id"] or b["status"] == "confirmed":
        return False
    c.execute("UPDATE flow_raw SET status='superseded', updated_at=? WHERE batch_id=?",
              (now(), b["parent_id"]))
    c.execute("UPDATE import_batch SET status='superseded', updated_at=? WHERE id=?",
              (now(), b["parent_id"]))
    return True


def confirm_flows(ids, code, operator="操作员"):
    """人工确认：事务内更新状态 + 反写规则 + 留痕 + 聚合刷新"""
    c = conn()
    sm = c.execute("SELECT * FROM subject_map WHERE code=?", (code,)).fetchone()
    if not sm:
        c.close()
        return {"error": "科目编码不存在"}
    done, rules_back, batches = 0, 0, set()
    for fid in ids:
        f = c.execute("SELECT * FROM flow_raw WHERE id=?", (fid,)).fetchone()
        if not f:
            continue
        if f["status"] == "confirmed" and f["code"] == code:
            continue  # 已确认且科目未变，不重复确认、不改写 matched_by
        rk = restricted_kind_of(c, f["counterparty"], f["memo"], sm["is_internal"] or 0)
        c.execute("""UPDATE flow_raw SET code=?,l1=?,l2=?,sub_detail=?,cf_section=?,cf_item=?,restricted_kind=?,
                     matched_by='manual',confidence='人工确认',status='confirmed',modified=0,updated_at=? WHERE id=?""",
                  (code, sm["l1"], sm["l2"], sm["sub_detail"], sm["cf_section"], sm["cf_item"], rk, now(), fid))
        c.execute("""INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,created_at)
                     VALUES('flow',?,'confirm','status,l2',?,?,?,?)""",
                  (fid, "%s/%s" % (f["status"], f["l2"] or ""), "confirmed/%s" % sm["l2"], operator, now()))
        # 反写规则：用对手方做一条精确规则（低优先、可停用）
        cp = (f["counterparty"] or "").strip()
        if cp and len(cp) >= 4 and not c.execute(
                "SELECT 1 FROM match_rule WHERE keyword=? AND code=? AND source='manual_back'", (re.escape(cp), code)).fetchone():
            c.execute("""INSERT INTO match_rule(pri,direction,scope,keyword,code,enabled,source,hit_count,created_at)
                         VALUES(0.45,?, 'cp', ?, ?, 1, 'manual_back', 0, ?)""",
                      (f["direction"], re.escape(cp), code, now()))
            rules_back += 1
        if f["batch_id"]:
            batches.add(int(f["batch_id"]))
        done += 1
    for bid in batches:
        _refresh_batch(c, bid, allow_confirm=True, operator=operator)
    bump_agg(c)
    c.commit()
    c.close()
    return {"confirmed": done, "rulesBack": rules_back}


def save_flow_assignments(batch_id, items, operator="操作员"):
    """人工识别「保存」：写科目与状态标记，不进看板实际数（仍为未确认）。
    items = [{id, code}]，code='' 表示清空科目回到未识别。

    与单条「修改」同口径：
      - 科目没有实际变化 → 不写、不标脏；已确认的仍是已确认
        （不能因为点一次「保存」就把整批已确认记录变成「未确认（已修改）」）
      - 已确认的行真的改了科目 → pending + modified=1，留痕，批次退回 parsed，重新确认才更新看板
      - 未确认的行 → 只写科目，保持 pending
    """
    c = conn()
    sm_cache, saved, changed, dirty_batches = {}, 0, 0, set()
    for it in items:
        fid, code = int(it["id"]), (it.get("code") or "").strip()
        f = c.execute("SELECT * FROM flow_raw WHERE id=?", (fid,)).fetchone()
        if not f:
            continue
        if code:
            if code not in sm_cache:
                sm_cache[code] = c.execute("SELECT * FROM subject_map WHERE code=?", (code,)).fetchone()
            sm = sm_cache[code]
            if not sm:
                continue
            new_vals = (code, sm["l1"], sm["l2"], sm["sub_detail"], sm["cf_section"], sm["cf_item"])
        else:
            new_vals = ("", "", "", "", "", "")
        old_vals = (f["code"] or "", f["l1"] or "", f["l2"] or "", f["sub_detail"] or "",
                    f["cf_section"] or "", f["cf_item"] or "")
        if new_vals == old_vals:
            saved += 1          # 选的就是库里已有的科目：不写、不标脏
            continue
        rk = restricted_kind_of(c, f["counterparty"], f["memo"], (sm["is_internal"] if code else 0) or 0)
        if f["status"] == "confirmed":
            c.execute("""UPDATE flow_raw SET code=?,l1=?,l2=?,sub_detail=?,cf_section=?,cf_item=?,restricted_kind=?,
                         matched_by='manual', status='pending', modified=1, updated_at=? WHERE id=?""",
                      new_vals + (rk, now(), fid))
            if f["batch_id"]:
                dirty_batches.add(int(f["batch_id"]))
        else:
            c.execute("""UPDATE flow_raw SET code=?,l1=?,l2=?,sub_detail=?,cf_section=?,cf_item=?,restricted_kind=?,
                         matched_by='manual', updated_at=? WHERE id=?""",
                      new_vals + (rk, now(), fid))
        c.execute("""INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,created_at)
                     VALUES('flow',?,'edit','code/l2',?,?,?,?)""",
                  (fid, "%s/%s" % (old_vals[0], old_vals[2]), "%s/%s" % (new_vals[0], new_vals[2]), operator, now()))
        saved += 1
        changed += 1
    # 已确认记录被改动 → 批次退回 parsed（与单条「修改」口径一致）并重算计数
    for bid in dirty_batches:
        _refresh_batch(c, bid)
    if changed and batch_id and int(batch_id) not in dirty_batches:
        c.execute("UPDATE import_batch SET updated_at=? WHERE id=?", (now(), int(batch_id)))
    bump_agg(c)
    c.commit()
    c.close()
    return {"saved": saved, "changed": changed, "at": now()}


def confirm_batch(batch_id, operator="操作员"):
    """「全部确认」：把本批所有「已识别」（code 非空）但未确认的记录整批置 confirmed。
    不受页面筛选影响；未识别记录保持待确认，批次状态按剩余情况判定。"""
    c = conn()
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (batch_id,)).fetchone()
    if not b:
        c.close()
        return {"error": "批次不存在"}
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM flow_raw WHERE batch_id=? AND status<>'confirmed' AND IFNULL(code,'')<>''", (batch_id,))]
    done, rules_back = 0, 0
    for f in rows:
        sm = c.execute("SELECT * FROM subject_map WHERE code=?", (f["code"],)).fetchone()
        c.execute("""UPDATE flow_raw SET status='confirmed', modified=0, matched_by=?, updated_at=?
                     WHERE id=?""", ("rule" if f["matched_by"] == "rule" else "manual", now(), f["id"]))
        c.execute("""INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,created_at)
                     VALUES('flow',?,'confirm','status',?,?,?,?)""",
                  (f["id"], f["status"], "confirmed", operator, now()))
        cp = (f["counterparty"] or "").strip()
        if cp and len(cp) >= 4 and sm and not c.execute(
                "SELECT 1 FROM match_rule WHERE keyword=? AND code=? AND source='manual_back'",
                (re.escape(cp), f["code"])).fetchone():
            c.execute("""INSERT INTO match_rule(pri,direction,scope,keyword,code,enabled,source,hit_count,created_at)
                         VALUES(0.45,?, 'cp', ?, ?, 1, 'manual_back', 0, ?)""",
                      (f["direction"], re.escape(cp), f["code"], now()))
            rules_back += 1
        done += 1
    left = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=? AND status<>'confirmed'",
                     (batch_id,)).fetchone()["n"]
    # 点过「全部确认」且确实确认了记录 → 批次置 confirmed（申请层面只读）；
    # 仍可能有未识别记录，留在批次里继续处理，通过 n_pending 体现
    st = "confirmed" if (done > 0 or left == 0) else "parsed"
    c.execute("""UPDATE import_batch SET n_rows=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=?),
                 n_confirmed=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=? AND status='confirmed'),
                 n_pending=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=? AND status='pending'),
                 status=?, updated_at=?, confirmed_at=?, confirmed_by=? WHERE id=?""",
              (batch_id, batch_id, batch_id, st, now(), now() if st == "confirmed" else None,
               operator if st == "confirmed" else None, batch_id))
    if st == "confirmed":
        _supersede_parent(c, b)
    bump_agg(c)
    c.commit()
    c.close()
    return {"confirmed": done, "remaining": left, "status": st, "rulesBack": rules_back, "at": now()}


def batch_status(batch_id):
    c = conn()
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (batch_id,)).fetchone()
    c.close()
    return dict(b) if b else None


def flow_change(batch_id, operator="操作员"):
    """已确认的流水批次「发起变更」：复制一份为新申请（记录回待确认），
    新批次重新确认后，原批次自动退出看板（superseded），历史保留。"""
    c = conn()
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (batch_id,)).fetchone()
    if not b:
        c.close()
        return {"error": "批次不存在"}
    st = b["status"]
    if st == "superseded":
        c.close()
        return {"error": "该批次已被变更版本取代，历史只读"}
    if st != "confirmed":
        c.close()
        return {"error": "只有「已确认」批次需要发起变更，当前为 %s" % st}
    pend = c.execute("""SELECT * FROM import_batch WHERE parent_id=? AND status IN ('parsed','draft')
                        ORDER BY id DESC LIMIT 1""", (batch_id,)).fetchone()
    if pend:
        c.close()
        return {"batch": pend["id"], "reused": True}
    n = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=?", (batch_id,)).fetchone()["n"]
    c.execute("""INSERT INTO import_batch(entity,period,kind,file_name,file_sha,n_rows,n_confirmed,n_pending,
                 status,operator,created_at,updated_at,parent_id)
                 VALUES(?,?,'flow',?,?,?,0,?,'parsed',?,?,?,?)""",
              (b["entity"], b["period"], b["file_name"], "change:%s:%s" % (batch_id, now()),
               n, n, operator, now(), now(), batch_id))
    nid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.execute("""INSERT INTO flow_raw(batch_id,entity,bank,account,txn_date,txn_time,direction,amount,balance,
                 counterparty,cp_account,memo,code,l1,l2,sub_detail,cf_section,cf_item,matched_by,rule_id,confidence,
                 status,modified,src_file,src_row,biz_key,created_at,updated_at)
                 SELECT ?,entity,bank,account,txn_date,txn_time,direction,amount,balance,
                 counterparty,cp_account,memo,code,l1,l2,sub_detail,cf_section,cf_item,matched_by,rule_id,confidence,
                 'pending',0,src_file,src_row,IFNULL(biz_key,'')||':chg'||?,?,?
                 FROM flow_raw WHERE batch_id=?""", (nid, nid, now(), now(), batch_id))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,operator,reason,created_at)
                 VALUES('import_batch',?,'change',?,?,?)""",
              (nid, operator, "由批次 #%s 发起变更" % batch_id, now()))
    bump_agg(c)
    c.commit()
    c.close()
    return {"batch": nid, "rows": n}


def edit_flow(fid, patch, operator="操作员", reason=""):
    c = conn()
    f = c.execute("SELECT * FROM flow_raw WHERE id=?", (fid,)).fetchone()
    if not f:
        c.close()
        return {"error": "流水不存在"}
    fields = [k for k in ("txn_date", "direction", "amount", "counterparty", "memo", "code", "l1", "l2") if k in patch]
    for k in fields:
        c.execute("INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,reason,created_at)"
                  " VALUES('flow',?, 'edit', ?, ?, ?, ?, ?, ?)",
                  (fid, k, str(f[k]), str(patch[k]), operator, reason, now()))
    sets = ", ".join("%s=?" % k for k in fields)
    vals = [patch[k] for k in fields]
    c.execute("UPDATE flow_raw SET %s, status='pending', modified=1, updated_at=? WHERE id=?" % sets, vals + [now(), fid])
    # 摘要/对方/科目改动后重算受限标签
    g = c.execute("SELECT * FROM flow_raw WHERE id=?", (fid,)).fetchone()
    smk = c.execute("SELECT is_internal FROM subject_map WHERE code=?", (g["code"] or "",)).fetchone()
    c.execute("UPDATE flow_raw SET restricted_kind=? WHERE id=?",
              (restricted_kind_of(c, g["counterparty"], g["memo"], (smk["is_internal"] if smk else 0) or 0), fid))
    # 已确认批次里做了修改 → 批次退回 parsed（等待重新确认），申请层面恢复可编辑提示
    if f["batch_id"]:
        c.execute("UPDATE import_batch SET status=CASE WHEN status='confirmed' THEN 'parsed' ELSE status END, "
                  "n_confirmed=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=? AND status='confirmed'), "
                  "n_pending=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=? AND status='pending'), updated_at=? WHERE id=?",
                  (f["batch_id"], f["batch_id"], now(), f["batch_id"]))
    bump_agg(c)
    c.commit()
    c.close()
    return {"ok": True, "fields": fields}


# ---------------------------------------------------------------- 计划
# 状态机：parsed（可编辑）→ submitted（审批中，可撤回）→ approved（已入数仓）
#         approved 之后要改 → 发起「变更」生成 version+1 的新批次（parsed），旧版继续生效到新版审批通过
PLAN_EDITABLE = ("parsed", "rejected")


def _plan_totals(rows):
    inflow = sum(float(r["amount"]) for r in rows if r["direction"] == "流入")
    outflow = sum(float(r["amount"]) for r in rows if r["direction"] == "流出")
    return inflow, outflow


def _plan_write_items(c, bid, entity, period, rows, status):
    for r in rows:
        c.execute("""INSERT INTO plan_item(batch_id,entity,period,direction,l1,l2,code,amount,note,status)
                     VALUES(?,?,?,?,?,?,?,?,?,?)""",
                  (bid, r.get("entity") or entity, r.get("period") or period, r["direction"],
                   r.get("l1", ""), r.get("l2", ""), r.get("code", ""), float(r["amount"]),
                   r.get("note", ""), status))


def plan_import(entity, period, file_name, rows, operator="操作员", approve=False):
    """上传校验通过后落库（默认 parsed：可编辑、不参与预实）。

    同一主体同一期间重复上传不报唯一约束错：
      - 有「审批中」批次 → 返回错误，提示先撤回/驳回
      - 有可编辑批次 → 覆盖该批次明细（不新增申请记录）
      - 只有「已审批」版本 → 自动按「发起变更」生成 v+1 的可编辑批次，审批通过后覆盖口径
    """
    c = conn()
    inflow, outflow = _plan_totals(rows)
    same = [dict(r) for r in c.execute(
        "SELECT * FROM plan_batch WHERE entity=? AND period=? ORDER BY id DESC", (entity, period))]
    sub = next((x for x in same if x["status"] == "submitted"), None)
    if sub and not approve:
        c.close()
        return {"error": "该期间已有审批中的计划批次 #%s，请先「撤回」或「驳回」后再上传" % sub["id"]}
    draft = next((x for x in same if x["status"] in PLAN_EDITABLE), None)
    if draft and not approve:
        c.execute("DELETE FROM plan_item WHERE batch_id=?", (draft["id"],))
        _plan_write_items(c, draft["id"], entity, period, rows, draft["status"])
        c.execute("""UPDATE plan_batch SET file_name=?, n_rows=?, inflow=?, outflow=?,
                     operator=COALESCE(NULLIF(?,''), operator), updated_at=? WHERE id=?""",
                  (file_name, len(rows), inflow, outflow, operator, now(), draft["id"]))
        c.execute("""INSERT INTO audit_log(target_type,target_id,action,operator,created_at)
                     VALUES('plan_batch',?,'reupload',?,?)""", (draft["id"], operator, now()))
        bump_agg(c)
        c.commit()
        c.close()
        return {"batch": draft["id"], "status": draft["status"], "rows": len(rows),
                "reused": True, "at": now()}
    approved = next((x for x in same if x["status"] == "approved"), None)
    if approved and not approve:
        c.close()
        return {"error": "该期间已有已审批计划 #%s，请先「撤回」后再重新导入（撤回后该版本退出预实，重新导入为覆盖）" % approved["id"]}
    status = "approved" if approve else "parsed"
    c.execute("""INSERT INTO plan_batch(entity,period,file_name,n_rows,inflow,outflow,version,status,
                 operator,created_at,updated_at,approved_at,approved_by)
                 VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?)""",
              (entity, period, file_name, len(rows), inflow, outflow, status, operator, now(), now(),
               now() if approve else None, operator if approve else None))
    bid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    _plan_write_items(c, bid, entity, period, rows, status)
    if approve:
        plan_approve(bid, operator, _c=c)
    bump_agg(c)
    c.commit()
    c.close()
    return {"batch": bid, "status": status, "rows": len(rows), "at": now()}


def _plan_batch(c, bid):
    return c.execute("SELECT * FROM plan_batch WHERE id=?", (bid,)).fetchone()


def plan_update_rows(bid, rows, operator="操作员"):
    """编辑可编辑状态下的计划批次（整批替换明细）。"""
    c = conn()
    b = _plan_batch(c, bid)
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    if b["status"] not in PLAN_EDITABLE:
        c.close()
        return {"error": "该批次状态为 %s，不可直接编辑（审批中请先撤回，已审批请发起变更）" % b["status"]}
    inflow, outflow = _plan_totals(rows)
    c.execute("DELETE FROM plan_item WHERE batch_id=?", (bid,))
    _plan_write_items(c, bid, b["entity"], b["period"], rows, b["status"])
    c.execute("UPDATE plan_batch SET n_rows=?, inflow=?, outflow=?, updated_at=? WHERE id=?",
              (len(rows), inflow, outflow, now(), bid))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,operator,created_at)
                 VALUES('plan_batch',?,'update_rows',?,?)""", (bid, operator, now()))
    bump_agg(c)
    c.commit()
    c.close()
    return {"ok": True, "rows": len(rows), "at": now()}


def plan_submit(bid, operator="操作员"):
    c = conn()
    b = _plan_batch(c, bid)
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    if b["status"] not in PLAN_EDITABLE:
        c.close()
        return {"error": "当前状态 %s 不能提交审批" % b["status"]}
    c.execute("""UPDATE plan_batch SET status='submitted', submitted_at=?, submitted_by=?, updated_at=?,
                 reject_reason=NULL WHERE id=?""", (now(), operator, now(), bid))
    c.execute("UPDATE plan_item SET status='submitted' WHERE batch_id=?", (bid,))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,before_val,after_val,operator,created_at)
                 VALUES('plan_batch',?,'submit',?,'submitted',?,?)""", (bid, b["status"], operator, now()))
    c.commit()
    c.close()
    return {"ok": True, "status": "submitted", "at": now()}


def plan_withdraw(bid, operator="操作员", reason="撤回修改"):
    c = conn()
    b = _plan_batch(c, bid)
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    if b["status"] not in ("submitted", "approved"):
        c.close()
        return {"error": "只有「审批中」或「已审批」的批次可以撤回，当前为 %s" % b["status"]}
    before = b["status"]
    # 撤回 = 终态：批次置 withdrawn，不可再编辑，需重新建申请上传
    c.execute("""UPDATE plan_batch SET status='withdrawn', approved_at=NULL, approved_by=NULL, updated_at=?
                 WHERE id=?""", (now(), bid))
    c.execute("UPDATE plan_item SET status='withdrawn' WHERE batch_id=?", (bid,))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,before_val,after_val,operator,reason,created_at)
                 VALUES('plan_batch',?,'withdraw',?,'withdrawn',?,?,?)""",
              (bid, before, operator, reason, now()))
    bump_agg(c)
    c.commit()
    c.close()
    return {"ok": True, "status": "withdrawn", "from": before, "at": now()}


def plan_reject(bid, reason="", operator="审批人"):
    c = conn()
    b = _plan_batch(c, bid)
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    if b["status"] != "submitted":
        c.close()
        return {"error": "只有「审批中」的批次可以驳回，当前为 %s" % b["status"]}
    c.execute("UPDATE plan_batch SET status='parsed', reject_reason=?, updated_at=? WHERE id=?",
              (reason, now(), bid))
    c.execute("UPDATE plan_item SET status='parsed' WHERE batch_id=?", (bid,))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,before_val,after_val,operator,reason,created_at)
                 VALUES('plan_batch',?,'reject','submitted','parsed',?,?,?)""",
              (bid, operator, reason, now()))
    c.commit()
    c.close()
    return {"ok": True, "status": "parsed", "at": now()}


def plan_approve(bid, operator="审批人", _c=None):
    """审批通过：同主体同期间的旧「已审批」版本置 superseded（保留历史），本版写库。"""
    c = _c or conn()
    b = _plan_batch(c, bid)
    if not b:
        if not _c:
            c.close()
        return {"error": "计划批次不存在"}
    if b["status"] in ("approved",):
        if not _c:
            c.close()
        return {"ok": True, "status": "approved", "already": True}
    c.execute("""UPDATE plan_batch SET status='superseded', updated_at=?
                 WHERE entity=? AND period=? AND status='approved' AND id<>?""",
              (now(), b["entity"], b["period"], bid))
    c.execute("""UPDATE plan_item SET status='superseded' WHERE batch_id IN
                 (SELECT id FROM plan_batch WHERE entity=? AND period=? AND status='superseded')""",
              (b["entity"], b["period"]))
    c.execute("UPDATE plan_batch SET status='approved', approved_at=?, approved_by=?, updated_at=? WHERE id=?",
              (now(), operator, now(), bid))
    c.execute("UPDATE plan_item SET status='approved' WHERE batch_id=?", (bid,))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,before_val,after_val,operator,created_at)
                 VALUES('plan_batch',?,'approve',?,'approved',?,?)""", (bid, b["status"], operator, now()))
    bump_agg(c)
    if not _c:
        c.commit()
        c.close()
    return {"ok": True, "status": "approved", "at": now()}


def plan_batch_rows(bid):
    c = conn()
    b = c.execute("SELECT * FROM plan_batch WHERE id=?", (bid,)).fetchone()
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    rows = [dict(r) for r in c.execute("SELECT * FROM plan_item WHERE batch_id=? ORDER BY id", (bid,))]
    c.close()
    return {"batch": dict(b), "rows": rows}


def plan_change(bid, operator="操作员"):
    """已审批批次发起变更：复制明细生成 version+1 的 parsed 批次，旧版继续生效。"""
    c = conn()
    b = _plan_batch(c, bid)
    if not b:
        c.close()
        return {"error": "计划批次不存在"}
    if b["status"] != "approved":
        c.close()
        return {"error": "只有「已审批」批次需要发起变更，当前为 %s" % b["status"]}
    # 同主体同期间已存在未完成的变更批次 → 直接返回它，避免重复开版
    pend = c.execute("""SELECT * FROM plan_batch WHERE parent_id=? AND status IN ('parsed','submitted','rejected')
                        ORDER BY id DESC LIMIT 1""", (bid,)).fetchone()
    if pend:
        c.close()
        return {"batch": pend["id"], "version": pend["version"], "reused": True}
    ver = int(b["version"] or 1) + 1
    c.execute("""INSERT INTO plan_batch(entity,period,file_name,n_rows,inflow,outflow,version,parent_id,status,
                 operator,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,'parsed',?,?,?)""",
              (b["entity"], b["period"], b["file_name"], b["n_rows"], b["inflow"], b["outflow"], ver, bid,
               operator, now(), now()))
    nid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    c.execute("""INSERT INTO plan_item(batch_id,entity,period,direction,l1,l2,code,amount,note,status)
                 SELECT ?,entity,period,direction,l1,l2,code,amount,note,'parsed' FROM plan_item WHERE batch_id=?""",
              (nid, bid))
    c.execute("""INSERT INTO audit_log(target_type,target_id,action,operator,reason,created_at)
                 VALUES('plan_batch',?,'change',?,?,?)""",
              (nid, operator, "由批次 #%d v%d 变更而来" % (bid, int(b["version"] or 1)), now()))
    c.commit()
    c.close()
    return {"batch": nid, "version": ver, "from": bid}


# ---------------------------------------------------------------- 汇率（人工）
def fx_for(pair, asof=None):
    c = conn()
    if asof:
        row = c.execute("SELECT * FROM fx_rate WHERE pair=? AND effective_date<=? ORDER BY effective_date DESC LIMIT 1",
                        (pair, asof)).fetchone()
    else:
        row = c.execute("SELECT * FROM fx_rate WHERE pair=? ORDER BY effective_date DESC LIMIT 1", (pair,)).fetchone()
    c.close()
    return dict(row) if row else None


def fx_all():
    c = conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM fx_rate ORDER BY pair, effective_date DESC")]
    c.close()
    return rows


def fx_set(pair, rate, effective_date, operator="操作员"):
    c = conn()
    c.execute("""INSERT INTO fx_rate(pair,rate,effective_date,operator,created_at) VALUES(?,?,?,?,?)
                 ON CONFLICT(pair,effective_date) DO UPDATE SET rate=excluded.rate, operator=excluded.operator, created_at=excluded.created_at""",
              (pair, float(rate), effective_date, operator, now()))
    c.commit()
    c.close()
    return {"ok": True}


# ---------------------------------------------------------------- 清空业务数据（保留主数据/映射/规则/汇率）
def reset_business_data():
    c = conn()
    before = {
        "flows": c.execute("SELECT COUNT(*) n FROM flow_raw").fetchone()["n"],
        "batches": c.execute("SELECT COUNT(*) n FROM import_batch").fetchone()["n"],
        "planItems": c.execute("SELECT COUNT(*) n FROM plan_item").fetchone()["n"],
        "planBatches": c.execute("SELECT COUNT(*) n FROM plan_batch").fetchone()["n"],
    }
    for t in ("flow_raw", "import_batch", "plan_item", "plan_batch", "audit_log"):
        c.execute("DELETE FROM %s" % t)
    # 只删人工反写规则，skill 种子规则与映射表保留
    c.execute("DELETE FROM match_rule WHERE source='manual_back'")
    # 批次号从 1 重新开始（便于对照测试）；失败不影响清空结果
    try:
        c.execute("DELETE FROM sqlite_sequence WHERE name IN "
                  "('flow_raw','import_batch','plan_item','plan_batch','audit_log')")
    except Exception:
        pass
    bump_agg(c)
    c.commit()
    c.close()
    return {"ok": True, "before": before}


# ---------------------------------------------------------------- 读取
def _seq_list(c, table):
    """批次列表：按创建顺序给 seq（页面显示 #seq），倒序返回最新的 100 条。"""
    rows = [dict(r) for r in c.execute("SELECT * FROM %s ORDER BY id ASC" % table)]
    for i, r in enumerate(rows, 1):
        r["seq"] = i
    return list(reversed(rows))[:100]


def meta():
    c = conn()
    out = {
        "entities": [dict(r) for r in c.execute("SELECT * FROM dim_entity ORDER BY rowid")],
        "accounts": [dict(r) for r in c.execute("SELECT * FROM dim_account ORDER BY entity, bank")],
        "subjectMap": [dict(r) for r in c.execute("SELECT * FROM subject_map ORDER BY code")],
        "fx": fx_all(),
        "aggAt": (c.execute("SELECT v FROM agg_meta WHERE k='agg_at'").fetchone() or {"v": ""})["v"],
        "rules": [dict(r) for r in c.execute("SELECT id,pri,direction,scope,keyword,code,enabled,source,hit_count FROM match_rule ORDER BY pri LIMIT 400")],
        "batches": _seq_list(c, "import_batch"),
        "planBatches": _seq_list(c, "plan_batch"),
        "safeLines": {r["entity"]: r["safe_line"] for r in c.execute("SELECT * FROM entity_rule")},
        "restrictedRules": [dict(r) for r in c.execute("SELECT * FROM restricted_rule ORDER BY kind, id")],
        "counts": {
            "flows": c.execute("SELECT COUNT(*) n FROM flow_raw").fetchone()["n"],
            "confirmed": c.execute("SELECT COUNT(*) n FROM flow_raw WHERE status='confirmed'").fetchone()["n"],
            "pending": c.execute("SELECT COUNT(*) n FROM flow_raw WHERE status='pending'").fetchone()["n"],
            "draft": c.execute("SELECT COUNT(*) n FROM flow_raw WHERE status='draft'").fetchone()["n"],
            "plans": c.execute("SELECT COUNT(*) n FROM plan_item WHERE status='approved'").fetchone()["n"],
        },
        "db": DB_PATH,
    }
    c.close()
    return out


def _period_filter(period):
    """返回 (start, end) 月份边界，支持 CUR/L3M/YTD/ALL/Qn/YYYY-MM"""
    c = conn()
    months = [r["p"] for r in c.execute(
        "SELECT DISTINCT substr(txn_date,1,7) p FROM flow_raw WHERE status='confirmed' ORDER BY p")]
    pmonths = [r["p"] for r in c.execute(
        "SELECT DISTINCT period p FROM plan_item WHERE status='approved' ORDER BY p")]
    c.close()
    allm = sorted(set(months) | set(pmonths))
    if not allm:
        return None, None, months, allm
    cur = months[-1] if months else allm[-1]
    if not period or period == "ALL":
        return allm[0], allm[-1], months, allm
    if re.match(r"^\d{4}-\d{2}$", period):
        return period, period, months, allm
    if re.match(r"^Q[1-4]$", period):
        y = cur[:4]
        q = int(period[1])
        return "%s-%02d" % (y, q * 3 - 2), "%s-%02d" % (y, q * 3), months, allm
    if period == "CUR":
        return cur, cur, months, allm
    if period == "L3M":
        y, m = int(cur[:4]), int(cur[5:])
        m2 = m - 2
        y2 = y
        if m2 <= 0:
            m2 += 12
            y2 -= 1
        return "%04d-%02d" % (y2, m2), cur, months, allm
    if period == "YTD":
        # 结束月取「流水或已审批计划」的最大月份（同年内），否则只上传了后续月份计划时会落在区间外
        y = cur[:4]
        end_y = allm[-1] if (allm and allm[-1][:4] == y and allm[-1] > cur) else cur
        return y + "-01", end_y, months, allm
    return allm[0], allm[-1], months, allm


def snapshot(entity="ALL", period="YTD"):
    """看板统一取数：行级 + 聚合 + 余额 + 计划，全部来自库"""
    start, end, months, allm = _period_filter(period)
    c = conn()
    ents = {r["short"]: dict(r) for r in c.execute("SELECT * FROM dim_entity")}
    accs = [dict(r) for r in c.execute("SELECT * FROM dim_account")]

    where = "status='confirmed'"
    args = []
    if entity and entity != "ALL":
        where += " AND entity=?"
        args.append(entity)
    if start:
        where += " AND substr(txn_date,1,7) BETWEEN ? AND ?"
        args += [start, end]
    rows = [dict(r) for r in c.execute("SELECT * FROM flow_raw WHERE " + where + " ORDER BY txn_date, id", args)]

    # 汇率：期间末生效汇率；缺失则为 None（页面显示"汇率缺失"）
    fxmap = {}
    for pair in ("USD/CNY", "HKD/CNY"):
        fxmap[pair] = fx_for(pair, (end + "-31") if end else None)
    missing_fx = set()
    for r in rows:
        ccy = ents.get(r["entity"], {}).get("ccy", "CNY")
        if ccy == "CNY":
            r["amt_cny"] = r["amount"]
            r["fx"] = 1
        else:
            fr = fxmap.get(ccy + "/CNY")
            if fr:
                r["amt_cny"] = round(r["amount"] * fr["rate"], 2)
                r["fx"] = fr["rate"]
            else:
                r["amt_cny"] = None
                r["fx"] = None
                missing_fx.add(ccy)

    # 内部往来标识一律取映射表（前端不硬编码 l1 判断）
    smflag = {r["code"]: (r["is_internal"] or 0, r["group_elim"] or 0)
              for r in c.execute("SELECT code,is_internal,group_elim FROM subject_map")}
    for r in rows:
        fl = smflag.get(r["code"] or "", (0, 0))
        r["is_internal"] = fl[0]
        r["group_elim"] = fl[1]

    plans = [dict(r) for r in c.execute(
        "SELECT * FROM plan_item WHERE status='approved'" +
        (" AND entity=?" if entity and entity != "ALL" else "") +
        (" AND period BETWEEN ? AND ?" if start else "") + " ORDER BY period, direction, l1, l2",
        ([entity] if entity and entity != "ALL" else []) + ([start, end] if start else []))]
    for p in plans:
        p["is_internal"] = 1 if p.get("l1") == "内部往来" else 0

    # 余额：每账户期间末最后一条带余额的流水
    bal = {}
    for r in c.execute("SELECT entity,bank,account,txn_date,balance FROM flow_raw WHERE status='confirmed' AND balance IS NOT NULL ORDER BY txn_date, id"):
        if start and r["txn_date"][:7] > end:
            continue
        bal[(r["entity"], r["bank"], r["account"])] = r

    pending = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE status='pending'" +
                        (" AND entity=?" if entity and entity != "ALL" else "") +
                        (" AND substr(txn_date,1,7) BETWEEN ? AND ?" if start else ""),
                        ([entity] if entity and entity != "ALL" else []) + ([start, end] if start else [])).fetchone()["n"]
    agg_at = (c.execute("SELECT v FROM agg_meta WHERE k='agg_at'").fetchone() or {"v": ""})["v"]
    c.close()
    ints = [r for r in rows if r.get("is_internal")]
    internal = {"n": len(ints), "amt": round(sum((r.get("amt_cny") or 0) for r in ints), 2)}
    return {
        "entity": entity, "period": period, "start": start, "end": end,
        "months": months, "allMonths": allm,
        "rows": rows, "plans": plans, "accounts": accs, "entities": list(ents.values()),
        "balances": [{"entity": k[0], "bank": k[1], "account": k[2], "balance": v["balance"], "date": v["txn_date"]}
                     for k, v in bal.items()],
        "fx": fxmap, "missingFx": sorted(missing_fx), "pending": pending, "aggAt": agg_at,
        "internal": internal,
        "restricted": restricted_summary(ents, fxmap, entity, end, bal),
    }


def restricted_summary(ents, fxmap, entity="ALL", end=None, bal=None):
    """受限资金汇总（按 账户×类别）：金额=转出-退回（沉淀在受限形态的部分），
    余额取该账户期间末最后一条流水余额（仅作参考，不等于受限金额）。"""
    c = conn()
    sql = ("SELECT id,entity,bank,account,txn_date,direction,amount,counterparty,memo,restricted_kind "
           "FROM flow_raw WHERE status='confirmed' AND IFNULL(restricted_kind,'')<>''")
    args = []
    if entity and entity != "ALL":
        sql += " AND entity=?"
        args.append(entity)
    if end:
        sql += " AND txn_date<=?"
        args.append(end + "-31")
    rows = [dict(r) for r in c.execute(sql + " ORDER BY txn_date, id", args)]
    rules = [dict(r) for r in c.execute("SELECT * FROM restricted_rule ORDER BY kind, id")]
    c.close()
    bal = bal or {}
    for r in rows:
        ccy = (ents.get(r["entity"]) or {}).get("ccy", "CNY")
        fr = {"rate": 1} if ccy == "CNY" else (fxmap.get(ccy + "/CNY") or {})
        r["ccy"] = ccy
        r["fx"] = fr.get("rate")
        r["amt_cny"] = round(r["amount"] * r["fx"], 2) if r["fx"] else None
    agg = {}
    for r in rows:
        k = (r["entity"], r["bank"] or "", r["account"] or "", r["restricted_kind"])
        o = agg.get(k)
        if not o:
            o = agg[k] = {"entity": k[0], "bank": k[1], "account": k[2], "kind": k[3], "inAmt": 0.0,
                          "outAmt": 0.0, "n": 0, "last": "", "ccy": r["ccy"], "missingFx": False}
        v = r["amt_cny"]
        if v is None:
            o["missingFx"] = True
            v = 0.0
        if r["direction"] == "收":
            o["inAmt"] += v
        else:
            o["outAmt"] += v
        o["n"] += 1
        o["last"] = r["txn_date"]
    accounts = []
    for o in agg.values():
        o["amt"] = round(max(0.0, o["outAmt"] - o["inAmt"]), 2)
        o["inAmt"] = round(o["inAmt"], 2)
        o["outAmt"] = round(o["outAmt"], 2)
        b = bal.get((o["entity"], o["bank"], o["account"]))
        o["balance"] = b["balance"] if b else None
        o["date"] = (b["txn_date"] if b else "") or o["last"]
        accounts.append(o)
    accounts.sort(key=lambda x: (-x["amt"], x["entity"], x["kind"]))
    kinds = {}
    for o in accounts:
        kk = kinds.get(o["kind"])
        if not kk:
            kk = kinds[o["kind"]] = {"kind": o["kind"], "amt": 0.0, "n": 0, "accounts": 0}
        kk["amt"] = round(kk["amt"] + o["amt"], 2)
        kk["n"] += o["n"]
        kk["accounts"] += 1
    manual_sql = "SELECT COALESCE(SUM(restricted),0) v FROM dim_account"
    c = conn()
    manual = c.execute(manual_sql + ("" if not entity or entity == "ALL" else " WHERE entity=?"),
                       ([] if not entity or entity == "ALL" else [entity])).fetchone()["v"]
    c.close()
    return {"total": round(sum(o["amt"] for o in accounts), 2), "n": len(rows),
            "manual": round(manual or 0, 2), "kinds": sorted(kinds.values(), key=lambda x: -x["amt"]),
            "accounts": accounts, "rows": rows[-300:], "rules": rules}


def pending_flows(entity, period="ALL"):
    start, end, _, _ = _period_filter(period)
    c = conn()
    sql = "SELECT * FROM flow_raw WHERE status IN ('pending','draft')"
    args = []
    if entity and entity != "ALL":
        sql += " AND entity=?"
        args.append(entity)
    if start:
        sql += " AND substr(txn_date,1,7) BETWEEN ? AND ?"
        args += [start, end]
    rows = [dict(r) for r in c.execute(sql + " ORDER BY txn_date DESC, id DESC LIMIT 500", args)]
    c.close()
    return rows


def batch_rows(bid):
    c = conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM flow_raw WHERE batch_id=? ORDER BY txn_date, id", (bid,))]
    smflag = {r["code"]: r["is_internal"] or 0 for r in c.execute("SELECT code,is_internal FROM subject_map")}
    c.close()
    for r in rows:
        r["is_internal"] = smflag.get(r["code"] or "", 0)
    return rows


def flows_by_ids(ids):
    if not ids:
        return []
    c = conn()
    q = ",".join("?" * len(ids))
    rows = [dict(r) for r in c.execute("SELECT * FROM flow_raw WHERE id IN (%s)" % q, ids)]
    c.close()
    return rows
