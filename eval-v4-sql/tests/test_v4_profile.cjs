// Offline regression checks for the approved v4 annotator form and rating data.
// Run from the repository root: node eval-v4-sql/tests/test_v4_profile.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '../..');
const folder = path.join(root, 'eval-v4-netlify');
const html = fs.readFileSync(path.join(folder, 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const elements = new Map(), storage = new Map();
let welcomeHtml = '';
const element = () => ({value: '', textContent: '', innerHTML: '', handlers: {}, hidden: false,
  addEventListener(k, fn) { this.handlers[k] = fn; },
  click() { this.handlers.click(); }, focus() { this.focused = true; }});
const app = {};
Object.defineProperty(app, 'innerHTML', {get() { return welcomeHtml; }, set(value) {
  welcomeHtml = value; elements.clear();
  for (const match of value.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], element());
  const name = elements.get('name');
  if (name) name.value = value.match(/id="name"[^>]*value="([^"]*)"/)[1].replace(/&quot;/g, '"');
}});
const ctx = vm.createContext({window: {addEventListener() {}}, console, URL, URLSearchParams,
  document: {getElementById: id => id === 'app' ? app : elements.get(id) || null, addEventListener() {}},
  localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
  location: new URL('https://evaluation.example/'),
  input: JSON.parse(fs.readFileSync(path.join(folder, 'qa_data.json'), 'utf8')),
  fetch() { throw new Error('No network requests are permitted in this test'); },
});
const run = code => vm.runInContext(code, ctx);
const plain = code => JSON.parse(JSON.stringify(run(code)));
run(fs.readFileSync(path.join(folder, 'config.js'), 'utf8'));
run(script.slice(0, script.indexOf('fetch("qa_data.json")')));
run('DATA = input.pairs.filter(p => !isExcluded(p.approach)); VIDEOS = input.videos; flush = () => {}; renderRate = () => {};');
const original = plain('makeSession("Assignment test")');
assert.equal(original.storeKey, 'dqa:v4:assignment-test:1uklxn');
assert.equal(original.uids.length, 40);
assert.equal(createHash('sha256').update(JSON.stringify(original.uids)).digest('hex'),
  '5bba15b6b7ea59233b7b3bca128815a1634dd2f56de45661c0ee786ec89573d3');
assert.deepEqual(plain('makeSession("Another name").uids'), original.uids);
assert.deepEqual(plain('ANNOTATOR_GROUPS.map(g => g.value)'), ['clinical', 'care', 'family', 'student', 'other']);

run('renderWelcome()');
assert.ok(welcomeHtml.indexOf('id="name"') < welcomeHtml.indexOf('id="annotator-group"'));
assert.doesNotMatch(welcomeHtml, /unpaid dementia care to a family member|optgroup|Advanced options/);
elements.get('start').click();
assert.match(elements.get('start-err').textContent, /enter your name/);
elements.get('name').value = 'Assignment test'; elements.get('name').handlers.input();
elements.get('start').click(); assert.match(elements.get('start-err').textContent, /choose your role/);
elements.get('annotator-group').value = 'other'; elements.get('annotator-group').handlers.change();
assert.equal(elements.get('annotator-other-field').hidden, false);
assert.equal(elements.get('annotator-other').required, true);
elements.get('start').click(); assert.match(elements.get('start-err').textContent, /describe your role/);
elements.get('annotator-other').value = '  Research volunteer  ';
elements.get('start').click(); assert.match(elements.get('start-err').textContent, /choose your dementia-care experience/);
elements.get('dementia-experience').value = 'none'; elements.get('start').click();
assert.deepEqual(plain('S.annotatorProfile'), {group: 'other', other: 'Research volunteer', experience: 'none'});
assert.deepEqual(plain('S.uids'), original.uids);
assert.equal(run('S.metrics.length'), 5); assert.equal(run('S.opts.blind'), true);

// A queued rating, its normalized database payload, and both exports retain the profile.
run(`S.scores[S.uids[0]] = Object.fromEntries(METRICS.map(m => [m.key, {
  binary: clearChoice(m), ...(m.attribute ? {attribute: m.attribute[0]} : {})
}])); S.recommendations[S.uids[0]] = 'Yes'; S.comments[S.uids[0]] = 'Keep this note';
S.seconds[S.uids[0]] = 35; enqueue(S.uids[0]);`);
let payload = plain('normalizeSupabaseRow(S.queue[0])');
assert.equal(payload.annotator_group, 'other'); assert.equal(payload.annotator_other, 'Research volunteer');
assert.equal(payload.dementia_care_experience, 'none');
assert.equal(payload.qa_uid, original.uids[0]); assert.equal(payload.evaluator_comment, 'Keep this note');
const csv = run('buildCsv()');
assert.match(csv, /Annotator Group,Annotator Other,Dementia-care Experience/);
assert.ok(csv.split('\r\n')[1].endsWith('other,Research volunteer,none'));
run('download = (name, text) => { globalThis.exported = text; }; exportJson();');
assert.deepEqual(JSON.parse(ctx.exported).annotator_profile, plain('S.annotatorProfile'));

// Switching between known annotators restores only the matching session's profile.
const saved = plain('S');
run('S = null; renderWelcome()');
assert.equal(elements.get('annotator-group').value, 'other');
assert.equal(elements.get('dementia-experience').value, 'none');
assert.equal(elements.get('annotator-other').value, 'Research volunteer');
elements.get('name').value = 'New annotator'; elements.get('name').handlers.input();
assert.equal(elements.get('annotator-group').value, '');
elements.get('annotator-group').value = 'clinical'; elements.get('annotator-group').handlers.change();
elements.get('name').value = 'New annotator continued'; elements.get('name').handlers.input();
assert.equal(elements.get('annotator-group').value, 'clinical', 'typing a new name does not clear choices');
elements.get('name').value = 'Assignment test'; elements.get('name').handlers.input();
assert.equal(elements.get('annotator-group').value, 'other');
elements.get('start').click();
assert.equal(run('S.index'), 1);
for (const key of ['scores', 'comments', 'recommendations', 'seconds', 'startedAt', 'sessionId', 'uids'])
  assert.deepEqual(plain(`S.${key}`), saved[key]);

// Upgrade a saved pre-profile session: same key, scores, cursor, and pending rating.
const legacy = JSON.parse(JSON.stringify(saved)); delete legacy.annotatorProfile;
for (const row of legacy.queue) for (const key of ['annotator_group', 'annotator_other', 'dementia_care_experience']) delete row[key];
storage.set(legacy.storeKey, JSON.stringify(legacy));
run('S = null; renderWelcome()');
assert.equal(elements.get('annotator-group').value, '');
elements.get('annotator-group').value = 'student'; elements.get('annotator-group').handlers.change();
elements.get('dementia-experience').value = 'less_one_year'; elements.get('start').click();
assert.equal(run('S.storeKey'), legacy.storeKey); assert.equal(run('S.index'), 1);
assert.deepEqual(plain('S.scores'), legacy.scores); assert.equal(run('S.queue.length'), legacy.queue.length);
payload = plain('normalizeSupabaseRow(S.queue[0])');
assert.equal(payload.annotator_group, 'student'); assert.equal(payload.annotator_other, null);
assert.equal(payload.dementia_care_experience, 'less_one_year');
assert.equal(elements.get('annotator-other-field').hidden, true);

for (const group of ['clinical', 'care', 'family', 'student']) {
  ctx.profileInput = {group, experience: 'more_three_years', other: 'Hidden stale text'};
  const cols = plain('profileColumns(profileInput)');
  assert.equal(cols.annotator_group, group); assert.equal(cols.annotator_other, null);
}
assert.deepEqual(plain('profileColumns(null)'), {annotator_group: null, annotator_other: null, dementia_care_experience: null});
assert.equal(plain('normalizeSupabaseRow({qa_uid: S.uids[0]})').annotator_group, null);
console.log('PASS: v4 only — five group choices, validation and Others box, exact 40-Q&A assignment/keys, matching profile restore, legacy progress/queue, database payloads, CSV/JSON exports, unknown legacy metadata.');
