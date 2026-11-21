export async function checkLocally(file, profile, onProgress = () => {}) {
  if (!file.size || file.size > 2 * 1024 * 1024) throw new Error('Choose a non-empty plist smaller than 2 MB.');
  if (!globalThis.Worker || !globalThis.WebAssembly) throw new Error('This browser needs WebAssembly and Web Workers to check files locally. Try a current browser.');
  const buffer = await file.arrayBuffer();
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./worker.mjs', import.meta.url), {type: 'module'});
    let timer;
    const finish = (error, result) => {
      clearTimeout(timer);
      worker.terminate();
      if (error) reject(error); else resolve(result);
    };
    const deadline = (milliseconds, message) => {
      clearTimeout(timer);
      timer = setTimeout(() => finish(new Error(message)), milliseconds);
    };
    worker.onmessage = ({data}) => {
      if (data.type === 'checking') {
        onProgress('Checking configuration on your device…');
        deadline(8000, 'Local validation timed out. No schema pass is claimed. Try a smaller or simpler config.');
      } else if (data.type === 'result') finish(null, data.result);
      else if (data.type === 'error') finish(new Error(data.message));
    };
    worker.onerror = () => finish(new Error('The local checker could not start. Reload the page in a current browser; no file was uploaded.'));
    deadline(120000, 'The local checker took too long to load. Check your connection and try again; no file was uploaded.');
    onProgress('Loading the local checker… First use downloads the runtime.');
    worker.postMessage({buffer, profile}, [buffer]);
  });
}
