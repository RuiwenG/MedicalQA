// Real-browser regression check. Every external request is intercepted; database
// inserts are mocked in memory, so no ratings are sent to Supabase or YouTube.
// Set V4_PLAYWRIGHT_MODULE if Playwright is outside normal module resolution,
// and V4_CHROME_EXECUTABLE to use an installed Chrome instead of bundled Chromium.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.V4_PLAYWRIGHT_MODULE || 'playwright');
const folder = path.resolve(__dirname, '../../eval-v4-netlify');
const origin = 'https://v4-evaluation.test';
const getSession = page => page.evaluate(() => {
  const key = Object.keys(localStorage).find(k => k.startsWith('dqa:v4:'));
  return key ? JSON.parse(localStorage.getItem(key)) : null;
});

(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.V4_CHROME_EXECUTABLE ? {executablePath: process.env.V4_CHROME_EXECUTABLE} : {})});
  const contexts = [];
  async function makePage(seed) {
    const context = await browser.newContext({viewport: {width: 1100, height: 1100}});
    contexts.push(context);
    const submissions = [], errors = [];
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const files = {'/': 'index.html', '/config.js': 'config.js', '/qa_data.json': 'qa_data.json'};
      if (url.origin === origin && files[url.pathname]) {
        const file = files[url.pathname];
        await route.fulfill({body: fs.readFileSync(path.join(folder, file)),
          contentType: file.endsWith('.json') ? 'application/json' : file.endsWith('.js') ? 'text/javascript' : 'text/html'});
      } else if (url.hostname.endsWith('.supabase.co') && url.pathname === '/rest/v1/ratings_v4' && request.method() === 'POST') {
        submissions.push(request.postDataJSON());
        await route.fulfill({status: 201, body: ''});
      } else await route.abort();
    });
    if (seed) await context.addInitScript(({seed, origin}) => {
      if (window !== window.top || location.origin !== origin) return;
      localStorage.setItem(seed.storeKey, JSON.stringify(seed));
      localStorage.setItem('dqa:lastopts:v4', JSON.stringify({name: seed.annotator}));
    }, {seed, origin});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.locator('#start').waitFor();
    return {page, submissions, errors};
  }
  try {
    const first = await makePage(), page = first.page;
    assert.equal(await page.locator('#annotator-group option').count(), 6, 'placeholder plus five choices');
    assert.equal(await page.locator('#annotator-group option').last().textContent(), 'Others');
    assert.equal(await page.locator('optgroup, #adv, #mqa-unpaid, #annotator-subcategory').count(), 0);
    await page.locator('#name').fill('Profile test Alice');
    await page.locator('#start').click();
    assert.match(await page.locator('#start-err').innerText(), /choose your role/);
    await page.locator('#annotator-group').selectOption('clinical');
    await page.locator('#dementia-experience').selectOption('none');
    await page.screenshot({path: '/private/tmp/medicalqa-v4-profile-desktop.png', fullPage: true});
    await page.locator('#start').click();
    await page.locator('#qtext').waitFor();
    const initial = await getSession(page);
    assert.equal(initial.uids.length, 40); assert.equal(initial.metrics.length, 5); assert.equal(initial.opts.blind, true);
    const metrics = await page.evaluate(() => METRICS.map(m => ({key: m.key,
      hasScale: hasAttributeScale(m), optional: Boolean(m.optionalIssues), clear: clearChoice(m)})));
    for (const metric of metrics) {
      if (metric.hasScale) await page.locator(`[data-key="${metric.key}"][data-field="attribute"]`).first().click();
      if (!metric.optional) await page.locator(`[data-key="${metric.key}"][data-choice="${metric.clear}"]`).click();
    }
    await page.locator('[data-recommend-v="Yes"]').click();
    await page.locator('#next').click();
    await page.waitForFunction(() => S.index === 1 && S.queue.length === 0);
    assert.equal(first.submissions.length, 1);
    const posted = first.submissions[0][0];
    assert.equal(posted.annotator_group, 'clinical'); assert.equal(posted.annotator_other, null);
    assert.equal(posted.dementia_care_experience, 'none');
    await page.locator('#options').click();
    assert.equal(await page.locator('#annotator-group').inputValue(), 'clinical');
    assert.equal(await page.locator('#dementia-experience').inputValue(), 'none');
    await page.locator('#start').click(); await page.locator('#qtext').waitFor();
    assert.equal((await getSession(page)).index, 1);

    const second = await makePage(), other = second.page;
    await other.setViewportSize({width: 390, height: 844});
    await other.locator('#name').fill('Profile test Bob');
    await other.locator('#annotator-group').selectOption('other');
    assert.equal(await other.locator('#annotator-other-field').isVisible(), true);
    await other.locator('#dementia-experience').selectOption('more_three_years');
    await other.locator('#start').click();
    assert.match(await other.locator('#start-err').innerText(), /describe your role/);
    await other.locator('#annotator-other').fill('Community volunteer');
    assert.equal(await other.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await other.screenshot({path: '/private/tmp/medicalqa-v4-profile-mobile.png', fullPage: true});
    await other.locator('#start').click(); await other.locator('#qtext').waitFor();
    const secondSession = await getSession(other);
    assert.deepEqual(secondSession.uids, initial.uids);
    assert.equal(secondSession.annotatorProfile.other, 'Community volunteer');

    const legacy = await getSession(page); delete legacy.annotatorProfile;
    legacy.queue = [{...posted}];
    for (const key of ['annotator_group', 'annotator_other', 'dementia_care_experience']) delete legacy.queue[0][key];
    const third = await makePage(legacy), resume = third.page;
    assert.equal(await resume.locator('#annotator-group').inputValue(), '');
    assert.match(await resume.locator('#resume-note').innerText(), /1 of 40 pairs already rated/);
    await resume.locator('#annotator-group').selectOption('student');
    await resume.locator('#dementia-experience').selectOption('less_one_year');
    await resume.locator('#start').click(); await resume.locator('#qtext').waitFor();
    await resume.waitForFunction(() => S.queue.length === 0);
    const resumed = await getSession(resume);
    assert.equal(resumed.storeKey, legacy.storeKey); assert.equal(resumed.index, 1);
    assert.deepEqual(resumed.scores, legacy.scores);
    assert.equal(third.submissions[0][0].annotator_group, 'student');
    assert.equal(third.submissions[0][0].dementia_care_experience, 'less_one_year');
    for (const fixture of [first, second, third]) assert.deepEqual(fixture.errors, []);
    console.log('PASS: real browser desktop/mobile form, five groups, Others validation, identical assignments, mocked rating POST with profile, matching resume and legacy retry upgrade; all external requests intercepted.');
  } finally {
    for (const context of contexts) await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
