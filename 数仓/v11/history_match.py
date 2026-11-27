# -*- coding: utf-8 -*-
"""人工确认历史匹配；不读写数据库或规则文件，供看板与识别技能复用。"""
import re
import unicodedata
from difflib import SequenceMatcher

TERMS = (
    '仓储', '物流', '运费', '快递', '采购', '货款', '纸箱', '彩盒', '标签', '包材', '原料',
    '广告', '推广', '投流', '营销', '客服', '外包', '咨询', '服务费', '软件', '检测', '研发',
    '工资', '劳务', '社保', '公积金', '租金', '物业', '水电', '手续费', '报销', '税款',
    '保证金', '借款', '退款', '返利', '利息', '理财', '赎回', '资产', '装修', '培训',
)


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')).strip()).upper()


def memo_terms(text):
    text = normalize(text)
    return {term for term in TERMS if term in text}


def match(rules, direction, counterparty, memo, entity, account):
    """返回 (code, rule_id, basis)；未找到历史返回 None，冲突返回 code=None。"""
    cp, memo = normalize(counterparty), normalize(memo)
    scoped = []
    for r in rules:
        if r['scope'] not in ('history', 'history_memo') or r['direction'] != direction:
            continue
        if str(r['entity'] or '') != str(entity or ''):
            continue
        spec_account = str(r['account'] or '*')
        if spec_account != '*' and normalize(spec_account) != normalize(account):
            continue
        scoped.append(r)

    cp_hits = []
    if cp:
        for r in scoped:
            if r['scope'] != 'history':
                continue
            candidate = normalize(r['keyword'])
            if not candidate:
                continue
            score = 1.0 if candidate == cp else 0.0
            if not score and min(len(candidate), len(cp)) >= 6:
                score = SequenceMatcher(None, candidate, cp, autojunk=False).ratio()
            if score >= 0.90:
                cp_hits.append((score, candidate, r))
    if cp_hits:
        best = max(x[0] for x in cp_hits)
        hits = [(name, r) for score, name, r in cp_hits if score >= best - 0.001]
        # 模糊匹配出现多个不同户名时，不因微小分差把其中一家当作确定对象。
        if best < 1 and len({name for _, name, _ in cp_hits}) > 1:
            return None, None, '相似户名对应多家历史对方，需人工确认'
        candidates = [r for _, r in hits]
        codes = {r['code'] for r in candidates}
        if len(codes) > 1:
            terms = memo_terms(memo)
            candidates = [r for r in candidates if terms & memo_terms(r['memo_pattern'])]
            codes = {r['code'] for r in candidates}
            if len(codes) != 1:
                return None, None, '同一户名的人工历史分类存在冲突，需人工确认'
        chosen = candidates[-1]
        return chosen['code'], chosen['id'], '人工历史命中（户名%s）' % ('一致' if best == 1 else '高度相似')

    terms = memo_terms(memo)
    by_memo = [r for r in scoped if terms and terms & memo_terms(r['memo_pattern'])]
    if by_memo:
        if len({r['code'] for r in by_memo}) > 1:
            return None, None, '摘要对应多种人工历史分类，需人工确认'
        chosen = by_memo[-1]
        return chosen['code'], chosen['id'], '人工历史命中（摘要业务关键词）'
    return None
