const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('polling renders remaining items and stops on completion', async () => {
  const nodes = Object.fromEntries(['run-progress', 'run-bar', 'run-status', 'run-counts', 'run-current', 'run-outcomes', 'run-error'].map(id => [id, { dataset: {runId: '1'}, textContent: '', removeAttribute() {} }]));
  let response = { status: 'running', total: 3, processed: 1, remaining: 2, current_source_title: '<Song>', added: 1, skipped: 0, removed: 0, failed: 0, ambiguous: 0 };
  const timers = [];
  vm.runInNewContext(fs.readFileSync('src/music_sync/run_progress.js', 'utf8'), {
    document: {getElementById: id => nodes[id]}, AbortSignal,
    fetch: async url => { assert.equal(url, '/api/runs/1'); return {ok: true, json: async () => response}; },
    setTimeout: callback => timers.push(callback),
  });
  await new Promise(setImmediate);
  assert.equal(nodes['run-bar'].value, 1);
  assert.match(nodes['run-counts'].textContent, /2 items left/);
  assert.equal(nodes['run-current'].textContent, 'Current song: <Song>');
  response = {...response, status: 'success', processed: 3, remaining: 0};
  await timers.shift()();
  assert.match(nodes['run-counts'].textContent, /0 items left/);
  assert.equal(nodes['run-bar'].value, 3);
  assert.equal(timers.length, 0);
});
