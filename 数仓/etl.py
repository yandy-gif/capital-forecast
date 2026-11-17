"""ETL：把银行导出的 Excel / CSV 解析成标准字段并写入数仓

用法
  python etl.py import  ../珀研生物工商银行10月流水.xlsx  ../珀研生物招行10月流水.xlsx
  python etl.py status
  python etl.py clear flow
  python etl.py export ../资金数仓导出.xlsx

与看板共用同一套表头别名，所以「看板能读的文件，ETL 也能读」。
"""
import os
import sys
import csv
import json
import io
import re

import db

# ---------- 表头别名（与看板 HEAD_ALIASES 保持一致） ----------
ALIASES = {
    "month": ["月份", "所属月份", "月度", "月", "会计期间", "年月", "所属期", "期间", "账期"],
    "date": ["交易日期", "交易日", "记账日", "记账日期", "入账日期", "入账日", "发生日期", "业务日期", "日期", "交易时间"],
    "dir": ["收支方向", "方向", "借贷标志", "收支标志", "收付方向", "借贷方向", "收付标志", "借贷", "交易方向", "收支"],
    "amt": ["金额", "交易金额", "发生额", "金额(元)", "交易额", "收付金额", "发生金额", "交易金额(元)", "本次发生额"],
    "amtIn": ["贷方金额", "贷方发生额", "收入金额", "收款金额", "存入金额", "贷方", "收入"],
    "amtOut": ["借方金额", "借方发生额", "支出金额", "付款金额", "支取金额", "借方", "支出"],
    "cp": ["对方户名", "对方名称", "对方单位", "户名", "客户名称", "往来单位", "对方账户名称", "对方单位名称",
           "对方账户名", "收款人", "付款人", "对方公司", "往来单位名称", "交易对方",
           "收(付)方名称", "收付方名称", "收(付)方户名", "对方户名(全称)", "对方全称", "交易对手名称"],
    "memo": ["摘要", "业务摘要", "其它摘要", "其他摘要", "用途", "备注", "说明", "交易摘要", "附言",
             "摘要说明", "交易说明", "用途说明", "摘要/用途", "交易类型", "业务名称"],
    "k1": ["收支科目(一级)", "一级科目", "收支科目", "科目", "会计科目", "科目名称", "科目大类"],
    "k2": ["明细科目(二级)", "二级科目", "明细科目", "子科目", "明细科目名称", "科目明细"],
    "org": ["公司主体", "主体", "公司全称", "公司名称", "编制单位", "核算主体"],
    "cny": ["折人民币(CNY)", "折人民币", "折合人民币", "人民币金额"],
    "orig": ["预算金额(原币)", "预算金额", "原币金额", "金额(原币)"],
    "cf": ["现金流量分类", "现金流分类", "现金流量类别", "活动分类", "现金流类别", "现金流量"],
}


def norm(h):
    if h is None:
        return ""
    s = str(h).replace("\ufeff", "")
    s = re.sub(r"[\s\u3000\u00a0]", "", s)
    for a, b in [("（", "("), ("）", ")"), ("［", "["), ("］", "]")]:
        s = s.replace(a, b)
    return s.strip()


def head_map(headers):
    hs = [norm(h) for h in headers]
    m, alls = {}, {}
    for f, al in ALIASES.items():
        aln = [norm(a) for a in al]
        idxs = [i for i, h in enumerate(hs) if h and h in aln]
        if idxs:
            m[f] = idxs[0]
            alls[f] = idxs
    return m, alls, len(m)


def read_matrix(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True)
        best = None
        for ws in wb.worksheets:
            m = [["" if v is None else v for v in row] for row in ws.iter_rows(values_only=True)]
            for i in range(min(12, len(m))):
                _, _, hit = head_map(m[i])
                if hit >= 2 and (best is None or hit > best[2]):
                    best = (m, i, hit, ws.title)
        if not best:
            raise SystemExit(f"[跳过] {path}: 前 12 行里找不到表头")
        m, i, hit, title = best
        return m, i, title
    # CSV：UTF-8 优先，失败回退 GBK
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"[跳过] {path}: 编码无法识别")
    sample = text[:2000]
    delim = max([",", "\t", ";"], key=lambda d: sample.count(d))
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    best = None
    for i in range(min(12, len(rows))):
        _, _, hit = head_map(rows[i])
        if hit >= 2 and (best is None or hit > best[2]):
            best = (rows, i, hit, "csv")
    if not best:
        raise SystemExit(f"[跳过] {path}: 前 12 行里找不到表头")
    return best[0], best[1], best[2]


def to_dir(v):
    s = norm(v)
    if not s:
        return ""
    if re.search(r"收|贷|进|入账|存入|转入|流入", s):
        return "收"
    if re.search(r"支|借|出账|付出|转出|付款|流出", s):
        return "支"
    return ""


def num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("¥", "").replace("￥", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def to_objects(matrix, header_idx):
    headers = matrix[header_idx]
    m, alls, _ = head_map(headers)
    out = []
    for row in matrix[header_idx + 1:]:
        if not any(str(x).strip() for x in row):
            continue

        def g(f):
            i = m.get(f)
            return "" if i is None or i >= len(row) else str(row[i] if row[i] is not None else "").strip()

        def j(f):
            idxs = alls.get(f) or ([m[f]] if f in m else [])
            vals = [str(row[i]).strip() for i in idxs if i < len(row) and row[i] is not None and str(row[i]).strip()]
            return " ".join(dict.fromkeys(vals))

        # 日期：跳过纯时分秒
        def pick_date():
            idxs = alls.get("date", [])
            for i in idxs:
                if i >= len(row):
                    continue
                t = str(row[i] if row[i] is not None else "").strip()
                if not t or re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", t):
                    continue
                if re.match(r"^\d{8}$", t) or re.match(r"^\d{4}[-/.年]\d{1,2}", t) or len(t) >= 8:
                    return t
            return ""

        amt_s, dir_s = g("amt"), g("dir")
        if not amt_s:
            iv = num(re.sub(r"[,¥\s]", "", g("amtIn")))
            ov = num(re.sub(r"[,¥\s]", "", g("amtOut")))
            if iv and not ov:
                amt_s, dir_s = g("amtIn"), dir_s or "收"
            elif ov and not iv:
                amt_s, dir_s = g("amtOut"), dir_s or "支"
            elif iv or ov:
                amt_s = str(iv or ov)
                dir_s = dir_s or ("收" if iv else "支")

        amt_raw = num(amt_s)
        out.append({
            "月份": g("month"),
            "交易日期": pick_date(),
            "收支方向": to_dir(dir_s) or ("支" if amt_raw < 0 else ""),
            "金额": abs(amt_raw),
            "对方户名": j("cp"),
            "摘要": j("memo")[:80],
            "收支科目(一级)": g("k1"),
            "明细科目(二级)": g("k2"),
            "现金流量分类": g("cf"),
            "公司主体": g("org"),
            "折人民币": g("cny"),
            "预算金额(原币)": g("orig"),
        })
    return out


PLAN_MAP = {
    "销售回款(商品/渠道分销)": "KA收入", "服务与咨询费收入": "KA收入",
    "内容分成及广告收入": "KA收入", "平台返点与佣金收入": "KA收入",
    "退税款及政府补贴": "KA收入", "其他经营流入": "KA收入",
    "人力薪酬及社保公积金": "部门费用-工资",
    "租金物业及水电": "房租水电",
    "采购货款与生产成本": "采购货款",
    "研发投入(材料/委外)": "部门费用-其他",
    "市场推广及渠道佣金": "部门费用-市场部非工资",
    "物流仓储及履约": "部门费用-其他",
    "差旅及业务招待": "部门费用-其他",
    "办公及行政开支": "部门费用-其他",
    "税费及附加": "税费-增值税及附加", "税金及附加": "税费-增值税及附加",
}


def plan_subject(raw):
    k = norm(raw)
    for key, val in PLAN_MAP.items():
        if norm(key) == k:
            return val
    if "税" in k:
        return "税费-增值税及附加"
    if re.search(r"薪|工资|社保|人力", k):
        return "部门费用-工资"
    if re.search(r"租|物业|水电", k):
        return "房租水电"
    if re.search(r"采购|货款|生产", k):
        return "采购货款"
    if re.search(r"推广|营销|渠道佣金|广告", k):
        return "部门费用-市场部非工资"
    if re.search(r"收入|回款|退款|补贴|返点", k):
        return "KA收入"
    return raw or ""


def plan_cashflow(cat):
    k = norm(cat)
    if re.search(r"内部往来|内部划转", k):
        return "不列入现金流量"
    if "投资" in k:
        return "投资活动"
    if re.search(r"筹资|融资", k):
        return "筹资活动"
    return "经营活动"


def normalize_plan(rows, fallback_month="2026.10"):
    """把模板行转成看板计划结构"""
    out = []
    for r in rows:
        org = (r.get("公司主体") or "").strip()
        d = (r.get("收支方向") or "").strip()
        d = "流入" if re.search(r"流入|收", d) else ("流出" if re.search(r"流出|支", d) else "")
        cat = plan_cashflow(r.get("收支科目(一级)") or r.get("现金流量分类") or "经营活动")
        raw = (r.get("明细科目(二级)") or r.get("科目明细") or r.get("收支科目(一级)") or "").strip()
        amt = num(r.get("折人民币")) or num(r.get("预算金额(原币)"))
        if not amt or not d:
            continue
        out.append({
            "月份": fallback_month, "收支方向": d, "现金流量分类": cat,
            "科目": plan_subject(raw), "科目原始": raw,
            "来源表": org or "导入", "计划金额": amt, "含税标记": "", "备注": "",
        })
    return out


def classify(rows):
    """判断这份文件像流水还是计划表"""
    p = f = 0
    for r in rows[:300]:
        k1 = r.get("收支科目(一级)", "")
        org = (r.get("公司主体") or "").strip()
        cny = r.get("折人民币")
        if re.match(r"^(KA收入|采购货款|部门费用-|房租水电|分摊费用|税费-|内部划转)", k1 or ""):
            p += 1
            continue
        if org and cny not in (None, "") and r.get("收支方向"):
            p += 1
            continue
        if r.get("收支方向") and r.get("金额") and (r.get("对方户名") or r.get("摘要") or k1):
            f += 1
    return "plan" if p > f else "flow"


def cmd_import(paths):
    db.init()
    total = {"flow": 0, "plan": 0, "files": 0}
    for p in paths:
        if not os.path.exists(p):
            print(f"[跳过] 文件不存在：{p}")
            continue
        try:
            matrix, hi, hit = read_matrix(p)
        except SystemExit as e:
            print(e)
            continue
        rows = to_objects(matrix, hi)
        if not rows:
            print(f"[跳过] {os.path.basename(p)}：没有数据行")
            continue
        kind = classify(rows)
        name = os.path.basename(p)
        if kind == "plan":
            rows = normalize_plan(rows)
            if not rows:
                print(f"[跳过] {name}：没有可识别的计划行（缺方向或金额）")
                continue
            r = db.save_plan(rows, name)
        else:
            r = db.save_flow(rows, name)
        total[kind] += r["rows"]
        total["files"] += 1
        print(f"[{kind}] {name} → {r['rows']} 行，批次 {r['batch']}")
    print(f"\n完成：{total['files']} 个文件，流水 {total['flow']} 笔，计划 {total['plan']} 行")
    print(json.dumps(db.stats(), ensure_ascii=False, indent=1))


def cmd_export(path):
    """导出数仓现状到 Excel（流水/计划/人工标签三张表）"""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    db.init()
    flow = db.load_flow()
    plan = db.load_plan()
    manual = db.load_manual()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "流水明细"
    heads = ["月份", "交易日期", "收支方向", "金额", "对方户名", "摘要",
             "收支科目(一级)", "明细科目(二级)", "现金流量分类"]
    ws.append(heads)
    for r in flow:
        ws.append([r.get(h, "") for h in heads])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(horizontal="center")
    for col, w in zip("ABCDEFGHI", [10, 12, 9, 14, 30, 40, 18, 18, 14]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"

    ws2 = wb.create_sheet("资金计划")
    heads2 = ["month", "direction", "cashflow", "subject", "source", "amount", "tax_flag", "note"]
    ws2.append(["月份", "收支方向", "现金流量分类", "科目", "来源表", "计划金额", "含税标记", "备注"])
    for r in plan:
        ws2.append([r.get(h, "") for h in heads2])
    for c in ws2[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")

    ws3 = wb.create_sheet("人工标签")
    ws3.append(["业务键", "交易日期", "金额", "对方户名", "一级", "二级", "现金流量分类"])
    for k, v in manual.items():
        parts = k.split("|")
        ws3.append([k, parts[0] if parts else "", parts[1] if len(parts) > 1 else "",
                    parts[2] if len(parts) > 2 else "", v.get("l1"), v.get("l2"), v.get("cf")])
    wb.save(path)
    print(f"已导出：{path}（流水 {len(flow)} 笔 / 计划 {len(plan)} 行 / 人工标签 {len(manual)} 条）")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "import":
        cmd_import(sys.argv[2:])
    elif cmd == "status":
        db.init()
        print(json.dumps(db.stats(), ensure_ascii=False, indent=1))
    elif cmd == "clear":
        db.clear(sys.argv[2] if len(sys.argv) > 2 else "flow")
        print("已清空", sys.argv[2] if len(sys.argv) > 2 else "flow")
    elif cmd == "export":
        cmd_export(sys.argv[2] if len(sys.argv) > 2 else os.path.join(db.BASE, "..", "资金数仓导出.xlsx"))
    else:
        print(__doc__)
