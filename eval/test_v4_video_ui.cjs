// Run the real v4 video helpers without submitting ratings or loading YouTube.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const folder = path.join(root, 'eval-v4-netlify');
const html = fs.readFileSync(path.join(folder, 'index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(folder, 'qa_data.json'), 'utf8'));
const location = new URL('https://medicalqa-eval-v4.netlify.app/?annotator=private-name');
const ctx = vm.createContext({VIDEOS: data.videos, URL, URLSearchParams, location,
  METRICS_BEFORE_VIDEO: ['qa_standalone']});
function section(start, end) {
  const a = html.indexOf(start), b = html.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `missing code section: ${start}`);
  return html.slice(a, b);
}
vm.runInContext(section('function videoLink(', 'function completeBinary(') + '\n' +
  section('function insertVideoPanel(', 'function renderRate('), ctx);

const active = data.pairs.filter(p => p.approach === 'SingleAgent');
let native = 0, aligned = 0, weak = 0;
for (const p of active) {
  const video = ctx.sourceVideo(p);
  assert.ok(video, `missing source: ${p.uid}`);
  assert.ok(video.embed, `missing embed: ${p.uid}`);
  const embed = new URL(video.embed);
  assert.match(embed.pathname, /^\/embed\/[A-Za-z0-9_-]{11}$/);
  assert.equal(embed.searchParams.get('controls'), '1');
  assert.equal(embed.searchParams.get('playsinline'), '1');
  assert.equal(embed.searchParams.get('origin'), location.origin);
  assert.ok(!video.embed.includes('private-name'));
  if (Number.isFinite(p.t)) {
    const start = Math.max(0, Math.round(p.t));
    assert.equal(embed.searchParams.get('start'), String(start));
    assert.equal(new URL(video.url).searchParams.get('t'), `${start}s`);
    assert.ok(video.timeLabel);
    if (p.ts === 'aligned-low') {
      weak++;
      assert.equal(embed.searchParams.get('end'), null);
      assert.ok(video.timeLabel.endsWith(' onward'));
    } else if (Number.isFinite(p.te) && p.te > p.t) {
      assert.equal(embed.searchParams.get('end'), String(Math.round(p.te)));
      assert.ok(video.timeLabel.includes('–'));
    }
  }
  if (p.ts === 'model') native++;
  if (p.ts === 'aligned') aligned++;
  const panel = ctx.insertVideoPanel(['STANDALONE_CARD', 'TRUST_CARD'],
    [{key: 'qa_standalone'}, {key: 'qa_alignment'}], video);
  assert.ok(panel.indexOf('STANDALONE_CARD') < panel.indexOf('source-video'));
  assert.ok(panel.indexOf('source-video') < panel.indexOf('TRUST_CARD'));
  assert.ok(panel.includes('referrerpolicy="strict-origin-when-cross-origin"'));
  assert.ok(panel.includes(`href="${ctx.escapeAttr(video.url)}"`));
  assert.ok(panel.includes('Open on YouTube'));
  if (video.timeLabel) {
    assert.ok(panel.slice(0, panel.indexOf('</summary>')).includes(video.timeLabel));
    assert.ok(panel.includes(video.approx ? 'Suggested time' : 'Recorded source time'));
  }
  if (video.weak) assert.ok(panel.includes('Low-confidence'));
}
assert.ok(native > 0 && aligned > 0 && weak > 0, 'exercise all timestamp sources');

const sample = {...active[0], t: 451, ts: 'model'};
delete sample.te;
assert.equal(ctx.sourceVideo(sample).timeLabel, '7:31 onward');
assert.equal(new URL(ctx.sourceVideo(sample).embed).searchParams.get('end'), null);
sample.t = 3601; sample.te = 3662;
assert.equal(ctx.sourceVideo(sample).timeLabel, '1:00:01–1:01:02');
sample.te = 3600; // Invalid end must not truncate playback.
assert.equal(ctx.sourceVideo(sample).timeLabel, '1:00:01 onward');
delete sample.t; delete sample.te;
const full = ctx.sourceVideo(sample);
assert.equal(full.timeLabel, null);
assert.equal(full.hasClip, false);
assert.equal(new URL(full.embed).searchParams.get('start'), '0');
const fullPanel = ctx.insertVideoPanel([], [], full);
assert.ok(fullPanel.includes('Exact segment timestamps are unavailable'));
assert.ok(fullPanel.includes('Open source video on YouTube'));

// Timestamp replacement must produce one t= parameter, even for a pre-timed URL.
ctx.VIDEOS = {Test: {'1': {url: 'https://www.youtube.com/watch?v=abcdefghijk&t=5s'}}};
assert.deepEqual(new URL(ctx.videoLink({dataset:'Test',video:1,t:91}, true))
  .searchParams.getAll('t'), ['91s']);

const headers = fs.readFileSync(path.join(folder, '_headers'), 'utf8');
assert.match(headers, /Referrer-Policy: strict-origin-when-cross-origin/);
const netlify = fs.readFileSync(path.join(root, 'netlify.toml'), 'utf8');
assert.match(netlify, /Referrer-Policy = "strict-origin-when-cross-origin"/);
assert.ok(html.includes('<meta name="referrer" content="strict-origin-when-cross-origin"'));
console.log(`PASS: ${active.length} active video URLs, player controls, referrer configuration, timestamp labels, direct links, bounded/open/missing ranges, and standalone-before-video placement.`);
