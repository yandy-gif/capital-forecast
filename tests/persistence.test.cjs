const {test}=require('node:test');
const assert=require('node:assert/strict');
const {boot,tick,clone}=require('./harness.cjs');
const key='2026-08-01|100|测试客户';
const label={l1:'管理费用',l2:'办公费',cf:'经营活动'};

test('fresh load restores locally retained manual confirmations',async()=>{
  const h=await boot({store:{'pryan.manual':JSON.stringify({[key]:label})}});
  try {assert.deepEqual(clone(h.state.S.manual[key]),label);} finally {h.close();}
});
test('rapid changes while a save is in flight all reach storage',async()=>{
  const h=await boot({delay:true});
  try {
    h.state.S.manual[key]={...label,l2:'旧值'};h.state.saveManual();h.state.flushManual('test');
    h.state.S.manual[key]={...label,l2:'新值'};h.state.saveManual();h.state.flushManual('test');
    h.pending.shift()();await tick();await tick();
    if(h.pending.length){h.pending.shift()();await tick();await tick();}
    assert.equal(h.db.manual[key].l2,'新值');
  } finally {h.close();}
});
test('failed HTTP save never reports database success',async()=>{
  const h=await boot({fail:true});
  try {
    h.state.S.manual[key]=label;
    await h.w.WH.pushManual({[key]:label}).catch(()=>{});
    assert.equal(h.state.S.manualSaved,undefined);
    assert.match(h.w.document.body.textContent,/失败|未保存|待重试|未连接/);
  } finally {h.close();}
});
test('navigation switches panes through actual click handlers',async()=>{
  const h=await boot();
  try {
    assert.deepEqual(h.errors,[]);
    h.w.document.querySelector('[data-t="p6"]').click();
    assert.equal(h.w.document.querySelector('.pane.on').id,'p6');
    h.w.document.querySelector('[data-t="p7"]').click();
    assert.equal(h.w.document.querySelector('.pane.on').id,'p7');
  } finally {h.close();}
});
test('pending confirmation beats a stale server value after refresh',async()=>{
  const value={...label,l2:'刷新前的新值'};
  const h=await boot({store:{'pryan.manual':JSON.stringify({[key]:value}),'pryan.manualPending':JSON.stringify({[key]:value})}});
  try {
    h.w.WH.restoreManual({[key]:label});await tick();await tick();
    assert.equal(h.state.S.manual[key].l2,value.l2);assert.equal(h.db.manual[key].l2,value.l2);
  }finally{h.close();}
});
test('configuration drafts survive redraw and a fresh page',async()=>{
  const h=await boot();let store;
  try{
    h.w.testEco.ECO.data.params=[{id:'safe.line',value:'2000000',label:'安全线',grp:'水位'}];
    h.w.testEco.renderP7();
    const inp=h.w.document.querySelector('[data-pid="safe.line"]');inp.value='3000000';inp.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    store=h.store();h.w.testEco.renderP7();
    assert.equal(h.w.document.querySelector('[data-pid="safe.line"]').value,'3000000');
  }finally{h.close();}
  const r=await boot({store});
  try{r.w.testEco.ECO.data.params=[{id:'safe.line',value:'2000000',label:'安全线',grp:'水位'}];r.w.testEco.renderP7();assert.equal(r.w.document.querySelector('[data-pid="safe.line"]').value,'3000000');}finally{r.close();}
});
test('saving a zero-day customer term sends zero',async()=>{
  const h=await boot();
  try{
    h.w.testEco.ECO.data.terms=[{customer:'测试客户',klass:'KA',term_days:30}];h.w.testEco.renderP7();
    const inp=h.w.document.querySelector('[data-term]');inp.value='0';inp.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    await h.w.testEco.saveTerms();await tick();
    assert.equal(h.db.terms[0].term_days,0);
  }finally{h.close();}
});
test('forecast compares closing balance to the configured safety line',async()=>{
  const h=await boot();
  try{
    h.w.testEco.ECO.locked=[{period:'2026-10',direction:'流入',amt_cny:100,subject:'销售回款',entity:'测试公司'}];
    h.w.testEco.ECO.data.params=[{id:'safe.line',value:200},{id:'fc.horizon',value:1}];
    h.w.__WH_BAL={byMonth:{'2026-09':{_total:50}}};
    assert.match(h.w.testEco.forecastHtml(),/低于安全线/);
    h.w.__WH_BAL={byMonth:{}};
    assert.match(h.w.testEco.forecastHtml(),/待补.*期初余额|期初余额.*待补/);
  }finally{h.close();}
});

test('a secondary-only manual confirmation survives a full reclassification',async()=>{
  const h=await boot();
  try{
    h.state.S.manual[key]={l2:'人工二级科目',cf:'投资活动'};
    h.state.applyFlow([{'交易日期':'2026-08-01','金额':100,'对方户名':'测试客户','收支方向':'支','一级科目':'管理费用'}],'all');
    assert.equal(h.state.DATA.actAll[0][7],'人工二级科目');
    assert.equal(h.state.DATA.actAll[0][9],'投资活动');
  }finally{h.close();}
});

test('save-only button does not announce success when the request fails',async()=>{
  const h=await boot({fail:true});
  try{
    h.state.S.manual[key]=label;
    const b=h.w.document.createElement('button');b.id='mSave';h.w.document.body.append(b);
    const tip=h.w.document.createElement('span');tip.id='mSaveTip';h.w.document.body.append(tip);
    b.click();
    assert.doesNotMatch(tip.textContent,/✓.*(?:已写入|已保存)/);
    await tick();await tick();
    assert.match(tip.textContent,/失败|待重试|未连接/);
  }finally{h.close();}
});

test('failed batch label edits retain their draft across a page refresh',async()=>{
  const h=await boot({fail:true});let store;
  try{
    h.state.S.mMode=true;h.state.S.mPend={[key]:label};h.state.S.mPendRaw={};h.state.S.mSel={0:true};
    h.state.DATA.actAll=[['2026.08','2026-08-01','支',100,'测试客户','','管理费用','','','经营活动',1,'test']];
    assert.equal((await h.state.saveSelected()).ok,false);
    store=h.store();assert.deepEqual(clone(h.state.S.mPend[key]),label);
  }finally{h.close();}
  const r=await boot({store});
  try{assert.deepEqual(clone(r.state.S.mPend[key]),label);assert.equal(r.state.S.mMode,true);}finally{r.close();}
});

test('pending custom rules survive server reload and rapid edits',async()=>{
  const h=await boot({delay:true,store:{'pryan.rulesPending':JSON.stringify([{id:'custom',l1:'旧值'}])}});
  try{
    h.w.WH.restoreRules([{id:'server-old'}]);
    h.state.S.rules[0].l1='新值';h.state.saveRulesLS();
    h.pending.shift()();await tick();await tick();
    if(h.pending.length){h.pending.shift()();await tick();await tick();}
    assert.equal(h.db.rules[0].id,'custom');assert.equal(h.db.rules[0].l1,'新值');
    assert.equal(h.store()['pryan.rulesPending'],'null');
  }finally{h.close();}
});

test('archive form retains edits after failed save and refresh without losing labels',async()=>{
  const row=['2026.08','2026-08-01','支',100,'测试客户','原摘要','管理费用','办公费','','经营活动',99,'test'];
  const setup=h=>{h.state.DATA.actAll=[row];h.state.S.archEdit=0;if(!h.w.document.getElementById('archBox')){const box=h.w.document.createElement('div');box.id='archBox';h.w.document.body.append(box);}h.state.renderArch();};
  const h=await boot({fail:true});let store;
  try{
    setup(h);const inp=h.w.document.querySelector('[data-a="摘要"]');inp.value='修改待保存';inp.dispatchEvent(new h.w.Event('input',{bubbles:true}));
    await h.state.saveArch(0);assert.equal(h.writes.at(-1).path,'/api/flow-edit');
    store=h.store();assert.equal(JSON.parse(store['pryan.archiveDrafts'])['99']['摘要'],'修改待保存');
  }finally{h.close();}
  const r=await boot({store});try{setup(r);assert.equal(r.w.document.querySelector('[data-a="摘要"]').value,'修改待保存');}finally{r.close();}
});
