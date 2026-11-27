const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function dashboard() {
  const handlers = {};
  const context = vm.createContext({
    location: { protocol: 'http:' },
    document: { addEventListener(event, handler) { (handlers[event] ||= []).push(handler); } },
    handlers,
    console, setTimeout, clearTimeout,
  });
  const dir = path.join(__dirname, '..', '数仓', 'v11');
  vm.runInContext(fs.readFileSync(path.join(dir, 'app.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(dir, 'ui.js'), 'utf8'), context);
  vm.runInContext(`
    S.entity = '测试公司'; S.period = 'ALL';
    S.meta = {entities:[{short:'测试公司',ccy:'CNY'}],accounts:[],subjectMap:[],fx:[],
      counts:{},planBatches:[],safeLines:{},restrictedRules:[]};
    S.snap = {start:'2026-09',end:'2026-10',allMonths:['2026-09','2026-10'],
      months:['2026-09'],rows:[],plans:[],fx:{},pending:0,missingFx:[],restricted:{},aggAt:''};
    S.full = S.snap;
  `, context);
  return context;
}

test('计划月份没有流水时不展示确定偏差', () => {
  const c = dashboard();
  vm.runInContext(`S.snap.plans=[{period:'2026-10',entity:'测试公司',direction:'流入',l1:'经营活动',l2:'货款收入',amount:100}]`, c);
  const html = vm.runInContext('paneVA()', c);
  assert.match(html, /偏差暂不可比/);
  assert.match(html, /2026-10/);
  assert.doesNotMatch(html, /-100\.0%/);
  assert.doesNotMatch(html, /偏差贡献度 Top5/);
});

test('同期有确认流水可展示偏差，同时提示完整性需核对', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.plans=[{period:'2026-10',entity:'测试公司',direction:'流入',l1:'经营活动',l2:'货款收入',amount:100}];
    S.snap.rows=[{id:1,txn_date:'2026-10-10',entity:'测试公司',direction:'收',l1:'经营活动',l2:'货款收入',amt_cny:80}];
  `, c);
  let html = vm.runInContext('paneVA()', c);
  assert.match(html, /偏差贡献度 Top5/);
  assert.match(html, /无法自动判断当月流水是否全部导入/);
  vm.runInContext('S.snap.pending=1', c);
  html = vm.runInContext('paneVA()', c);
  assert.match(html, /存在待确认流水/);
  assert.match(html, /报告期间数据待核查/);
  assert.doesNotMatch(html, /-20\.0%/);
  assert.doesNotMatch(html, /偏差贡献度 Top5/);
});

test('预实可切换月度、跨年自然季度和累计，汇总同期金额后重算偏差率', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.start='2026-11'; S.snap.end='2027-02';
    S.snap.allMonths=['2026-11','2026-12','2027-01','2027-02'];
    S.snap.plans=[['2026-11',100],['2026-12',900],['2027-01',300]].map(([period,amount]) =>
      ({period,amount,direction:'流入',l1:'经营活动',l2:'货款收入'}));
    S.snap.rows=[['2026-11',200],['2026-12',900],['2027-01',200],['2027-02',1000]].map(([month,amt_cny]) =>
      ({txn_date:month+'-10',amt_cny,direction:'收',l1:'经营活动',l2:'货款收入'}));
  `, c);
  const periodTable = source => vm.runInContext(source, c).match(/<table class="tbl va-period-table">([\s\S]*?)<\/table>/)[1];
  const month = periodTable('paneVA()');
  assert.match(month, /2026-11<\/th>[\s\S]*?\+100\.0%/);
  assert.match(month, /2027-02<\/th>[\s\S]*?1,000\.00[\s\S]*?暂无已审批计划，仅列实际/);
  const quarter = periodTable("S.vaGrain='quarter'; paneVA()");
  assert.match(quarter, /2026 Q4[\s\S]*?2026-11 至 2026-12[\s\S]*?1,000\.00[\s\S]*?1,100\.00[\s\S]*?\+10\.0%/);
  assert.match(quarter, /2027 Q1[\s\S]*?2027-01 至 2027-02[\s\S]*?-33\.3%[\s\S]*?仅计 1\/2 个有计划月/);
  const cumulative = periodTable("S.vaGrain='cumulative'; paneVA()");
  assert.match(cumulative, /所选期间累计[\s\S]*?2026-11 至 2027-02[\s\S]*?1,300\.00[\s\S]*?1,300\.00[\s\S]*?0\.0%/);

  const missing = periodTable("S.snap.rows.splice(2,1); S.vaGrain='quarter'; paneVA()");
  assert.match(missing, /2026 Q4[\s\S]*?\+10\.0%/);
  assert.match(missing, /2027 Q1[\s\S]*?300\.00<\/td><td class="num">—<\/td><td class="num">—<\/td>\s*<td class="num">—<\/td>/);
  assert.match(missing, /缺少同期确认流水（2027-01）/);
});

test('总览展示计划覆盖，并标明统计范围', () => {
  const c = dashboard();
  const html = vm.runInContext('paneOverview()', c);
  assert.match(html, /未来三个月 · 计划覆盖/);
  assert.match(html, /不含尚无余额流水的主体/);
  assert.match(html, /刷新时间不等于流水截至时间/);
});

test('公司预测逐月说明计划或历史外推', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.rows=[{id:1,txn_date:'2026-09-30',entity:'测试公司',bank:'测试银行',account:'A',balance:200,
      direction:'收',l1:'经营活动',l2:'货款收入',amt_cny:200}];
    S.snap.plans=[{period:'2026-10',entity:'测试公司',direction:'流入',l1:'经营活动',l2:'货款收入',amount:100}];
  `, c);
  const html = vm.runInContext('paneEntity()', c);
  assert.match(html, /2026-10 · 已审批计划/);
  assert.match(html, /2026-11 · 缺计划 \/ 历史外推/);
  assert.match(html, /安全垫 \/ 缺口/);
});

test('极端环比改为显示金额变化及基数提示', () => {
  const c = dashboard();
  const text = vm.runInContext('mom(10000, 1).txt', c);
  assert.match(text, /上期基数小/);
  assert.doesNotMatch(text, /%/);
});

test('集团总貌两张图各有公司筛选，公司透视按新顺序展示月度收支', () => {
  const c = dashboard();
  vm.runInContext("S.meta.accounts=[{entity:'测试公司',bank:'测试银行',account:'A'}]", c);
  const html = vm.runInContext('paneOverview()', c);
  assert.doesNotMatch(html, /data-act="overview-entity"/);
  assert.match(html, /id="group-trends-heading"[\s\S]*class="chart-filter"[\s\S]*data-act="period"/);
  assert.match(html, /data-act="overview-chart-company" data-chart="balance"/);
  assert.match(html, /data-act="overview-chart-company" data-chart="flow"/);
  assert.doesNotMatch(html, /公司 · 银行账户/);
  const entity = vm.runInContext('paneEntity()', c);
  assert.match(entity, /id="company-water-heading"[\s\S]*class="chart-filter"[\s\S]*data-act="entity"/);
  assert.match(entity, /未来三个月资金水位推算[\s\S]*月度现金流入 \/ 流出[\s\S]*受限资金（自动识别）/);
  assert.match(entity, /银行分布/);
  assert.doesNotMatch(entity, /<h3>(?:账户余额|本期计划)(?:\s|<)/);
  assert.match(vm.runInContext('JSON.stringify(NAV)', c), /资金下钻/);
});

test('已有余额可展示，缺汇率部分不混入人民币，单家公司看原币', () => {
  const c = dashboard();
  vm.runInContext(`
    S.meta.entities.push({short:'外币公司',ccy:'USD'},{short:'另一家公司',ccy:'HKD'});
    S.meta.fx = [
      {pair:'HKD/CNY',rate:0.9,effective_date:'2020-01-01'},
      {pair:'HKD/CNY',rate:99,effective_date:'2099-01-01'}
    ];
    S.snap.rows = [
      {id:1,entity:'测试公司',bank:'银行甲',account:'A',txn_date:'2026-08-31',balance:100,direction:'收',amount:40},
      {id:2,entity:'外币公司',bank:'银行乙',account:'B',txn_date:'2026-10-30',balance:200,direction:'支',amount:20},
      {id:3,entity:'另一家公司',bank:'银行甲',account:'C',txn_date:'2026-10-30',balance:100,direction:'收',amount:100}
    ];
    S.meta.accounts = [{entity:'测试公司',bank:'银行甲',account:'未录余额'}];
  `, c);
  const group = JSON.parse(vm.runInContext("JSON.stringify(overviewChartData('balance','ALL'))", c));
  assert.deepEqual(group.points, [
    {month:'2026-08',balance:100}, {month:'2026-09'}, {month:'2026-10',balance:190}
  ]);
  assert.deepEqual(group.missing, ['USD']);
  const html = vm.runInContext("overviewChartBody('balance','外币公司')", c);
  assert.match(html, /200\.00 USD/);
  assert.match(html, /公司原币/);
  assert.match(vm.runInContext('paneOverview()', c), /未计入缺汇率的 USD 金额/);
});

test('收支柱图保留单公司内部往来与冲减负值，集团剔除内部往来', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.rows = [
      {id:1,entity:'测试公司',txn_date:'2026-10-01',direction:'收',amount:300,is_internal:1},
      {id:2,entity:'测试公司',txn_date:'2026-10-05',direction:'支',amount:100,is_offset:1}
    ];
  `, c);
  assert.match(vm.runInContext("overviewChartBody('flow','测试公司')", c), /2026-10 流入 300\.00 CNY/);
  assert.match(vm.runInContext("overviewChartBody('flow','ALL')", c), /2026-10 流出 -100\.00 CNY/);
  assert.match(vm.runInContext("overviewChartBody('flow','ALL')", c), /2026-10 流入 0\.00 CNY/);
  assert.match(vm.runInContext("overviewChartBody('flow','测试公司')", c), /2026-10 净流量 400\.00 CNY/);
  assert.match(vm.runInContext("overviewChartBody('flow','测试公司',true)", c), /<details class="support-chart"><summary>月度净流量 <span>2026-10 · \+400 CNY/);
});

test('银行分布只汇总所选公司，保留部分余额与缺项标记', () => {
  const c = dashboard();
  const html = vm.runInContext(`bankDistribution('测试公司', [], [
    {entity:'测试公司', bank:'银行甲', cnyBalance:133209},
    {entity:'测试公司', bank:'银行甲', cnyBalance:null},
    {entity:'测试公司', bank:'银行乙', cnyBalance:880},
    {entity:'测试公司', bank:'银行缺余额', cnyBalance:null},
    {entity:'其他公司', bank:'银行甲', cnyBalance:1000000},
    {entity:'其他公司', bank:'其他银行', cnyBalance:100}
  ], true)`, c);
  assert.match(html, /comparison-bar/);
  assert.match(html, /133,209\.00 元/);
  assert.match(html, /银行乙/);
  assert.match(html, /部分数据/);
  assert.match(html, /<summary>1 家银行暂无可用数据<\/summary>/);
  assert.match(html, /<select data-act="bank-company"><option value="测试公司" selected>/);
  assert.doesNotMatch(html, /其他银行|compare-dimension|按公司/);
});

test('首个流水月没有期初余额时仍显示净变动，不伪造余额闭合', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.rows=[{id:1,txn_date:'2026-09-30',entity:'测试公司',bank:'测试银行',account:'A',balance:200,
      direction:'收',l1:'经营活动',amt_cny:200}];
  `, c);
  const html = vm.runInContext("entityWaterfall('测试公司')", c);
  assert.match(html, /缺少期初余额：测试银行/);
  assert.match(html, /aria-label="收支净变动瀑布"/);
  assert.doesNotMatch(html, /<title>期[初末]余额/);
});

test('瀑布图独立读取起止余额并显示未解释变动', () => {
  const c = dashboard();
  vm.runInContext(`
    S.waterEnd='2026-10';
    S.snap.rows=[
      {id:1,txn_date:'2026-09-30',entity:'测试公司',bank:'测试银行',account:'A',balance:100,direction:'收',l1:'经营活动',amt_cny:100},
      {id:2,txn_date:'2026-10-31',entity:'测试公司',bank:'测试银行',account:'A',balance:130,direction:'收',l1:'经营活动',amt_cny:20}
    ];
  `, c);
  const html = vm.runInContext("entityWaterfall('测试公司')", c);
  assert.match(html, /未解释变动 10\.00 CNY/);
  assert.match(html, /data-act="water-mode"/);
});

test('同一银行的空账号流水和待补登台账共用余额，真实账号保持独立', () => {
  const c = dashboard();
  vm.runInContext(`
    S.meta.accounts = [
      {entity:'测试公司',bank:'招商银行',account:'A'},
      {entity:'测试公司',bank:'工商银行',account:'（待补登）'}
    ];
    S.snap.rows = [
      {id:1,entity:'测试公司',bank:'招商银行',account:'A',txn_date:'2026-09-30',balance:100,direction:'收',amt_cny:100},
      {id:2,entity:'测试公司',bank:'工商银行',account:'',txn_date:'2026-09-30',balance:50,direction:'收',amt_cny:50},
      {id:3,entity:'测试公司',bank:'工商银行',account:'',txn_date:'2026-10-03',balance:70,direction:'收',amt_cny:20}
    ];
  `, c);
  const acc = JSON.parse(vm.runInContext('JSON.stringify(accountRows())', c));
  assert.equal(acc.length, 2);
  const icbc = acc.find(a => a.bank === '工商银行');
  assert.equal(icbc.balance, 70);
  assert.equal(icbc.masterAccount, '（待补登）');
  const chart = vm.runInContext("entityWaterfall('测试公司')", c);
  assert.match(chart, /<title>期初余额 150<\/title>/);
  assert.match(chart, /<title>期末余额 170<\/title>/);
});

test('活动及科目结构图可下钻，保留期间、方向及同名科目的上级活动', async () => {
  const c = dashboard();
  await vm.runInContext(`
    S.tab='g-ent'; S.period='2026-10'; S.drillSrc='plan'; S.drillAmt=1000;
    S.snap.rows=[
      {txn_date:'2026-10-03',entity:'测试公司',l1:'经营活动',l2:'其他',direction:'收',counterparty:'经营流入样例',amt_cny:30},
      {txn_date:'2026-10-04',entity:'测试公司',l1:'投资活动',l2:'其他',direction:'支',counterparty:'投资流出样例',amt_cny:50},
      {txn_date:'2026-10-05',entity:'测试公司',l1:'投资活动',l2:'处置',direction:'收',counterparty:'投资流入样例',amt_cny:10}
    ];
    document.querySelector=()=>({querySelector:()=>({scrollIntoView(){}})});
    render=async()=>{};
    handlers.click[0]({target:{closest:()=>({dataset:{act:'structure-drill',a:'投资活动',c:'其他',d:'支'}})}});
  `, c);
  assert.equal(vm.runInContext("[S.tab,S.entity,S.period,S.drillSrc,S.drillAmt].join('|')", c),
    'p-drill|测试公司|2026-10|actual|0');
  let html = vm.runInContext('paneDrill()', c);
  assert.match(html, /投资流出样例/);
  assert.doesNotMatch(html, /经营流入样例|投资流入样例/);
  await vm.runInContext(`
    handlers.click[0]({target:{closest:()=>({dataset:{act:'structure-drill',a:'投资活动',c:'',d:'both'}})}});
  `, c);
  html = vm.runInContext('paneDrill()', c);
  assert.match(html, /投资流出样例/);
  assert.match(html, /投资流入样例/);
  assert.doesNotMatch(html, /经营流入样例/);
});

test('数值变动标识按本期减上期并明确不可比', () => {
  const c = dashboard();
  assert.match(vm.runInContext('periodChange(80,100)', c), /▼ −20 元/);
  assert.match(vm.runInContext('periodChange(0,null)', c), /不可比/);
});

test('可支撑月数按近三月净流出而非总流出计算', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.end='2026-10';
    S.snap.rows=['2026-07','2026-08','2026-09'].flatMap((m,i) => [
      {id:i*2+1,txn_date:m+'-01',entity:'测试公司',direction:'收',amt_cny:80},
      {id:i*2+2,txn_date:m+'-02',entity:'测试公司',direction:'支',amt_cny:100}
    ]);
  `, c);
  const html = vm.runInContext("burnSummary('测试公司',[{cnyAvail:100}])", c);
  assert.match(html, /5\.0 个月/);
  assert.match(html, /不能证明流水完整/);
});

test('账户余额取报告期末前最后记录，不取未来流水', () => {
  const c = dashboard();
  vm.runInContext(`
    S.snap.end='2026-10'; S.snap.rows=[];
    S.meta.accounts=[{entity:'测试公司',bank:'测试银行',account:'A'}];
    S.full={rows:[
      {id:1,entity:'测试公司',bank:'测试银行',account:'A',txn_date:'2026-09-30',balance:100},
      {id:2,entity:'测试公司',bank:'测试银行',account:'A',txn_date:'2026-11-30',balance:500}
    ],restricted:{}};
  `, c);
  assert.equal(vm.runInContext('accountRows()[0].balance', c), 100);
});
