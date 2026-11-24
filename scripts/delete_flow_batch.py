# -*- coding: utf-8 -*-
"""按「公司 + 期间」删除流水批次及其记录（给需要重走上传/确认流程的场景用）。

默认只体检，加 --apply 才真正删库，且删除前自动备份。

用法：
  .venv/Scripts/python.exe scripts/delete_flow_batch.py --entity 内曜 --period 2026-07
  .venv/Scripts/python.exe scripts/delete_flow_batch.py --entity 内曜 --period 2026-07 --apply
  # 同时停用这批人工确认反写的规则（想完整重走「人工识别」，不让规则直接命中）
  .venv/Scripts/python.exe scripts/delete_flow_batch.py --entity 内曜 --period 2026-07 --apply --disable-manual-rules
"""
import os
import sys
import shutil
import sqlite3
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "数仓", "v11", "warehouse_v11.db")


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    entity, period = arg("--entity"), arg("--period")
    kind = arg("--kind", "flow")
    apply = "--apply" in sys.argv
    disable_rules = "--disable-manual-rules" in sys.argv
    db = arg("--db", DEFAULT_DB)
    if not entity or not period:
        print("必须指定 --entity 与 --period，例如 --entity 内曜 --period 2026-07")
        return 2
    if not os.path.exists(db):
        print("库不存在：%s" % db)
        return 2
    print("库：%s" % db)
    print("范围：%s / %s / kind=%s" % (entity, period, kind))
    print("模式：%s" % ("执行删除" if apply else "只体检（加 --apply 执行）"))

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    batches = c.execute("SELECT * FROM import_batch WHERE entity=? AND period=? AND kind=?",
                        (entity, period, kind)).fetchall()
    if not batches:
        c.close()
        print("没有匹配的批次，无需处理。")
        return 0
    bids = [b["id"] for b in batches]
    q = ",".join("?" * len(bids))
    print("匹配到批次 %d 个：%s" % (len(bids), "、".join("#%d" % i for i in bids)))
    for b in batches:
        print("  #%d %s %s 行数 %d 已确认 %d 待确认 %d 状态 %s 文件 %s"
              % (b["id"], b["entity"], b["period"], b["n_rows"], b["n_confirmed"], b["n_pending"],
                 b["status"], b["file_name"] or ""))

    n_flow = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id IN (%s)" % q, bids).fetchone()["n"]
    ids = [r["id"] for r in c.execute("SELECT id FROM flow_raw WHERE batch_id IN (%s)" % q, bids)]
    n_audit = 0
    if ids:
        qa = ",".join("?" * len(ids))
        n_audit = c.execute("SELECT COUNT(*) n FROM audit_log WHERE target_type='flow' AND target_id IN (%s)" % qa,
                            ids).fetchone()["n"]
    cps = [r["counterparty"] for r in c.execute(
        "SELECT DISTINCT counterparty FROM flow_raw WHERE batch_id IN (%s) AND matched_by='manual' AND IFNULL(counterparty,'')<>''" % q,
        bids)]
    rules = []
    if cps:
        for r in c.execute("SELECT id,keyword,code,enabled FROM match_rule WHERE source='manual_back' AND enabled=1"):
            if r["keyword"] in cps:
                rules.append(r)
    print("将删除：流水 %d 笔、导入批次 %d 个、该批流水的修改留痕 %d 条" % (n_flow, len(bids), n_audit))
    if rules:
        print("该批人工确认反写的规则 %d 条（%s）：%s"
              % (len(rules), "将停用" if disable_rules else "默认保留，加 --disable-manual-rules 停用",
                 "；".join("#%d %s→%s" % (r["id"], r["keyword"], r["code"]) for r in rules)))
    if not apply:
        c.close()
        print("未做任何改动。")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = "%s.bak_%s" % (db, stamp)
    c.close()
    shutil.copy2(db, bak)
    print("已备份：%s" % bak)

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        if ids:
            qa = ",".join("?" * len(ids))
            c.execute("DELETE FROM audit_log WHERE target_type='flow' AND target_id IN (%s)" % qa, ids)
        c.execute("DELETE FROM flow_raw WHERE batch_id IN (%s)" % q, bids)
        c.execute("DELETE FROM import_batch WHERE id IN (%s)" % q, bids)
        c.execute("UPDATE import_batch SET n_rows=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id),"
                  " n_confirmed=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status='confirmed'),"
                  " n_pending=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status='pending')")
        if disable_rules and rules:
            qr = ",".join("?" * len(rules))
            c.execute("UPDATE match_rule SET enabled=0 WHERE id IN (%s)" % qr, [r["id"] for r in rules])
            for r in rules:
                c.execute("INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,reason,created_at)"
                          " VALUES('rule',?,'disable','enabled','1','0','删除批次脚本',?,?)",
                          (r["id"], "删除 %s/%s 批次后停用反写规则" % (entity, period),
                           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        c.execute("INSERT INTO agg_meta(k,v) VALUES('agg_at',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                  (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        c.commit()
    except Exception:
        c.rollback()
        c.close()
        raise
    left = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE entity=? AND substr(txn_date,1,7)=?",
                     (entity, period)).fetchone()["n"]
    c.close()
    print("已删除：流水 %d 笔、批次 %d 个、留痕 %d 条" % (n_flow, len(bids), n_audit))
    if disable_rules and rules:
        print("已停用反写规则 %d 条（规则表可重新启用）" % len(rules))
    print("该主体该期间剩余流水：%d 笔" % left)
    return 0


if __name__ == "__main__":
    sys.exit(main())