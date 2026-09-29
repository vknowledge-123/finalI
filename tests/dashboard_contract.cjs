const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
// Lightweight DOM doubles exercise the shipped JavaScript, not visual layout.
function element(value = '') {
  return {value, innerHTML: '', textContent: '', children: [], style: {}, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() {return false;}},
    appendChild(child) {this.children.push(child);}, setAttribute() {},
    append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;},
    addEventListener() {}, querySelectorAll() {return [];}, remove() {}, focus() {}};
}
const elements = Object.fromEntries(Object.entries(fixture.fields).map(([k,v]) => [k, element(v)]));
const saved = [];
const response = data => ({ok: true, status: 200, text: async () => JSON.stringify(data)});
const context = vm.createContext({
  console: {log() {}, error() {}, warn() {}}, URLSearchParams, URL, Date, Number, String, AbortController,
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  location: {search: '', protocol: 'http:', host: 'localhost'},
  WebSocket: class {constructor(url) {this.url = url;}},
  window: {location: {href: ''}, addEventListener() {}}, navigator: {},
  document: {getElementById: id => elements[id] || null, createElement: () => element(),
    addEventListener() {}, querySelector() {return element();}, querySelectorAll() {return [];}, body: element()},
  fetch: async (url, options = {}) => {
    if (url.startsWith('/api/alert-config')) {
      if (options.method === 'POST') saved.push(JSON.parse(options.body));
      return response(options.method === 'POST' ? {status: 'saved', config: {alert_name: Object.keys(fixture.configs.configs)[0]}} : fixture.configs);
    }
    if (url.startsWith('/api/positions')) return response({positions: []});
    return response({ok: true, config: {}});
  }
});
for (const script of fixture.scripts) new vm.Script(script).runInContext(context);
const run = code => vm.runInContext(code, context);
(async () => {
  let checks = 0;
  assert.equal(run(`escapeHtml('<img src=x onerror="x">')`), '&lt;img src=x onerror=&quot;x&quot;&gt;'); checks++;
  run(`toast('<img src=x onerror="x">', 'error')`);
  assert(!elements['toast-container'].children.at(-1).innerHTML.includes('<img')); checks++;
  await assert.rejects(context.readJsonResponse({ok: false, status: 500, text: async () => 'Internal Server Error'})); checks++;
  await assert.rejects(context.readJsonResponse({ok: false, status: 401, text: async () => '{"error":"ADMIN_AUTH_REQUIRED"}'}));
  assert.equal(context.window.location.href, '/auth'); checks++;
  run(`ALERTS.push({alert_name:'<img src=x onerror="x">', time:Date.now(), result:[{symbol:'SBIN', ltp:'100.50', status:'ERROR', reason:'<img src=x onerror="x">'}]}); renderAlerts();`);
  const table = elements.alertsBody;
  assert(table); assert(!table.innerHTML.includes('<img')); checks++;
  run(`ALERTS.length=0; ALERTS.push({alert_name:'QA', time:Date.now(), result:[
    {symbol:'SBIN',status:'ENTERED',trade_id:'T1'},
    {symbol:'SBIN',status:'SKIPPED',reason:'ALREADY_OPEN'},
    {symbol:'SBIN',status:'SKIPPED',reason:'ALREADY_OPEN'}]});
    POS_MAP.SBIN={symbol:'SBIN',alert_name:'QA',trade_id:'T1',status:'OPEN',qty:1,pnl:2.4}; renderAlerts();`);
  assert.equal((elements.posBody.innerHTML.match(/data-label="P&L"/g) || []).length, 1);
  assert.equal((elements.alertsBody.innerHTML.match(/ALREADY_OPEN/g) || []).length, 2);
  assert(!elements.alertsBody.innerHTML.includes('data-label="P&L"'));
  assert(!elements.alertsBody.innerHTML.includes('data-squareoff'));
  run(`CLOSED_POSITIONS.T1={...POS_MAP.SBIN,status:'CLOSED',qty:0};
    POS_MAP.SBIN={...POS_MAP.SBIN,trade_id:'T2',pnl:0}; renderAlerts();`);
  assert.equal((elements.posBody.innerHTML.match(/data-label="P&L"/g) || []).length, 2);
  assert.equal((elements.posBody.innerHTML.match(/data-squareoff="SBIN"/g) || []).length, 1);
  assert.equal((elements.posBody.innerHTML.match(/data-symbol="SBIN" data-field="pnl"/g) || []).length, 1);
  assert.equal(run("alertPosition({alert_name:'QA'}, {status:'SKIPPED',reason:'ALREADY_OPEN'}, POS_MAP.SBIN)"), null);
  checks++;
  run(`POS_SNAPSHOT.SBIN={symbol:'SBIN',status:'OPEN',qty:1};`);
  await context.refreshPositions();
  assert.equal(run('Object.keys(POS_SNAPSHOT).length'), 0); checks++;
  run(`startWS(); POS_MAP.SBIN={symbol:'SBIN',status:'OPEN',qty:2,side:'BUY',entry_price:100,
    realized_pnl:3,trail_price:99,tsl_pct:1,tsl_stepwise:true,strategy_mode:'CLASSIC'};
    WS.onmessage({data:JSON.stringify({type:'tick',symbol:'SBIN',ltp:101.5,close:100})});`);
  assert.equal(run('POS_MAP.SBIN.ltp'), 101.5);
  assert.equal(run('POS_MAP.SBIN.pnl'), 6);
  assert.equal(run('POS_SNAPSHOT.SBIN.pnl'), 6);
  assert.equal(run('POS_MAP.SBIN.trail_price'), 99); checks++;
  run(`POS_MAP.SBIN.side='SELL'; WS.onmessage({data:JSON.stringify({type:'tick',symbol:'SBIN',ltp:99,close:100})});`);
  assert.equal(run('POS_MAP.SBIN.pnl'), 5); checks++;
  run(`WS.onmessage({data:JSON.stringify({type:'tick',symbol:'SBIN',ltp:'bad'})});`);
  assert.equal(run('POS_MAP.SBIN.ltp'), 99); checks++;
  run(`CLOSED_POSITIONS={L1:{symbol:'OLD',trade_id:'L1',status:'CLOSED',pnl:100,realized_pnl:100},
    P1:{symbol:'PAPEROLD',trade_id:'P1',status:'CLOSED',pnl:20,paper_trading:true}};
    POS_SNAPSHOT={OLD:{...CLOSED_POSITIONS.L1}};
    POS_MAP={SBIN:{symbol:'SBIN',trade_id:'L2',status:'OPEN',qty:1,pnl:75,realized_pnl:25},
    AEQUS:{symbol:'AEQUS',trade_id:'P2',status:'OPEN',qty:1,pnl:-5,paper_trading:true}};
    renderPositions(Object.values(POS_MAP));`);
  assert.equal(elements.strategyPnlValue.textContent, '\u20b9 +175.00');
  assert.equal(elements.paperPnlValue.textContent, '\u20b9 +15.00');
  run(`CLOSED_POSITIONS.L2={...POS_MAP.SBIN,status:'CLOSED',qty:0,pnl:80};
    POS_SNAPSHOT.SBIN=CLOSED_POSITIONS.L2; delete POS_MAP.SBIN; renderPositions(Object.values(POS_MAP));`);
  assert.equal(elements.strategyPnlValue.textContent, '\u20b9 +180.00');
  run(`POS_MAP.SBIN={symbol:'SBIN',trade_id:'L3',status:'OPEN',pnl:-10}; renderPositions(Object.values(POS_MAP));`);
  assert.equal(elements.strategyPnlValue.textContent, '\u20b9 +170.00');
  run(`ACCOUNT_MTM_DATA={status:'FRESH',total:999,as_of:Date.now()/1000}; renderAccountMtm();`);
  assert.equal(elements.accountMtmValue.textContent, '\u20b9 +999.00');
  assert.equal(elements.strategyPnlValue.textContent, '\u20b9 +170.00');
  run(`ACCOUNT_MTM_DATA.as_of-=60; renderAccountMtm();`);
  assert(elements.accountMtmState.textContent.startsWith('Stale'));
  run(`ACCOUNT_MTM_DATA={status:'UNAVAILABLE',total:null}; renderAccountMtm();`);
  assert.equal(elements.accountMtmValue.textContent, '--');
  assert.equal(elements.accountMtmState.textContent, 'Unavailable'); checks++;
  const key = Object.keys(fixture.configs.configs)[0];
  await context.fillCfg(key);
  assert.equal(String(elements.cfg_tsl_on.value), 'false');
  assert.equal(String(elements.cfg_cost_sl_on.value), 'true');
  assert.equal(String(elements.cfg_exit_alert_on.value), 'true'); checks++;
  await context.saveCfg();
  assert.equal(saved.length, 1);
  assert.equal(saved[0].trailing_sl_enabled, false);
  assert.equal(saved[0].cost_sl_enabled, true);
  assert.equal(saved[0].exit_alert_enabled, true); checks++;
  process.stdout.write(JSON.stringify({checks, saved}));
})().catch(error => {console.error(error); process.exitCode = 1;});
