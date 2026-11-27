import pathlib
import sys
import unittest
import io

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '数仓' / 'v11'))
import pdf_flow


def word(text, x, y, width=10):
    return {'text': text, 'x0': x, 'x1': x + width, 'top': y}


class FakePage:
    width = 600

    def __init__(self, words):
        self.words = words

    def extract_words(self, **kwargs):
        return self.words


class FakeTablePage:
    def __init__(self, table):
        self.table = table

    def extract_tables(self):
        return [self.table]


class PdfFlowTests(unittest.TestCase):
    def test_synthetic_ccb_pdf_end_to_end(self):
        try:
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            from reportlab.platypus import Table, TableStyle
        except ImportError:
            self.skipTest('reportlab is needed only to generate a synthetic PDF')
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        data = io.BytesIO()
        c = canvas.Canvas(data, pagesize=(900, 500))
        c.setFont('STSong-Light', 10)
        c.drawString(20, 470, '中国建设银行账户明细信息')
        c.drawString(20, 450, '本方户名：测试公司 查询时间段：20261001 - 20261031')
        header = ['账号', '交易时间', '借方发生额', '贷方发生额', '余额', '币种', '对方户名', '对方账号', '摘要', '备注']
        rows = [header,
                ['12345678', '20261001093000', '20.00', '0.00', '80.00', '人民币元', '测试供应商', '123456', '转账', '采购'],
                ['12345678', '20261001103000', '0.00', '50.00', '130.00', '人民币元', '测试客户', '', '回款', '']]
        table = Table(rows, colWidths=[90, 120, 75, 75, 75, 70, 100, 90, 80, 80], rowHeights=25)
        table.setStyle(TableStyle([('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'), ('FONTSIZE', (0, 0), (-1, -1), 8), ('GRID', (0, 0), (-1, -1), 0.5, 'black')]))
        table.wrapOn(c, 900, 400)
        table.drawOn(c, 10, 340)
        c.save()
        parsed = pdf_flow.parse_pdf(data.getvalue())
        self.assertEqual(parsed['currency'], 'CNY')
        self.assertEqual(parsed['owner'], '测试公司')
        self.assertEqual(len(parsed['rows']), 3)
        self.assertEqual(parsed['rows'][1][10], '09:30:00')

    def test_ccb_multiline_fields_and_balance_chain(self):
        header = ['账号', '交易\n时间', '借方发\n生额', '贷方发\n生额', '余额', '币种',
                  '对方户\n名', '对方账\n号', '摘要', '备注', '账户明细编号-交易流水号']
        rows = [header,
                ['1234\n5678', '20261001\n09:30:00', '20.00', '0.00', '80.00', '人民币\n元', '测试\n供应商', '123\n456', '转账', '采购\n服务', '001'],
                ['12345678', '20261001\n10:30:00', '0.00', '50.00', '130.00', '人民币元', '测试客户', '', '回款', '', '002']]
        owner, parsed, warnings, currency = pdf_flow._ccb([FakeTablePage(rows)], '本方户名：测试公司 查询时间段')
        self.assertEqual((owner, currency, len(parsed), warnings), ('测试公司', 'CNY', 2, []))
        self.assertEqual(parsed[0], ['2026-10-01', '支', 20.0, 80.0, '测试供应商', '123456', '转账 采购服务', '12345678', '建设银行', '2026-10-01', '09:30:00'])
        rows[2][4] = '131.00'
        with self.assertRaisesRegex(ValueError, '余额链'):
            pdf_flow._ccb([FakeTablePage(rows)], '本方户名：测试公司 查询时间段')

    def test_ccb_rejects_incomplete_page_and_ambiguous_debits(self):
        header = ['账号', '交易时间', '借方发生额', '贷方发生额', '余额', '币种', '对方户名', '对方账号', '摘要', '备注']
        both = [header, ['12345678', '20261001', '1.00', '1.00', '10.00', '人民币元', '', '', '', '']]
        with self.assertRaisesRegex(ValueError, '方向'):
            pdf_flow._ccb([FakeTablePage(both)], '本方户名：测试公司 查询时间段')
        with self.assertRaisesRegex(ValueError, '表头'):
            pdf_flow._ccb([FakeTablePage([['无关']])], '本方户名：测试公司 查询时间段')

    def test_synthetic_cmb_pdf_end_to_end(self):
        try:
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        except ImportError:
            self.skipTest('reportlab is needed only to generate a synthetic PDF')
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        data = io.BytesIO()
        c = canvas.Canvas(data, pagesize=(600, 800))
        c.setFont('STSong-Light', 10)
        def put(x, y, text):
            c.drawString(x, 800 - y, text)
        put(10, 20, '账务明细清单')
        put(10, 40, '账号:123456 货币:人民币')
        put(10, 55, '账户名称:测试公司 上页余额:100.00')
        put(10, 70, '日期 业务类型 票据号 摘要 借方/贷方金额 余额 对手户名')
        put(5, 100, '20260901'); put(100, 100, '付款'); put(335, 100, '-20.00')
        put(400, 100, '80.00'); put(470, 100, '供应商')
        put(10, 130, '期末余额:80.00')
        c.save()
        parsed = pdf_flow.parse_pdf(data.getvalue())
        self.assertEqual(parsed['owner'], '测试公司')
        self.assertEqual(parsed['rows'][1][:5], ['2026-09-01', '支', 20.0, 80.0, '供应商'])

    def test_synthetic_ocbc_pdf_end_to_end(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest('reportlab is needed only to generate a synthetic PDF')
        data = io.BytesIO()
        c = canvas.Canvas(data, pagesize=(600, 800))
        def put(x, y, text):
            c.drawString(x, 800 - y, text)
        put(10, 20, 'Daily Statement of Account - Details   OCBC')
        put(10, 40, 'Account Number 123456 - HKD Opening Balance 200.00 18-Nov-2026')
        put(10, 55, 'Account Name EXAMPLE LIMITED Ledger Balance 200.00 18-Nov-2026')
        put(10, 70, 'Debits 1 Amount 50.00')
        put(10, 85, 'Credits 1 Amount 100.00')
        put(10, 120, '01 Nov 2026'); put(250, 120, 'INVOICE'); put(510, 120, '100.00')
        put(10, 132, '01 Nov 2026'); put(90, 132, 'DIRECT CREDIT')
        put(10, 160, '10 Nov 2026'); put(250, 160, 'FEE'); put(420, 160, '50.00')
        put(10, 172, '10 Nov 2026'); put(90, 172, 'SERVICE FEE')
        c.save()
        parsed = pdf_flow.parse_pdf(data.getvalue())
        self.assertEqual(parsed['owner'], 'EXAMPLE LIMITED')
        self.assertEqual(len(parsed['rows']), 3)
        self.assertEqual(parsed['rows'][-1][9], '2026-11-18')

    def test_ocbc_uses_amount_column_and_separate_balance_date(self):
        text = ('Account Number 123456 - HKD Opening Balance 200.00 18-Nov-2026\n'
                'Account Name EXAMPLE LIMITED Ledger Balance 200.00 18-Nov-2026\n'
                'Debits 1 Amount 50.00\nCredits 1 Amount 100.00')
        p = FakePage([
            word('01', 5, 100), word('Nov', 17, 100), word('2026', 32, 100),
            word('INVOICE', 250, 100), word('100.00', 510, 100),
            word('01', 5, 112), word('Nov', 17, 112), word('2026', 32, 112), word('CREDIT', 70, 112),
            word('10', 5, 130), word('Nov', 17, 130), word('2026', 32, 130),
            word('FEE', 250, 130), word('50.00', 420, 130),
            word('10', 5, 142), word('Nov', 17, 142), word('2026', 32, 142), word('SERVICE', 70, 142),
        ])
        owner, rows, warnings = pdf_flow._ocbc([p], text)
        self.assertEqual(owner, 'EXAMPLE LIMITED')
        self.assertEqual([r[1] for r in rows], ['收', '支'])
        self.assertIsNone(rows[0][3])
        self.assertEqual((rows[1][0], rows[1][3], rows[1][9]), ('2026-11-10', 200.0, '2026-11-18'))
        self.assertTrue(warnings)

    def test_ocbc_rejects_mismatched_summary(self):
        text = ('Account Number 123 - HKD Opening Balance 5.00 18-Nov-2026\n'
                'Account Name EXAMPLE Ledger Balance 5.00 18-Nov-2026\n'
                'Debits 0 Amount 0.00\nCredits 1 Amount 200.00')
        p = FakePage([word('01', 5, 100), word('Nov', 17, 100), word('2026', 32, 100), word('100.00', 510, 100)])
        with self.assertRaisesRegex(ValueError, '汇总不符'):
            pdf_flow._ocbc([p], text)

    def test_cmb_balance_chain_and_owner(self):
        text = '账号:123456 货币:人民币\n账户名称:测试公司 上页余额:100.00\n期末余额:80.00'
        p = FakePage([word('20260901', 5, 100, 45), word('付款', 100, 100),
                      word('-20.00', 335, 100), word('80.00', 400, 100), word('供应商', 470, 100)])
        owner, rows, _ = pdf_flow._cmb([p], text)
        self.assertEqual(owner, '测试公司')
        self.assertEqual(rows[0][:5], ['2026-09-01', '支', 20.0, 80.0, '供应商'])
        with self.assertRaisesRegex(ValueError, '余额'):
            pdf_flow._cmb([p], text.replace('100.00', '101.00'))


if __name__ == '__main__':
    unittest.main()
