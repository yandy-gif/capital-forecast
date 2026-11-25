# -*- coding: utf-8 -*-
"""清理某批次里不属于该期间（month != period）的流水。

默认只体检；--apply 先备份数据库再删除，并重算批次计数。

用法：
  python scripts/clean_batch_period.py --batch 4
  python scripts/clean_batch_period.py --batch 4 --apply
"""
import os, sys, shutil, sqlite3, argparse, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "数仓", "v11"))
DB = os.environ.get("V11_DB") or os.path.join(ROOT, "数仓", "v11", "warehouse_v11.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()
    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row
    b = c.execute("SELECT * FROM import_batch WHERE id=?", (a.batch,)).fetchone()
    if not b:
        print("批次不存在:", a.batch)
        return 1
    print("批次 #%s %s %s 状态=%s 行数=%s" % (b["id"], b["entity"], b["period"], b["status"], b["n_rows"]))
    rows = list(c.execute("""SELECT substr(txn_date,1,7) m, status, COUNT(*) n
                             FROM flow_raw WHERE batch_id=? GROUP BY m, status ORDER BY m""", (a.batch,)))
    for r in rows:
        mark = "" if r["m"] == b["period"] else "  ← 超出期间"
        print("  %s %s %s 笔%s" % (r["m"], r["status"], r["n"], mark))
    bad = c.execute("SELECT COUNT(*) n FROM flow_raw WHERE batch_id=? AND substr(txn_date,1,7)<>?",
                    (a.batch, b["period"])).fetchone()["n"]
    print("超出期间行数:", bad)
    if not bad:
        print("无需清理")
        c.close()
        return 0
    if not a.apply:
        print("（体检模式，未改动；加 --apply 执行删除）")
        c.close()
        return 0
    bak = a.db + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(a.db, bak)
    print("已备份:", bak)
    c.execute("DELETE FROM flow_raw WHERE batch_id=? AND substr(txn_date,1,7)<>?", (a.batch, b["period"]))
    c.commit()
    c.close()
    import db11
    os.environ["V11_DB"] = a.db
    cc = db11.conn()
    db11._refresh_batch(cc, a.batch, allow_confirm=False, operator="清理脚本")
    db11.bump_agg(cc)
    cc.commit()
    left = cc.execute("SELECT status, n_rows, n_confirmed, n_pending FROM import_batch WHERE id=?", (a.batch,)).fetchone()
    print("清理后批次:", dict(left) if left else None)
    print("剩余月份:", [dict(x) for x in cc.execute(
        "SELECT substr(txn_date,1,7) m, status, COUNT(*) n FROM flow_raw WHERE batch_id=? GROUP BY m,status", (a.batch,))])
    cc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())