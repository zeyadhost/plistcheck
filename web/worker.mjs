import { loadPyodide } from './runtime/pyodide/pyodide.mjs';
import { validateXML } from './validator.mjs';

// A disposable worker isolates each input, keeps the UI responsive, and can be terminated.
self.onmessage = async ({data: {buffer, profile}}) => {
  let pyodide;
  try {
    const indexURL = new URL('./runtime/pyodide/', import.meta.url).href;
    const [runtime, archive] = await Promise.all([
      loadPyodide({indexURL}),
      fetch(new URL('./runtime/rules.zip', import.meta.url)).then(response => {
        if (!response.ok) throw new Error('The local rules could not load. Reload the page and try again.');
        return response.arrayBuffer();
      }),
    ]);
    pyodide = runtime;
    await pyodide.loadPackage('hashlib');
    pyodide.unpackArchive(archive, 'zip', {extractDir: '/app'});
    pyodide.runPython("import sys; sys.path.insert(0, '/app'); import browser_bridge");
    self.postMessage({type: 'checking'});
    pyodide.FS.writeFile('/input.plist', new Uint8Array(buffer));
    pyodide.FS.writeFile('/profile.json', JSON.stringify(profile));
    pyodide.runPython('browser_bridge.prepare()');
    const native = await validateXML(pyodide.FS.readFile('/normalized.plist'));
    pyodide.globals.set('native_json', JSON.stringify(native));
    const result = JSON.parse(pyodide.runPython('browser_bridge.finish(native_json)'));
    self.postMessage({type: 'result', result});
  } catch (error) {
    // ConfigError messages contain the existing actionable parser/profile guidance.
    const configError = error.message?.match(/(?:ConfigError|ValueError): ([^\n]+)/);
    self.postMessage({type: 'error', message: configError?.[1] || 'The local checker could not load or finish. Reload the page and try again; no file was uploaded.'});
  } finally {
    if (pyodide) {
      for (const path of ['/input.plist', '/profile.json', '/normalized.plist']) {
        try { pyodide.FS.unlink(path); } catch { /* May already be removed by finish(). */ }
      }
    }
  }
};
