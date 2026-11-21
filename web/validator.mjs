import createOcvalidate from './runtime/ocvalidate.mjs';

// Finish all downloads/compilation before starting the short validation deadline.
export async function prepareValidator() {
  const lines = [];
  try {
    const module = await createOcvalidate({
      noInitialRun: true,
      print: line => lines.push(line),
      printErr: line => lines.push(line),
    });
    return {module, lines};
  } catch {
    return {lines};
  }
}

// Each check gets a fresh upstream process and an in-memory file; nothing is uploaded.
export async function validateXML(bytes, prepared) {
  const {module, lines} = prepared ?? await prepareValidator();
  try {
    module.FS.writeFile('/config.plist', bytes);
    const code = module.callMain(['/config.plist']);
    const output = lines.join('\n').replaceAll('/config.plist', '[local config]').slice(0, 50000);
    const match = output.match(/Found (\d+) issues?/);
    return {status: code === 0 ? 'passed' : 'failed', output, issues: match ? Number(match[1]) : code === 0 ? 0 : 1};
  } catch {
    return {status: 'unavailable', output: 'Official WebAssembly validator could not finish. No schema pass is claimed.', issues: 0};
  } finally {
    if (module) {
      try { module.FS.unlink('/config.plist'); } catch { /* A failed startup may not create the file. */ }
    }
  }
}
