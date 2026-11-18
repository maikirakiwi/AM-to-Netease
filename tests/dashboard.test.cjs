const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

for (const outcome of ['success', 'failed', 'cancelled']) {
  test(`background correction polling handles ${outcome}`, async () => {
    const message = {textContent: ''};
    const banner = {dataset: {runId: '42'}, querySelector: () => message, classList: {replace() {}}};
    const timers = [], redirects = [];
    let run = {status: 'running'};
    vm.runInNewContext(fs.readFileSync('src/music_sync/dashboard.js', 'utf8'), {
      document: {addEventListener() {}, getElementById: id => id === 'correction-status' ? banner : null},
      window: {addEventListener() {}},
      location: {hash: '', href: 'http://localhost/attention?correction_run=42', replace: url => redirects.push(String(url))},
      AbortSignal, URL, setTimeout: callback => timers.push(callback),
      fetch: async url => { assert.equal(url, '/api/runs/42'); return {ok: true, json: async () => run}; },
    });
    await new Promise(setImmediate);
    assert.match(message.textContent, /background/);
    run = {status: outcome, error: '<Provider unavailable>'};
    await timers.shift()();
    assert.equal(timers.length, 0);
    if (outcome === 'success') assert.deepEqual(redirects, ['http://localhost/attention']);
    else {
      assert.deepEqual(redirects, []);
      assert.match(message.textContent, outcome === 'cancelled' ? /reverted/ : /<Provider unavailable>/);
    }
  });
}
