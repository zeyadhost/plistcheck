const $ = (selector) => document.querySelector(selector);
const form = $('#profile');
let selectedFile = null;
let currentReport = null;
let running = false;
let revision = 0;
let reportRevision = -1;
let severity = 'all';
const escape = (text) => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function error(message) { $('#form-error').textContent = message; $('#form-error').hidden = !message; $('#file').setAttribute('aria-invalid', message ? 'true' : 'false'); }
function stale() {
  revision += 1;
  if (currentReport) { $('#result-status').textContent = 'INPUT CHANGED · RUN AGAIN'; $('#report').classList.add('stale'); }
}
function setFile(file) {
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { error('Choose a plist smaller than 2 MB.'); return; }
  selectedFile = file;
  $('#file-title').textContent = file.name;
  $('#file-subtitle').textContent = `${(file.size / 1024).toFixed(1)} KB · ready to inspect · click to replace`;
  $('#dropzone').classList.add('has-file');
  error(''); stale();
}
$('#file').addEventListener('change', event => setFile(event.target.files[0]));
for (const event of ['dragenter', 'dragover']) $('#dropzone').addEventListener(event, e => { e.preventDefault(); $('#dropzone').classList.add('dragging'); });
for (const event of ['dragleave', 'drop']) $('#dropzone').addEventListener(event, e => { e.preventDefault(); $('#dropzone').classList.remove('dragging'); });
$('#dropzone').addEventListener('drop', event => setFile(event.dataTransfer.files[0]));
form.addEventListener('change', stale);
form.addEventListener('input', stale);

$('#demo').addEventListener('click', async () => {
  if (running) return;
  $('#demo').disabled = true;
  try {
    const response = await fetch('/api/demo');
    if (!response.ok) throw new Error('The example could not load. Please try again.');
    setFile(new File([await response.blob()], 'demo-config.plist', {type: 'application/x-plist'}));
    const defaults = {cpu:'coffee-lake',cpu_model:'Intel Core i7-9700K',gpu:'amd-supported',gpu_model:'Radeon RX 580',chipset:'z390',board:'Gigabyte Z390 AORUS PRO',macos:'14',cfg_lock:'enabled',vt_d:'unknown',rebar:'disabled'};
    for (const [key, value] of Object.entries(defaults)) form.elements[key].value = value;
    await runCheck();
  } catch (e) { error(e.message); } finally { $('#demo').disabled = false; }
});

form.addEventListener('submit', event => { event.preventDefault(); runCheck(); });
async function runCheck() {
  if (running) return;
  if (!selectedFile) { error('Add a config.plist, or try the example first.'); $('#file').focus(); return; }
  running = true; error('');
  const currentRevision = revision;
  const data = new FormData(form);
  data.append('config', selectedFile);
  $('#run').disabled = true;
  $('#run').textContent = 'Checking configuration…';
  $('#result-status').textContent = 'RUNNING CHECKS…';
  try {
    const response = await fetch('/api/check', {method:'POST',body:data});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The check failed. Please try again.');
    if (revision !== currentRevision) { $('#result-status').textContent = 'INPUT CHANGED · RUN AGAIN'; return; }
    currentReport = result; reportRevision = currentRevision; severity = 'all'; renderReport();
    if (matchMedia('(max-width: 1000px)').matches) $('#results').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block:'start'});
  } catch (e) { error(e.message || 'Could not reach the checker. Please try again.'); $('#result-status').textContent = 'CHECK NOT COMPLETED'; }
  finally { running = false; $('#run').disabled = false; $('#run').innerHTML = 'Check configuration <span>↗</span>'; }
}

function renderReport() {
  const r = currentReport;
  const outdated = reportRevision !== revision;
  $('#empty-state').hidden = true; $('#report').hidden = false; $('#report').classList.toggle('stale', outdated);
  $('#result-status').textContent = outdated ? 'INPUT CHANGED · RUN AGAIN' : r.status === 'incomplete' ? 'INCOMPLETE COVERAGE' : 'CHECK COMPLETE';
  const total = r.findings.length;
  $('#report').innerHTML = `<div class="report-heading"><h3>${r.status === 'incomplete' ? 'Not fully checked.' : total ? 'Here’s what needs attention.' : 'No known issues found.'}</h3><p>${r.checked} additional checks evaluated · ${r.passed} passed · ${r.skipped} not applicable / unknown</p><p class="profile-summary">${escape(r.profile.cpu_model || r.profile.cpu)} / ${escape(r.profile.gpu_model || r.profile.gpu)}<br>${escape(r.profile.board || r.profile.chipset)} · macOS ${escape(r.profile.macos)}</p></div>
  <div class="count-grid"><div class="error-count"><strong>${r.counts.error}</strong><span>Errors</span></div><div class="warning-count"><strong>${r.counts.warning}</strong><span>Warnings</span></div><div><strong>${r.counts.info}</strong><span>Notes</span></div></div>
  <div class="report-toolbar"><div class="filters" role="group" aria-label="Filter findings">${['all','error','warning','info'].map(s => `<button type="button" data-filter="${s}" aria-pressed="${severity === s}">${s === 'all' ? 'All' : s === 'info' ? 'Notes' : s[0].toUpperCase() + s.slice(1) + 's'}</button>`).join('')}</div><button class="download" type="button" id="download" aria-label="Download diagnostic report as JSON">↓ JSON</button></div>
  <div class="findings">${r.findings.filter(f => severity === 'all' || f.severity === severity).map(f => `<details class="finding ${escape(f.severity)}"><summary><span class="severity-mark">${f.severity === 'error' ? '×' : f.severity === 'warning' ? '!' : 'i'}</span><span><small>${escape(f.path)}</small><strong>${escape(f.title)}</strong></span><i>+</i></summary><div class="finding-body"><p>${escape(f.detail)}</p><div class="fix"><span>WHAT TO DO</span><p>${escape(f.fix)}</p></div><a href="${escape(f.source)}" target="_blank" rel="noreferrer">Read the source ↗</a></div></details>`).join('') || '<p class="no-findings">No findings in this category. Review the coverage gaps below.</p>'}</div>
  <details class="native"><summary><span>Official ocvalidate output</span><span class="native-status">${escape(r.native.status)} ↗</span></summary><p>Native issue counts can include several diagnostics. The error card above groups them into one finding.</p><pre>${escape(r.native.output)}</pre></details>
  <details class="gaps" open><summary>Not checked — read before you boot <span>↗</span></summary><ul>${r.gaps.map(g => `<li>${escape(g)}</li>`).join('')}</ul><p>No automatic fixes. No boot guarantee. Keep a backup EFI and verify every change.</p></details>`;
  document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { severity = button.dataset.filter; renderReport(); document.querySelector(`[data-filter="${severity}"]`).focus({preventScroll:true}); }));
  $('#download').addEventListener('click', () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'plistcheck-report.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
}
