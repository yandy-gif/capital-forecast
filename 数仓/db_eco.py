"""本地数仓 · 生态多主体资金计划层（模板 / 提报 / 审批 / 参数 / 桥接）

与 db.py 的分工：
- db.py     ：流水、旧口径计划、人工标签、规则集（保持零第三方依赖）
- db_eco.py ：生态多主体资金计划的提报-审批-参数-桥接-匹配（本文件）

状态机：草稿 → 提交 → 待审 → 批准 → 已锁定 → 核销 → 已核销。
待审可退回；变更复制新草稿，旧锁定版在新版批准前继续生效。
"""
import json
from datetime import datetime

from db import connect

# 两个环节：草稿 →(提交)→ 待审 →(审批通过)→ 已锁定；不通过则退回，改完重新提交
STATUS_FLOW = {
    "草稿":   {"提交": "待审"},
    "待审":   {"批准": "已锁定", "退回": "退回"},
    "退回":   {"提交": "待审"},
    "已锁定": {"核销": "已核销"},
    "已核销": {},
    "已作废": {},
}
ACTION_ROLE = {
    "创建": "系统", "提交": "提报人", "批准": "审批人", "退回": "审批人", "核销": "系统",
}
ACTION_GATE = {"提交": "G1", "批准": "G2"}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== 模板注册 ====================
def tpl_list():
    conn = connect()
    rs = conn.execute("SELECT * FROM tpl_registry ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def tpl_get(id_):
    conn = connect()
    row = conn.execute("SELECT * FROM tpl_registry WHERE id=?", (id_,)).fetchone()
    conn.close()
    return dict(row) if row else None


def tpl_upsert(t):
    conn = connect()
    conn.execute(
        """INSERT INTO tpl_registry(id,name,version,scope,fields,rule_ver,effective,file_path,status,note)
           VALUES(:id,:name,:version,:scope,:fields,:rule_ver,:effective,:file_path,:status,:note)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, version=excluded.version, scope=excluded.scope,
             fields=excluded.fields, rule_ver=excluded.rule_ver, effective=excluded.effective,
             file_path=excluded.file_path, status=excluded.status, note=excluded.note,
             updated_at=datetime('now','localtime')""",
        {**{"scope": None, "fields": None, "rule_ver": None, "effective": None,
            "file_path": None, "status": "启用", "note": None}, **t})
    conn.commit()
    conn.close()


def tpl_hit(id_):
    """下载计数 +1，返回模板记录（不存在返回 None）"""
    conn = connect()
    row = conn.execute("SELECT * FROM tpl_registry WHERE id=?", (id_,)).fetchone()
    if row:
        conn.execute("UPDATE tpl_registry SET downloads=downloads+1 WHERE id=?", (id_,))
        conn.commit()
    conn.close()
    return dict(row) if row else None


# ==================== 科目字典 / 主体台账 / 汇率 ====================
def dict_replace(subjects=None, entities=None, fx=None):
    conn = connect()
    if subjects is not None:
        conn.execute("DELETE FROM subject_dict")
        for s in subjects:
            conn.execute("INSERT OR REPLACE INTO subject_dict(direction,cat,subject,cf_attr,note,tpl_ver) "
                         "VALUES(?,?,?,?,?,?)",
                         (s.get("direction"), s.get("cat"), s.get("subject"),
                          s.get("cf_attr"), s.get("note"), s.get("tpl_ver", "v1.0")))
    if entities is not None:
        conn.execute("DELETE FROM entity_ledger")
        for e in entities:
            conn.execute("INSERT OR REPLACE INTO entity_ledger(name,segment,region,currency,account,"
                         "consolidated,pooling,owner,note) VALUES(?,?,?,?,?,?,?,?,?)",
                         (e.get("name"), e.get("segment"), e.get("region"), e.get("currency"),
                          e.get("account"), e.get("consolidated"), e.get("pooling"), e.get("owner"), e.get("note")))
    if fx is not None:
        for f in fx:
            conn.execute("INSERT OR REPLACE INTO fx_rate(currency,name,rate_cny,updated_at,updated_by) "
                         "VALUES(?,?,?,?,?)",
                         (f.get("currency"), f.get("name"), float(f.get("rate_cny") or 1),
                          f.get("updated_at") or _now(), f.get("updated_by") or "系统"))
    conn.commit()
    conn.close()



def dict_merge(subjects=None, entities=None):
    """合并字典/台账：只增改，不删除。
    上传件自带的科目字典可能只是局部（甚至测试用的小字典），
    整表覆盖会把集团口径洗掉，所以这里一律走合并。"""
    conn = connect()
    if subjects:
        for s_ in subjects:
            if not s_.get("subject"):
                continue
            conn.execute("INSERT OR REPLACE INTO subject_dict(direction,cat,subject,cf_attr,note,tpl_ver) "
                         "VALUES(?,?,?,?,?,?)",
                         (s_.get("direction"), s_.get("cat"), s_.get("subject"),
                          s_.get("cf_attr"), s_.get("note"), s_.get("tpl_ver", "v1.0")))
    if entities:
        for e in entities:
            if not e.get("name"):
                continue
            conn.execute("INSERT OR REPLACE INTO entity_ledger(name,segment,region,currency,account,"
                         "consolidated,pooling,owner,note) VALUES(?,?,?,?,?,?,?,?,?)",
                         (e.get("name"), e.get("segment"), e.get("region"), e.get("currency"),
                          e.get("account"), e.get("consolidated"), e.get("pooling"), e.get("owner"), e.get("note")))
    conn.commit()
    conn.close()


def dict_all():
    conn = connect()
    out = {
        "subjects": [dict(r) for r in conn.execute(
            "SELECT direction,cat,subject,cf_attr,note FROM subject_dict ORDER BY direction,cat,subject")],
        "entities": [dict(r) for r in conn.execute("SELECT * FROM entity_ledger ORDER BY name")],
        "fx": [dict(r) for r in conn.execute("SELECT * FROM fx_rate ORDER BY currency")],
    }
    conn.close()
    return out


# ==================== 可调整业务参数 ====================
def params_load():
    conn = connect()
    rs = conn.execute("SELECT * FROM biz_param ORDER BY grp, id").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def params_map():
    return {p["id"]: p["value"] for p in params_load()}


def params_save(params, actor=""):
    """params: [{id,grp,label,value,unit,scope,note,locked}] 登记/更新，或 {id: value} 只改值"""
    conn = connect()
    if isinstance(params, dict):
        for k, v in params.items():
            conn.execute("UPDATE biz_param SET value=?, updated_at=?, updated_by=? WHERE id=?",
                         (str(v), _now(), actor, k))
    else:
        for p in params:
            conn.execute(
                """INSERT INTO biz_param(id,grp,label,value,unit,scope,note,locked,updated_at,updated_by)
                   VALUES(:id,:grp,:label,:value,:unit,:scope,:note,:locked,datetime('now','localtime'),:by)
                   ON CONFLICT(id) DO UPDATE SET grp=excluded.grp, label=excluded.label, value=excluded.value,
                     unit=excluded.unit, scope=excluded.scope, note=excluded.note, locked=excluded.locked,
                     updated_at=datetime('now','localtime'), updated_by=excluded.updated_by""",
                {**{"grp": "其他", "unit": "", "scope": "", "note": "", "locked": 0}, **p,
                 "value": str(p.get("value", "")), "by": actor})
    conn.commit()
    conn.close()


def terms_load():
    conn = connect()
    rs = conn.execute("SELECT * FROM cust_term ORDER BY klass, customer").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def terms_save(rows, actor=""):
    conn = connect()
    for r in rows:
        conn.execute("""INSERT INTO cust_term(customer,klass,term_days,note,updated_at,updated_by)
                        VALUES(?,?,?,?,datetime('now','localtime'),?)
                        ON CONFLICT(customer) DO UPDATE SET klass=excluded.klass, term_days=excluded.term_days,
                          note=excluded.note, updated_at=datetime('now','localtime'), updated_by=excluded.updated_by""",
                     (r.get("customer"), r.get("klass"), int(30 if r.get("term_days") in (None, "") else r["term_days"]),
                      r.get("note"), actor))
    conn.commit()
    conn.close()


# ==================== 科目桥接（流水 → 计划科目） ====================
def bridge_load():
    conn = connect()
    rs = conn.execute("SELECT * FROM flow_bridge ORDER BY priority, id").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def bridge_save(rows):
    conn = connect()
    for r in rows:
        conn.execute("""INSERT INTO flow_bridge(id,direction,l1,l2,subject,cashflow,handle,priority,status,note,updated_at)
                        VALUES(:id,:direction,:l1,:l2,:subject,:cashflow,:handle,:priority,:status,:note,datetime('now','localtime'))
                        ON CONFLICT(id) DO UPDATE SET direction=excluded.direction, l1=excluded.l1, l2=excluded.l2,
                          subject=excluded.subject, cashflow=excluded.cashflow, handle=excluded.handle,
                          priority=excluded.priority, status=excluded.status, note=excluded.note,
                          updated_at=datetime('now','localtime')""",
                     {**{"direction": "", "l1": "", "l2": "", "subject": "", "cashflow": "",
                         "handle": "归集", "priority": 5, "status": "启用", "note": ""}, **r})
    conn.commit()
    conn.close()


# ==================== 提报 / 明细 / 审批留痕 ====================
def submission_create(meta, items, actor="系统"):
    conn = connect()
    cur = conn.execute(
        """INSERT INTO plan_submission(entity,period,version,file_name,tpl_ver,rule_ver,status,item_count,
                                       inflow,outflow,net,gate_report,submitter)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta.get("entity") or "多主体", meta["period"], int(meta.get("version") or 1),
         meta.get("file_name"), meta.get("tpl_ver"), meta.get("rule_ver"),
         meta.get("status") or "草稿", len(items),
         float(meta.get("inflow") or 0), float(meta.get("outflow") or 0), float(meta.get("net") or 0),
         json.dumps(meta.get("gates") or {}, ensure_ascii=False), actor))
    sub_id = cur.lastrowid
    for i, it in enumerate(items, 1):
        conn.execute(
            """INSERT INTO plan_item(sub_id,seq,entity,segment,region,currency,direction,cat,subject,timing,
                                     amt_orig,amt_cny,owner,item_status,note,src_row,flag)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sub_id, it.get("seq") or i, it.get("entity"), it.get("segment"), it.get("region"),
             it.get("currency"), it.get("direction"), it.get("cat"), it.get("subject"), it.get("timing"),
             it.get("amt_orig"), it.get("amt_cny"), it.get("owner"), it.get("item_status") or "待编",
             it.get("note"), it.get("src_row"), it.get("flag") or "ok"))
    conn.execute("INSERT INTO approval_log(sub_id,action,from_status,to_status,actor,role,reason) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (sub_id, "创建", None, meta.get("status") or "草稿", actor, "系统",
                  meta.get("file_name") or ""))
    conn.commit()
    conn.close()
    return sub_id


def next_version(period, entity):
    conn = connect()
    row = conn.execute("SELECT MAX(version) v FROM plan_submission WHERE period=? AND entity=?",
                       (period, entity)).fetchone()
    conn.close()
    return int(row["v"] or 0) + 1


def submission_list(limit=200):
    conn = connect()
    rs = conn.execute("SELECT * FROM plan_submission ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    out = []
    for r in rs:
        d = dict(r)
        try:
            d["gates"] = json.loads(d.pop("gate_report") or "{}")
        except Exception:
            d["gates"] = {}
        out.append(d)
    return out


def submission_get(sub_id):
    conn = connect()
    row = conn.execute("SELECT * FROM plan_submission WHERE id=?", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    try:
        d["gates"] = json.loads(d.pop("gate_report") or "{}")
    except Exception:
        d["gates"] = {}
    d["items"] = [dict(x) for x in conn.execute(
        "SELECT * FROM plan_item WHERE sub_id=? ORDER BY seq, id", (sub_id,))]
    d["logs"] = [dict(x) for x in conn.execute(
        "SELECT * FROM approval_log WHERE sub_id=? ORDER BY id", (sub_id,))]
    conn.close()
    return d



def _start_change(conn, sub_id, actor, reason):
    """发起变更：复制当前明细生成 v+1 草稿；旧版保持「已锁定」，
    这样变更审批期间预实仍用旧版取数，不会断档。"""
    row = conn.execute("SELECT * FROM plan_submission WHERE id=?", (sub_id,)).fetchone()
    if not row:
        return None
    newv = int(row["version"] or 1) + 1
    cur = conn.execute(
        """INSERT INTO plan_submission(entity,period,version,file_name,tpl_ver,rule_ver,status,item_count,
                                       inflow,outflow,net,gate_report,submitter)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["entity"], row["period"], newv, row["file_name"], row["tpl_ver"], row["rule_ver"],
         "草稿", row["item_count"], row["inflow"], row["outflow"], row["net"], row["gate_report"], actor))
    new_id = cur.lastrowid
    conn.execute(
        """INSERT INTO plan_item(sub_id,seq,entity,segment,region,currency,direction,cat,subject,timing,
                                 amt_orig,amt_cny,owner,item_status,note,src_row,flag)
           SELECT ?,seq,entity,segment,region,currency,direction,cat,subject,timing,
                  amt_orig,amt_cny,owner,item_status,note,src_row,flag
           FROM plan_item WHERE sub_id=? ORDER BY seq, id""", (new_id, sub_id))
    conn.execute("UPDATE plan_submission SET replaced_by=?, updated_at=? WHERE id=?",
                 (new_id, _now(), sub_id))
    conn.execute("INSERT INTO approval_log(sub_id,action,from_status,to_status,actor,role,reason) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (sub_id, "发起变更", "已锁定", "已锁定", actor, "提报人",
                  "已生成新版本 #%d（v%d，草稿）；旧版本继续生效直至新版批准%s" % (
                      new_id, newv, ("｜" + reason if reason else ""))))
    conn.execute("INSERT INTO approval_log(sub_id,action,from_status,to_status,actor,role,reason) "
                 "VALUES(?,?,?,?,?,?,?)",
                 (new_id, "创建(变更)", None, "草稿", actor, "系统", "由 #%d v%d 复制而来" % (sub_id, row["version"])))
    return new_id


def submission_action(sub_id, action, actor="", role="", reason="", gates=None, amount_after=None):
    """按状态机推进一步；返回 (ok, msg, new_status)"""
    conn = connect()
    row = conn.execute("SELECT * FROM plan_submission WHERE id=?", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return False, "提报单不存在", None
    cur_status = row["status"]
    if action == "发起变更":
        if cur_status != "已锁定":
            conn.close()
            return False, "只有「已锁定」的提报单才能发起变更（当前：%s）" % cur_status, cur_status
        new_id = _start_change(conn, sub_id, actor, reason)
        conn.commit()
        conn.close()
        return True, "已生成新版本 #%s（草稿），旧版本继续生效" % new_id, "已锁定"
    allowed = STATUS_FLOW.get(cur_status, {})
    if action not in allowed:
        conn.close()
        return False, "当前状态「%s」不允许「%s」（可执行：%s）" % (
            cur_status, action, "、".join(allowed) or "无"), cur_status
    if action == "退回" and not str(reason or "").strip():
        conn.close()
        return False, "退回必须填写原因（留痕要求）", cur_status
    new_status = allowed[action]
    ts = _now()
    fields, vals = ["status=?", "updated_at=?"], [new_status, ts]
    voided = []
    if action == "批准":
        # 同期间同主体只允许一个有效版本：批准新版即作废旧版（旧版数据与留痕全部保留）
        for old in conn.execute(
                "SELECT id, version, net FROM plan_submission WHERE period=? AND entity=? "
                "AND status='已锁定' AND id<>?", (row["period"], row["entity"], sub_id)).fetchall():
            conn.execute("UPDATE plan_submission SET status='已作废', updated_at=? WHERE id=?",
                         (ts, old["id"]))
            conn.execute(
                "INSERT INTO approval_log(sub_id,action,from_status,to_status,actor,role,reason,amount_before,amount_after) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (old["id"], "作废", "已锁定", "已作废", actor, "系统",
                 "被 #%d v%d 替换（同期间同主体只能有一个有效版本）" % (sub_id, int(row["version"] or 0) + 0),
                 old["net"], old["net"]))
            voided.append(old["id"])
    if action == "提交":
        fields += ["submitter=?", "submitted_at=?"]
        vals += [actor, ts]
    elif action == "复核通过":
        fields += ["reviewer=?", "reviewed_at=?", "review_note=?"]
        vals += [actor, ts, reason]
    elif action == "批准":
        fields += ["approver=?", "approved_at=?", "locked_at=?"]
        vals += [actor, ts, ts]
    elif action == "退回":
        fields += ["reject_by=?", "rejected_at=?", "reject_reason=?"]
        vals += [actor, ts, reason]
    if amount_after is not None:
        fields += ["net=?"]
        vals += [float(amount_after)]
    vals += [sub_id]
    conn.execute("UPDATE plan_submission SET %s WHERE id=?" % ", ".join(fields), vals)
    conn.execute(
        """INSERT INTO approval_log(sub_id,action,from_status,to_status,actor,role,reason,amount_before,
                                    amount_after,gate)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (sub_id, action, cur_status, new_status, actor, role or ACTION_ROLE.get(action, ""),
         reason, row["net"], amount_after if amount_after is not None else row["net"],
         json.dumps(gates or {}, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return True, "%s → %s" % (cur_status, new_status), new_status



def save_appendix(sub_id, period, entity, version, rows):
    """写入资金计划附页（销售回款明细 / 费用申报明细）"""
    if not rows:
        return 0
    conn = connect()
    n = 0
    for r in rows:
        conn.execute(
            """INSERT INTO plan_appendix(sub_id,period,entity,version,kind,customer,klass,product,
                                        dept,item,qty,price,rate,amount,src_row,note)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sub_id, period, entity, version, r.get("kind"), r.get("customer"), r.get("klass"),
             r.get("product"), r.get("dept"), r.get("item"), r.get("qty"), r.get("price"),
             r.get("rate"), r.get("amount"), r.get("src_row"), r.get("note")))
        n += 1
    conn.commit()
    conn.close()
    return n


def locked_items(period=None, entity=None):
    """已锁定（含已核销/变更中）版本的明细行 —— 预实比对只认这一层"""
    conn = connect()
    sql = ("SELECT s.id AS sub_id, s.period, s.version, i.id AS item_id, i.entity, i.segment, i.direction, "
           "i.cat, i.subject, i.timing, i.amt_orig, i.amt_cny, i.owner, i.note "
           "FROM plan_item i JOIN plan_submission s ON s.id=i.sub_id "
           "WHERE s.status IN ('已锁定','已核销')")
    args = []
    if period:
        sql += " AND s.period=?"
        args.append(period)
    if entity:
        sql += " AND i.entity=?"
        args.append(entity)
    sql += " ORDER BY s.period, i.entity, i.direction, i.subject"
    rs = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rs]


def match_link_save(links):
    conn = connect()
    n = 0
    for l in links:
        conn.execute("""INSERT INTO match_link(sub_id,item_id,period,entity,subject,flow_key,flow_date,
                                                flow_amount,amount,basis,confirmer)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (l.get("sub_id"), l.get("item_id"), l.get("period"), l.get("entity"), l.get("subject"),
                      l.get("flow_key"), l.get("flow_date"), l.get("flow_amount"), l.get("amount"),
                      l.get("basis"), l.get("confirmer")))
        n += 1
    conn.commit()
    conn.close()
    return n


def match_link_load(period=None):
    conn = connect()
    if period:
        rs = conn.execute("SELECT * FROM match_link WHERE period=? ORDER BY id", (period,)).fetchall()
    else:
        rs = conn.execute("SELECT * FROM match_link ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rs]


def eco_stats():
    conn = connect()
    out = {
        "submissions": conn.execute("SELECT COUNT(*) c FROM plan_submission").fetchone()["c"],
        "by_status": {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) c FROM plan_submission GROUP BY status")},
        "items": conn.execute("SELECT COUNT(*) c FROM plan_item").fetchone()["c"],
        "params": conn.execute("SELECT COUNT(*) c FROM biz_param").fetchone()["c"],
        "bridge": conn.execute("SELECT COUNT(*) c FROM flow_bridge").fetchone()["c"],
        "terms": conn.execute("SELECT COUNT(*) c FROM cust_term").fetchone()["c"],
        "subjects": conn.execute("SELECT COUNT(*) c FROM subject_dict").fetchone()["c"],
        "entities": conn.execute("SELECT COUNT(*) c FROM entity_ledger").fetchone()["c"],
        "templates": conn.execute("SELECT COUNT(*) c FROM tpl_registry").fetchone()["c"],
        "match_links": conn.execute("SELECT COUNT(*) c FROM match_link").fetchone()["c"],
        "periods": [r["period"] for r in conn.execute(
            "SELECT DISTINCT period FROM plan_submission ORDER BY period")],
    }
    conn.close()
    return out
