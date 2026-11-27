# -*- coding: utf-8 -*-
"""历史人工分类迁移。默认只预览；--apply 备份后迁移规则，不改流水。"""
import argparse
import collections
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile
from datetime import datetime

import db11


def _eligible(conn):
    rows = conn.execute("""SELECT f.* FROM flow_raw f JOIN subject_map s ON s.code=f.code
                           WHERE f.status='confirmed' AND f.matched_by='manual'
                             AND IFNULL(f.offset_of,0)=0 AND IFNULL(f.code,'')<>''
                           ORDER BY f.id""").fetchall()
    good, skipped = [], 0
    for row in rows:
        head = (row['code'] or '')[:1]
        direction_ok = head == 'G' or (head in 'ACE' and row['direction'] == '收') or (head in 'BDF' and row['direction'] == '支')
        if not direction_ok or not db11._manual_rule_fields(row, row['code']):
            skipped += 1
        else:
            good.append(row)
    return good, skipped


def _plan(conn, doc):
    rows, skipped = _eligible(conn)
    rules = doc['rules']
    existing = {r.get('id'): r for r in rules}
    created = updated = unchanged = 0
    groups = collections.defaultdict(set)
    for row in rows:
        fields = db11._manual_rule_fields(row, row['code'])
        groups[(row['entity'], fields['account'][0], row['direction'], fields['pattern'])].add(row['code'])
        prior = existing.get(db11._history_rule_key(row))
        if prior and all(prior.get(k) == v for k, v in fields.items()) and prior.get('enabled'):
            unchanged += 1
            continue
        result = db11._record_history_rule(doc, row, row['code'], '历史迁移', increment=False)
        created += int(result['created'])
        updated += int(result['updated'])
    disabled_json = 0
    for rule in rules:
        if str(rule.get('id', '')).startswith('ML-') and rule.get('source') == 'manual_back' and rule.get('enabled'):
            rule['enabled'] = False
            disabled_json += 1
    old = conn.execute("SELECT id FROM match_rule WHERE source='manual_back' AND enabled=1").fetchall()
    return {'history_rows': len(rows), 'skipped': skipped, 'new_rules': created,
            'updated_rules': updated, 'unchanged': unchanged, 'legacy_rules_to_disable': len(old),
            'old_json_rules_to_disable': disabled_json,
            'conflicting_counterparties': sum(1 for x in groups.values() if len(x) > 1)}, [r['id'] for r in old]


def _atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(prefix='.rule-migration-', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def migrate(db_path, rule_path, apply=False, backup_dir=None):
    db_path, rule_path = pathlib.Path(db_path).resolve(), pathlib.Path(rule_path).resolve()
    if not db_path.is_file() or not rule_path.is_file():
        raise ValueError('数据库或规则库不存在；未做任何修改')
    uri = db_path.as_uri() + ('?mode=rw' if apply else '?mode=ro')
    conn = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    original = None
    replaced = False
    try:
        if apply:
            conn.execute('BEGIN IMMEDIATE')
        else:
            conn.execute('PRAGMA query_only=ON')
        original = rule_path.read_bytes()
        doc = json.loads(original.decode('utf-8'))
        if not isinstance(doc.get('rules'), list):
            raise ValueError('规则库缺少 rules 列表')
        plan, old_ids = _plan(conn, doc)
        if not apply:
            return {'mode': 'preview', **plan}
        if not backup_dir:
            raise ValueError('执行迁移必须指定备份目录')
        backup = pathlib.Path(backup_dir).resolve()
        backup.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        db_backup = backup / ('warehouse_before_rule_migration_' + stamp + '.db')
        rule_backup = backup / ('rulebook_before_rule_migration_' + stamp + '.json')
        with rule_backup.open('xb') as stream:
            stream.write(original)
        read_conn = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True, timeout=30)
        try:
            target = sqlite3.connect(str(db_backup))
            try:
                read_conn.backup(target)
            finally:
                target.close()
        finally:
            read_conn.close()
        if hashlib.sha256(rule_path.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise RuntimeError('规则库在迁移期间发生变化，已停止，请重试')
        ts = db11.now()
        doc['version'] = ts[:10].replace('-', '.')
        doc['updated_at'] = ts
        payload = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        _atomic_write(rule_path, payload)
        replaced = True
        previous_rulebook = db11.RULEBOOK
        db11.RULEBOOK = str(rule_path)
        try:
            sync = db11.sync_rules(conn)
        finally:
            db11.RULEBOOK = previous_rulebook
        if not sync.get('ok'):
            raise RuntimeError('同步失败：' + sync.get('reason', '未知错误'))
        for rid in old_ids:
            conn.execute("UPDATE match_rule SET enabled=0 WHERE id=?", (rid,))
            conn.execute("""INSERT INTO audit_log(target_type,target_id,action,field,before_val,after_val,operator,reason,created_at)
                            VALUES('match_rule',?,'migration','enabled','1','0','历史规则迁移','由人工确认历史替代旧规则',?)""", (rid, ts))
        db11.bump_agg(conn)
        conn.commit()
        return {'mode': 'applied', **plan, 'database_backup': str(db_backup), 'rulebook_backup': str(rule_backup)}
    except Exception:
        if apply:
            conn.rollback()
            if replaced and original is not None:
                _atomic_write(rule_path, original)
        raise
    finally:
        conn.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default=db11.DB_PATH)
    p.add_argument('--rulebook', default=db11.RULEBOOK)
    p.add_argument('--backup-dir')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    print(json.dumps(migrate(a.db, a.rulebook, a.apply, a.backup_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
