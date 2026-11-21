import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import { loadPyodide } from '../dist/assets/runtime/pyodide/pyodide.mjs';
import { validateXML } from '../dist/assets/validator.mjs';
import { checkLocally } from '../dist/assets/engine.mjs';

const py = await loadPyodide({indexURL: resolve('dist/assets/runtime/pyodide')});
await py.loadPackage('hashlib');
py.unpackArchive(readFileSync('dist/assets/runtime/rules.zip'), 'zip', {extractDir: '/app'});
py.runPython("import sys; sys.path.insert(0, '/app'); import browser_bridge");
const cases = JSON.parse(readFileSync('vendor/browser-fixtures.json', 'utf8'));

for (const fixture of cases) {
  test(`browser/native parity: ${fixture.name}`, async () => {
    py.FS.writeFile('/input.plist', Buffer.from(fixture.data, 'base64'));
    py.FS.writeFile('/profile.json', JSON.stringify(fixture.profile));
    try {
      if (fixture.error) {
        assert.throws(() => py.runPython('browser_bridge.prepare()'), error => error.message.includes(fixture.error));
        return;
      }
      py.runPython('browser_bridge.prepare()');
      const native = await validateXML(py.FS.readFile('/normalized.plist'));
      assert.notEqual(native.status, 'unavailable', native.output);
      assert.match(native.output, /1\.0\.8/);
      py.globals.set('native_json', JSON.stringify(native));
      const actual = JSON.parse(py.runPython('browser_bridge.finish(native_json)'));
      delete actual.native.output;
      assert.deepEqual(actual, fixture.expected);
      assert.throws(() => py.FS.stat('/input.plist'));
    } finally {
      for (const path of ['/input.plist', '/profile.json', '/normalized.plist']) {
        try { py.FS.unlink(path); } catch { /* Removed by finish(). */ }
      }
    }
  });
}

test('unavailable WASM cannot claim complete validation', () => {
  const fixture = cases[0];
  py.FS.writeFile('/input.plist', Buffer.from(fixture.data, 'base64'));
  py.FS.writeFile('/profile.json', JSON.stringify(fixture.profile));
  py.runPython('browser_bridge.prepare()');
  py.globals.set('native_json', JSON.stringify({status: 'unavailable', output: 'Unavailable in this test', issues: 0}));
  const report = JSON.parse(py.runPython('browser_bridge.finish(native_json)'));
  assert.equal(report.status, 'incomplete');
  assert.match(report.gaps[0], /validator unavailable/i);
});

test('file bytes transfer only to a disposable local worker, never fetch', async () => {
  const originalWorker = globalThis.Worker;
  const originalFetch = globalThis.fetch;
  let terminated = false;
  globalThis.fetch = () => { throw new Error('No network call is allowed to carry an input'); };
  globalThis.Worker = class {
    constructor(url, options) {
      assert.match(url.href, /assets\/worker\.mjs$/);
      assert.equal(options.type, 'module');
    }
    postMessage(message, transfer) {
      assert.equal(transfer[0], message.buffer);
      assert.equal(new TextDecoder().decode(message.buffer), 'private input');
      queueMicrotask(() => this.onmessage({data: {type: 'result', result: {status: 'issues'}}}));
    }
    terminate() { terminated = true; }
  };
  try {
    assert.deepEqual(await checkLocally(new Blob(['private input']), {}), {status: 'issues'});
    assert(terminated);
    await assert.rejects(checkLocally(new Blob([]), {}), /non-empty/);
    await assert.rejects(checkLocally(new Blob(['x'.repeat(2 * 1024 * 1024 + 1)]), {}), /smaller than/);
  } finally {
    globalThis.Worker = originalWorker;
    globalThis.fetch = originalFetch;
  }
});

test('static site is self-contained and subpath-safe', () => {
  const html = readFileSync('dist/index.html', 'utf8');
  assert(!/(?:src|href)="\/(?!\/)/.test(html));
  assert(!html.includes('app.devin.ai'));
  for (const asset of ['app.js', 'engine.mjs', 'worker.mjs']) {
    const source = readFileSync(`dist/assets/${asset}`, 'utf8');
    assert(!source.includes('/api/check') && !source.includes('/api/demo'));
  }
  const manifest = JSON.parse(readFileSync('dist/assets/manifest.json', 'utf8'));
  assert.equal(manifest.start_url, '../');
  for (const icon of manifest.icons) assert(!icon.src.startsWith('/'));
  const build = JSON.parse(readFileSync('dist/build-manifest.json', 'utf8'));
  for (const path of Object.keys(build.files)) assert(existsSync(`dist/${path}`), path);
});
