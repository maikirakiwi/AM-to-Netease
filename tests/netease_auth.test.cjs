const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

for (const terminal of ['authorized', 'expired', 'failed']) {
  test(`QR login displays scanned and ${terminal} states then stops polling`, async () => {
    const message = {textContent: ''}, timers = [];
    let status = 'scanned';
    vm.runInNewContext(fs.readFileSync('src/music_sync/netease_auth.js', 'utf8'), {
      document: {getElementById: id => id === 'netease-login' ? {dataset: {attempt: 'abc'}} : message},
      fetch: async (url, options) => {
        assert.equal(url, '/setup/netease/abc/check'); assert.equal(options.method, 'POST');
        return {ok: true, json: async () => ({status})};
      }, AbortSignal, setTimeout: fn => timers.push(fn),
    });
    await new Promise(setImmediate);
    assert.match(message.textContent, /Confirm login/);
    status = terminal; await timers.shift()();
    assert.match(message.textContent, terminal === 'authorized' ? /session has been saved/ : terminal === 'expired' ? /expired/ : /failed/);
    assert.equal(timers.length, 0);
  });
}
