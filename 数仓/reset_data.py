"""显式重置本地测试数据；调用方须串行化 HTTP 请求。"""
import json
import os
import sqlite3
import uuid
from datetime import datetime
from contextlib import closing
from pathlib import Path

import db


def reset_all(upload_dir, browser_state):
    root = Path(db.DB_PATH).resolve().parent
    uploads = Path(upload_dir).resolve()
    if uploads.parent != root or uploads.name != 'uploads':
        raise ValueError('上传目录必须位于当前数据库目录内')
    backup = root / 'backup' / ('reset-' + datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    token = uuid.uuid4().hex
    moved = False
    conn = db.connect()
    try:
        with closing(sqlite3.connect(backup / 'warehouse.db')) as dest:
            conn.backup(dest)
        (backup / 'browser-state.json').write_text(json.dumps(browser_state, ensure_ascii=False), encoding='utf-8')
        defaults = json.loads(Path(__file__).with_name('defaults.json').read_text(encoding='utf-8'))
        conn.execute('BEGIN IMMEDIATE')
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            conn.execute('DELETE FROM "' + table.replace('"', '""') + '"')
        for table, key in [('subject_dict','subjects'), ('fx_rate','fx'), ('biz_param','params'), ('flow_bridge','bridge')]:
            for row in defaults[key]:
                cols = ','.join('"' + k.replace('"','""') + '"' for k in row)
                conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
        for tpl_id, name, fields in [('TPL-05','生态多主体月度资金预算与资金计划表模板',14),
                                     ('TPL-06','银行流水导入模板',9)]:
            conn.execute("INSERT INTO tpl_registry(id,name,version,scope,fields,rule_ver,file_path,note) VALUES(?,?,'v1.0','全局',?,'v1','','按当前配置动态生成空白模板，不含历史金额或样例行')", (tpl_id,name,fields))
        conn.execute("INSERT INTO meta(k,v) VALUES('reset_token',?)", (token,))
        if uploads.exists():
            os.replace(uploads, backup / 'uploads')
            moved = True
        conn.commit()
    except Exception:
        conn.rollback()
        if moved:
            os.replace(backup / 'uploads', uploads)
        raise
    finally:
        conn.close()
    return {'ok': True, 'resetToken': token, 'backup': str(backup)}
