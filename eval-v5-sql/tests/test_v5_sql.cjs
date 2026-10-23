// Integration test for the v5 routing and permissions, against a new, isolated
// local PostgreSQL cluster. Requires initdb, pg_ctl and psql on PATH; it never
// connects to an existing database. Run from the repository root:
//   node eval-v5-sql/tests/test_v5_sql.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync, execFile} = require('node:child_process');
const sqlFolder = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync('/private/tmp/medicalqa-v5-pg-');
const dataDir = path.join(temporary, 'data');
const PORT = '55440';
let started = false;
const command = (name, args, input) => execFileSync(name, args,
  {input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 8 * 1024 * 1024});
const psqlArgs = database => ['-h', temporary, '-p', PORT, '-d', database, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1'];
const query = (database, sql) => command('psql', psqlArgs(database), sql).trim();
const queryAsync = (database, sql) => new Promise((resolve, reject) => {
  const child = execFile('psql', psqlArgs(database), {encoding: 'utf8'}, (error, stdout) =>
    error ? reject(error) : resolve(stdout.trim()));
  child.stdin.end(sql);
});
const read = name => fs.readFileSync(path.join(sqlFolder, name), 'utf8');
const setup = read('01_create_v5.sql');
const load = read('04_load_version_pairs_v5.sql');
const versions = JSON.parse(fs.readFileSync(path.resolve(sqlFolder, '../eval-v5-netlify/versions.json'), 'utf8')).versions;

const MEDICAL = ['clinical', 'student'];
const NON_MEDICAL = ['care', 'family', 'other'];
let arrivals = 0;
const claimSql = (group, asAnon = true) => {
  arrivals += 1;
  const other = group === 'other' ? "'Research volunteer'" : 'null';
  return `${asAnon ? 'set role anon; ' : ''}select eval_version from public.claim_version_v5('Annotator ${arrivals}', '${group}', ${other}, 'none');`;
};
const claim = (database, group) => Number(query(database, claimSql(group)));
const medical = (database, i = 0) => claim(database, MEDICAL[i % 2]);
const nonMedical = (database, i = 0) => claim(database, NON_MEDICAL[i % 3]);
// Per version: "medical/non-medical" for slots currently held.
const held = database => query(database, `select string_agg(medical_held || '/' || non_medical_held, ' ' order by eval_version)
  from public.version_status_v5();`);
const totals = database => query(database, `select string_agg(total_held::text, '-' order by eval_version)
  from public.version_status_v5();`);
const fresh = database => {
  query('postgres', `create database ${database};`);
  query(database, setup);
  query(database, load);
};
const rate = (database, assignment, uid, extra = '') => query(database, `set role anon; insert into public.ratings_v5
  (assignment_id, eval_version, session_id, annotator, qa_uid, dataset, video, approach, question_text, answer_text, qna_clarity_attribute${extra ? ', ' + extra.split('=')[0] : ''})
  values ('${assignment}', 1, 'browser-session', 'Browser name', '${uid}', 'Spoofed', 999, 'Spoofed', 'Q', 'A', 'Easy'${extra ? ', ' + extra.split('=')[1] : ''});`);
const latestAssignment = database => query(database, `select id || '|' || eval_version from public.assignments_v5 order by claimed_at desc, ctid desc limit 1;`).split('|');

(async () => {
  try {
    command('initdb', ['-D', dataDir, '--no-locale', '--encoding=UTF8', '--auth-local=trust', '--auth-host=reject']);
    command('pg_ctl', ['-D', dataDir, '-l', path.join(temporary, 'postgres.log'),
      '-o', `-F -h '' -k ${temporary} -p ${PORT} -c max_connections=100`, '-w', 'start']);
    started = true;
    query('postgres', 'create role anon; create role authenticated;');

    // Setup is one-time; claims are refused until the 100 pairs are loaded.
    query('postgres', 'create database setup_only;');
    query('setup_only', setup);
    assert.throws(() => query('setup_only', setup), 'setup refuses to run twice');
    assert.throws(() => claim('setup_only', 'clinical'), 'no claims before the versions are loaded');
    query('setup_only', load);
    assert.throws(() => query('setup_only', load), 'loading the pairs refuses to run twice');
    assert.equal(query('setup_only', 'select count(*) from public.version_pairs_v5;'), '100');
    for (const [v, uids] of Object.entries(versions)) {
      assert.equal(query('setup_only', `select string_agg(qa_uid, ',' order by position) from public.version_pairs_v5 where eval_version=${v};`),
        uids.join(','), `version ${v} matches versions.json`);
    }

    // 1. 20 medical and 10 non-medical in a mixed order -> 4/2 in every version.
    fresh('sequential');
    const order = [];
    for (let i = 0; i < 10; i++) order.push('m', 'n', 'm');
    const seen = order.map((kind, i) => kind === 'm' ? medical('sequential', i) : nonMedical('sequential', i));
    assert.deepEqual(seen.filter((_, i) => order[i] === 'm').slice(0, 4), [1, 1, 1, 1], 'version 1 fills first');
    assert.equal(held('sequential'), '4/2 4/2 4/2 4/2 4/2');
    assert.equal(totals('sequential'), '6-6-6-6-6');
    assert.equal(query('sequential', `select count(*) from public.assignments_v5 where routed_by='base';`), '30');
    medical('sequential'); assert.equal(totals('sequential'), '7-6-6-6-6');
    nonMedical('sequential'); assert.equal(totals('sequential'), '7-7-6-6-6');
    for (let i = 0; i < 3; i++) medical('sequential', i);
    assert.equal(totals('sequential'), '7-7-7-7-7');
    nonMedical('sequential'); assert.equal(totals('sequential'), '8-7-7-7-7');

    // 2. Every medical annotator arrives first: extras spread one at a time,
    //    then the non-medical base slots still go 2 per version.
    fresh('medical_first');
    for (let i = 0; i < 20; i++) medical('medical_first', i);
    assert.equal(held('medical_first'), '4/0 4/0 4/0 4/0 4/0');
    assert.equal(medical('medical_first'), 1);
    assert.equal(medical('medical_first'), 2);
    assert.equal(totals('medical_first'), '5-5-4-4-4');
    for (let i = 0; i < 10; i++) nonMedical('medical_first', i);
    assert.equal(held('medical_first'), '5/2 5/2 4/2 4/2 4/2');
    assert.equal(totals('medical_first'), '7-7-6-6-6');
    assert.equal(nonMedical('medical_first'), 3, 'past the base, the smallest version wins');
    assert.equal(medical('medical_first'), 4);

    // 3. An unfinished assignment releases its slot when its 48-hour lease ends.
    fresh('expiry');
    for (let i = 0; i < 4; i++) medical('expiry', i);
    assert.equal(medical('expiry'), 2);
    const [expiring] = latestAssignment('expiry');
    query('expiry', `update public.assignments_v5 set expires_at = now() - interval '1 second' where id='${expiring}';`);
    assert.equal(query('expiry', `select status from public.assignment_status_v5 where id='${expiring}';`), 'expired');
    assert.equal(medical('expiry'), 2, 'the released slot is offered again');
    assert.equal(held('expiry'), '4/0 1/0 0/0 0/0 0/0');
    assert.equal(query('expiry', `select interval '48 hours' = (expires_at - claimed_at) from public.assignments_v5 order by claimed_at desc limit 1;`), 't');

    // 4. Completion is detected from the ratings, and a late rating is kept and marked.
    fresh('completion');
    medical('completion');
    const [done, doneVersion] = latestAssignment('completion');
    assert.equal(doneVersion, '1');
    versions[doneVersion].slice(0, 19).forEach(uid => rate('completion', done, uid));
    assert.equal(query('completion', `select status || '|' || qnas_rated from public.assignment_status_v5 where id='${done}';`), 'active|19');
    rate('completion', done, versions[doneVersion][0]);  // a re-rating does not count twice
    assert.equal(query('completion', `select completed_at is null from public.assignments_v5 where id='${done}';`), 't');
    rate('completion', done, versions[doneVersion][19]);
    assert.equal(query('completion', `select status || '|' || qnas_rated from public.assignment_status_v5 where id='${done}';`), 'completed|20');
    query('completion', `update public.assignments_v5 set expires_at = now() - interval '1 second' where id='${done}';`);
    assert.equal(held('completion'), '1/0 0/0 0/0 0/0 0/0', 'a completed assignment keeps its slot after the lease');
    assert.equal(query('completion', 'select count(*) from public.ratings_v5_final;'), '20');
    // The database, not the browser, decides who, which version and which pair.
    assert.equal(query('completion', `select string_agg(distinct eval_version || '|' || session_id || '|' || annotator || '|' || dataset || '|' || late, ',')
      from public.ratings_v5 where qa_uid='${versions[doneVersion][0]}';`), `1|${done}|Annotator ${arrivals}|Master|false`);
    medical('completion');
    const [lateId, lateVersion] = latestAssignment('completion');
    query('completion', `update public.assignments_v5 set expires_at = now() - interval '1 second' where id='${lateId}';`);
    rate('completion', lateId, versions[lateVersion][0]);
    assert.equal(query('completion', `select late from public.ratings_v5 where assignment_id='${lateId}';`), 't');

    // 5. Concurrent claims never overfill: 36 people arrive at once.
    fresh('concurrent');
    const groups = [...Array(24)].map((_, i) => MEDICAL[i % 2]).concat([...Array(12)].map((_, i) => NON_MEDICAL[i % 3]));
    await Promise.all(groups.map(group => queryAsync('concurrent', claimSql(group))));
    assert.equal(query('concurrent', 'select count(*) from public.assignments_v5;'), '36');
    assert.equal(query('concurrent', `select count(*) from public.assignments_v5 where routed_by='base';`), '30');
    assert.equal(query('concurrent', `select string_agg(c::text, '-') from (select count(*) filter (where routed_by='base' and is_medical) c
      from public.assignments_v5 group by eval_version order by eval_version) x;`), '4-4-4-4-4');
    assert.equal(query('concurrent', `select string_agg(c::text, '-') from (select count(*) filter (where routed_by='base' and not is_medical) c
      from public.assignments_v5 group by eval_version order by eval_version) x;`), '2-2-2-2-2');
    const spread = totals('concurrent').split('-').map(Number);
    assert.ok(Math.max(...spread) - Math.min(...spread) <= 1, `balanced after the base: ${spread.join('-')}`);
    assert.equal(spread.reduce((a, b) => a + b), 36);

    // 6. The browser role can claim and append ratings, and nothing else.
    fresh('permissions');
    medical('permissions');
    const [mine, myVersion] = latestAssignment('permissions');
    const otherVersion = String(Number(myVersion) % 5 + 1);
    for (const table of ['assignments_v5', 'ratings_v5', 'ratings_v5_final', 'version_pairs_v5',
                         'role_categories_v5', 'study_settings_v5', 'assignment_status_v5'])
      assert.throws(() => query('permissions', `set role anon; select * from public.${table};`), `anon cannot read ${table}`);
    assert.throws(() => query('permissions', 'set role anon; select * from public.version_status_v5();'));
    assert.throws(() => query('permissions', `set role anon; update public.assignments_v5 set expires_at = now();`));
    assert.throws(() => query('permissions', `set role anon; delete from public.ratings_v5;`));
    assert.throws(() => rate('permissions', '00000000-0000-0000-0000-000000000000', versions[myVersion][0]), 'no rating for an unknown assignment');
    assert.throws(() => rate('permissions', mine, versions[otherVersion][0]), 'no rating for a pair outside the version');
    assert.throws(() => rate('permissions', mine, 'Master_v1_SingleAgent_q1'), 'no rating for a pair outside the study');
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('', 'clinical', null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', 'doctor', null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', 'other', null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', 'clinical', null, 'unknown');`));
    rate('permissions', mine, versions[myVersion][0], 'source_unverifiable=true');
    assert.throws(() => rate('permissions', mine, versions[myVersion][1], "source_unverifiable, qna_trustworthiness_attribute=true, 'Poor'"));
    assert.equal(query('permissions', `select
      has_table_privilege('anon','public.ratings_v5','insert'),
      has_table_privilege('anon','public.ratings_v5','select'),
      has_table_privilege('anon','public.assignments_v5','select'),
      has_function_privilege('anon','public.claim_version_v5(text,text,text,text)','execute'),
      has_function_privilege('anon','public.version_status_v5()','execute');`), 't|f|f|t|f');

    // 7. v5 is created beside a live v4 study and leaves it untouched.
    query('postgres', 'create database coexist;');
    query('coexist', fs.readFileSync(path.resolve(sqlFolder, '../eval-v4-sql/01_create_ratings_v4.sql'), 'utf8'));
    query('coexist', `insert into public.ratings_v4 (session_id, annotator, qa_uid, dataset, video, approach, question_text, answer_text)
      values ('v4-session', 'v4 annotator', 'Master_v1_SingleAgent_q1', 'Master', 1, 'SingleAgent', 'Q', 'A');`);
    const v4Snapshot = `select (select jsonb_agg(to_jsonb(r) order by id)::text from public.ratings_v4 r) || '|' ||
      (select string_agg(privilege_type, ',' order by privilege_type) from information_schema.role_table_grants
       where table_name = 'ratings_v4' and grantee = 'anon') || '|' ||
      (select count(*) from pg_policies where tablename = 'ratings_v4');`;
    const v4Before = query('coexist', v4Snapshot);
    query('coexist', setup);
    query('coexist', load);
    medical('coexist');
    const [coId, coVersion] = latestAssignment('coexist');
    rate('coexist', coId, versions[coVersion][0]);
    assert.equal(query('coexist', v4Snapshot), v4Before, 'v4 rows, grants and policies are unchanged');
    assert.equal(query('coexist', 'select count(*) from public.ratings_v4;'), '1');
    assert.equal(query('coexist', `select count(*) from pg_trigger where tgrelid = 'public.ratings_v4'::regclass and not tgisinternal;`), '0');
    const verify = read('02_verify_v5.sql');
    const separation = verify.slice(verify.indexOf('with deps as'), verify.indexOf('-- Before launch every count'));
    const [dependencies, bodies] = separation.split(/;\s*\n/).filter(s => s.trim().startsWith('with') || s.includes('select p.oid'));
    assert.equal(query('coexist', dependencies + ';'), '', 'no v5 object depends on a non-v5 object');
    assert.equal(query('coexist', bodies.slice(bodies.indexOf('select')) + ';'), '', 'no v5 function names an earlier ratings table');
    // The check is live: a v5 view over ratings_v4 is reported.
    query('coexist', 'create view public.leak_v5 as select * from public.ratings_v4;');
    assert.match(query('coexist', dependencies + ';'), /leak_v5\|ratings_v4/);
    query('coexist', 'drop view public.leak_v5;');

    // 8. The researcher scripts run cleanly against a populated database.
    query('sequential', read('02_verify_v5.sql'));
    query('sequential', read('03_analysis_queries.sql'));
    console.log('PASS: one-time setup and pair loading, base fill 4/2 then one-at-a-time balance (6-6-6-6-6 -> 7-6-6-6-6 -> 7-7-6-6-6), medical-first order, 48 h lease release, completion and late ratings, 36 concurrent claims, browser-role permissions, separation from a live v4 study, verify and analysis scripts.');
  } finally {
    if (started) command('pg_ctl', ['-D', dataDir, '-m', 'fast', '-w', 'stop']);
    // Only this test's newly created cluster and logs are removed.
    if (path.dirname(temporary) === '/private/tmp' && path.basename(temporary).startsWith('medicalqa-v5-pg-'))
      fs.rmSync(temporary, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
