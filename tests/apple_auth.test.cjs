const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const script = fs.readFileSync('src/music_sync/apple_auth.js', 'utf8');

function setup() {
  const elements = {authorize: {}, status: {}, 'apple-config': {textContent: '"test-developer"'}};
  const popup = {closed: false, location: {}, close() { this.closed = true; }, postMessage() {}};
  let handler, poll;
  const requests = [];
  const context = {
    document: {getElementById: id => elements[id]},
    window: {open: () => popup, addEventListener: (_, fn) => { handler = fn; }, removeEventListener() {}},
    location: {host: 'sync.example', href: 'https://sync.example/setup/apple'},
    URL, URLSearchParams, btoa, unescape, encodeURIComponent,
    setInterval: fn => { poll = fn; return 1; }, setTimeout: () => 2,
    clearInterval() {}, clearTimeout() {},
    fetch: async (url, options) => { requests.push({url, options}); return {ok: true}; },
  };
  vm.runInNewContext(script, context);
  elements.authorize.onclick();
  return {elements, popup, requests, message: event => handler(event), poll: () => poll()};
}

test('captures Apple token then verifies on backend after popup closes', async () => {
  const flow = setup();
  assert.match(flow.popup.location.href, /^https:\/\/authorize.music.apple.com\/woa\?/);
  flow.message({source: flow.popup, origin: 'https://authorize.music.apple.com', data: {jsonrpc: '2.0', id: 1, method: 'authorize', params: ['test-user']}});
  assert.equal(flow.requests.length, 0);
  flow.popup.closed = true;
  flow.poll();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(flow.requests[0].url, '/setup/apple');
  assert.equal(flow.requests[0].options.body.get('user_token'), 'test-user');
  assert.match(flow.elements.status.textContent, /Authorized and verified/);
});

test('rejects messages from other origins and windows', () => {
  const flow = setup();
  const data = {jsonrpc: '2.0', id: 1, method: 'authorize', params: ['untrusted']};
  flow.message({source: flow.popup, origin: 'https://apple.com.attacker.example', data});
  flow.message({source: {}, origin: 'https://authorize.music.apple.com', data});
  flow.popup.closed = true;
  flow.poll();
  assert.equal(flow.requests.length, 0);
  assert.match(flow.elements.status.textContent, /closed before a token/);
});
