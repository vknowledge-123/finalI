const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
// Lightweight DOM doubles exercise the shipped JavaScript, not visual layout.
function element(value = '') {
  return {value, innerHTML: '', textContent: '', children: [], style: {}, dataset: {},
    classList: {add() {}, remove() {}, toggle() {}, contains() {return false;}},
    appendChild(child) {this.children.push(child);}, setAttribute() {},
    addEventListener() {}, querySelectorAll() {return [];}, remove() {}, focus() {}};
}
const elements = Object.fromEntries(Object.entries(fixture.fields).map(([k,v]) => [k, element(v)]));
const saved = [];
const response = data => ({ok: true, status: 200, text: async () => JSON.stringify(data)});
const context = vm.createContext({
  console: {log() {}, error() {}, warn() {}}, URLSearchParams, URL, Date, Number, String,
  setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  location: {search: '', protocol: 'http:', host: 'localhost'},
  window: {location: {href: ''}, addEventListener() {}}, navigator: {},
  document: {getElementById: id => elements[id] || null, createElement: () => element(),
    addEventListener() {}, querySelectorAll() {return [];}, body: element()},
  fetch: async (url, options = {}) => {
    if (url.startsWith('/api/alert-config')) {
      if (options.method === 'POST') saved.push(JSON.parse(options.body));
      return response(options.method === 'POST' ? {ok: true} : fixture.configs);
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
  const table = Object.values(elements).find(e => e.innerHTML.includes('100.50') && e.innerHTML.includes('data-label="Alert"'));
  assert(table); assert(!table.innerHTML.includes('<img')); checks++;
  run(`POS_SNAPSHOT.SBIN={symbol:'SBIN',status:'OPEN',qty:1};`);
  await context.refreshPositions();
  assert.equal(run('Object.keys(POS_SNAPSHOT).length'), 0); checks++;
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
