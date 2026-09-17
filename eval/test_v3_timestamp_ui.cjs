// Exercise real session/rating/video functions without browser or Supabase writes.
const assert = require('node:assert/strict'), fs = require('node:fs');
const path = require('node:path'), vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..'), folder = 'eval-pilot-netlify-review';
const baseline = 'd4ad467527ce45d0af120a5ebd68345a19d39ad8';
const oldFile = name => execFileSync('git', ['show', `${baseline}:${folder}/${name}`],
  {cwd: root, encoding: 'utf8', maxBuffer: 5 * 1024 * 1024});
const oldHtml = oldFile('index.html'), oldData = JSON.parse(oldFile('qa_data.json'));
const html = fs.readFileSync(path.join(root, folder, 'index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(root, folder, 'qa_data.json'), 'utf8'));
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const storage = new Map();
const localStorage = {getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,v)};
const section = (text,a,b) => text.slice(text.indexOf(a), text.indexOf(b));
function context(text, corpus) {
  const ctx = vm.createContext({DATA: corpus.pairs.filter(p => p.approach === 'SingleAgent'),
    VIDEOS: corpus.videos, S: null, EXCLUDED: new Set(), localStorage, URL, URLSearchParams, console});
  vm.runInContext(section(text, 'const METRICS = [', '// FNV-1a') + '\n' +
    section(text, 'function hashStr(', 'let DATA =') + '\n' +
    section(text, 'function buildPairs(', 'const pairOf =') + '\n' +
    section(text, 'function videoLink(', 'function enqueue(') + '\n' +
    section(text, 'function insertVideoPanel(', 'function renderRate('), ctx);
  return ctx;
}
const before = context(oldHtml, oldData), after = context(html, data);
const metrics = vm.runInContext('METRICS', before);
for (const mode of ['auto', 'custom']) {
  const opts = {mode, datasets: ['Master','Teepa'], approaches: ['SingleAgent'],
    videos: mode === 'custom' ? ['Master|1','Teepa|2'] : null,
    metrics: Array.from(metrics, m => m.key), sample: mode === 'custom' ? 2 : 0, maxPairs: 40};
  before.S = before.makeSession('V3 progress regression', opts);
  const uid = before.S.uids[0];
  before.S.scores[uid] = {};
  for (const metric of metrics) before.S.scores[uid][metric.key] = {
    binary: before.errorTrigger(metric) === 'Yes' ? 'No' : 'Yes',
    ...(metric.attribute ? {attribute: metric.attribute[0]} : {})};
  before.S.recommendations[uid] = 'Yes';
  before.S.comments[uid] = 'Saved before timestamp update';
  before.S.seconds[uid] = 42;
  before.S.queue = [{qa_uid: uid, session_id: before.S.sessionId, source_start_sec: null}];
  before.S.index = 1;
  assert.equal(vm.runInContext('ratedCount()', before), 1);
  before.save();
  const fresh = after.makeSession('V3 progress regression', opts);
  assert.equal(fresh.storeKey, before.S.storeKey);
  assert.equal(fresh.sessionId, before.S.sessionId);
  assert.equal(JSON.stringify(fresh.uids), JSON.stringify(before.S.uids));
  after.S = after.restore(fresh.storeKey);
  assert.equal(JSON.stringify(after.S), JSON.stringify(before.S));
  assert.equal(vm.runInContext('ratedCount()', after), 1);
  assert.equal(vm.runInContext('S.uids.findIndex(uid => !isRated(uid))', after), 1);
}
const active = data.pairs.filter(p => p.approach === 'SingleAgent');
for (const p of active) {
  const video = after.sourceVideo(p), url = new URL(video.embed);
  assert.equal(url.searchParams.get('start'), String(p.t));
  assert.equal(url.searchParams.get('end'), p.ts === 'aligned-low' ? null : String(p.te));
  assert.equal(video.approx, p.ts !== 'model');
  assert.equal(new URL(video.url).searchParams.get('t'), `${p.t}s`);
  const panel = after.insertVideoPanel(['STANDALONE_CARD', 'TRUST_CARD'],
    [{key: 'qa_standalone'}, {key: 'qa_alignment'}], video);
  assert.ok(panel.indexOf('STANDALONE_CARD') < panel.indexOf('source-video'));
  assert.ok(panel.indexOf('source-video') < panel.indexOf('TRUST_CARD'));
  assert.ok(panel.includes(video.timeLabel));
  if (p.ts === 'aligned-low') assert.ok(panel.includes('Low-confidence'));
}
const sample = {...active[0], ts: 'model', t: 451}; delete sample.te;
const open = after.sourceVideo(sample);
assert.equal(new URL(open.embed).searchParams.get('end'), null);
assert.equal(open.approx, false);
assert.equal(open.timeLabel, '7:31 onward');
console.log('PASS: syntax; auto/custom progress and completed-count recovery; pending queue; all 225 video links; native open-ended ranges; standalone-before-video layout.');
