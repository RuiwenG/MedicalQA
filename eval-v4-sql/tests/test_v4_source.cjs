// Source-access failures must never become negative Trustworthiness scores.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const folder = path.resolve(__dirname, '../../eval-v4-netlify');
const html = fs.readFileSync(path.join(folder, 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const storage = new Map();
const ctx = vm.createContext({console, URL, URLSearchParams,
  window: {addEventListener() {}},
  document: {getElementById() { return {}; }, addEventListener() {}},
  location: new URL('http://127.0.0.1/'),
  localStorage: {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v)},
  input: JSON.parse(fs.readFileSync(path.join(folder, 'qa_data.json'), 'utf8')),
  fetch() { throw new Error('Unexpected network request'); },
});
const run = code => vm.runInContext(code, ctx);
const plain = code => JSON.parse(JSON.stringify(run(code)));
run(fs.readFileSync(path.join(folder, 'config.js'), 'utf8'));
run(script.slice(0, script.indexOf('fetch("qa_data.json")')));
run(`DATA = input.pairs.filter(p => !isExcluded(p.approach)); VIDEOS = input.videos;
  flush = () => {}; S = makeSession('Source access test');
  S.scores[S.uids[0]] = Object.fromEntries(METRICS.map(m => [m.key, {
    binary: clearChoice(m), ...(m.attribute ? {attribute: m.attribute[0]} : {})
  }]));
  S.recommendations[S.uids[0]] = 'Yes';
  S.scores[S.uids[0]].qa_alignment.attribute = 'Poor';
  S.scores[S.uids[0]].qa_alignment.errors = ['Contradiction'];
  S.scores[S.uids[0]].qa_alignment.sourceUnverifiable = true;`);
assert.equal(run('isRated(S.uids[0])'), true, 'source failure permits completion of the other ratings');
assert.equal(run('completeMetric({sourceUnverifiable:true}, METRICS.find(m => m.key === "qa_accessibility"))'), false,
  'flag cannot bypass an unrelated criterion');
run('enqueue(S.uids[0]);');
const row = plain('normalizeSupabaseRow(S.queue[0])');
assert.equal(row.source_unverifiable, true);
for (const suffix of ['attribute', 'issue', 'binary']) assert.equal(row['qna_trustworthiness_' + suffix], null);
assert.equal(row.qna_clarity_attribute, 'Very easy to understand');
assert.equal(row.qna_standalone_binary, 'Yes');
run('var capturedCells = []; var originalCsvCell = csvCell; csvCell = value => { capturedCells.push(value); return originalCsvCell(value); };');
const csv = run('buildCsv()').split('\r\n');
run('csvCell = originalCsvCell;');
const header = csv[0].split(',');
assert.ok(header.includes('Source Unverifiable'));
const firstCells = plain('capturedCells').slice(0, header.length);
for (const column of ['Q&A Trustworthiness Attribute', 'Q&A Trustworthiness Error Type', 'Q&A Trustworthiness Yes/No'])
  assert.equal(firstCells[header.indexOf(column)], '');
assert.equal(firstCells[header.indexOf('Source Unverifiable')], 'Yes');
assert.equal(firstCells[header.indexOf('Q&A Clarity Attribute')], 'Very easy to understand');
const exported = plain('scoresForExport()');
const uid = run('S.uids[0]');
assert.deepEqual(exported[uid].qa_alignment, {sourceUnverifiable:true});
assert.equal(run('S.scores[S.uids[0]].qa_alignment.attribute'), 'Poor', 'local draft is preserved for an explicit uncheck');
assert.deepEqual(plain('restore(S.storeKey).scores[S.uids[0]].qa_alignment'), plain('S.scores[S.uids[0]].qa_alignment'));

// Rechecking the source restores normal scoring, with the same session and QA.
run('S.scores[S.uids[0]].qa_alignment.sourceUnverifiable = false; enqueue(S.uids[0]);');
const reassessed = plain('normalizeSupabaseRow(S.queue[0])');
assert.equal(reassessed.source_unverifiable, false);
assert.equal(reassessed.qna_trustworthiness_attribute, 'Poor');
assert.equal(reassessed.qna_trustworthiness_issue, 'Contradiction');
assert.equal(run('S.queue.length'), 1, 'pending re-rating replaces only the same QA');
run('delete S.queue[0].source_unverifiable;');
assert.equal(run('normalizeSupabaseRow(S.queue[0]).source_unverifiable'), null, 'legacy queue has unknown source status');
console.log('PASS: source-access missingness, isolated criterion bypass, null database scores, JSON exports, local resume, reassessment and legacy retries.');
