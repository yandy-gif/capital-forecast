import io
import pathlib
import sys
import tempfile
import unittest
from contextlib import closing

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '数仓'))
import db, db_eco, seed_eco, template_export
from openpyxl import load_workbook


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = str(pathlib.Path(self.tmp.name) / 'warehouse.db')
        seed_eco.seed_defaults()

    def tearDown(self):
        db.DB_PATH = self.old
        self.tmp.cleanup()

    def test_fresh_install_has_no_business_data(self):
        self.assertEqual(db.load_flow(), [])
        self.assertEqual(db.load_plan(), [])
        self.assertEqual(db.load_manual(), {})
        self.assertEqual(db_eco.submission_list(), [])
        self.assertEqual(db_eco.terms_load(), [])
        self.assertEqual(db_eco.dict_all()['entities'], [])
        self.assertGreater(len(db_eco.dict_all()['subjects']), 0)
        for generate in (template_export.plan_template, template_export.flow_template):
            wb = load_workbook(io.BytesIO(generate()))
            if '主体台账' in wb.sheetnames:
                self.assertEqual(wb['主体台账'].max_row, 1)
                self.assertIsNone(wb['预算明细']['I4'].value)
            else:
                self.assertEqual(wb.active.max_row, 1)
            wb.close()

    def test_restart_preserves_user_configuration(self):
        db_eco.params_save({'safe.line': '7654'})
        db_eco.bridge_save([{'id':'B-01','direction':'全','l1':'自定义','handle':'挂起','status':'停用'}])
        db_eco.terms_save([{'customer':'测试客户','term_days':0}])
        seed_eco.seed_defaults()
        self.assertEqual(db_eco.params_map()['safe.line'], '7654')
        self.assertEqual(next(b['status'] for b in db_eco.bridge_load() if b['id']=='B-01'), '停用')
        self.assertEqual(db_eco.terms_load()[0]['term_days'], 0)

    def test_appendix_schema_exists_on_fresh_install(self):
        count = db_eco.save_appendix(1, '2027-01', '测试主体', 1,
                                    [{'kind':'费用申报明细','item':'测试项','amount':1}])
        self.assertEqual(count, 1)
        with closing(db.connect()) as conn:
            self.assertEqual(conn.execute('SELECT amount FROM plan_appendix').fetchone()[0], 1)
