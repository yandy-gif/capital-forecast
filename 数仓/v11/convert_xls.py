# -*- coding: utf-8 -*-
"""convert_xls.py —— 把旧版 .xls 批量转成 .xlsx（浏览器无法直读 BIFF8）

用法：
  1) 把 .xls 文件（可多个）拖到「转换xls.bat」上；
  2) 或命令行：.venv/Scripts/python.exe 数仓/v11/convert_xls.py 文件1.xls 目录2

输出：同目录下同名 .xlsx，不覆盖原文件。只搬运值，不保留格式/图表。
"""
import os
import sys

import xlrd
from openpyxl import Workbook
from openpyxl.utils import get_column_letter


def convert(path):
    out = os.path.splitext(path)[0] + '.xlsx'
    if os.path.abspath(out) == os.path.abspath(path):
        out = os.path.splitext(path)[0] + '_转换.xlsx'
    book = xlrd.open_workbook(path, formatting_info=False)
    wb = Workbook()
    wb.remove(wb.active)
    for sh in book.sheets():
        ws = wb.create_sheet(sh.name[:31] or 'Sheet')
        for r in range(sh.nrows):
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        v = xlrd.xldate.xldate_as_datetime(v, book.datemode).strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass
                elif cell.ctype == xlrd.XL_CELL_NUMBER:
                    v = int(v) if float(v).is_integer() else v
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                ws.cell(row=r + 1, column=c + 1, value=v)
        for c in range(sh.ncols):
            ws.column_dimensions[get_column_letter(c + 1)].width = 16
    wb.save(out)
    return out


def main(argv):
    targets = []
    for a in argv:
        if os.path.isdir(a):
            targets += [os.path.join(a, f) for f in os.listdir(a) if f.lower().endswith('.xls')]
        elif a.lower().endswith('.xls'):
            targets.append(a)
    if not targets:
        print('没有找到 .xls。用法：convert_xls.py 文件.xls [目录]')
        return 1
    for p in targets:
        try:
            print('OK  ', p, '->', convert(p))
        except Exception as e:
            print('FAIL', p, e)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
