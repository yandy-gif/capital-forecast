const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', '数仓', 'v11', 'app.js'), 'utf8');
const context = vm.createContext({ location: { protocol: 'http:' }, TextEncoder, TextDecoder, Blob, Date });
vm.runInContext(src + `
const raw = [
  ['交易日期', '收支方向', '金额', '余额', '对方户名', '对方账号', '摘要', '本方账号', '银行', '余额日期'],
  ['2026-11-10', '收', 100, 200, '', '', 'DIRECT CREDIT', '123456', 'OCBC', '2026-11-18']
];
globalThis.result = parseFlowFile(raw, 'test.pdf', '示例公司');
S.meta = { entities: [{short:'示例公司', ccy:'CNY'}], accounts: [] };
S.snap = { rows: [{ id:1, entity:'示例公司', bank:'OCBC', account:'123456', txn_date:'2026-11-10', balance:200, balance_date:'2026-11-18' }] };
globalThis.before = balancesAt('2026-10');
globalThis.after = balancesAt('2026-11');
`, context);
assert.equal(context.result.rows.length, 1);
assert.equal(context.result.rows[0].balance_date, '2026-11-18');
assert.equal(context.result.rows[0].account, '123456');
assert.equal(context.result.rows[0].bank, 'OCBC');
assert.equal(context.before.total, 0);
assert.equal(context.after.total, 200);
console.log('PDF mapping and balance date: OK');
