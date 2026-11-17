import pathlib,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'数仓'))
import db,db_eco

class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_PATH
        db.DB_PATH=str(pathlib.Path(self.tmp.name)/'warehouse.db');db.init()
    def tearDown(self):
        db.DB_PATH=self.old;self.tmp.cleanup()
    def test_zero_day_term_survives_roundtrip(self):
        db_eco.terms_save([{'customer':'测试客户','term_days':0}])
        self.assertEqual(db_eco.terms_load()[0]['term_days'],0)
    def test_invalid_manual_batch_is_atomic(self):
        with self.assertRaises((ValueError,TypeError)):
            db.save_manual({'2026-08-01|100|A':{'l1':'管理费用'},'bad|amount|B':{'l1':'管理费用'}})
        self.assertEqual(db.load_manual(),{})
    def test_edit_moves_label_without_leaving_stale_key(self):
        db.save_flow([{'交易日期':'2026-08-01','月份':'2026.08','收支方向':'支','金额':'100','对方户名':'A','摘要':'原备注'}],'test.xlsx')
        row=db.load_flow(True)[0];old='2026-08-01|100|A';new='2026-08-01|100|B'
        db.save_manual({old:{'l1':'管理费用','l2':'办公费','cf':'经营活动'}})
        result=db.edit_flow(row['__id'],{'对方户名':'B'})
        self.assertTrue(result['ok']);self.assertIn(new,db.load_manual());self.assertNotIn(old,db.load_manual())
    def test_edit_memo_and_label_atomically_with_unchanged_key(self):
        db.save_flow([{'交易日期':'2026-08-01','月份':'2026.08','收支方向':'支','金额':'100','对方户名':'A','摘要':'原备注'}],'test.xlsx')
        row=db.load_flow(True)[0];key='2026-08-01|100|A';other='2026-08-01|200|B'
        db.save_manual({key:{'l1':'原分类'},other:{'l1':'其他分类'}})
        result=db.edit_flow(row['__id'],{'摘要':'修正摘要'},label={'l1':'管理费用','l2':'办公费','cf':'经营活动'})
        self.assertTrue(result['ok']);self.assertEqual(db.load_manual()[key]['l2'],'办公费')
        self.assertEqual(db.load_flow(True)[0]['摘要'],'修正摘要');self.assertEqual(db.load_manual()[other]['l1'],'其他分类')
        db.edit_flow(row['__id'],{'摘要':'仅修改备注'},label={})
        self.assertEqual(db.load_manual()[key]['l2'],'办公费')
        self.assertFalse(db.edit_flow(row['__id'],{'金额':'不是金额'})['ok'])
        self.assertEqual(float(db.load_flow(True)[0]['金额']),100)
    def test_formatted_bank_amount_can_be_edited_without_losing_its_label(self):
        db.save_flow([{'交易日期':'2026-08-21','收支方向':'支','金额':'9,002.00','对方户名':'A'}],'bank.xlsx')
        row=db.load_flow(True)[0];key='2026-08-21|9002|A'
        db.save_manual({key:{'l1':'管理费用','l2':'办公费','cf':'经营活动'}})
        result=db.edit_flow(row['__id'],{'摘要':'保存格式化金额对应流水'},label={'l1':'销售费用','l2':'费用报销','cf':'经营活动'})
        self.assertTrue(result['ok']);self.assertEqual(result['new_key'],key)
        self.assertEqual(db.load_manual()[key]['l1'],'销售费用')

if __name__=='__main__':unittest.main()
