"""从当前数仓字典生成空白模板，不包含历史预算金额。"""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
import db_eco


def plan_template():
    data = db_eco.dict_all()
    params = db_eco.params_map()
    wb = Workbook()
    ws = wb.active
    ws.title = "预算明细"
    ws.append(["生态多主体月度资金预算与资金计划表"])
    ws.merge_cells("A1:N1")
    ws.append(["在系统中选择预算期间；按科目字典填写，金额填正数，50万元及以上请填写说明。折人民币由系统按币种汇率复算。"])
    ws.merge_cells("A2:N2")
    ws.append(["序号", "公司主体", "业务板块", "境内外", "币种", "收支方向", "科目大类",
               "科目明细", "预算金额(原币)", "折人民币(CNY)", "资金时点", "责任人", "审批状态", "备注"])
    subjects = wb.create_sheet("科目字典")
    subjects.append(["收支方向", "科目大类", "科目明细", "现金流量属性", "说明"])
    for r in data["subjects"]:
        subjects.append([r.get(k) for k in ("direction", "cat", "subject", "cf_attr", "note")])
    entities = wb.create_sheet("主体台账")
    entities.append(["公司全称", "业务板块", "境内外", "币种", "银行账户", "是否合并", "是否归集", "负责人", "备注"])
    for r in data["entities"]:
        entities.append([r.get(k) for k in ("name", "segment", "region", "currency", "account", "consolidated", "pooling", "owner", "note")])
    fx = wb.create_sheet("记账汇率")
    fx.append(["币种", "名称", "汇率(人民币)"])
    for r in data["fx"]:
        fx.append([r.get("currency"), r.get("name"), float(params.get("fx." + r["currency"], r["rate_cny"]))])
    for column, formula in [
        ("B", 'INDIRECT("\'主体台账\'!$A$2:$A$%s")' % max(2, entities.max_row)),
        ("E", 'INDIRECT("\'记账汇率\'!$A$2:$A$%s")' % fx.max_row),
        ("F", '"流入,流出"'),
        ("H", 'INDIRECT("\'科目字典\'!$C$2:$C$%s")' % subjects.max_row),
    ]:
        if column == "B" and entities.max_row < 2:
            continue  # 新安装尚无主体，允许先填写自己的主体台账。
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        dv.showErrorMessage = True
        dv.error = "请从下拉列表选择已有值"
        ws.add_data_validation(dv)
        dv.add("%s4:%s503" % (column, column))
    for sheet in wb:
        header = 3 if sheet == ws else 1
        sheet.freeze_panes = "A%d" % (header + 1)
        for cell in sheet[header]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = PatternFill("solid", fgColor="1F3864")
            cell.alignment = Alignment(wrap_text=True)
        sheet.row_dimensions[header].height = 32
        for col in "ABCDEFGHIJKLMN":
            sheet.column_dimensions[col].width = 20
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["H"].width = 32
    ws.column_dimensions["N"].width = 48
    for row in ws.iter_rows(min_row=4, max_row=503, min_col=9, max_col=10):
        for cell in row:
            cell.number_format = '#,##0.00'
    ws.auto_filter.ref = "A3:N503"
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


def flow_template():
    """银行流水空白模板；不附加演示交易。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "流水明细"
    ws.append(["月份", "交易日期", "收支方向", "金额", "对方户名", "摘要",
               "收支科目(一级)", "明细科目(二级)", "现金流量分类"])
    ws.freeze_panes = "A2"
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="1F3864")
        ws.column_dimensions[cell.column_letter].width = 24
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()
