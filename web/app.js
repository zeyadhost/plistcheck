import { checkLocally } from './engine.mjs';

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
const disclosureIcon = '<svg class="chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const exampleProfile = {device:'desktop',cpu:'coffee-lake',cpu_model:'Intel Core i7-9700K',gpu:'amd-polaris-vega',gpu_model:'Radeon RX 580',chipset:'z390',board:'Gigabyte Z390 AORUS PRO',macos:'14',cfg_lock:'enabled',vt_d:'unknown',rebar:'disabled'};

function error(message) { $('#form-error').textContent = message; $('#form-error').hidden = !message; $('#file').setAttribute('aria-invalid', message ? 'true' : 'false'); }
function revealError() { $('#file').focus({preventScroll:true}); $('#form-error').scrollIntoView({block:'nearest', behavior:'instant'}); }
function stale() {
  revision += 1;
  if (currentReport) {
    $('#result-status').textContent = 'Inputs changed · Check again';
    $('#results').dataset.state = 'stale';
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
    $('#file-title').textContent = 'Choose another file';
    $('#file-subtitle').textContent = 'XML or binary plist, up to 2 MB';
    $('#dropzone').classList.remove('has-file');
    $('#example-note').hidden = true;
    stale();
    $('#result-status').textContent = 'File rejected';
    $('#run').disabled = true;
    error(file.size === 0 ? 'This file is empty. Choose a non-empty OpenCore config.plist.' : 'Choose a plist smaller than 2 MB. The previous file is no longer selected.');
    return;
  }
  selectedFile = file;
  exampleSelected = example;
  $('#file-title').textContent = file.name;
  $('#file-subtitle').textContent = `${(file.size / 1024).toFixed(1)} KB · Select to replace`;
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
  if (exampleSelected) $('#example-note').textContent = 'Sample config uses your selections on the next run. Do not boot this file.';
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
    const response = await fetch(new URL('./runtime/example.plist', import.meta.url));
    if (!response.ok) throw new Error('The example could not load. Please try again.');
    const blob = await response.blob();
    if (revision !== requestedRevision || running) return;
    setFile(new File([blob], 'example-coffee-lake.plist', {type: 'application/x-plist'}), true);
    $('#file').value = '';
    $('#example-note').textContent = profileEdited
      ? 'Sample Coffee Lake/Z390 config checked against your selections. Do not boot this file.'
      : 'Sample Coffee Lake/Z390 config and profile. Your selections are unchanged. Do not boot this file.';
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
  $('#run').disabled = true;
  $('#run').setAttribute('aria-busy', 'true');
  $('#run').textContent = 'Checking configuration…';
  $('#result-status').textContent = 'Checking configuration…';
  $('#results').dataset.state = 'running';
  $('#filter-status').textContent = '';
  try {
    const result = await checkLocally(selectedFile, Object.fromEntries(data), message => {
      if (revision === currentRevision) $('#result-status').textContent = message;
    });
    if (revision !== currentRevision) { $('#result-status').textContent = 'Inputs changed · Check again'; $('#results').dataset.state = 'stale'; return; }
    currentReport = result; reportRevision = currentRevision; severity = 'all'; renderReport();
    $('#results').focus({preventScroll:true});
    $('#results').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block:'start'});
  } catch (e) {
    if (revision !== currentRevision) { $('#result-status').textContent = 'Inputs changed · Check again'; $('#results').dataset.state = 'stale'; return; }
    error(e.message || 'The local checker could not finish. Please try again.');
    $('#result-status').textContent = 'Check not completed';
    $('#results').dataset.state = 'error';
    revealError();
  }
  finally { running = false; $('#run').disabled = !selectedFile; $('#run').removeAttribute('aria-busy'); $('#run').textContent = 'Check configuration'; }
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
  $('#result-status').textContent = outdated ? 'Inputs changed · Check again' : r.status === 'incomplete' ? 'Coverage incomplete' : 'Check finished';
  const outcome = r.status === 'incomplete' ? 'warning' : r.counts.error ? 'error' : r.counts.warning ? 'warning' : 'complete';
  $('#results').dataset.state = outdated ? 'stale' : outcome;
  const failed = r.checked - r.passed;
  const heading = r.status === 'incomplete' ? 'Checks incomplete' : r.counts.error ? 'Errors found' : r.counts.warning ? 'Warnings found' : r.counts.info ? 'Notes found' : 'No errors or warnings';
  const board = r.profile.board || (r.profile.device === 'desktop' ? familyLabel('chipset', r.profile.chipset) : '');
  const nativeLog = `<pre class="native-log" tabindex="0" aria-label="Official ocvalidate output">${escape(r.native.output)}</pre>`;
  const nativeFailed = r.native.status === 'failed';
  $('#report').innerHTML = `<div class="report-heading"><span class="report-outcome ${outcome}" aria-hidden="true">${outcome === 'error' ? '×' : outcome === 'warning' ? '!' : '✓'}</span><h3>${heading}</h3><p class="hardware-coverage">${escape(r.hardware_coverage)}</p><details class="report-context"><summary>Coverage &amp; profile ${disclosureIcon}</summary><p>${r.checks.length} rules: ${r.passed} passed · ${failed} flagged · ${r.skipped} skipped. ${nativeFailed ? 'Native validation adds one error.' : ''}</p><p class="profile-summary">${escape(familyLabel('device', r.profile.device))} · ${escape(r.profile.cpu_model || familyLabel('cpu', r.profile.cpu))} / ${escape(r.profile.gpu_model || familyLabel('gpu', r.profile.gpu))}<br>${board ? `${escape(board)} · ` : ''}macOS ${escape(r.profile.macos)}${r.profile.cpu === 'zen' && r.profile.cpu_cores ? ` · ${r.profile.cpu_cores} physical cores` : ''}</p></details></div>
  <div class="count-grid"><div class="error-count"><strong>${r.counts.error}</strong><span>Errors</span></div><div class="warning-count"><strong>${r.counts.warning}</strong><span>Warnings</span></div><div><strong>${r.counts.info}</strong><span>Notes</span></div></div>
  <div class="report-toolbar"><div class="filters" role="group" aria-label="Filter findings">${['all','error','warning','info'].map(s => `<button type="button" data-filter="${s}" aria-pressed="${severity === s}">${s === 'all' ? 'All' : s === 'info' ? 'Notes' : s[0].toUpperCase() + s.slice(1) + 's'}</button>`).join('')}</div><button class="download" type="button" id="download" aria-label="Download diagnostic report as JSON">↓ JSON</button></div>
  <div class="findings">${r.findings.map(f => `<details class="finding ${escape(f.severity)}" data-severity="${escape(f.severity)}"><summary><span class="severity-mark" aria-hidden="true">${f.severity === 'error' ? '×' : f.severity === 'warning' ? '!' : 'i'}</span><span><small>${severityName[f.severity]} · ${escape(f.path)}</small><strong>${escape(f.title)}</strong></span>${disclosureIcon}</summary><div class="finding-body"><p>${escape(f.detail)}</p>${f.id === 'ocvalidate' ? nativeLog : ''}<div class="fix"><span>Suggested fix</span><p>${escape(f.fix)}</p></div><a href="${escape(f.source)}" target="_blank" rel="noreferrer">Source ↗</a></div></details>`).join('')}<p id="no-findings" class="no-findings" ${r.findings.length ? 'hidden' : ''}>No findings in this category.</p></div>
  ${nativeFailed ? '' : `<details class="native"><summary><span>ocvalidate output</span><span class="native-status">${escape(r.native.status)} ${disclosureIcon}</span></summary>${nativeLog}</details>`}
  <details class="gaps"><summary>Not checked ${disclosureIcon}</summary><ul>${r.gaps.map(g => `<li>${escape(g)}</li>`).join('')}</ul></details>`;
  $('#report').querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => filterFindings(button.dataset.filter)));
  $('#download').addEventListener('click', () => {
    if (reportRevision !== revision) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = 'plistcheck-report.json'; a.click();
    setTimeout(() => URL.revokeObjectURL(url),1000);
  });
}
