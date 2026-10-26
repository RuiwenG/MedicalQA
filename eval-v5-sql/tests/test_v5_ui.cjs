// Offline checks for the v5 landing page, version claim, seeded order, resume
// and rating payloads. No network: the Supabase calls are stubbed.
// Run from the repository root: node eval-v5-sql/tests/test_v5_ui.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const folder = path.join(root, 'eval-v5-netlify');
const html = fs.readFileSync(path.join(folder, 'index.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
const elements = new Map(), storage = new Map();
let welcomeHtml = '';
const element = () => ({value: '', textContent: '', innerHTML: '', handlers: {}, hidden: false, disabled: false,
  addEventListener(k, fn) { this.handlers[k] = fn; },
  click() { this.handlers.click(); }, focus() { this.focused = true; }});
const app = {};
Object.defineProperty(app, 'innerHTML', {get() { return welcomeHtml; }, set(value) {
  welcomeHtml = value; elements.clear();
  for (const match of value.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], element());
  const name = elements.get('name');
  if (name) name.value = value.match(/id="name"[^>]*value="([^"]*)"/)[1].replace(/&quot;/g, '"');
}});

// Stubbed Supabase: the claim RPC hands out versions 3, 5, ... in turn.
const claims = [], posts = [];
let nextVersion = [3, 5, 2];
const fetchStub = async (url, init) => {
  const body = JSON.parse(init.body);
  if (url.endsWith('/rest/v1/rpc/claim_version_v5')) {
    claims.push(body);
    const v = nextVersion.shift();
    return {ok: true, json: async () => [{assignment_id: `0000000${claims.length}-aaaa-bbbb-cccc-dddddddddddd`,
      eval_version: v, expires_at: '2026-10-24T00:00:00Z'}]};
  }
  if (url.endsWith('/rest/v1/ratings_v5')) { posts.push(...body); return {ok: true, text: async () => ''}; }
  throw new Error('unexpected request ' + url);
};
const ctx = vm.createContext({window: {addEventListener() {}}, console: {...console, warn() {}, info() {}}, URL, URLSearchParams,
  document: {getElementById: id => id === 'app' ? app : elements.get(id) || null, addEventListener() {}},
  localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
  location: new URL('https://evaluation.example/'),
  input: JSON.parse(fs.readFileSync(path.join(folder, 'qa_data.json'), 'utf8')),
  versionsInput: JSON.parse(fs.readFileSync(path.join(folder, 'versions.json'), 'utf8')),
  fetch: fetchStub, setTimeout,
});
const run = code => vm.runInContext(code, ctx);
const plain = code => JSON.parse(JSON.stringify(run(code)));
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
run(fs.readFileSync(path.join(folder, 'config.js'), 'utf8'));
run('window.EVAL_CONFIG.SUPABASE_URL = "https://stub.supabase.co/rest/v1/"; window.EVAL_CONFIG.SUPABASE_ANON_KEY = "public-anon";');
run(script.slice(0, script.indexOf('const loadJson =')));
run(`DATA = input.pairs.filter(p => !isExcluded(p.approach)); VIDEOS = input.videos;
  VERSIONS = versionsInput.versions; PROVISIONAL = versionsInput.provisional === true; renderRate = () => {};`);
const versions = ctx.versionsInput.versions;
assert.equal(run('DATA.length'), 100, 'all 100 Qwen3-14B pairs pass the approach allowlist');
assert.equal(run('backendOn'), true);

// A v4 session already in this browser is never read or touched.
storage.set('dqa:v4:jane-doe:1uklxn', '{"version":7,"uids":["x"]}');
storage.set('dqa:lastopts:v4', '{"name":"Old v4 name"}');

(async () => {
  run('renderWelcome()');
  assert.match(welcomeHtml, /Provisional Q&amp;A selection|Provisional Q&A selection/, 'the placeholder selection is flagged on screen');
  assert.match(welcomeHtml, /You will receive <b>20 Q&As<\/b>/);
  assert.match(welcomeHtml, />Continue</);
  assert.equal(elements.get('name').value, '', 'the v4 name is not prefilled');
  assert.ok(welcomeHtml.indexOf('id="name"') < welcomeHtml.indexOf('id="role-healthcare"'));
  assert.equal((welcomeHtml.match(/type="checkbox" id="role-/g) || []).length, 4, 'one checkbox per role');
  assert.equal((welcomeHtml.match(/id="role-detail-[a-z]+"/g) || []).length, 4, 'one optional description box per role');
  for (const label of ['Healthcare professional', 'Medical student', 'Family/friend dementia caregiver', 'Others']) assert.match(welcomeHtml, new RegExp(label));
  assert.match(welcomeHtml, /Select all that apply/);
  elements.get('start').click();
  assert.match(elements.get('start-err').textContent, /enter your name/);
  elements.get('name').value = 'Jane Doe'; elements.get('name').handlers.input();
  elements.get('start').click(); assert.match(elements.get('start-err').textContent, /choose at least one role/);
  elements.get('role-family').checked = true; elements.get('role-family').handlers.change();
  elements.get('role-healthcare').checked = true; elements.get('role-healthcare').handlers.change();
  assert.equal(elements.get('role-detail-field-family').hidden, false, 'a ticked role shows its description box');
  assert.equal(elements.get('role-detail-field-other').hidden, true, 'an unticked role hides it');
  elements.get('role-detail-family').value = ' Caring for my mother ';
  elements.get('role-detail-other').value = 'typed, then not ticked';
  elements.get('start').click(); assert.match(elements.get('start-err').textContent, /dementia-care experience/);
  assert.equal(claims.length, 0, 'no claim until the form is complete');
  elements.get('dementia-experience').value = 'one_to_three_years';
  elements.get('start').click();
  elements.get('start').click();  // a double click must not claim twice
  await settle(); await settle();
  assert.equal(claims.length, 1);
  assert.deepEqual(claims[0], {p_name: 'Jane Doe', p_groups: ['healthcare', 'family'],
    p_role_details: {family: 'Caring for my mother'}, p_experience: 'one_to_three_years'},
    'both ticked roles in the listed order; descriptions only for ticked roles, trimmed, blanks left out');

  // The claimed version's 20 Q&As, in an order seeded by the assignment id.
  const first = plain('S');
  assert.equal(first.evalVersion, 3);
  assert.equal(first.assignmentId, claims.length && '00000001-aaaa-bbbb-cccc-dddddddddddd');
  assert.equal(first.uids.length, 20);
  assert.deepEqual(first.uids.slice().sort(), versions['3'].slice().sort());
  assert.notDeepEqual(first.uids, versions['3'], 'the order is shuffled');
  assert.deepEqual(plain(`versionUids(3, "${first.assignmentId}")`), first.uids, 'the shuffle is deterministic');
  assert.notDeepEqual(plain('versionUids(3, "another-assignment")'), first.uids, 'another assignment gets another order');
  assert.equal(first.storeKey, 'dqa:v5:a:' + first.assignmentId);
  assert.equal(run('S.index'), 0);

  // Rate the first Q&A: the row carries the assignment, version and profile.
  run(`S.scores[S.uids[0]] = Object.fromEntries(METRICS.map(m => [m.key, {
    binary: clearChoice(m), ...(m.attribute ? {attribute: m.attribute[0]} : {})
  }])); S.recommendations[S.uids[0]] = 'Yes'; S.seconds[S.uids[0]] = 41; enqueue(S.uids[0]); S.index = 1; save();`);
  await settle(); await settle();
  assert.equal(posts.length, 1);
  assert.equal(posts[0].assignment_id, first.assignmentId);
  assert.equal(posts[0].eval_version, 3);
  assert.equal(posts[0].session_id, first.assignmentId);
  assert.equal(posts[0].study_version, 'v5');
  assert.equal(posts[0].batch, 'version-3');
  assert.equal(posts[0].qa_uid, first.uids[0]);
  assert.deepEqual(posts[0].annotator_groups, ['healthcare', 'family']);
  assert.deepEqual(posts[0].annotator_role_details, {family: 'Caring for my mother'});
  assert.equal(posts[0].dementia_care_experience, 'one_to_three_years');
  assert.equal(posts[0].blind, true);
  assert.equal(run('S.queue.length'), 0);
  const csv = run('buildCsv()');
  assert.match(csv.split('\r\n')[0], /Session ID,Version,Seconds/);
  assert.match(csv.split('\r\n')[1], new RegExp(first.assignmentId + ',3,41,'));
  assert.match(csv.split('\r\n')[0], /Annotator Roles/);
  assert.match(csv.split('\r\n')[0], /Role Details/);
  assert.match(csv.split('\r\n')[1], /,healthcare; family,Family\/friend dementia caregiver: Caring for my mother,/);
  run('download = (name, text) => { globalThis.exported = text; }; exportJson();');
  assert.equal(JSON.parse(ctx.exported).assignment_id, first.assignmentId);
  assert.equal(JSON.parse(ctx.exported).eval_version, 3);

  // Reload: the same name resumes the same assignment without a new claim.
  run('S = null; renderWelcome()');
  assert.equal(elements.get('name').value, 'Jane Doe');
  assert.match(elements.get('resume-note').innerHTML, /Welcome back — 1 of 20/);
  assert.equal(elements.get('role-healthcare').checked, true, 'the saved roles are ticked again');
  assert.equal(elements.get('role-family').checked, true);
  assert.equal(elements.get('role-student').checked, false);
  assert.equal(elements.get('role-detail-family').value, 'Caring for my mother', 'with their descriptions');
  elements.get('start').click();
  await settle();
  assert.equal(claims.length, 1, 'resuming does not claim again');
  assert.equal(run('S.assignmentId'), first.assignmentId);
  assert.deepEqual(plain('S.uids'), first.uids);
  assert.equal(run('S.index'), 1, 'continues at the first unrated Q&A');

  // A different person in the same browser gets their own claim and order.
  run('S = null; renderWelcome()');
  elements.get('name').value = 'Sam Lee'; elements.get('name').handlers.input();
  assert.match(elements.get('resume-note').innerHTML, /New session: 20 pairs/);
  elements.get('role-other').checked = true; elements.get('role-other').handlers.change();
  assert.equal(elements.get('role-detail-field-other').hidden, false, 'ticking Others shows its box');
  elements.get('dementia-experience').value = 'none';
  elements.get('start').click();
  await settle(); await settle();
  assert.equal(claims.length, 2);
  assert.deepEqual(claims[1], {p_name: 'Sam Lee', p_groups: ['other'], p_role_details: {}, p_experience: 'none'},
    'Others needs no description');
  assert.equal(run('S.evalVersion'), 5);
  assert.deepEqual(plain('S.uids').slice().sort(), versions['5'].slice().sort());

  // A failed claim leaves the page usable and asks to retry.
  nextVersion = [];
  run('S = null; renderWelcome()');
  elements.get('name').value = 'Pat Kim'; elements.get('name').handlers.input();
  elements.get('role-student').checked = true; elements.get('role-student').handlers.change();
  elements.get('dementia-experience').value = 'none';
  elements.get('start').click();
  await settle(); await settle();
  assert.match(elements.get('start-err').textContent, /Could not reach the study server/);
  assert.equal(elements.get('start').disabled, false);
  assert.equal(run('S'), null);

  // Storage stays inside the v5 namespace, and the v4 entries are untouched.
  const keys = [...storage.keys()];
  assert.ok(keys.filter(k => !k.startsWith('dqa:v4:') && k !== 'dqa:lastopts:v4').every(k => k.startsWith('dqa:v5:')), keys.join(' '));
  assert.equal(storage.get('dqa:v4:jane-doe:1uklxn'), '{"version":7,"uids":["x"]}');
  assert.equal(storage.get('dqa:lastopts:v4'), '{"name":"Old v4 name"}');
  assert.equal(JSON.parse(storage.get('dqa:v5:assignments')).length, 2);

  // Model names stay hidden while rating.
  assert.equal(run('fixedStudyOptions().blind'), true);
  assert.doesNotMatch(html, /ratings_v4|claim_version_v4/);
  // A session saved before multiple roles (one "group") still loads with that role ticked.
  assert.deepEqual(plain('normalizeAnnotatorProfile({group: "care", experience: "none"})'),
    {groups: ['healthcare'], details: {}, experience: 'none'});
  assert.deepEqual(plain('normalizeAnnotatorProfile({group: "other", other: "Volunteer", experience: "none"})'),
    {groups: ['other'], details: {other: 'Volunteer'}, experience: 'none'});
  console.log('PASS: v5 landing page validation, role checkboxes (four roles, several at once, optional description per role, legacy single role), one claim per new annotator (double click safe), claimed version with seeded order, rating payload with assignment/version/profile, CSV/JSON exports, resume without re-claiming, second annotator in the same browser, failed-claim retry, dqa:v5:* storage only, v4 storage untouched.');
})().catch(error => { console.error(error); process.exitCode = 1; });
