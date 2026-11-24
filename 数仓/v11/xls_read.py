# -*- coding: utf-8 -*-
"""xls_read.py —— 服务端读取旧版 Excel（BIFF8 / .xls）

浏览器无法直读 .xls，这里用 xlrd 转成二维数组回给前端，
前端继续走同一套表头识别与入库逻辑（不产生第二套解析口径）。

兼容三种"假 xls"：
  1) 真正的 OLE2 复合文档（BIFF8）
  2) 实为 xlsx（ZIP）——交给 openpyxl
  3) 实为 HTML/XML 表格（部分银行导出）——按 <td>/<tr> 提取
"""
import re
import xlrd


def _cell(sh, r, c, datemode):
    cell = sh.cell(r, c)
    v = cell.value
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate.xldate_as_datetime(v, datemode).strftime("%Y-%m-%d")
        except Exception:
            return v
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        return int(v) if float(v).is_integer() else v
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return ""
    return v


def rows_from_xls(data):
    wb = xlrd.open_workbook(file_contents=data)
    out, sheets = [], []
    for sh in wb.sheets():
        sheets.append(sh.name)
        for r in range(sh.nrows):
            out.append([_cell(sh, r, c, wb.datemode) for c in range(sh.ncols)])
    return {"rows": out, "sheets": sheets, "kind": "xls"}


def rows_from_xlsx(data):
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(data), data_only=True, read_only=True)
    out, sheets = [], []
    for ws in wb.worksheets:
        sheets.append(ws.title)
        for row in ws.iter_rows(values_only=True):
            out.append(["" if v is None else v for v in row])
    return {"rows": out, "sheets": sheets, "kind": "xlsx"}


def rows_from_html(data):
    txt = data.decode("utf-8", "ignore")
    if "charset=gb" in txt.lower() or "charset=\"gb" in txt.lower():
        txt = data.decode("gbk", "ignore")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", txt, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        clean = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in cells]
        if any(clean):
            rows.append(clean)
    return {"rows": rows, "sheets": ["HTML"], "kind": "html"}


def parse_any(data, filename=""):
    head = data[:8]
    if head[:2] == b"PK":                       # xlsx / xlsm
        return rows_from_xlsx(data)
    if head[:4] == b"\xd0\xcf\x11\xe0":          # OLE2 → BIFF8
        try:
            return rows_from_xls(data)
        except Exception:
            pass
    low = data[:400].lower()
    if b"<html" in low or b"<table" in low or b"<?xml" in low:
        return rows_from_html(data)
    # 兜底：按扩展名再试一次
    if filename.lower().endswith(".xls"):
        return rows_from_xls(data)
    raise ValueError("无法识别的文件格式（既不是 xls/xlsx，也不是 HTML 表格）")
