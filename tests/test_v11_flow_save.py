# -*- coding: utf-8 -*-
"""v11 流水「保存 → 全部确认」回归测试（临时库，不碰工作库）"""
import os
import sys
import json
import tempfile
import unittest
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
V11 = ROOT / "数仓" / "v11"


class FlowSaveConfirmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.old_db = os.environ.get("V11_DB")
        self.old_rulebook = os.environ.get("BANK_FLOW_RULEBOOK")
        os.environ["V11_DB"] = str(pathlib.Path(self.tmp.name) / "v11test.db")
        rulebook = pathlib.Path(self.tmp.name) / "rules.json"
        rulebook.write_text(json.dumps({"schema": 1, "version": "test", "rules": [{
            "id": "TEST-B99", "pri": 1.0, "direction": "支", "match_on": "memo",
            "pattern": "小额验证", "code": "B99", "enabled": True,
            "entity": ["*"], "account": ["*"]
        }]}, ensure_ascii=False),
                            encoding="utf-8")
        os.environ["BANK_FLOW_RULEBOOK"] = str(rulebook)
        sys.path.insert(0, str(V11))
        for m in ("db11", "xls_read"):
            sys.modules.pop(m, None)
        import db11
        self.db = db11
        self.db.init_db()
        batch, dup = self.db.create_batch("内曜", "2026-11", "测试A.xlsx", "sha-test-save-1")
        self.assertFalse(dup)
        self.bid = batch["id"]
        # 停用种子规则，保证两行都进待确认（规则命中会影响「未确认」前提）
        c = self.db.conn()
        c.execute("UPDATE match_rule SET enabled=0")
        c.commit()
        c.close()
        self.db.import_rows(self.bid, [
            {"date": "2026-11-03", "dir": "收", "amount": 1000.0, "cp": "测试客户", "memo": "货款"},
            {"date": "2026-11-04", "dir": "支", "amount": 200.0, "cp": "测试供应商", "memo": "采购"},
        ], "测试员", True)
        # 两行都指定科目（变成「已识别 · 待确认」）
        self.db.save_flow_assignments(self.bid, [
            {"id": r["id"], "code": "A01" if r["direction"] == "收" else "B01"} for r in self.rows()], "测试员")

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

    def test_save_without_change_keeps_confirmed(self):
        """已确认记录点「保存」但科目没变：不标脏、状态不变、批次仍是 confirmed"""
        self.db.confirm_batch(self.bid, "测试员")
        self.assertEqual(self.db.batch_status(self.bid)["status"], "confirmed")
        before = {r["id"]: (r["status"], r["modified"]) for r in self.rows()}
        res = self.db.save_flow_assignments(self.bid, [{"id": r["id"], "code": r["code"]} for r in self.rows()], "测试员")
        self.assertEqual(res["changed"], 0)
        after = {r["id"]: (r["status"], r["modified"]) for r in self.rows()}
        self.assertEqual(before, after)
        self.assertEqual(self.db.batch_status(self.bid)["status"], "confirmed")

    def test_changed_confirmed_needs_reconfirm_and_confirm_clears_flag(self):
        """已确认记录改科目：置未确认（已修改）+ 批次退回 parsed；再「全部确认」后 modified 清零"""
        self.db.confirm_batch(self.bid, "测试员")
        rs = self.rows()
        target = rs[0]
        new_code = "B10" if target["code"] != "B10" else "B11"
        res = self.db.save_flow_assignments(self.bid, [{"id": target["id"], "code": new_code}], "测试员")
        self.assertEqual(res["changed"], 1)
        row = next(r for r in self.rows() if r["id"] == target["id"])
        self.assertEqual((row["status"], row["modified"]), ("pending", 1))
        self.assertEqual(self.db.batch_status(self.bid)["status"], "parsed")
        confirm = self.db.confirm_batch(self.bid, "测试员")
        self.assertGreaterEqual(confirm["confirmed"], 1)
        row = next(r for r in self.rows() if r["id"] == target["id"])
        self.assertEqual((row["status"], row["modified"]), ("confirmed", 0))
        self.assertEqual(self.db.batch_status(self.bid)["status"], "confirmed")

    def test_import_all_rule_hit_marks_batch_confirmed(self):
        """整批都命中规则（无待确认）时，导入后批次直接 confirmed，不留 parsed 死结"""
        c = self.db.conn()
        c.execute("UPDATE match_rule SET enabled=1")
        c.commit()
        c.close()
        batch, dup = self.db.create_batch("内曜", "2026-12", "测试规则命中.xlsx", "sha-test-save-rule")
        self.assertFalse(dup)
        self.db.import_rows(batch["id"], [
            {"date": "2026-12-02", "dir": "支", "amount": 5.0, "cp": "", "memo": "小额验证"},
            {"date": "2026-12-03", "dir": "支", "amount": 5.0, "cp": "", "memo": "小额验证2"},
        ], "测试员", True)
        st = self.db.batch_status(batch["id"])
        self.assertEqual(st["n_pending"], 0)
        self.assertEqual(st["n_confirmed"], st["n_rows"])
        self.assertEqual(st["status"], "confirmed")
        self.assertTrue(st["confirmed_at"])

    def test_confirm_selected_refreshes_batch_counts(self):
        """「确认所选」逐笔确认后，批次计数同步；全批确认完批次才置 confirmed"""
        rs = self.rows()
        first = rs[0]
        self.db.confirm_flows([first["id"]], first["code"] or "A01", "测试员")
        st = self.db.batch_status(self.bid)
        self.assertEqual(st["n_confirmed"], 1)
        self.assertEqual(st["n_rows"], 2)
        self.assertEqual(st["status"], "parsed")
        left = [r for r in self.rows() if r["status"] != "confirmed"]
        for r in left:
            self.db.confirm_flows([r["id"]], r["code"] or "A01", "测试员")
        st = self.db.batch_status(self.bid)
        self.assertEqual(st["n_confirmed"], 2)
        self.assertEqual(st["n_pending"], 0)
        self.assertEqual(st["status"], "confirmed")

    def test_save_pending_row_keeps_pending(self):
        """未确认行保存科目：只写科目、保持 pending，计数同步"""
        rs = self.rows()
        r0 = next(r for r in rs if r["direction"] == "支")
        self.db.save_flow_assignments(self.bid, [{"id": r0["id"], "code": "B10"}], "测试员")
        row = next(r for r in self.rows() if r["id"] == r0["id"])
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["code"], "B10")
        self.assertNotEqual(self.db.batch_status(self.bid)["status"], "confirmed")

    def test_save_without_batch_id_does_not_crash(self):
        """没有打开具体批次（batchId 缺省）时保存不应抛错"""
        rs = self.rows()
        res = self.db.save_flow_assignments(0, [{"id": rs[0]["id"], "code": "A01"}], "测试员")
        self.assertEqual(res["saved"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
