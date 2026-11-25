# -*- coding: utf-8 -*-
"""修复历史「假脏」标记：已确认但被「保存」误标为 modified=1 的流水。

背景：旧版 /api/flow/save 对 status='confirmed' 的记录无条件置 modified=1，
      页面随即显示「未确认（已修改）」，而「全部确认」只处理未确认记录，
      导致该标记无法清除、看起来像「保存不了」。

规则：status='confirmed' 且 modified=1 的记录 → 视为科目不需要重确认，modified 清 0；
      并按实际状态重算所有 import_batch 的 n_rows / n_confirmed / n_pending。

用法：
  .venv/Scripts/python.exe scripts/fix_flow_modified_flag.py            # 只体检，不改库
  .venv/Scripts/python.exe scripts/fix_flow_modified_flag.py --apply    # 执行修复（先备份）
  .venv/Scripts/python.exe scripts/fix_flow_modified_flag.py --apply --db 路径
"""
import os
import sys
import shutil
import sqlite3
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "数仓", "v11", "warehouse_v11.db")


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    db = DEFAULT_DB
    if "--db" in args:
        db = args[args.index("--db") + 1]
    if not os.path.exists(db):
        print("库不存在：%s" % db)
        return 1
    print("库：%s" % db)
    print("模式：%s" % ("执行修复" if apply else "只体检（加 --apply 执行）"))

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    dirty = c.execute("""SELECT batch_id, COUNT(*) n FROM flow_raw
                         WHERE status='confirmed' AND IFNULL(modified,0)=1 GROUP BY batch_id""").fetchall()
    total = sum(r["n"] for r in dirty)
    print("待修复：%d 笔（分布在 %d 个批次）" % (total, len(dirty)))
    for r in dirty:
        print("  批次 #%s：%d 笔" % (r["batch_id"], r["n"]))
    stuck = c.execute("""SELECT id FROM import_batch
                         WHERE status='parsed'
                           AND (SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id)>0
                           AND (SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status<>'confirmed')=0""").fetchall()
    if stuck:
        print("另有「已全部确认但状态仍是 parsed」的批次 %d 个：%s"
              % (len(stuck), "、".join("#%s" % r["id"] for r in stuck)))
    if not total and not stuck:
        c.close()
        print("无需修复。")
        return 0
    if not apply:
        c.close()
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = "%s.bak_%s" % (db, stamp)
    c.close()
    shutil.copy2(db, bak)
    print("已备份：%s" % bak)

    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    cur = c.execute("UPDATE flow_raw SET modified=0 WHERE status='confirmed' AND IFNULL(modified,0)=1")
    print("已清理 modified 标记：%d 笔" % cur.rowcount)
    # 批次卡在 parsed 但已无任何未确认记录（旧版全部命中规则时会这样）→ 补置 confirmed
    cur2 = c.execute("""UPDATE import_batch SET status='confirmed',
                          confirmed_at=COALESCE(confirmed_at, updated_at, created_at),
                          confirmed_by=COALESCE(confirmed_by, operator)
                        WHERE status='parsed'
                          AND (SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id)>0
                          AND (SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status<>'confirmed')=0""")
    print("已补齐「已全部确认但状态仍是 parsed」的批次：%d 个" % cur2.rowcount)
    c.execute("""UPDATE import_batch SET
                   n_rows=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id),
                   n_confirmed=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status='confirmed'),
                   n_pending=(SELECT COUNT(*) FROM flow_raw WHERE batch_id=import_batch.id AND status='pending')""")
    c.execute("""INSERT INTO agg_meta(k,v) VALUES('agg_at',?)
                 ON CONFLICT(k) DO UPDATE SET v=excluded.v""",
              (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    c.commit()
    c.close()
    print("批次计数已重算，聚合时间已刷新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())