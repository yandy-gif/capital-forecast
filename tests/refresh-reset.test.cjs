const {test}=require('node:test');
const assert=require('node:assert/strict');
const {boot,tick,clone}=require('./harness.cjs');
const csv='交易日期,收支方向,金额,对方户名,摘要\n2026-11-01,收,100,测试客户,货款';
test('an old tab cannot restore drafts into the new reset generation',async()=>{
  const h=await boot({serverBoot:true});let store;
  try{
    h.state.S.cash=98765;
    h.w.localStorage.clear();
    h.w.localStorage.setItem('pryan.resetToken',JSON.stringify('new-generation'));
    h.w.dispatchEvent(new h.w.Event('pagehide'));
    h.state.S.manual['2026-11-01|100|测试客户']={l1:'旧分类'};h.state.saveManual();
    store=h.store();
    assert.equal(store['pryan.workspace'],undefined);
    assert.equal(store['pryan.manual'],undefined);
    assert.equal(store['pryan.manualPending'],undefined);
  }finally{h.close();}
  const r=await boot({store,serverBoot:true,resetToken:'new-generation'});
  try{assert.equal(r.state.S.cash,0);assert.deepEqual(clone(r.state.S.manual),{});}finally{r.close();}
});
async function upload(h){
  await h.w.readMany([new h.w.File([csv],'test.csv',{type:'text/csv'})],'flow',{append:true});
  await tick();
}

test('refresh restores imported rows and the selected workflow step',async()=>{
  const h=await boot({serverBoot:true});let store,flow;
  try{
    await upload(h);
    h.w.document.querySelector('[data-step="3"]').click();await tick();
    store=h.store();flow=clone(h.db.flow);
    assert.equal(flow.length,1);
  }finally{h.close();}
  const r=await boot({store,flow,serverBoot:true});
  try{
    assert.equal(r.state.S.step,3);
    assert.equal(r.state.DATA.actAll.length,1);
    assert.equal(r.w.document.querySelector('.step.cur').dataset.step,'3');
  }finally{r.close();}
});

test('failed import remains recoverable after refreshing against an empty server',async()=>{
  const h=await boot({fail:true,serverBoot:true});let store;
  try{
    await upload(h);store=h.store();
    assert.match(h.w.document.body.textContent,/未保存|保存失败/);
  }finally{h.close();}
  const r=await boot({store,fail:true,serverBoot:true});
  try{assert.equal(r.state.S.raw.flow.rows.length,1);assert.equal(r.state.DATA.actAll.length,1);}
  finally{r.close();}
});

test('reset is explicit and cancelling it leaves data and drafts untouched',async()=>{
  const h=await boot({serverBoot:true});
  try{
    await upload(h);const before=h.store();const calls=h.writes.length;
    h.w.confirm=()=>false;
    const button=h.w.document.getElementById('resetTestData');assert.ok(button);
    button.click();await tick();
    assert.deepEqual(h.store(),before);
    assert.equal(h.writes.length,calls);
    assert.equal(h.state.DATA.actAll.length,1);
  }finally{h.close();}
});

test('a failed reset keeps browser drafts available for recovery',async()=>{
  const store={'eco.actor':'测试人员','pryan.manual':JSON.stringify({'2026-11-01|100|测试客户':{l1:'管理费用'}})};
  const h=await boot({store,fail:true,serverBoot:true});
  try{
    const button=h.w.document.getElementById('resetTestData');assert.ok(button);
    button.click();await tick();await tick();
    assert.equal(h.w.localStorage.getItem('eco.actor'),'测试人员');
    assert.ok(h.w.localStorage.getItem('pryan.manual'));
    assert.match(h.w.document.body.textContent,/重置失败/);
  }finally{h.close();}
});
