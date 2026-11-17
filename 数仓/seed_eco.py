"""初始化科目、桥接、参数和空白模板；不导入业务或演示数据。"""
import json
from pathlib import Path
import db
import db_eco


def seed_defaults():
    """仅补齐缺失的配置项，重复启动不覆盖用户修改。"""
    db.init()
    defaults = json.loads(Path(__file__).with_name('defaults.json').read_text(encoding='utf-8'))
    current = db_eco.dict_all()
    have = {(s['direction'], s['subject']) for s in current['subjects']}
    db_eco.dict_merge(subjects=[s for s in defaults['subjects'] if (s['direction'], s['subject']) not in have])
    currencies = {f['currency'] for f in current['fx']}
    db_eco.dict_replace(fx=[f for f in defaults['fx'] if f['currency'] not in currencies])
    params = {p['id'] for p in db_eco.params_load()}
    db_eco.params_save([p for p in defaults['params'] if p['id'] not in params], actor='初始化')
    ensure_fx_params()
    bridges = {b['id'] for b in db_eco.bridge_load()}
    db_eco.bridge_save([b for b in defaults['bridge'] if b['id'] not in bridges])
    for tpl_id, name, fields in [('TPL-05','生态多主体月度资金预算与资金计划表模板',14),
                                 ('TPL-06','银行流水导入模板',9)]:
        if not db_eco.tpl_get(tpl_id):
            db_eco.tpl_upsert({'id':tpl_id,'name':name,'version':'v1.0','fields':fields,
                               'rule_ver':'v1','scope':'全局','file_path':'',
                               'note':'按当前配置动态生成空白模板，不含历史金额或样例行'})


def ensure_fx_params():
    """导入新币种后提供可编辑汇率；已有人工确认值不覆盖。"""
    have = db_eco.params_map()
    rows = [{'id':'fx.'+f['currency'], 'grp':'汇率', 'label':f['currency']+'/CNY 汇率',
             'value':str(f['rate_cny']), 'unit':'', 'scope':'全局',
             'note':'折人民币 = 原币 × 本汇率', 'locked':0}
            for f in db_eco.dict_all()['fx']
            if f['currency'] != 'CNY' and 'fx.'+f['currency'] not in have]
    if rows:
        db_eco.params_save(rows, actor='初始化')


if __name__ == '__main__':
    seed_defaults()
    print('已初始化基础配置；流水、计划、客户、主体与账户保持为空或原有内容。')
