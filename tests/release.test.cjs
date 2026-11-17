const test = require('node:test');
const assert = require('node:assert/strict');
const {boot} = require('./harness.cjs');

test('approved purchase and tax amounts are never replaced by scenario constants', async () => {
  const h = await boot();
  try {
    h.state.DATA.plan = [
      {month:'2027-01',dir:'流出',subj:'采购货款',amt:123},
      {month:'2027-01',dir:'流出',subj:'税费-增值税及附加',amt:45}
    ];
    const rows = h.w.eval('effRows()');
    assert.deepEqual(Array.from(rows, r=>r.amt), [123,45]);
  } finally { h.close(); }
});

test('fresh release can render every navigation pane without script errors', async () => {
  const h = await boot();
  try {
    for (let i=0;i<9;i++) h.w.showTab('p'+i);
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
});
