const $ = (selector) => document.querySelector(selector);
const form = $('#profile');
let selectedFile = null;
let exampleSelected = false;
let currentReport = null;
let running = false;
let revision = 0;
let reportRevision = -1;
let severity = 'all';
let profileEdited = false;
const escape = (text) => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const familyLabel = (field, value) => [...form.elements[field].options].find(option => option.value === value)?.textContent.trim() || 'Not specified';
const severityLabel = {all:'All findings', error:'Errors', warning:'Warnings', info:'Notes'};
const severityName = {error:'Error', warning:'Warning', info:'Note'};
const exampleProfile = {device:'desktop',cpu:'coffee-lake',cpu_model:'Intel Core i7-9700K',gpu:'amd-polaris-vega',gpu_model:'Radeon RX 580',chipset:'z390',board:'Gigabyte Z390 AORUS PRO',macos:'14',cfg_lock:'enabled',vt_d:'unknown',rebar:'disabled'};

function error(message) { $('#form-error').textContent = message; $('#form-error').hidden = !message; $('#file').setAttribute('aria-invalid', message ? 'true' : 'false'); }
function revealError() { $('#file').focus({preventScroll:true}); $('#form-error').scrollIntoView({block:'nearest', behavior:'instant'}); }
function stale() {
  revision += 1;
  if (currentReport) {
    $('#result-status').textContent = 'INPUT CHANGED · RUN AGAIN';
    $('#report').classList.add('stale');
    $('#download').disabled = true;
  }
}
function setFile(file, example = false) {
  if (!file) return;
  if (file.size === 0 || file.size > 2 * 1024 * 1024) {
    selectedFile = null;
    exampleSelected = false;
    $('#file').value = '';
    $('#file-title').textContent = 'File rejected — choose another';
    $('#file-subtitle').textContent = 'Use a non-empty XML or binary config.plist under 2 MB';
    $('#dropzone').classList.remove('has-file');
    $('#example-note').hidden = true;
    stale();
    $('#result-status').textContent = 'FILE REJECTED';
    $('#run').disabled = true;
    error(file.size === 0 ? 'This file is empty. Choose a non-empty OpenCore config.plist.' : 'Choose a plist smaller than 2 MB. The previous file is no longer selected.');
    return;
  }
  selectedFile = file;
  exampleSelected = example;
  $('#file-title').textContent = file.name;
  $('#file-subtitle').textContent = `${(file.size / 1024).toFixed(1)} KB · ready to inspect · click to replace`;
  $('#dropzone').classList.add('has-file');
  $('#example-note').hidden = !example;
  $('#run').disabled = running;
  error(''); stale();
}
$('#file').addEventListener('change', event => setFile(event.target.files[0]));
const touchInput = matchMedia('(pointer: coarse)');
function uploadPrompt() {
  if (selectedFile || !$('#form-error').hidden) return;
  $('#file-title').textContent = touchInput.matches ? 'Tap to choose config.plist' : 'Drop your config.plist';
  $('#file-subtitle').textContent = touchInput.matches ? 'Browse files on your device' : 'or click to browse files';
}
touchInput.addEventListener('change', uploadPrompt);
uploadPrompt();
for (const event of ['dragenter', 'dragover']) $('#dropzone').addEventListener(event, e => { e.preventDefault(); $('#dropzone').classList.add('dragging'); });
for (const event of ['dragleave', 'drop']) $('#dropzone').addEventListener(event, e => { e.preventDefault(); $('#dropzone').classList.remove('dragging'); });
$('#dropzone').addEventListener('drop', event => setFile(event.dataTransfer.files[0]));
function updateHardwareFields() {
  const {device, cpu, chipset, cpu_cores: cores} = form.elements;
  const desktop = device.value === 'desktop';
  for (const option of cpu.querySelectorAll('[data-device]')) {
    option.disabled = option.hidden = option.dataset.device !== device.value;
  }
  cpu.disabled = device.value === 'other';
  if (cpu.disabled || cpu.selectedOptions[0]?.disabled) cpu.value = 'other';
  $('#chipset-field').hidden = chipset.disabled = !desktop;
  $('#core-count-field').hidden = cores.disabled = !desktop || cpu.value !== 'zen';
}
function profileChanged() {
  profileEdited = true;
  updateHardwareFields();
  if (exampleSelected) $('#example-note').textContent = 'The synthetic Coffee Lake example uses your selections on the next run. Never boot the example.';
  stale();
}
form.addEventListener('change', profileChanged);
form.addEventListener('input', profileChanged);
updateHardwareFields();

$('#demo').addEventListener('click', async () => {
  if (running) return;
  const requestedRevision = revision;
  $('#demo').disabled = true;
  try {
    const response = await fetch('/api/demo');
    if (!response.ok) throw new Error('The example could not load. Please try again.');
    const blob = await response.blob();
    if (revision !== requestedRevision || running) return;
    setFile(new File([blob], 'example-coffee-lake.plist', {type: 'application/x-plist'}), true);
    $('#file').value = '';
    $('#example-note').textContent = profileEdited
      ? 'Synthetic Coffee Lake/Z390 config, checked against your unchanged hardware selections. This is not your EFI; never boot this example.'
      : 'Synthetic Coffee Lake/Z390 example with a sample profile for this run only. Your hardware selections are unchanged. Never boot the example.';
    await runCheck();
  } catch (e) { error(e.message); revealError(); } finally { $('#demo').disabled = false; }
});

form.addEventListener('submit', event => { event.preventDefault(); runCheck(); });
async function runCheck() {
  if (running) return;
  if (!selectedFile) { error('Add a config.plist, or try the example first.'); revealError(); return; }
  running = true; error('');
  const currentRevision = revision;
  const data = new FormData(form);
  if (exampleSelected && !profileEdited) {
    for (const [field, value] of Object.entries(exampleProfile)) data.set(field, value);
  }
  data.append('config', selectedFile);
  $('#run').disabled = true;
  $('#run').textContent = 'Checking configuration…';
  $('#result-status').textContent = 'RUNNING CHECKS…';
  $('#filter-status').textContent = '';
  try {
    const response = await fetch('/api/check', {method:'POST',body:data});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The check failed. Please try again.');
    if (revision !== currentRevision) { $('#result-status').textContent = 'INPUT CHANGED · RUN AGAIN'; return; }
    currentReport = result; reportRevision = currentRevision; severity = 'all'; renderReport();
    $('#results').focus({preventScroll:true});
    $('#results').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block:'start'});
  } catch (e) {
    if (revision !== currentRevision) { $('#result-status').textContent = 'INPUT CHANGED · RUN AGAIN'; return; }
    error(e.message || 'Could not reach the checker. Please try again.');
    $('#result-status').textContent = 'CHECK NOT COMPLETED';
    revealError();
  }
  finally { running = false; $('#run').disabled = !selectedFile; $('#run').innerHTML = 'Check configuration <span>↗</span>'; }
}

function filterFindings(filter) {
  severity = filter;
  let count = 0;
  for (const finding of $('#report').querySelectorAll('[data-severity]')) {
    finding.hidden = severity !== 'all' && finding.dataset.severity !== severity;
    if (!finding.hidden) count += 1;
  }
  $('#no-findings').hidden = count !== 0;
  for (const button of $('#report').querySelectorAll('[data-filter]')) {
    button.setAttribute('aria-pressed', String(button.dataset.filter === severity));
  }
  $('#filter-status').textContent = `${severityLabel[severity]} selected, ${count} ${count === 1 ? 'finding' : 'findings'}.${reportRevision !== revision ? ' Previous report: inputs changed; run again.' : ''}`;
}

function renderReport() {
  const r = currentReport;
  const outdated = reportRevision !== revision;
  $('#empty-state').hidden = true; $('#report').hidden = false; $('#report').classList.toggle('stale', outdated);
  $('#result-status').textContent = outdated ? 'INPUT CHANGED · RUN AGAIN' : r.status === 'incomplete' ? 'INCOMPLETE COVERAGE' : 'CHECK FINISHED';
  const failed = r.checked - r.passed;
  const heading = r.status === 'incomplete' ? 'Not fully checked.' : r.counts.error ? 'Errors need attention.' : r.counts.warning ? 'Warnings to review.' : r.counts.info ? 'Notes only.' : 'No errors or warnings found.';
  const board = r.profile.board || (r.profile.device === 'desktop' ? familyLabel('chipset', r.profile.chipset) : '');
  const nativeLog = `<pre class="native-log" tabindex="0" aria-label="Official ocvalidate output">${escape(r.native.output)}</pre>`;
  const nativeFailed = r.native.status === 'failed';
  $('#report').innerHTML = `<div class="report-heading"><h3>${heading}</h3><p class="hardware-coverage">${escape(r.hardware_coverage)}</p><p>${r.checks.length} source-linked rules: ${r.passed} passed · ${failed} flagged · ${r.skipped} skipped. ${nativeFailed ? 'The native validator error card counts as one error.' : 'Official validation is shown separately below.'}</p><p class="profile-summary">${escape(familyLabel('device', r.profile.device))} · ${escape(r.profile.cpu_model || familyLabel('cpu', r.profile.cpu))} / ${escape(r.profile.gpu_model || familyLabel('gpu', r.profile.gpu))}<br>${board ? `${escape(board)} · ` : ''}macOS ${escape(r.profile.macos)}${r.profile.cpu === 'zen' && r.profile.cpu_cores ? ` · ${r.profile.cpu_cores} physical cores` : ''}</p></div>
  <div class="count-grid"><div class="error-count"><strong>${r.counts.error}</strong><span>Errors</span></div><div class="warning-count"><strong>${r.counts.warning}</strong><span>Warnings</span></div><div><strong>${r.counts.info}</strong><span>Notes</span></div></div>
  <div class="report-toolbar"><div class="filters" role="group" aria-label="Filter findings">${['all','error','warning','info'].map(s => `<button type="button" data-filter="${s}" aria-pressed="${severity === s}">${s === 'all' ? 'All' : s === 'info' ? 'Notes' : s[0].toUpperCase() + s.slice(1) + 's'}</button>`).join('')}</div><button class="download" type="button" id="download" aria-label="Download diagnostic report as JSON">↓ JSON</button></div>
  <div class="findings">${r.findings.map(f => `<details class="finding ${escape(f.severity)}" data-severity="${escape(f.severity)}"><summary><span class="severity-mark" aria-hidden="true">${f.severity === 'error' ? '×' : f.severity === 'warning' ? '!' : 'i'}</span><span><small>${severityName[f.severity]} · ${escape(f.path)}</small><strong>${escape(f.title)}</strong></span><i aria-hidden="true">+</i></summary><div class="finding-body"><p>${escape(f.detail)}</p>${f.id === 'ocvalidate' ? nativeLog : ''}<div class="fix"><span>WHAT TO DO</span><p>${escape(f.fix)}</p></div><a href="${escape(f.source)}" target="_blank" rel="noreferrer">Read the source ↗</a></div></details>`).join('')}<p id="no-findings" class="no-findings" ${r.findings.length ? 'hidden' : ''}>No findings in this category. Review the coverage gaps below.</p></div>
  ${nativeFailed ? '' : `<details class="native"><summary><span>Official ocvalidate output</span><span class="native-status">${escape(r.native.status)} ↗</span></summary>${nativeLog}</details>`}
  <details class="gaps" open><summary>Not checked — read before you boot <span>↗</span></summary><ul>${r.gaps.map(g => `<li>${escape(g)}</li>`).join('')}</ul><p>No automatic fixes. No boot guarantee. Keep a backup EFI and verify every change.</p></details>`;
  $('#report').querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => filterFindings(button.dataset.filter)));
  $('#download').addEventListener('click', () => {
    if (reportRevision !== revision) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'plistcheck-report.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
}
