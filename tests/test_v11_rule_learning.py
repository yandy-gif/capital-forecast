# -*- coding: utf-8 -*-
"""人工确认沉淀 skill 规则的回归测试（临时库、临时规则库）。"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
V11 = ROOT / "数仓" / "v11"


class RuleLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.old_db = os.environ.get("V11_DB")
        self.old_rulebook = os.environ.get("BANK_FLOW_RULEBOOK")
        os.environ["V11_DB"] = str(pathlib.Path(self.tmp.name) / "rule-learning.db")
        self.rulebook = pathlib.Path(self.tmp.name) / "rules.json"
        self.rulebook.write_text(json.dumps({
            "schema": 1, "version": "test", "updated_at": "", "rules": []
        }, ensure_ascii=False), encoding="utf-8")
        os.environ["BANK_FLOW_RULEBOOK"] = str(self.rulebook)
        sys.path.insert(0, str(V11))
        sys.modules.pop("migrate_history_rules", None)
        sys.modules.pop("db11", None)
        import db11
        self.db = db11
        self.db.init_db()
        batch, duplicate = self.db.create_batch("内曜", "2026-11", "人工确认.xlsx", "rule-learning-sha")
        self.assertFalse(duplicate)
        self.bid = batch["id"]
        self.db.import_rows(self.bid, [{
            "date": "2026-11-03", "dir": "支", "amount": 1800.0,
            "cp": "杭州示例服务商", "memo": "采购 服务费"
        }], "测试员", True)
        self.row_id = self.db.batch_rows(self.bid)[0]["id"]

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("V11_DB", None)
        else:
            os.environ["V11_DB"] = self.old_db
        if self.old_rulebook is None:
            os.environ.pop("BANK_FLOW_RULEBOOK", None)
        else:
            os.environ["BANK_FLOW_RULEBOOK"] = self.old_rulebook
        sys.modules.pop("db11", None)
        sys.modules.pop("migrate_history_rules", None)
        self.tmp.cleanup()

    def rules(self):
        return json.loads(self.rulebook.read_text(encoding="utf-8"))["rules"]

    def test_manual_confirmation_learns_company_counterparty_rule(self):
        self.db.save_flow_assignments(self.bid, [{"id": self.row_id, "code": "B06"}], "测试员")
        got = self.db.confirm_flows([self.row_id], "B06", "测试员")
        self.assertEqual(got["ruleWarnings"], [])
        self.assertEqual(got["rulesBack"], 1)

        rules = self.rules()
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertEqual(rule["match_on"], "history")
        self.assertEqual(rule["entity"], ["内曜"])
        self.assertEqual(rule["account"], ["*"])
        self.assertEqual(rule["pattern"], "杭州示例服务商")
        self.assertEqual(rule["memo_pattern"], "采购服务费")
        self.assertEqual(rule["code"], "B06")

        c = self.db.conn()
        hit, _, _ = self.db.classify(c, "支", "杭州 示例服务商", "采购服务费", "内曜", 1800)
        varied, _, _ = self.db.classify(c, "支", "杭州示例服务商", "下月服务费", "内曜", 1800)
        miss, _, _ = self.db.classify(c, "支", "杭州示例服务商", "采购服务费", "另一公司", 1800)
        c.commit()
        c.close()
        self.assertEqual(hit, "B06")
        self.assertEqual(varied, "B06")
        self.assertIsNone(miss)

    def test_similar_counterparty_and_conflicting_history(self):
        self.db.save_flow_assignments(self.bid, [{'id': self.row_id, 'code': 'B06'}], '测试员')
        self.db.confirm_batch(self.bid, '测试员')
        result = self.db.import_rows(self.bid, [{
            'date':'2026-11-04','dir':'支','amount':200,'cp':'杭州示例服务商','memo':'仓储物流费', 'src_row':2
        }], '测试员', False)
        self.assertEqual(result['inserted'], 1)
        second = next(r for r in self.db.batch_rows(self.bid) if r['txn_date'] == '2026-11-04')
        self.db.save_flow_assignments(self.bid, [{'id':second['id'],'code':'B02'}], '测试员')
        self.db.confirm_batch(self.bid, '测试员')
        c = self.db.conn()
        matched = self.db.classify(c, '支', '杭州示例服务商', '十月仓储物流服务', '内曜', 200)
        ambiguous = self.db.classify(c, '支', '杭州示例服务商', '电子转账', '内曜', 200)
        c.close()
        self.assertEqual(matched[0], 'B02')
        self.assertIsNone(ambiguous[0])
        self.assertIn('冲突', ambiguous[2])

    def test_memo_fallback_and_high_similarity(self):
        self.db.save_flow_assignments(self.bid, [{'id': self.row_id, 'code':'B06'}], '测试员')
        self.db.confirm_batch(self.bid, '测试员')
        c = self.db.conn()
        similar = self.db.classify(c, '支', '杭州示例服务商店', '电子转账', '内曜', 200)
        memo = self.db.classify(c, '支', '', '十月服务费', '内曜', 200)
        blank = self.db.classify(c, '支', '', '电子转账', '内曜', 200)
        c.close()
        self.assertEqual(similar[0], 'B06')
        self.assertEqual(memo[0], 'B06')
        self.assertIsNone(blank[0])

    def test_similar_multiple_counterparties_are_not_auto_resolved(self):
        import history_match
        def row(name, code, idx):
            return {'scope':'history','direction':'支','entity':'内曜','account':'*',
                    'keyword':name,'memo_pattern':'电子转账','code':code,'id':idx}
        rules = [row('杭州明华科技有限公司', 'B01', 1), row('杭州明达科技有限公司', 'B04', 2)]
        result = history_match.match(rules, '支', '杭州明伟科技有限公司', '', '内曜', '')
        self.assertIsNone(result[0])
        self.assertIn('多家', result[2])

    def test_historical_migration_disables_legacy_rule_without_modifying_flow(self):
        import migrate_history_rules
        c = self.db.conn()
        c.execute("UPDATE flow_raw SET code='B06',status='confirmed',matched_by='manual' WHERE id=?", (self.row_id,))
        c.execute("""INSERT INTO match_rule(pri,direction,scope,keyword,code,enabled,source,entity,account)
                     VALUES(0.45,'支','cp','杭州示例服务商','B01',1,'manual_back','*','*')""")
        before = dict(c.execute('SELECT * FROM flow_raw WHERE id=?', (self.row_id,)).fetchone())
        c.commit()
        c.close()
        dbpath = os.environ['V11_DB']
        preview = migrate_history_rules.migrate(dbpath, self.rulebook)
        self.assertEqual((preview['new_rules'], preview['legacy_rules_to_disable']), (1, 1))
        self.assertEqual(self.rules(), [])
        backup = pathlib.Path(self.tmp.name) / 'backups'
        applied = migrate_history_rules.migrate(dbpath, self.rulebook, True, backup)
        self.assertTrue(pathlib.Path(applied['database_backup']).is_file())
        self.assertTrue(pathlib.Path(applied['rulebook_backup']).is_file())
        c = self.db.conn()
        self.assertEqual(dict(c.execute('SELECT * FROM flow_raw WHERE id=?', (self.row_id,)).fetchone()), before)
        self.assertEqual(c.execute("SELECT enabled FROM match_rule WHERE source='manual_back'").fetchone()[0], 0)
        self.assertEqual(self.db.classify(c, '支', '杭州示例服务商', '不同摘要', '内曜', 100)[0], 'B06')
        c.close()
        again = migrate_history_rules.migrate(dbpath, self.rulebook)
        self.assertEqual((again['new_rules'], again['legacy_rules_to_disable']), (0, 0))

    def test_migration_failure_restores_rulebook_and_database(self):
        import migrate_history_rules
        c = self.db.conn()
        c.execute("UPDATE flow_raw SET code='B06',status='confirmed',matched_by='manual' WHERE id=?", (self.row_id,))
        c.execute("INSERT INTO match_rule(pri,direction,scope,keyword,code,enabled,source) VALUES(0.45,'支','cp','示例','B01',1,'manual_back')")
        c.commit()
        c.close()
        before = self.rulebook.read_bytes()
        with patch.object(self.db, 'sync_rules', return_value={'ok': False, 'reason': '模拟错误'}):
            with self.assertRaisesRegex(RuntimeError, '同步失败'):
                migrate_history_rules.migrate(os.environ['V11_DB'], self.rulebook, True, pathlib.Path(self.tmp.name) / 'backups')
        self.assertEqual(self.rulebook.read_bytes(), before)
        c = self.db.conn()
        self.assertEqual(c.execute("SELECT enabled FROM match_rule WHERE source='manual_back'").fetchone()[0], 1)
        c.close()


    def test_repeat_manual_confirmation_updates_one_learned_rule(self):
        self.db.save_flow_assignments(self.bid, [{"id": self.row_id, "code": "B06"}], "测试员")
        self.db.confirm_batch(self.bid, "测试员")
        self.db.save_flow_assignments(self.bid, [{"id": self.row_id, "code": "B04"}], "测试员")
        got = self.db.confirm_batch(self.bid, "测试员")
        self.assertEqual(got["ruleWarnings"], [])

        rules = self.rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["code"], "B04")
        self.assertEqual(rules[0]["previous_codes"], ["B06"])
        self.assertEqual(rules[0]["confirmations"], 2)

    def test_balance_date_survives_import_and_is_reported_as_of_date(self):
        result = self.db.import_rows(self.bid, [{
            'date': '2026-11-10', 'dir': '支', 'amount': 50,
            'balance': 200, 'balance_date': '2026-11-18',
            'cp': '例示供应商', 'memo': '服务', 'bank': '示例银行', 'account': '123456',
            'src_row': 2,
        }], '测试员', True)
        self.assertEqual(result['inserted'], 1)
        row = next(r for r in self.db.batch_rows(self.bid) if r['account'] == '123456')
        self.assertEqual(row['balance_date'], '2026-11-18')
        self.db.confirm_flows([row['id']], 'B06', '测试员')
        balance = next(x for x in self.db.snapshot('内曜', '2026-11')['balances'] if x['account'] == '123456')
        self.assertEqual((balance['balance'], balance['date']), (200, '2026-11-18'))

    def test_decimal_account_is_not_used_as_scope(self):
        c = self.db.conn()
        c.execute('UPDATE flow_raw SET account=? WHERE id=?', ('45881.01', self.row_id))
        c.commit()
        flow = c.execute('SELECT * FROM flow_raw WHERE id=?', (self.row_id,)).fetchone()
        self.assertEqual(self.db._manual_rule_fields(flow, 'B06')['account'], ['*'])
        c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
