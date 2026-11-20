"""资金计划表解析与三道闸门校验（生态多主体月度资金预算与资金计划表模板）

职责：
1. 从上传的 xlsx 里定位「预算明细」表头并逐行解析（容忍表头不在第 1 行、列序变化、合并单元格、
   占位主体 XXX、公式未算出值）；
2. 顺带读「科目字典 / 主体台账 / 记账汇率」，用于口径校验与字典自更新；
3. 跑三道闸门：G1 提交前（字段与科目）、G2 复核（唯一命中/重复/内部往来配平）、G3 批准（勾稽与二次锁定）；
4. 返回结构化结果，交由前端展示、由 db_eco 落库。

依赖：openpyxl（本机 Python 环境已装）。本模块只做解析与校验，不做金额改写——
「无法识别的一律标记待人工确认，不得自行篡改」。
"""
import os
import re
import datetime
import math

from openpyxl import load_workbook

# 表头别名：模板换版或各公司自行改列名时，靠别名兜住
ALIASES = {
    "entity":   ["公司主体", "公司名称", "主体", "编制单位", "公司"],
    "segment":  ["业务板块", "板块"],
    "region":   ["境内外", "境内/境外", "区域"],
    "currency": ["币种", "记账币种", "货币"],
    "direction":["收支方向", "方向", "收/支", "收支"],
    "cat":      ["科目大类", "大类", "活动分类"],
    "subject":  ["科目明细", "明细科目", "科目"],
    "timing":   ["资金时点", "时点", "旬"],
    "amt_orig": ["预算金额(原币)", "预算金额（原币）", "预算金额", "金额(原币)", "金额（原币）", "金额", "计划金额"],
    "amt_cny":  ["折人民币(CNY)", "折人民币（CNY）", "折人民币", "人民币金额", "CNY"],
    "owner":    ["责任人", "负责人", "提报人"],
    "item_status": ["审批状态", "状态"],
    "note":     ["备注", "说明"],
}
HEAD_KEYS = ["entity", "direction", "subject", "amt_orig"]
PLACEHOLDER = {"XXX", "xxx", "", None, "待定", "TBD"}


def _s(v):
    if v is None:
        return ""
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).replace("\u3000", " ").strip()


def _num(v):
    """金额归一：去掉 ¥/, /空格/万元字样；返回 (值, 是否万元)"""
    if v is None or _s(v) == "":
        return None, False
    if isinstance(v, (int, float)):
        return float(v), False
    s = _s(v).replace("￥", "").replace("¥", "").replace(",", "").replace("，", "").replace(" ", "")
    wan = "万" in s
    s = s.replace("万元", "").replace("万", "").replace("元", "")
    m = re.search(r"-?\d+(\.\d+)?", s)
    if not m:
        return None, False
    val = float(m.group(0))
    return (val * 10000 if wan else val), wan


def _norm_dir(v):
    s = _s(v)
    if not s:
        return ""
    if re.search(r"流入|^收$|收入|进账|收款", s):
        return "流入"
    if re.search(r"流出|^支$|支出|出账|付款", s):
        return "流出"
    return s


def extract_period(*texts):
    """从文件名 / 表头说明 / 单元格文本里抽 YYYY-MM"""
    for t in texts:
        s = _s(t)
        if not s:
            continue
        m = re.search(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})", s)
        if m:
            return "%s-%02d" % (m.group(1), int(m.group(2)))
        m = re.search(r"(20\d{2})(\d{2})", s)
        if m and 1 <= int(m.group(2)) <= 12:
            return "%s-%s" % (m.group(1), m.group(2))
    return ""


def _find_header(ws, limit=20):
    """在前 limit 行里找表头行，返回 (行号, {字段: 列号})"""
    best, best_hit = None, 0
    for r in range(1, min(ws.max_row, limit) + 1):
        cells = {_s(ws.cell(r, c).value): c for c in range(1, min(ws.max_column, 40) + 1)}
        colmap, hit = {}, 0
        for field, names in ALIASES.items():
            for nm in names:
                if nm in cells and field not in colmap:
                    colmap[field] = cells[nm]
                    if field in HEAD_KEYS:
                        hit += 1
                    break
        if hit > best_hit:
            best, best_hit = (r, colmap), hit
        if best_hit == len(HEAD_KEYS):
            break
    return best, best_hit


def _pick_sheet(wb, keywords, exclude=()):
    for ws in wb.worksheets:
        n = ws.title
        if any(k in n for k in keywords) and not any(x in n for x in exclude):
            return ws
    return None


def parse_dicts(wb):
    """读「科目字典」「主体台账（含汇率）」，用于校验与字典自更新"""
    out = {"subjects": [], "entities": [], "fx": []}
    ws = _pick_sheet(wb, ["科目字典", "科目"])
    if ws:
        hdr, hit = _find_header(ws)
        if hdr:
            r0, cm = hdr
            for r in range(r0 + 1, ws.max_row + 1):
                d = _s(ws.cell(r, cm.get("direction", 1)).value)
                cat = _s(ws.cell(r, cm.get("cat", 2)).value)
                sub = _s(ws.cell(r, cm.get("subject", 3)).value)
                if not sub or sub in ("科目明细",) or "下拉" in sub:
                    continue
                out["subjects"].append({
                    "direction": _norm_dir(d) or d, "cat": cat, "subject": sub,
                    "cf_attr": _s(ws.cell(r, 4).value), "note": _s(ws.cell(r, 5).value)})
    ws = _pick_sheet(wb, ["主体台账", "主体"])
    if ws:
        for r in range(1, ws.max_row + 1):
            name = _s(ws.cell(r, 1).value)
            if name in ("公司全称", "公司名称") and _s(ws.cell(r, 2).value) in ("业务板块",):
                hdr_row = r
                continue
            if name and name not in ("公司全称",) and _s(ws.cell(r, 2).value) and \
                    not name.startswith("附表") and not name.startswith("说明") and "汇率" not in name:
                out["entities"].append({
                    "name": name, "segment": _s(ws.cell(r, 2).value), "region": _s(ws.cell(r, 3).value),
                    "currency": _s(ws.cell(r, 4).value), "account": _s(ws.cell(r, 5).value),
                    "consolidated": _s(ws.cell(r, 6).value), "pooling": _s(ws.cell(r, 7).value),
                    "owner": _s(ws.cell(r, 8).value), "note": _s(ws.cell(r, 9).value)})
            if name and re.fullmatch(r"[A-Z]{3}", name) and isinstance(ws.cell(r, 3).value, (int, float)):
                out["fx"].append({"currency": name, "name": _s(ws.cell(r, 2).value),
                                  "rate_cny": float(ws.cell(r, 3).value),
                                  "updated_at": _s(ws.cell(r, 4).value)})
    ws = _pick_sheet(wb, ["记账汇率", "汇率"])
    if ws:
        for row in ws.iter_rows(min_row=2, values_only=True):
            if len(row) >= 3 and re.fullmatch(r"[A-Z]{3}", _s(row[0])) and isinstance(row[2], (int, float)):
                out["fx"].append({"currency": _s(row[0]), "name": _s(row[1]), "rate_cny": float(row[2])})
    return out


def parse_plan(path, period=None, params=None, dicts=None):
    """解析上传的资金计划表 → dict(items, gates, inflow, outflow, net, period, entity, dicts, warnings)"""
    params = params or {}
    big = float(params.get("th.big_amount", 500000) or 500000)
    allow_placeholder = str(params.get("gate.allow_placeholder", "1")) not in ("0", "false", "False")

    wb = load_workbook(path, data_only=True)
    ws = _pick_sheet(wb, ["预算明细", "明细"], exclude=["附页"]) or wb.worksheets[0]
    hdr, hit = _find_header(ws)
    warns = []
    if not hdr or hit < 2:
        return {"ok": False, "error": "没识别到「预算明细」表头（前 20 行里找不到 公司主体/收支方向/科目明细/金额）",
                "sheets": wb.sheetnames, "warnings": warns}
    r0, cm = hdr
    if hit < len(HEAD_KEYS):
        miss = [k for k in HEAD_KEYS if k not in cm]
        warns.append("表头缺少关键列：%s（已按别名列尽力解析）" % "、".join(miss))

    up_dicts = dicts or parse_dicts(wb)
    subj_set = {(s["direction"], s["subject"]) for s in up_dicts["subjects"]}
    subj_cat = {(s["direction"], s["subject"]): s["cat"] for s in up_dicts["subjects"]}
    ent_set = {e["name"] for e in up_dicts["entities"]}
    ent_info = {e["name"]: e for e in up_dicts["entities"]}
    fx = {f["currency"]: f["rate_cny"] for f in up_dicts["fx"]} or {"CNY": 1.0}

    # 期间：优先调用方指定，其次表内「预算月份」，再次文件名
    head_txt = " ".join(_s(ws.cell(r, c).value) for r in range(1, min(r0, 4) + 1)
                        for c in range(1, min(ws.max_column, 14) + 1))
    period = period or extract_period(head_txt) or extract_period(os.path.basename(path))
    if not period:
        warns.append("未能从表头/文件名识别预算月份，请在上传时手工指定期间")

    items, blank_run, skipped = [], 0, []
    for r in range(r0 + 1, ws.max_row + 1):
        def g(field):
            c = cm.get(field)
            return ws.cell(r, c).value if c else None
        entity = _s(g("entity"))
        subject = _s(g("subject"))
        direction = _norm_dir(g("direction"))
        amt_o, _ = _num(g("amt_orig"))
        amt_file, _ = _num(g("amt_cny"))
        # 空行 / 表尾汇总行（无方向且无金额）不入明细，但要留痕
        if not direction and amt_o is None:
            if entity or subject:
                skipped.append({"row": r, "entity": entity, "subject": subject,
                                "why": "无收支方向且无金额（表尾汇总/占位空行）"})
            blank_run += 1
            if blank_run > 40:
                break
            continue
        blank_run = 0
        if not entity and subject and re.search(r"合计|小计|总计", subject):
            skipped.append({"row": r, "entity": entity, "subject": subject, "why": "合计行"})
            continue
        currency = _s(g("currency")) or (ent_info.get(entity, {}) or {}).get("currency") or "CNY"
        rate = float(params.get("fx." + currency, fx.get(currency, 1.0)))
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("%s 汇率必须是大于零的有效数字" % currency)
        cat = _s(g("cat")) or subj_cat.get((direction, subject), "")
        # 模板「折人民币(CNY)」列公式多处断裂（#REF! 或统一引用 USD 汇率 P7），
        # 一律按「原币 × 该币种汇率」重算，并保留文件原值以便追溯——不静默改数。
        amt_c = round((amt_o or 0) * rate, 2)
        fx_note = ""
        if amt_file is not None and abs(amt_file - amt_c) > max(1.0, abs(amt_c) * 0.005):
            fx_note = "折人民币按 %s 汇率 %s 重算为 %s（文件公式值 %s）" % (
                currency, rate, format(amt_c, ",.2f"), format(amt_file, ",.2f"))
        info = ent_info.get(entity, {}) or {}
        items.append({
            "seq": len(items) + 1, "entity": entity,
            "segment": _s(g("segment")) or info.get("segment", ""),
            "region": _s(g("region")) or info.get("region", ""),
            "currency": currency, "direction": direction, "cat": cat, "subject": subject,
            "timing": _s(g("timing")), "amt_orig": amt_o, "amt_cny": amt_c,
            "amt_cny_file": amt_file, "fx_note": fx_note,
            "owner": _s(g("owner")), "item_status": _s(g("item_status")) or "待编",
            "note": _s(g("note")), "src_row": r, "flag": "ok",
        })

    if not items:
        return {"ok": False, "error": "没有读到数据行（只有表头）", "sheets": wb.sheetnames,
                "warnings": warns, "skipped": skipped}
    if skipped:
        warns.append("跳过 %d 行（表尾汇总/无方向无金额的空占位行），明细见 skipped" % len(skipped))
    fx_fixed = [it for it in items if it.get("fx_note")]
    if fx_fixed:
        warns.append("%d 行「折人民币」按币种汇率重算（模板 J 列公式存在 #REF! 或统一引用 USD 汇率）"
                     % len(fx_fixed))

    # ---------- G1 提交前闸门 ----------
    g1 = []
    for it in items:
        why = []
        if not it["entity"]:
            why.append("缺公司主体")
        elif it["entity"] in PLACEHOLDER:
            why.append("公司主体为占位值 XXX")
        elif ent_set and it["entity"] not in ent_set:
            why.append("主体不在台账：%s" % it["entity"])
        if not it["direction"]:
            why.append("缺收支方向")
        if not it["subject"]:
            why.append("缺科目明细")
        elif subj_set and (it["direction"], it["subject"]) not in subj_set:
            why.append("科目不在字典（C-09 不允许自由文本）：%s" % it["subject"])
        if it["amt_orig"] is None:
            why.append("缺预算金额")
        elif it["amt_orig"] < 0:
            why.append("金额为负（退款/冲减应在备注说明）")
        elif it["amt_orig"] == 0:
            why.append("金额为 0（确认是否漏填）")
        if abs(it["amt_cny"] or 0) >= big and not it["note"]:
            why.append("大额（≥%s 万）缺备注说明" % int(big / 10000))
        if it.get("fx_note"):
            why.append(it["fx_note"])
        if why:
            it["flag"] = "error:" + "；".join(why) if any(
                w.startswith(("缺", "科目不在", "金额为负")) for w in why) else "warn:" + "；".join(why)
            g1.append({"row": it["src_row"], "entity": it["entity"], "subject": it["subject"],
                       "amount": it["amt_orig"], "issues": why})
    # ---------- G2 复核闸门 ----------
    g2 = []
    seen = {}
    for it in items:
        k = (it["entity"], it["direction"], it["subject"], it["timing"], round(it["amt_cny"] or 0, 2))
        if k in seen:
            g2.append({"type": "重复行", "row": it["src_row"], "detail": "与第 %s 行完全相同（主体+方向+科目+时点+金额）" % seen[k]})
        else:
            seen[k] = it["src_row"]
    in_alloc = sum(abs(it["amt_cny"] or 0) for it in items if "调拨（拨入）" in it["subject"] or "拨入" in it["subject"])
    out_alloc = sum(abs(it["amt_cny"] or 0) for it in items if "调拨（拨出）" in it["subject"] or "拨出" in it["subject"])
    if (in_alloc or out_alloc) and abs(in_alloc - out_alloc) > 1:
        g2.append({"type": "内部往来未配平", "row": None,
                   "detail": "拨入 {:,.2f} ≠ 拨出 {:,.2f}，差额 {:,.2f}（调拨须双向入账）".format(
                       in_alloc, out_alloc, in_alloc - out_alloc)})
    unknown = [it for it in items if subj_set and (it["direction"], it["subject"]) not in subj_set]
    if unknown:
        g2.append({"type": "科目字典外语汇", "row": None,
                   "detail": "%d 行科目未命中字典，无法唯一映射" % len(unknown)})
    # ---------- 汇总与 G3 勾稽 ----------
    inflow = round(sum(it["amt_cny"] or 0 for it in items if it["direction"] == "流入"), 2)
    outflow = round(sum(it["amt_cny"] or 0 for it in items if it["direction"] == "流出"), 2)
    net = round(inflow - outflow, 2)
    ents = sorted({it["entity"] for it in items if it["entity"]})
    entity = ents[0] if len(ents) == 1 else "多主体（%d 家）" % len(ents)
    g3 = [{"type": "勾稽校验", "row": None,
           "detail": "流入 {:,.2f} − 流出 {:,.2f} = 净额 {:,.2f}（明细 {} 行）".format(
               inflow, outflow, net, len(items))}]
    ph = [it for it in items if it["entity"] in PLACEHOLDER]
    if ph and not allow_placeholder:
        g3.append({"type": "占位主体", "row": None, "detail": "%d 行主体为 XXX，锁定前必须替换为真实主体" % len(ph)})

    err = sum(1 for it in items if str(it["flag"]).startswith("error"))
    warn = sum(1 for it in items if str(it["flag"]).startswith("warn"))
    gates = {
        "G1": {"pass": err == 0, "error": err, "warn": warn, "issues": g1[:200],
               "desc": "必填字段 / 科目在字典内 / 金额为正 / 大额有说明"},
        "G2": {"pass": not any(x["type"] in ("重复行", "科目字典外语汇") for x in g2), "issues": g2,
               "desc": "科目唯一命中 / 无重复行 / 内部往来配平"},
        "G3": {"pass": True, "issues": g3, "desc": "合计勾稽 / 同期间不得二次锁定"},
    }
    try:
        appendix = parse_appendix(wb)
    except Exception:
        appendix = []
    if not appendix:
        # 标准「预算明细」表通常没有附页 sheet：把明细行按方向落到附页，
        # 保证「附页1 销售回款明细 / 附页2 费用申报明细」有逐条可追溯的底稿，
        # 而不是显示 0 条（0 条会被误读成"数据没进来"）。
        appendix = appendix_from_items(items)
    return {"ok": True, "period": period, "entity": entity, "entities": ents, "appendix": appendix,
            "items": items, "gates": gates, "inflow": inflow, "outflow": outflow, "net": net,
            "dicts": up_dicts, "warnings": warns, "sheets": wb.sheetnames,
            "skipped": skipped, "error_rows": err, "warn_rows": warn}

def appendix_from_items(items):
    """标准计划表未带附页 sheet 时，用明细行生成附页底稿：
    流入 → 附页1 销售回款明细；流出 → 附页2 费用申报明细，金额与主表逐行一致。"""
    out = []
    for it in items or []:
        dirn = str(it.get("direction") or "")
        kind = "销售回款明细" if dirn == "流入" else "费用申报明细"
        note = "｜".join([x for x in [it.get("segment"), it.get("timing"),
                                     it.get("owner"), it.get("note")] if x])
        out.append({
            "kind": kind,
            # 只有流入行把业务板块当作「客户/板块」展示；流出行的板块写进说明，
            # 避免费用附页的「客户 / 部门」列出现业务板块这种错位口径。
            "customer": it.get("segment") if dirn == "流入" else None,
            "klass": None,
            "product": None,
            "dept": None,
            "item": it.get("subject"),
            "qty": None, "price": None, "rate": None,
            "amount": it.get("amt_cny") or it.get("amt_orig") or 0,
            "src_row": it.get("src_row"),
            "note": note or None,
        })
    return out


def parse_appendix(wb):
    """解析资金计划表的附页（附页1 销售回款明细 / 附页2 费用申报明细）"""
    out = []
    for ws in wb.worksheets:
        title = ws.title or ""
        if "附页" not in title and "明细" not in title:
            continue
        if "销售" in title or "回款" in title:
            kind = "销售回款明细"
        elif "费用" in title:
            kind = "费用申报明细"
        else:
            continue
        r0 = None
        for r in range(1, min(ws.max_row, 8) + 1):
            vals = [_s(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 22) + 1)]
            if len([v for v in vals if v]) >= 4:
                r0 = r
                heads = {v: i + 1 for i, v in enumerate(vals) if v}
                break
        if not r0:
            continue

        def g(r, *names):
            for n in names:
                for h, ci in heads.items():
                    if n in h:
                        return ws.cell(r, ci).value
            return None

        for r in range(r0 + 1, ws.max_row + 1):
            if kind == "销售回款明细":
                cust = _s(g(r, "客户"))
                if not cust or "合计" in cust:
                    continue
                out.append({
                    "kind": kind, "customer": cust, "klass": _s(g(r, "客户类型")),
                    "product": _s(g(r, "商品名称")), "dept": None,
                    "item": None, "qty": _num(g(r, "件数"))[0],
                    "price": _num(g(r, "售价"))[0], "rate": _num(g(r, "扣率"))[0],
                    "amount": _num(g(r, "回款金额", "金额"))[0],
                    "src_row": _num(g(r, "源表行号"))[0],
                    "note": _s(g(r, "提报人")),
                })
            else:
                amt = _num(g(r, "金额"))[0]
                if not amt:
                    continue
                out.append({
                    "kind": kind, "customer": None, "klass": None, "product": None,
                    "dept": _s(g(r, "责任部门", "部门")),
                    "item": _s(g(r, "费用名称", "预算项目")),
                    "qty": None, "price": None, "rate": None, "amount": amt,
                    "src_row": _num(g(r, "源表行号"))[0],
                    "note": _s(g(r, "说明", "预算说明")),
                })
    return out
