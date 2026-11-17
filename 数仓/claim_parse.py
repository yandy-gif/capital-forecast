# -*- coding: utf-8 -*-
"""预算提报源表 → by 月资金计划表（按 C-01~C-09 规则拆解）

适用源表：《珀研第四季度预算提报多维管理系统》
  商品主数据 / 客户主数据 / 销售提报表 / 部门费用提报表 / 人力资源预算提报表

规则（编号沿用《资金计划-规则化与流水匹配设计 v1》与配置包 01 转换规则）：
  C-01 回款 = 件数 × 售价 × 扣率；**禁止读源表「收入金额」列**（该列是错误值，实测为正确值的平方）
  C-02 客户分类 = KA 才计入主表；非 KA 不进合计，写「已剔除」附页
  C-03 扣率为空且客户分类 = 中心化 → 按客户主数据扣率补算（小程序 = 100%）
  C-04 扣率为空且非中心化 → 不进主表，写「待补清单」
  C-05 部门费用类型 → 计划科目（营销费/差旅费/招待费/其他杂费）
  C-06 人力资源预算按「预算说明」实质归类（招聘/培训/团建/福利/系统）
  C-07 人力预算不含工资社保 → 不估算不补数，表头标注「薪酬缺口待补」
  C-08 汇总粒度：流入按客户 1 行、流出按科目 1 行；明细进附页
  C-09 科目必须命中科目字典，出现字典外语汇直接报错（不允许自由文本）

输出：按期间分组的计划明细（可直接进审批流与数仓）+ 附页 + 待补/剔除清单 + 规则命中统计。
"""
import re
import datetime

from openpyxl import load_workbook

# ---------- 科目映射（C-05） ----------
DEPT_EXP_SUBJECT = {
    "营销费": "市场推广及渠道佣金",
    "营销费用": "市场推广及渠道佣金",
    "差旅费": "差旅及业务招待",
    "招待费": "差旅及业务招待",
    "业务招待费": "差旅及业务招待",
    "其他杂费": "其他经营流出",
    "办公费": "其他经营流出",
    "其他": "其他经营流出",
}
# ---------- 人力预算按说明归类（C-06）：命中关键词即归到对应科目 ----------
HR_KEYWORDS = [
    ("人力薪酬及社保公积金", ["团建", "拓展", "体检", "福利", "节日", "礼品", "心理健康", "工会"]),
    ("IT与软件订阅", ["系统", "软件", "IT", "信息化", "模块开发", "数据迁移", "SaaS"]),
    ("其他经营流出", ["招聘", "猎头", "展位", "培训", "讲师", "教材", "学习", "认证", "咨询"]),
]
FALLBACK_SUBJECT = "其他经营流出"

SHEET_SALES = ["销售提报表", "销售提报", "销售"]
SHEET_DEPT = ["部门费用提报表", "部门费用", "费用提报表"]
SHEET_HR = ["人力资源预算提报表", "人力资源", "人力预算"]
SHEET_GOODS = ["商品主数据", "商品"]
SHEET_CUST = ["客户主数据", "客户"]


def _s(v):
    if v is None:
        return ""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).replace("\u3000", " ").strip()


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _s(v).replace("￥", "").replace("¥", "").replace(",", "").replace("，", "").replace(" ", "")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group(0)) if m else None


def _period(v, fallback=""):
    s = _s(v)
    m = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})", s)
    if m:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    m = re.search(r"(20\d{2})(\d{2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return "%s-%s" % (m.group(1), m.group(2))
    return fallback


def _pick(wb, keys, exclude=()):
    for ws in wb.worksheets:
        if any(k in ws.title for k in keys) and not any(x in ws.title for x in exclude):
            return ws
    return None


def _rows(ws, head_scan=12):
    """定位表头行（第一行含 ≥3 个非空文本），返回 [{表头: 值}]"""
    if ws is None:
        return []
    hdr_row, cols = None, {}
    for r in range(1, min(ws.max_row, head_scan) + 1):
        vals = [_s(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 30) + 1)]
        filled = [v for v in vals if v]
        if len(filled) >= 3 and sum(1 for v in filled if 1 <= len(v) <= 14) >= 3:
            hdr_row = r
            cols = {v: i + 1 for i, v in enumerate(vals) if v}
            break
    if not hdr_row:
        return []
    out = []
    for r in range(hdr_row + 1, ws.max_row + 1):
        row = {name: ws.cell(r, idx).value for name, idx in cols.items()}
        if any(_s(v) for v in row.values()):
            row["__row__"] = r
            out.append(row)
    return out


def _g(row, *names, default=""):
    for n in names:
        for k in row:
            if k == "__row__":
                continue
            if _s(k) == n:
                return row[k]
    for n in names:                       # 模糊匹配（表头带单位/括号）
        for k in row:
            if k != "__row__" and n in _s(k):
                return row[k]
    return default


def parse_claim(path, period=None, dicts=None, entity_default=""):
    wb = load_workbook(path, data_only=True)
    ws_sales, ws_dept, ws_hr = _pick(wb, SHEET_SALES), _pick(wb, SHEET_DEPT), _pick(wb, SHEET_HR)
    if not (ws_sales or ws_dept or ws_hr):
        return {"ok": False, "error": "没找到「销售提报表 / 部门费用提报表 / 人力资源预算提报表」中的任何一张，"
                                     "既不是已拆解的计划表，也不是可识别的提报源表", "sheets": wb.sheetnames}

    goods, cust = {}, {}
    for row in _rows(_pick(wb, SHEET_GOODS)):
        name = _s(_g(row, "商品名称"))
        if name:
            goods[name] = {"price": _num(_g(row, "OTC售价", "售价")), "cost": _num(_g(row, "进货价")),
                           "series": _s(_g(row, "系列"))}
    for row in _rows(_pick(wb, SHEET_CUST)):
        name = _s(_g(row, "客户名称"))
        if name:
            cust[name] = {"klass": _s(_g(row, "客户分类")), "rate": _num(_g(row, "客户扣率"))}

    subj_set = {(s["direction"], s["subject"]) for s in (dicts or {}).get("subjects", [])}
    entities = (dicts or {}).get("entities", [])
    ent_name = entity_default or (entities[0]["name"] if len(entities) == 1 else "")
    if not ent_name:
        wb.close()
        return {"ok": False, "error": "源表转换需要明确公司主体：请先通过计划模板维护唯一主体台账；多主体请分别填写标准预算明细后上传。"}

    warnings, pending, excluded, sales_detail, exp_detail = [], [], [], [], []
    hits = {"C-01": 0, "C-02": 0, "C-03": 0, "C-04": 0, "C-05": 0, "C-06": 0, "C-07": 0}
    per = {}          # period -> {"in": {客户: 金额}, "out": {科目: 金额}}

    def bucket(p):
        p = p or (period or "")
        return per.setdefault(p, {"in": {}, "out": {}, "in_src": {}, "out_src": {}})

    # ---------- 销售提报 → 回款（流入） ----------
    for row in _rows(ws_sales):
        p = _period(_g(row, "提报月份"), period or "")
        qty = _num(_g(row, "提报件数", "件数")) or 0
        gname = _s(_g(row, "关联商品", "商品名称"))
        cname = _s(_g(row, "关联客户", "客户名称", "其他客户名称"))
        rate = _num(_g(row, "客户实际扣率", "扣率"))
        by = _s(_g(row, "销售人员", "提报人"))
        if not (qty or gname or cname):
            continue
        info = goods.get(gname, {})
        price = info.get("price") or _num(_g(row, "商品售价", "售价"))
        klass = (cust.get(cname, {}) or {}).get("klass", "")
        if rate is None:
            mrate = (cust.get(cname, {}) or {}).get("rate")
            if klass == "中心化" and mrate is not None:
                rate = mrate
                hits["C-03"] += 1
            else:
                hits["C-04"] += 1
                pending.append({"类型": "扣率待补", "客户": cname, "商品": gname, "件数": qty,
                                "客户分类": klass, "源行号": row.get("__row__")})
                continue
        if price is None:
            pending.append({"类型": "售价待补", "客户": cname, "商品": gname, "件数": qty,
                            "源行号": row.get("__row__")})
            continue
        amt = round(qty * price * rate, 2)          # C-01：件数 × 售价 × 扣率
        hits["C-01"] += 1
        rec = {"客户": cname, "客户类型": klass or "未标注", "商品名称": gname, "系列": info.get("series", ""),
               "件数": qty, "售价(元)": price, "扣率": rate, "回款金额(元)": amt,
               "进货价(元)": info.get("cost"), "成本金额(元)": round(qty * (info.get("cost") or 0), 2),
               "提报人": by, "期间": p, "源行号": row.get("__row__")}
        if klass == "KA":
            b = bucket(p)
            b["in"][cname] = round(b["in"].get(cname, 0) + amt, 2)
            b["in_src"].setdefault(cname, []).append(rec)
            hits["C-02"] += 1
        else:
            excluded.append(rec)                    # C-02：非 KA 不进主表
        sales_detail.append(rec)

    # ---------- 部门费用 → 流出 ----------
    for row in _rows(ws_dept):
        amt = _num(_g(row, "费用金额", "金额")) or 0
        if not amt:
            continue
        p = _period(_g(row, "提报月份"), period or "")
        etype = _s(_g(row, "费用类型"))
        subj = DEPT_EXP_SUBJECT.get(etype) or FALLBACK_SUBJECT
        hits["C-05"] += 1
        dept = _s(_g(row, "提报部门", "部门")) or "多部门"
        b = bucket(p)
        b["out"][subj] = round(b["out"].get(subj, 0) + amt, 2)
        exp_detail.append({"来源表": "部门费用提报表", "科目明细": subj, "责任部门": dept,
                           "提报人": _s(_g(row, "提报人")), "费用名称": _s(_g(row, "费用名称")),
                           "费用类型": etype, "金额(元)": amt,
                           "说明": _s(_g(row, "费用说明")), "源行号": row.get("__row__"), "期间": p})
        b["out_src"].setdefault(subj, []).append(dept)

    # ---------- 人力资源预算 → 流出（按说明归类，C-06） ----------
    hr_has_payroll = False
    for row in _rows(ws_hr):
        amt = _num(_g(row, "预算金额", "金额")) or 0
        if not amt:
            continue
        p = _period(_g(row, "提报月份"), period or "")
        item = _s(_g(row, "预算项目"))
        memo = _s(_g(row, "预算说明", "说明"))
        subj = FALLBACK_SUBJECT
        for s, kws in HR_KEYWORDS:
            if any(k.lower() in (memo + item).lower() for k in kws):
                subj = s
                break
        if re.search(r"工资|薪酬|社保|公积金", item + memo):
            hr_has_payroll = True
        hits["C-06"] += 1
        dept = _s(_g(row, "预算部门", "部门")) or "人力资源部"
        b = bucket(p)
        b["out"][subj] = round(b["out"].get(subj, 0) + amt, 2)
        exp_detail.append({"来源表": "人力资源预算提报表", "科目明细": subj, "责任部门": dept,
                           "提报人": _s(_g(row, "提报人")),
                           "费用名称": item + ("（原填「%s」）" % item if False else ""),
                           "费用类型": "人力预算", "金额(元)": amt, "说明": memo,
                           "源行号": row.get("__row__"), "期间": p})
        b["out_src"].setdefault(subj, []).append(dept)
    if not hr_has_payroll:
        hits["C-07"] += 1
        warnings.append("C-07：人力资源预算里没有工资/社保科目，按规则不估算、不补数——薪酬部分需单独补报（口径缺口）")

    # ---------- 组装成计划明细（C-08/C-09） ----------
    periods = []
    for p in sorted([x for x in per if x]):
        b = per[p]
        items, seq = [], 0
        for cname, amt in sorted(b["in"].items(), key=lambda kv: -kv[1]):
            seq += 1
            note = "KA客户：%s｜详见附页1（%d 条商品级明细）" % (cname, len(b["in_src"].get(cname, [])))
            items.append({"seq": seq, "entity": ent_name, "direction": "流入", "cat": "经营活动",
                          "subject": "销售回款（商品/渠道分销）", "currency": "CNY",
                          "amt_orig": amt, "amt_cny": amt,
                          "owner": (b["in_src"].get(cname) or [{}])[0].get("提报人", ""),
                          "note": note, "src_row": None, "flag": "ok"})
        for subj, amt in sorted(b["out"].items(), key=lambda kv: -kv[1]):
            seq += 1
            depts = sorted(set(b["out_src"].get(subj, [])))
            note = "部门分布：%s｜详见附页2" % "、".join(depts[:4]) if depts else "详见附页2"
            items.append({"seq": seq, "entity": ent_name, "direction": "流出", "cat": "经营活动",
                          "subject": subj, "currency": "CNY", "amt_orig": amt, "amt_cny": amt,
                          "owner": "、".join(depts[:3]) or "多部门", "note": note,
                          "src_row": None, "flag": "ok"})
        inflow = round(sum(i["amt_cny"] for i in items if i["direction"] == "流入"), 2)
        outflow = round(sum(i["amt_cny"] for i in items if i["direction"] == "流出"), 2)
        # C-09 字典校验
        for i in items:
            if subj_set and (i["direction"], i["subject"]) not in subj_set:
                i["flag"] = "error:科目不在字典（C-09）：%s" % i["subject"]
        periods.append({"period": p, "items": items, "inflow": inflow, "outflow": outflow,
                        "net": round(inflow - outflow, 2)})

    if not periods:
        return {"ok": False, "error": "源表里没有可用数据行（销售/部门费用/人力三张表都为空或未识别到表头）",
                "sheets": wb.sheetnames, "pending": pending}

    errs = sum(1 for x in periods for i in x["items"] if str(i["flag"]).startswith("error"))
    return {"ok": True, "kind": "claim", "entity": ent_name, "periods": periods,
            "appendix_sales": sales_detail[:400], "appendix_expense": exp_detail[:400],
            "pending": pending[:200], "excluded": excluded[:200],
            "warnings": warnings, "rules": hits, "dict_errors": errs,
            "sheets": wb.sheetnames}
