# -*- coding: utf-8 -*-
"""v11 冲减（offset_of=-1 按科目冲减）回归测试

口径（与看板一致）：
  冲减行 = 不挂原笔，snapshot 会把该行「方向取反 + 金额取负」：
    支 100 万 冲减到货款收入 → 记成「收 −100 万」：收入被抵减、支出不再虚增，现金净额不变；
    收 3 万 冲减到其他支出 → 记成「支 −3 万」：支出被抵减。
  只允许冲反方向科目；冲减行确认时不反写规则。
全部使用临时库，不碰工作库。
"""
import os
import sys
import json
import tempfile
import unittest
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
V11 = ROOT / "数仓" / "v11"


class OffsetBySubjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.old_db = os.environ.get("V11_DB")
        self.old_rulebook = os.environ.get("BANK_FLOW_RULEBOOK")
        os.environ["V11_DB"] = str(pathlib.Path(self.tmp.name) / "v11offset.db")
        rulebook = pathlib.Path(self.tmp.name) / "rules.json"
        rulebook.write_text(json.dumps({"schema": 1, "version": "test", "rules": []}, ensure_ascii=False),
                            encoding="utf-8")
        os.environ["BANK_FLOW_RULEBOOK"] = str(rulebook)
        sys.path.insert(0, str(V11))
        for m in ("db11", "xls_read"):
            sys.modules.pop(m, None)
        import db11
        self.db = db11
        self.db.init_db()
        batch, dup = self.db.create_batch("内曜", "2026-11", "冲减测试.xlsx", "sha-test-offset-1")
        self.assertFalse(dup)
        self.bid = batch["id"]
        c = self.db.conn()
        c.execute("UPDATE match_rule SET enabled=0")   # 全部进待确认，便于逐笔控制
        c.commit()
        c.close()
        self.db.import_rows(self.bid, [
            {"date": "2026-11-03", "dir": "收", "amount": 20000.00,
             "cp": "示例药业有限公司", "memo": "货款"},
            {"date": "2026-11-10", "dir": "支", "amount": 1000000.00,
             "cp": "示例药业有限公司", "memo": "退款"},
            {"date": "2026-11-27", "dir": "收", "amount": 30000.00,
             "cp": "示例数据服务商（无历史）", "memo": "退还"},
        ], "测试员", True)
        rows = {r["memo"]: r for r in self.rows()}
        self.rev = rows["货款"]["id"]        # 收 2 万（同对方历史科目 → 冲减建议来源）
        self.pay = rows["退款"]["id"]        # 支 100 万（要冲减货款收入）
        self.back = rows["退还"]["id"]       # 收 3 万（要冲减支出）
        # 先确认一笔同对方的收入，作为「同对方历史科目」
        self.db.save_flow_assignments(self.bid, [{"id": self.rev, "code": "A01"}], "测试员")
        self.db.confirm_flows([self.rev], "A01", "测试员")

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
        self.tmp.cleanup()

    def rows(self):
        return self.db.batch_rows(self.bid)

    def row(self, fid):
        return next(r for r in self.rows() if r["id"] == fid)

    def totals(self):
        snap = self.db.snapshot("内曜", "2026-11")
        inflow = sum(r["amt_cny"] for r in snap["rows"] if r["direction"] == "收")
        outflow = sum(r["amt_cny"] for r in snap["rows"] if r["direction"] == "支")
        return round(inflow, 2), round(outflow, 2)

    # ---------- 1. 支 100 万 → 冲减货款收入 ----------
    def test_offset_moves_direction_and_negates_amount(self):
        r = self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A01", "offset": True}], "测试员")
        self.assertEqual(r["errors"], [])
        got = self.row(self.pay)
        self.assertEqual(got["offset_of"], -1)
        self.assertEqual(got["code"], "A01")
        self.db.confirm_flows([self.pay], "A01", "测试员")
        snap = self.db.snapshot("内曜", "2026-11")
        hit = next(x for x in snap["rows"] if x["id"] == self.pay)
        self.assertEqual(hit["direction"], "收")          # 方向取反
        self.assertEqual(round(hit["amt_cny"], 2), -1000000.00)   # 金额取负
        self.assertEqual(hit["is_offset"], 1)

    # ---------- 2. 同向科目必须被拦住 ----------
    def test_offset_rejects_same_direction_subject(self):
        r = self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "B01", "offset": True}], "测试员")
        self.assertTrue(r["errors"])                       # 支出科目不能冲减一笔支出
        got = self.row(self.pay)
        self.assertIsNone(got["offset_of"])
        self.assertEqual(got["code"] or "", "")

    # ---------- 3. 收 3 万 → 冲减支出 ----------
    def test_offset_on_income_row_offsets_expense(self):
        r = self.db.save_flow_assignments(self.bid, [{"id": self.back, "code": "B06", "offset": True}], "测试员")
        self.assertEqual(r["errors"], [])
        self.db.confirm_flows([self.back], "B06", "测试员")
        snap = self.db.snapshot("内曜", "2026-11")
        hit = next(x for x in snap["rows"] if x["id"] == self.back)
        self.assertEqual(hit["direction"], "支")
        self.assertEqual(round(hit["amt_cny"], 2), -30000.00)

    # ---------- 4. 加总校验：净额守恒，收入与支出各减 100 万 ----------
    def test_net_cash_conserved_and_revenue_reduced(self):
        # 基线：这笔按「支 100 万 · 采购支出」正常记账
        self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "B01"}], "测试员")
        self.db.confirm_flows([self.pay], "B01", "测试员")
        in0, out0 = self.totals()
        # 改冲减：收 −100 万
        self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A01", "offset": True}], "测试员")
        self.assertEqual(self.row(self.pay)["status"], "pending")     # 改数后必须重新确认
        self.db.confirm_flows([self.pay], "A01", "测试员")
        in1, out1 = self.totals()
        self.assertAlmostEqual(in1 - in0, -1000000.00, places=2)       # 收入被抵减
        self.assertAlmostEqual(out1 - out0, -1000000.00, places=2)     # 支出不再虚增
        self.assertAlmostEqual((in1 - out1) - (in0 - out0), 0.00, places=2)   # 净额守恒

    # ---------- 5. 已确认行标冲减 → 待确认 + 批次退回；再确认生效 ----------
    def test_confirmed_row_offset_needs_reconfirm(self):
        self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "B01"}], "测试员")
        self.db.confirm_batch(self.bid, "测试员")
        self.assertEqual(self.db.batch_status(self.bid)["status"], "confirmed")
        r = self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A01", "offset": True}], "测试员")
        self.assertEqual(r["errors"], [])
        got = self.row(self.pay)
        self.assertEqual((got["status"], got["modified"], got["offset_of"]), ("pending", 1, -1))
        self.assertEqual(self.db.batch_status(self.bid)["status"], "parsed")
        self.db.confirm_flows([self.pay], "A01", "测试员")
        got = self.row(self.pay)
        self.assertEqual((got["status"], got["modified"]), ("confirmed", 0))

    # ---------- 6. 取消冲减：清标记，不动科目 ----------
    def test_cancel_offset_clears_flag(self):
        self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A01", "offset": True}], "测试员")
        self.assertEqual(self.row(self.pay)["offset_of"], -1)
        r = self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A01", "offset": False}], "测试员")
        self.assertEqual(r["errors"], [])
        got = self.row(self.pay)
        self.assertIsNone(got["offset_of"])
        self.assertEqual(got["code"], "A01")               # 只取消冲减，科目保留

    # ---------- 7. 继承原笔的冲减行：普通保存不误清 offset_of ----------
    def test_plain_save_keeps_inherited_offset(self):
        c = self.db.conn()
        c.execute("UPDATE flow_raw SET offset_of=? WHERE id=?", (self.rev, self.pay))
        c.commit()
        c.close()
        self.db.save_flow_assignments(self.bid, [{"id": self.pay, "code": "A02"}], "测试员")
        self.assertEqual(self.row(self.pay)["offset_of"], self.rev)

    # ---------- 8. 同对方历史科目推测（UI 下拉默认项来源） ----------
    def test_offset_suggest_from_same_counterparty(self):
        got = self.row(self.pay)
        self.assertEqual(got["offset_suggest"], "A01")      # 同对方反方向的已确认科目
        self.assertEqual(self.row(self.back)["offset_suggest"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
