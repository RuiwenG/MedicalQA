// Run with: node eval/test_fixed_assignment_ui.cjs
// Exercises the real welcome/session code offline; no ratings are submitted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const snapshots = [
  {folder: 'eval/web', count: 226, key: 'dqa:assignment-test:vvvcyl',
    sha: '9830310e58277c1def1e3932d6c293b464ef45b3a5ded8661c3d78f912003765'},
  {folder: 'eval-v4-netlify', count: 40, key: 'dqa:v4:assignment-test:1uklxn',
    sha: '5bba15b6b7ea59233b7b3bca128815a1634dd2f56de45661c0ee786ec89573d3'},
];

function element() {
  return {value: '', textContent: '', innerHTML: '', handlers: {},
    addEventListener(event, fn) { this.handlers[event] = fn; },
    click() { this.handlers.click(); }, focus() { this.focused = true; }};
}

for (const expected of snapshots) {
  const folder = path.join(root, expected.folder);
  const html = fs.readFileSync(path.join(folder, 'index.html'), 'utf8');
  const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
  new vm.Script(script);
  const storage = new Map();
  const elements = new Map();
  let welcomeHtml = '';
  const app = {};
  Object.defineProperty(app, 'innerHTML', {
    get() { return welcomeHtml; },
    set(value) {
      welcomeHtml = value;
      elements.clear();
      for (const match of value.matchAll(/\bid="([^"]+)"/g)) elements.set(match[1], element());
      const name = elements.get('name');
      if (name) name.value = value.match(/id="name"[^>]*value="([^"]*)"/)[1].replace(/&quot;/g, '"');
    },
  });
  const ctx = vm.createContext({
    window: {addEventListener() {}},
    document: {getElementById: id => id === 'app' ? app : elements.get(id) || null,
      addEventListener() {}},
    localStorage: {getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value)},
    location: new URL('https://evaluation.example/'), URL, URLSearchParams, console,
    fetch() { throw new Error('Tests must not make network requests'); },
    input: JSON.parse(fs.readFileSync(path.join(folder, 'qa_data.json'), 'utf8')),
  });
  const run = code => vm.runInContext(code, ctx);
  const plain = code => JSON.parse(JSON.stringify(run(code)));
  vm.runInContext(fs.readFileSync(path.join(folder, 'config.js'), 'utf8'), ctx);
  run(script.slice(0, script.indexOf('fetch("qa_data.json")')));
  run('DATA = input.pairs.filter(p => !isExcluded(p.approach)); VIDEOS = input.videos;');
  // Only stub the destination screen and network sync, not welcome or session code.
  run('flush = () => {}; renderRate = () => {};');
  const session = plain('makeSession("Assignment test")');
  assert.equal(session.storeKey, expected.key, 'existing default session key stays compatible');
  assert.equal(session.uids.length, expected.count);
  assert.equal(createHash('sha256').update(JSON.stringify(session.uids)).digest('hex'), expected.sha,
    'exact pre-change questions and order stay unchanged');
  assert.deepEqual(plain('makeSession("Another annotator").uids'), session.uids);
  assert.deepEqual(plain(`makeSession('Custom prefs', {mode:'custom', datasets:[],
    approaches:[], videos:[], metrics:[], blind:false, maxPairs:1, sample:1}).uids`), session.uids);
  run('DATA.reverse()');
  assert.deepEqual(plain('buildPairs().map(p => p.uid)'), session.uids, 'order independent of input order');

  const lastKey = run('LAST_OPTS_KEY');
  const oldCustomKey = 'dqa:retained-custom-session';
  storage.set(oldCustomKey, JSON.stringify({scores: {old: 'keep me'}}));
  const customBackup = storage.get(oldCustomKey);
  storage.set(lastKey, JSON.stringify({name: 'Returning custom annotator', mode: 'custom',
    datasets: [], approaches: [], videos: [], metrics: [], sample: 1, maxPairs: 1, blind: false}));
  run('renderWelcome()');
  assert.equal(elements.get('name').value, 'Returning custom annotator');
  assert.match(welcomeHtml, new RegExp(`<b>${expected.count} Q&As</b>`));
  assert.match(welcomeHtml, /rates all 5 criteria/);
  assert.doesNotMatch(welcomeHtml, /Advanced options|Choose videos|type="(?:checkbox|radio|number)"/);
  for (const id of ['adv', 'custom-opts', 'video-picker', 'sample', 'maxpairs', 'blind']) {
    assert.equal(elements.has(id), false, `removed control: ${id}`);
  }
  elements.get('name').value = '  ';
  elements.get('start').click();
  assert.match(elements.get('start-err').textContent, /enter your name/);
  assert.equal(run('S'), null);
  elements.get('name').value = 'Assignment test';
  elements.get('name').handlers.input();
  elements.get('name').handlers.keydown({key: 'Enter'});
  assert.deepEqual(plain('S.uids'), session.uids);
  assert.equal(run('S.opts.blind'), true);
  assert.equal(run('S.metrics.length'), 5);
  assert.equal(run('S.opts.mode'), 'auto');
  assert.equal(storage.get(oldCustomKey), customBackup, 'old custom session is not deleted');

  // Simulate a default session saved by the old UI, including an unblinded preference.
  run(`S.opts.blind = false;
    S.scores[S.uids[0]] = Object.fromEntries(METRICS.map(m => [m.key, {
      binary: clearChoice(m), ...(m.attribute ? {attribute: m.attribute[0]} : {})
    }]));
    S.recommendations[S.uids[0]] = 'Yes';
    S.comments[S.uids[0]] = 'Existing annotator note';
    S.seconds[S.uids[0]] = 42;
    S.queue = [{qa_uid: S.uids[0], session_id: S.sessionId}];
    save();`);
  const saved = plain('S');
  run('S = null; renderWelcome()');
  assert.match(elements.get('resume-note').innerHTML, new RegExp(`1 of ${expected.count} pairs already rated`));
  elements.get('start').click();
  assert.equal(run('S.index'), 1, 'resume at first incomplete Q&A');
  assert.equal(run('S.opts.blind'), true, 'old unblinded preference cannot leak into rating');
  for (const key of ['scores', 'recommendations', 'comments', 'seconds', 'queue', 'startedAt', 'sessionId']) {
    assert.deepEqual(plain(`S.${key}`), saved[key], `preserved ${key}`);
  }

  // A mismatched same-key session must never be silently trimmed or overwritten.
  for (const mismatch of ['uids', 'metrics']) {
    const incompatible = JSON.parse(JSON.stringify(saved));
    incompatible[mismatch] = incompatible[mismatch].slice(1);
    storage.set(session.storeKey, JSON.stringify(incompatible));
    const untouched = storage.get(session.storeKey);
    run('S = null; renderWelcome()');
    assert.match(elements.get('resume-note').textContent, /different assignment/);
    elements.get('start').click();
    assert.match(elements.get('start-err').textContent, /contact the research team/);
    assert.equal(run('S'), null);
    assert.equal(storage.get(session.storeKey), untouched);
  }
  assert.equal(storage.get(oldCustomKey), customBackup);
  console.log(`PASS: ${expected.folder} — fixed ${expected.count} pairs, exact original order/keys, all criteria, hidden models, welcome/start/resume, legacy preferences ignored, saved progress protected.`);
}
