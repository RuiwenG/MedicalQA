// No network, browser state, or database writes: exercise the real UI functions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const baseline = 'b18cbdaba4b40fd3425156d419a80e4ee8ab3d08';
const oldHtml = execFileSync('git', ['show', `${baseline}:eval-v1-netlify/index.html`], {cwd: root, encoding: 'utf8'});
const oldData = JSON.parse(execFileSync('git', ['show', `${baseline}:eval-v1-netlify/qa_data.json`], {cwd: root, encoding: 'utf8'}));
const html = fs.readFileSync(path.join(root, 'eval-v1-netlify/index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(root, 'eval-v1-netlify/qa_data.json'), 'utf8'));
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
const storage = new Map();
const localStorage = {getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v)};
const section = (text, a, b) => text.slice(text.indexOf(a), text.indexOf(b));
function context(text, corpus) {
  const ctx = vm.createContext({DATA: corpus.pairs, VIDEOS: corpus.videos, S: null,
    EXCLUDED: new Set(), localStorage, URL, URLSearchParams, console});
  vm.runInContext(section(text, 'function hashStr(', 'let DATA =') + '\n' +
    section(text, 'function buildPairs(', 'const pairOf =') + '\n' +
    section(text, 'function videoLink(', 'function escapeAttr('), ctx);
  return ctx;
}
const before = context(oldHtml, oldData), after = context(html, data);
const opts = {mode: 'auto', datasets: ['Master','Teepa'], approaches: ['SingleAgent-v1'],
  videos: null, metrics: ['qna_standalone','qa_alignment'], sample: 0, maxPairs: 40};
before.S = before.makeSession('Timestamp regression test', opts);
const uid = before.S.uids[0];
before.S.scores[uid] = {qna_standalone: {binary: 'Yes'}};
before.S.comments[uid] = 'Saved before timestamps';
before.S.index = 1;
before.save();
const fresh = after.makeSession('Timestamp regression test', opts);
assert.equal(fresh.storeKey, before.S.storeKey);
assert.equal(fresh.sessionId, before.S.sessionId);
assert.equal(JSON.stringify(fresh.uids), JSON.stringify(before.S.uids));
assert.equal(JSON.stringify(after.restore(fresh.storeKey)), JSON.stringify(before.S));
for (const pair of data.pairs) {
  const video = after.sourceVideo(pair), url = new URL(video.embed);
  assert.equal(url.searchParams.get('start'), String(pair.t));
  assert.equal(url.searchParams.get('end'), pair.ts === 'aligned-low' ? null : String(pair.te));
  assert.equal(video.approx, true);
  assert.match(video.timeLabel, pair.ts === 'aligned-low' ? /^\d+:\d{2} onward$/ : /^\d+:\d{2}–\d+:\d{2}$/);
  assert.equal(new URL(video.url).searchParams.get('t'), `${pair.t}s`);
}
const noClip = after.sourceVideo(oldData.pairs[0]);
assert.equal(noClip.hasClip, false);
assert.equal(new URL(noClip.embed).searchParams.get('start'), '0');
assert.equal(new URL(noClip.embed).searchParams.get('end'), null);
console.log('PASS: JavaScript syntax, unchanged session IDs, saved progress restored, 487 embed/link timestamps, weak-match fallback.');
