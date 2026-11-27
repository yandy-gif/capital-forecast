# -*- coding: utf-8 -*-
"""解析文字型招行、建行及 OCBC 银行流水 PDF；不支持扫描件。"""
import io
import re
from datetime import datetime
from decimal import Decimal

import pdfplumber

AMOUNT = r"-?[\d,]+\.\d{2}"
HEAD = ['交易日期', '收支方向', '金额', '余额', '对方户名', '对方账号', '摘要', '本方账号', '银行', '余额日期', '交易时间']


def _number(text):
    return Decimal(str(text).replace(',', ''))


def _text(words):
    return ''.join(w['text'] for w in sorted(words, key=lambda w: (w['top'], w['x0'])))


def _meta(text, pattern, label):
    hit = re.search(pattern, text, re.I)
    if not hit:
        raise ValueError('PDF 未找到%s，无法安全导入' % label)
    return hit.group(1).strip()


def _cmb(pages, text):
    owner = _meta(text, r'账户名称[:：]\s*(\S+)', '账户名称')
    account = _meta(text, r'账号[:：]\s*(\d+)', '本方账号')
    rows = []
    for page in pages:
        words = sorted(page.extract_words(use_text_flow=False), key=lambda w: (w['top'], w['x0']))
        anchors = [w for w in words if re.fullmatch(r'\d{8}', w['text']) and w['x1'] <= 66]
        groups = [{'date': a['text'], 'desc': [], 'amt': [], 'bal': [], 'cp': []} for a in anchors]
        for w in words:
            if (any(w is a for a in anchors) or not anchors or w['top'] > anchors[-1]['top'] + 12
                    or any(s in w['text'] for s in ('期末余额', '打印时间', '特别提示', 'Special', '招商银行股份', '第1页', '第2页'))):
                continue
            idx = next((i for i in range(len(anchors)-1, -1, -1) if anchors[i]['top'] <= w['top'] + 3), None)
            if idx is None:
                continue
            x = w['x0']
            groups[idx]['cp' if x >= 452 else 'bal' if x >= 392 else 'amt' if x >= 322 else 'desc'].append(w)
        for g in groups:
            nums = [[w for w in g[k] if re.fullmatch(AMOUNT, w['text'])] for k in ('amt', 'bal')]
            if len(nums[0]) != 1 or len(nums[1]) != 1:
                raise ValueError('招行明细存在金额或余额列无法唯一识别，请人工核对')
            amount, balance = (_number(n[0]['text']) for n in nums)
            if not amount:
                raise ValueError('招行明细存在零金额行，请人工核对')
            extra = [w for k in ('amt', 'bal') for w in g[k] if not re.fullmatch(AMOUNT, w['text'])]
            desc = _text(extra + g['desc'])
            day = datetime.strptime(g['date'], '%Y%m%d').strftime('%Y-%m-%d')
            rows.append([day, '收' if amount > 0 else '支', float(abs(amount)), float(balance),
                         _text(g['cp']), '', desc, account, '招商银行', day])
    if not rows:
        raise ValueError('招行 PDF 没有识别到交易明细')
    opening = _number(_meta(text, r'上页余额[:：]\s*(' + AMOUNT + ')', '上页余额'))
    closing = _number(_meta(text, r'期末余额[:：]\s*(' + AMOUNT + ')', '期末余额'))
    for row in rows:
        opening += Decimal(str(row[2])) * (1 if row[1] == '收' else -1)
        if abs(opening - Decimal(str(row[3]))) > Decimal('0.01'):
            raise ValueError('招行 PDF 逐笔余额核对失败，请检查页面或版式')
    if abs(opening - closing) > Decimal('0.01'):
        raise ValueError('招行 PDF 期末余额核对失败')
    return owner, rows, []


def _lines(words, tolerance=4):
    lines = []
    for word in sorted(words, key=lambda w: (w['top'], w['x0'])):
        if not lines or abs(word['top'] - lines[-1][0]['top']) > tolerance:
            lines.append([word])
        else:
            lines[-1].append(word)
    return [sorted(line, key=lambda w: w['x0']) for line in lines]


def _ccb(pages, text):
    owner = _meta(text, r'本方户名[:：]\s*(\S+)', '本方户名')
    required = {'账号', '交易时间', '借方发生额', '贷方发生额', '余额', '币种', '对方户名', '对方账号', '摘要', '备注'}
    rows, accounts, currencies = [], set(), set()
    for page_number, page in enumerate(pages, 1):
        tables = page.extract_tables() or []
        found = False
        for table in tables:
            for header_index, header in enumerate(table):
                names = [re.sub(r'\s+', '', value or '') for value in header]
                if not required.issubset(names):
                    continue
                found = True
                if len(set(names)) != len(names):
                    raise ValueError('建行 PDF 表头列名重复，请核对版式')
                for line_number, line in enumerate(table[header_index + 1:], header_index + 2):
                    cells = [re.sub(r'\s+', '', value or '') for value in line]
                    cells += [''] * max(0, len(names) - len(cells))
                    rec = dict(zip(names, cells))
                    if not any(cells):
                        continue
                    digits = re.sub(r'\D', '', rec['交易时间'])
                    if len(digits) < 8:
                        raise ValueError('建行 PDF 第 %d 页第 %d 行交易日期无法识别' % (page_number, line_number))
                    try:
                        day = datetime.strptime(digits[:8], '%Y%m%d').strftime('%Y-%m-%d')
                        dr = _number(rec['借方发生额'] or '0')
                        cr = _number(rec['贷方发生额'] or '0')
                        balance = _number(rec['余额'])
                    except Exception as exc:
                        raise ValueError('建行 PDF 第 %d 页第 %d 行金额或日期无法识别' % (page_number, line_number)) from exc
                    if dr < 0 or cr < 0 or (dr > 0) == (cr > 0):
                        raise ValueError('建行 PDF 第 %d 页第 %d 行借贷方向无法唯一确定' % (page_number, line_number))
                    account = rec['账号']
                    if not re.fullmatch(r'\d{6,}', account):
                        raise ValueError('建行 PDF 第 %d 页第 %d 行本方账号无法识别' % (page_number, line_number))
                    currency = rec['币种'].upper()
                    currency = 'CNY' if currency in ('人民币', '人民币元', 'CNY', 'RMB') else currency
                    if not currency:
                        raise ValueError('建行 PDF 缺少币种')
                    accounts.add(account)
                    currencies.add(currency)
                    memo = ' '.join(dict.fromkeys(x for x in (rec['摘要'], rec['备注']) if x))
                    time = ':'.join((digits[8:10], digits[10:12], digits[12:14])) if len(digits) >= 14 else ''
                    rows.append([day, '支' if dr else '收', float(dr or cr), float(balance),
                                 rec['对方户名'], rec['对方账号'], memo, account, '建设银行', day, time])
                break
        if not found:
            raise ValueError('建行 PDF 第 %d 页表头无法识别' % page_number)
    if not rows or len(accounts) != 1 or len(currencies) != 1:
        raise ValueError('建行 PDF 无明细或包含多个账户/币种，请分开导入')

    def chain_ok(items):
        return all(abs(Decimal(str(cur[3])) - (Decimal(str(prev[3])) +
                   Decimal(str(cur[2])) * (1 if cur[1] == '收' else -1))) <= Decimal('0.01')
                   for prev, cur in zip(items, items[1:]))

    if len(rows) > 1 and not (chain_ok(rows) or chain_ok(list(reversed(rows)))):
        raise ValueError('建行 PDF 逐笔余额链核对失败，请检查流水是否完整、排序是否正确')
    return owner, rows, [], next(iter(currencies))


def _ocbc(pages, text):
    owner = _meta(text, r'Account Name\s+(.+?)\s+Ledger Balance', '账户名称')
    account = _meta(text, r'Account Number\s+(\d+)\s*-', '本方账号')
    amount = _number(_meta(text, r'Ledger Balance\s+(' + AMOUNT + ')', '账面余额'))
    asof = _meta(text, r'Ledger Balance\s+' + AMOUNT + r'\s+(\d{1,2}-[A-Za-z]{3}-\d{4})', '余额日期')
    asof = datetime.strptime(asof.title(), '%d-%b-%Y').strftime('%Y-%m-%d')
    rows = []
    for page in pages:
        for line in _lines(page.extract_words()):
            full = ' '.join(w['text'] for w in line)
            date = re.match(r'^(\d{2} [A-Za-z]{3} \d{4})\s+', full)
            if not date:
                continue
            amounts = [w for w in line if re.fullmatch(AMOUNT, w['text'])]
            if amounts:
                if len(amounts) != 1:
                    raise ValueError('OCBC 明细金额无法唯一识别')
                w = amounts[0]
                # 依据银行借贷栏位置，而非交易描述猜测方向。
                direction = '支' if w['x0'] < page.width * .76 else '收'
                day = datetime.strptime(date.group(1), '%d %b %Y').strftime('%Y-%m-%d')
                rest = full[date.end():].replace(w['text'], '').strip()
                rows.append([day, direction, float(_number(w['text'])), None, '', '', rest,
                             account, 'OCBC BANK (HONG KONG)', ''])
            elif rows:
                rows[-1][6] = (rows[-1][6] + ' ' + full[date.end():]).strip()
    if not rows:
        raise ValueError('OCBC PDF 没有识别到交易明细')
    for label, direction in [('Debits', '支'), ('Credits', '收')]:
        match = re.search(label + r'\s+(\d+)\s+Amount\s+(' + AMOUNT + ')', text, re.I)
        actual = [r for r in rows if r[1] == direction]
        if not match or len(actual) != int(match.group(1)) or abs(sum(Decimal(str(r[2])) for r in actual) - _number(match.group(2))) > Decimal('0.01'):
            raise ValueError('OCBC %s 笔数或金额与报表汇总不符' % label)
    latest = max(r[0] for r in rows)
    if latest > asof:
        raise ValueError('OCBC 余额日期早于最后交易，无法安全关联')
    if latest[:7] != asof[:7]:
        raise ValueError('OCBC 最后交易与余额不在同月，当前无法安全关联余额')
    last = next(r for r in reversed(rows) if r[0] == latest)
    last[3], last[9] = float(amount), asof
    return owner, rows, ['仅末笔关联报表余额，余额日期为 %s；其余交易无逐笔余额' % asof]


def parse_pdf(data):
    if not data.startswith(b'%PDF-'):
        raise ValueError('文件不是有效的 PDF')
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > 300:
            raise ValueError('PDF 超过 300 页，请拆分后导入')
        text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
        if 'Daily Statement of Account' in text and 'OCBC' in text:
            owner, rows, warnings = _ocbc(pdf.pages, text)
            currency = _meta(text, r'Account Number\s+\d+\s*-\s*([A-Za-z]{3})', '币种').upper()
        elif '账务明细清单' in text and '借方/贷方金额' in text:
            owner, rows, warnings = _cmb(pdf.pages, text)
            raw_currency = _meta(text, r'货币[:：]\s*(\S+)', '币种')
            currency = 'CNY' if raw_currency in ('人民币', 'CNY', 'RMB') else raw_currency
        elif '中国建设银行账户明细信息' in re.sub(r'\s+', '', text):
            owner, rows, warnings, currency = _ccb(pdf.pages, text)
        else:
            raise ValueError('未识别此 PDF 格式；目前支持招行账务明细、建行账户明细与 OCBC 英文账户明细，不支持扫描件')
    return {'rows': [HEAD] + rows, 'owner': owner, 'currency': currency, 'warnings': warnings, 'kind': 'pdf'}
