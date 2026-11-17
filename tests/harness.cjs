const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const clone = x => x===undefined?undefined:JSON.parse(JSON.stringify(x));
const tick = () => new Promise(r => setImmediate(r));
async function boot({store={}, manual={}, fail=false, delay=false, flow=[], serverBoot=false, resetToken=''}={}) {
  const errors=[], timers=[], writes=[], pending=[];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e.message));
  const html = fs.readFileSync(path.join(root,'生态资金计划系统v10.html'),'utf8');
  const dom = new JSDOM(html,{url:'http://localhost:9876/',runScripts:'outside-only',virtualConsole:vc});
  const w=dom.window;
  w.TextDecoder=TextDecoder;
  w.Headers=Headers;
  for(const [k,v] of Object.entries(store)) w.localStorage.setItem(k,v);
  w.scrollTo=()=>{}; w.alert=msg=>errors.push(msg); w.confirm=()=>true;
  w.setTimeout=(fn,ms)=>{const t={fn,ms};timers.push(t);return t;};
  w.clearTimeout=t=>{if(t)t.cancel=true;};
  const db={manual:clone(manual),params:[],terms:[],flow:clone(flow),rules:[],resetToken};
  w.fetch=async (url, opts={})=>{
    const p=String(url).replace(/^https?:\/\/[^/]+/,'');
    if(opts.method==='POST') {
      const body=JSON.parse(opts.body); writes.push({path:p,body});
      if(delay) await new Promise(r=>pending.push(r));
      if(fail) throw new Error('模拟连接断开');
      if(p==='/api/manual') Object.assign(db.manual,clone(body.manual));
      if(p==='/api/rules') db.rules=JSON.parse(body.payload);
      if(p==='/api/params') db.params=Object.entries(body.params).map(([id,value])=>({id,value}));
      if(p==='/api/terms') db.terms=clone(body.rows);
      if(p==='/api/import' && body.kind==='flow') db.flow=clone(body.rows);
      return {ok:true,json:async()=>({ok:true,count:Object.keys(db.manual).length,saved:Object.keys(body.manual||{}).length})};
    }
    const body=p==='/api/rows'?{flow:db.flow,manual:db.manual,plan:[],items:[],resetToken:db.resetToken,stats:{},balances:{byMonth:{}}}:
      p==='/api/eco'?{ok:true,params:db.params,terms:db.terms,templates:[],subjects:[],entities:[],bridge:[],submissions:[],stats:{periods:[]}}:
      p==='/api/locked'?{ok:true,items:[]}:p==='/api/rules'?{payload:null}:{ok:true,rows:[]};
    return {ok:true,json:async()=>clone(body)};
  };
  for(let code of [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1])) {
    if(code.includes('function forecastHtml()')) code=code.replace(/boot\(\);\s*\}\)\(\);/,'window.testEco={ECO,forecastHtml,saveParams,saveTerms,renderP7};\nboot();\n})();');
    try { vm.runInContext(code,dom.getInternalVMContext()); } catch(e) { errors.push(e.stack); }
  }
  vm.runInContext('window.testState={S,DATA,WH,saveManual,flushManual,manualKey,applyFlow,saveSelected,saveRulesLS,renderArch,saveArch};',dom.getInternalVMContext());
  w.WH.on=true;
  if(serverBoot){
    const source=fs.readFileSync(path.join(root,'数仓','server.py'),'utf8');
    const code=source.match(/BOOT = r"""([\s\S]*?)"""/)[1].replace(/<\/?script>/g,'');
    vm.runInContext(code,dom.getInternalVMContext());
  }
  await tick();
  return {dom,w,db,errors,timers,writes,pending,state:w.testState,close:()=>w.close(),store:()=>Object.fromEntries(Object.keys(w.localStorage).map(k=>[k,w.localStorage.getItem(k)]))};
}
module.exports={boot,root,tick,clone};
