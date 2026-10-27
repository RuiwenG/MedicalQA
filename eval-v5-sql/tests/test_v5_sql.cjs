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

const MEDICAL = ['healthcare', 'student'];
const NON_MEDICAL = ['family', 'other'];
let arrivals = 0;
const claimSql = (group, asAnon = true) => {
  arrivals += 1;
  const groups = (Array.isArray(group) ? group : [group]).map(g => `'${g}'`).join(', ');
  // Descriptions are optional: every third arrival leaves them out.
  const details = arrivals % 3 === 0 ? 'null' : `'${JSON.stringify(Object.fromEntries([].concat(group).map(g => [g, 'About my ' + g + ' role'])))}'::jsonb`;
  return `${asAnon ? 'set role anon; ' : ''}select eval_version from public.claim_version_v5('Annotator ${arrivals}', array[${groups}]::text[], ${details}, 'none');`;
};
const claim = (database, group) => Number(query(database, claimSql(group)));
const medical = (database, i = 0) => claim(database, MEDICAL[i % 2]);
const nonMedical = (database, i = 0) => claim(database, NON_MEDICAL[i % NON_MEDICAL.length]);
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
    assert.throws(() => claim('setup_only', 'healthcare'), 'no claims before the versions are loaded');
    query('setup_only', load);
    assert.throws(() => query('setup_only', load), 'loading the pairs refuses to run twice');
    assert.equal(query('setup_only', 'select count(*) from public.version_pairs_v5;'), '100');
    for (const [v, uids] of Object.entries(versions)) {
      assert.equal(query('setup_only', `select string_agg(qa_uid, ',' order by position) from public.version_pairs_v5 where eval_version=${v};`),
        uids.join(','), `version ${v} matches versions.json`);
    }

    // 1. 15 medical and 10 non-medical in a mixed order -> 3/2 in every version.
    fresh('sequential');
    const order = [];
    for (let i = 0; i < 5; i++) order.push('m', 'n', 'm', 'n', 'm');
    const seen = order.map((kind, i) => kind === 'm' ? medical('sequential', i) : nonMedical('sequential', i));
    assert.deepEqual(seen.filter((_, i) => order[i] === 'm').slice(0, 4), [1, 1, 1, 2], 'version 1 fills first');
    assert.equal(held('sequential'), '3/2 3/2 3/2 3/2 3/2');
    assert.equal(totals('sequential'), '5-5-5-5-5');
    assert.equal(query('sequential', `select count(*) from public.assignments_v5 where routed_by='base';`), '25');
    medical('sequential'); assert.equal(totals('sequential'), '6-5-5-5-5');
    nonMedical('sequential'); assert.equal(totals('sequential'), '6-6-5-5-5');
    for (let i = 0; i < 3; i++) medical('sequential', i);
    assert.equal(totals('sequential'), '6-6-6-6-6');
    nonMedical('sequential'); assert.equal(totals('sequential'), '7-6-6-6-6');

    // 2. Every medical annotator arrives first: extras spread one at a time,
    //    then the non-medical base slots still go 2 per version.
    fresh('medical_first');
    for (let i = 0; i < 15; i++) medical('medical_first', i);
    assert.equal(held('medical_first'), '3/0 3/0 3/0 3/0 3/0');
    assert.equal(medical('medical_first'), 1);
    assert.equal(medical('medical_first'), 2);
    assert.equal(totals('medical_first'), '4-4-3-3-3');
    for (let i = 0; i < 10; i++) nonMedical('medical_first', i);
    assert.equal(held('medical_first'), '4/2 4/2 3/2 3/2 3/2');
    assert.equal(totals('medical_first'), '6-6-5-5-5');
    assert.equal(nonMedical('medical_first'), 3, 'past the base, the smallest version wins');
    assert.equal(medical('medical_first'), 4);

    // 3. An unfinished assignment releases its slot when its 48-hour lease ends.
    fresh('expiry');
    for (let i = 0; i < 3; i++) medical('expiry', i);
    assert.equal(medical('expiry'), 2);
    const [expiring] = latestAssignment('expiry');
    query('expiry', `update public.assignments_v5 set expires_at = now() - interval '1 second' where id='${expiring}';`);
    assert.equal(query('expiry', `select status from public.assignment_status_v5 where id='${expiring}';`), 'expired');
    assert.equal(medical('expiry'), 2, 'the released slot is offered again');
    assert.equal(held('expiry'), '3/0 1/0 0/0 0/0 0/0');
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
    const groups = [...Array(24)].map((_, i) => MEDICAL[i % 2]).concat([...Array(12)].map((_, i) => NON_MEDICAL[i % NON_MEDICAL.length]));
    await Promise.all(groups.map(group => queryAsync('concurrent', claimSql(group))));
    assert.equal(query('concurrent', 'select count(*) from public.assignments_v5;'), '36');
    assert.equal(query('concurrent', `select count(*) from public.assignments_v5 where routed_by='base';`), '25');
    assert.equal(query('concurrent', `select string_agg(c::text, '-') from (select count(*) filter (where routed_by='base' and is_medical) c
      from public.assignments_v5 group by eval_version order by eval_version) x;`), '3-3-3-3-3');
    assert.equal(query('concurrent', `select string_agg(c::text, '-') from (select count(*) filter (where routed_by='base' and not is_medical) c
      from public.assignments_v5 group by eval_version order by eval_version) x;`), '2-2-2-2-2');
    // Extras are balanced when they arrive; a base slot of the other category can
    // still land on a version later, so the spread is at most 2.
    const spread = totals('concurrent').split('-').map(Number);
    assert.ok(Math.max(...spread) - Math.min(...spread) <= 2, `balanced after the base: ${spread.join('-')}`);
    assert.equal(spread.reduce((a, b) => a + b), 36);

    // 5b. Several roles: medical when any role is medical; each role stored once, in the page's order,
    //     with its optional description; the ratings carry both.
    fresh('multi_role');
    query('multi_role', `set role anon; select * from public.claim_version_v5('Annotator A', array['family', 'healthcare', 'family'],
      '{"family": "  Caring for my mother ", "healthcare": "", "student": "not ticked"}'::jsonb, 'none');`);
    assert.equal(query('multi_role', `select array_to_string(annotator_groups, ',') || '|' || is_medical || '|' || annotator_role_details::text
      from public.assignments_v5;`), 'healthcare,family|true|{"family": "Caring for my mother"}',
      'medical because one role is; blank and unticked descriptions are dropped, the rest trimmed');
    const [multi, multiVersion] = latestAssignment('multi_role');
    rate('multi_role', multi, versions[multiVersion][0]);
    assert.equal(query('multi_role', `select array_to_string(annotator_groups, ',') || '|' || annotator_role_details::text
      from public.ratings_v5 where assignment_id='${multi}';`), 'healthcare,family|{"family": "Caring for my mother"}');
    query('multi_role', `set role anon; select * from public.claim_version_v5('Annotator B', array['other', 'family'], null, 'none');`);
    assert.equal(query('multi_role', `select array_to_string(annotator_groups, ',') || '|' || is_medical || '|' || annotator_role_details::text
      from public.assignments_v5 order by claimed_at desc limit 1;`), 'family,other|false|{}', 'every description is optional, Other too');
    assert.equal(held('multi_role'), '1/1 0/0 0/0 0/0 0/0');

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
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('', array['healthcare'], null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['doctor'], null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['family', 'doctor'], null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array[]::text[], null, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['clinical'], null, 'none');`), 'the old role codes are gone');
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['other'], '"text"'::jsonb, 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['other'], jsonb_build_object('other', repeat('x', 201)), 'none');`));
    assert.throws(() => query('permissions', `set role anon; select * from public.claim_version_v5('X', array['healthcare'], null, 'unknown');`));
    rate('permissions', mine, versions[myVersion][0], 'source_unverifiable=true');
    assert.throws(() => rate('permissions', mine, versions[myVersion][1], "source_unverifiable, qna_trustworthiness_attribute=true, 'Poor'"));
    assert.equal(query('permissions', `select
      has_table_privilege('anon','public.ratings_v5','insert'),
      has_table_privilege('anon','public.ratings_v5','select'),
      has_table_privilege('anon','public.assignments_v5','select'),
      has_function_privilege('anon','public.claim_version_v5(text,text[],jsonb,text)','execute'),
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

    // 10. Batches: versions 6-10 load into a running study, the capacity alert
    //     fires once at 15 people per batch, and new base slots come first.
    //     The batch 2 loader is generated by build_versions.py from a selection of
    //     versions.json plus 100 further Qwen3-14B pairs from the repository.
    const netlify = path.resolve(sqlFolder, '../eval-v5-netlify');
    const built = path.join(temporary, 'built');
    fs.mkdirSync(path.join(built, 'site'), {recursive: true});
    fs.mkdirSync(path.join(built, 'sql'), {recursive: true});
    const selection = command('python3', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(netlify)})
import build_versions as b
v = json.load(open(${JSON.stringify(path.join(netlify, 'versions.json'))}))['versions']
used = {u for uids in v.values() for u in uids}
rest = sorted(u for u in b.load_pairs() if u not in used)[:100]
print(json.dumps({'versions': [v[str(i)] for i in range(1, 6)] + [rest[i * 20:(i + 1) * 20] for i in range(5)]}))`]);
    fs.writeFileSync(path.join(built, 'selection.json'), selection);
    command('python3', [path.join(netlify, 'build_versions.py'), '--selection', path.join(built, 'selection.json'),
      '--site-dir', path.join(built, 'site'), '--sql-dir', path.join(built, 'sql')]);
    const batch2 = fs.readFileSync(path.join(built, 'sql', '04_load_version_pairs_v5_batch2.sql'), 'utf8');
    const versions10 = JSON.parse(fs.readFileSync(path.join(built, 'site', 'versions.json'), 'utf8')).versions;
    assert.equal(Object.keys(versions10).length, 10);
    assert.equal(fs.readFileSync(path.join(built, 'sql', '04_load_version_pairs_v5.sql'), 'utf8').split('-- Run once')[1],
      load.split('-- Run once')[1], 'batch 1 loader is unchanged by adding a batch');
    const alerts = database => query(database, `select coalesce(string_agg(batch || ':' || held_at_alert, ',' order by batch), '')
      from public.capacity_alerts_v5;`);

    fresh('batches');
    for (let i = 0; i < 14; i++) i % 5 < 3 ? medical('batches', i) : nonMedical('batches', i);
    assert.equal(alerts('batches'), '', 'no alert at 14 people');
    medical('batches');
    assert.equal(alerts('batches'), '1:15', 'alert at the 15th person');
    assert.match(query('batches', 'select message from public.capacity_alerts_v5;'), /versions 1-5: prepare the Q&As for versions 6-10/);
    while (query('batches', `select count(*) from public.assignments_v5 where is_medical;`) !== '15') medical('batches');
    while (query('batches', `select count(*) from public.assignments_v5 where not is_medical;`) !== '10') nonMedical('batches');
    assert.equal(held('batches'), '3/2 3/2 3/2 3/2 3/2');
    assert.equal(alerts('batches'), '1:15', 'raised once');
    assert.equal(medical('batches'), 1, 'groups 1-5 full: an extra rater in version 1');
    assert.equal(query('batches', `select routed_by from public.assignments_v5 order by claimed_at desc, ctid desc limit 1;`), 'balance');
    assert.equal(query('batches', `select batch || '|' || versions || '|' || total_held || '|' || base_slots from public.batch_status_v5();`), '1|1-5|26|25');

    query('postgres', 'create database batch_guard;');
    query('batch_guard', setup);
    assert.throws(() => query('batch_guard', batch2), /versions 1-5 loaded/, 'batch 2 needs batch 1 first');
    query('batch_guard', load);
    query('batch_guard', `update public.version_pairs_v5 set qa_uid = qa_uid || '_changed' where eval_version = 3 and position = 7;`);
    assert.throws(() => query('batch_guard', batch2), /differ from versions.json/, 'batch 2 refuses a database whose versions differ');

    query('batches', batch2);
    assert.throws(() => query('batches', batch2), 'batch 2 loads once');
    assert.equal(query('batches', 'select count(*) from public.version_pairs_v5;'), '200');
    assert.equal(query('batches', `select count(*) from public.assignments_v5;`), '26', 'nothing already stored changes');
    assert.equal(medical('batches'), 6, 'new base slots come before extras');
    assert.equal(nonMedical('batches'), 6);
    assert.equal(held('batches').split(' ').slice(5).join(' '), '1/1 0/0 0/0 0/0 0/0');
    for (let i = 0; i < 12; i++) i % 5 < 3 ? medical('batches', i) : nonMedical('batches', i);
    assert.equal(alerts('batches'), '1:15', '14 people in batch 2: no second alert yet');
    medical('batches');
    assert.equal(alerts('batches'), '1:15,2:15');
    assert.match(query('batches', 'select message from public.capacity_alerts_v5 where batch = 2;'), /versions 6-10: prepare the Q&As for versions 11-15/);
    // A slot released in batch 1 is refilled before batch 2's remaining slots.
    query('batches', `update public.assignments_v5 set expires_at = now() - interval '1 second'
      where id = (select id from public.assignments_v5 where eval_version = 2 and is_medical order by claimed_at limit 1);`);
    assert.equal(medical('batches'), 2);
    // A rating for a batch 2 pair is stored against its version.
    assert.equal(latestAssignment('batches')[1], '2');
    medical('batches');
    const [b6Id, b6Version] = latestAssignment('batches');
    rate('batches', b6Id, versions10[b6Version][0]);
    assert.equal(query('batches', `select eval_version from public.ratings_v5 where assignment_id = '${b6Id}';`), b6Version);
    assert.ok(Number(b6Version) >= 6);
    assert.throws(() => query('batches', 'set role anon; select * from public.capacity_alerts_v5;'), 'anon cannot read alerts');
    assert.throws(() => query('batches', 'set role anon; select * from public.batch_status_v5();'), 'anon cannot read batch status');
    query('batches', read('02_verify_v5.sql'));
    query('batches', read('03_analysis_queries.sql'));
    // 9. Upgrade a populated original single-role v5 database, preserving every
    // existing field, routing decision, raw edit and dependent researcher view.
    const upgrade = read('05_upgrade_v5_multi_role.sql');
    const historical = read('tests/fixtures/v5_single_role.sql');
    const inspect = read('00_check_v5_state.sql');
    assert.match(query('postgres', inspect), /NEW V5:/);
    query('postgres', 'create database upgrade_legacy;');
    query('upgrade_legacy', fs.readFileSync(path.resolve(sqlFolder, '../eval-v4-sql/01_create_ratings_v4.sql'), 'utf8'));
    query('upgrade_legacy', `insert into public.ratings_v4 (session_id, annotator, qa_uid, dataset, video, approach, question_text, answer_text)
      values ('v4-session', 'v4 annotator', 'Master_v1_SingleAgent_q1', 'Master', 1, 'SingleAgent', 'Q', 'A');`);
    const olderStudy = query('upgrade_legacy', v4Snapshot);
    query('upgrade_legacy', historical);
    query('upgrade_legacy', load);
    assert.match(query('upgrade_legacy', inspect), /OLD SINGLE-ROLE V5:/);
    for (const role of ['clinical', 'care', 'student', 'family', 'other']) {
      query('upgrade_legacy', `set role anon; select * from public.claim_version_v5('Legacy ${role}', '${role}',
        ${role === 'other' ? "'  Volunteer  '" : 'null'}, 'one_to_three_years');`);
      const [id, version] = latestAssignment('upgrade_legacy');
      rate('upgrade_legacy', id, versions[version][0]);
      rate('upgrade_legacy', id, versions[version][0]); // Preserve raw revisions as well as final rows.
    }
    query('upgrade_legacy', `update public.assignments_v5 set completed_at = now() where annotator_group='clinical';
      update public.assignments_v5 set expires_at = now() - interval '1 day' where annotator_group='care';
      update public.ratings_v5 set annotator_group=null, annotator_other=null, dementia_care_experience=null
        where id=(select min(id) from public.ratings_v5);
      create view public.researcher_assignments as select * from public.assignment_status_v5;
      create view public.researcher_ratings as select * from public.ratings_v5_final;`);
    // The upgrade adds the multi-role fields and, once, moves the medical quota
    // from 4 to 3 and adds the alert threshold; everything else stays.
    const snapshot = table => `select coalesce(jsonb_agg(to_jsonb(t) - 'annotator_groups' - 'annotator_role_details'
      - 'base_medical' - 'alert_at_held' order by to_jsonb(t)::text), '[]'::jsonb)::text from public.${table} t;`;
    const preserveTables = ['assignments_v5', 'ratings_v5', 'version_pairs_v5', 'study_settings_v5'];
    const before = Object.fromEntries(preserveTables.map(t => [t, query('upgrade_legacy', snapshot(t))]));
    const dependentBefore = ['researcher_assignments', 'researcher_ratings'].map(t => query('upgrade_legacy', snapshot(t)));
    assert.equal(query('upgrade_legacy', 'select base_medical || \'/\' || base_non_medical from public.study_settings_v5;'), '4/2');
    query('upgrade_legacy', upgrade);
    assert.match(query('upgrade_legacy', inspect), /MULTI-ROLE V5:/);
    assert.equal(query('upgrade_legacy', `select base_medical || '/' || base_non_medical || '/' || alert_at_held
      from public.study_settings_v5;`), '3/2/15', 'quotas move to 3 medical + 2 non-medical, alert at 15');
    for (const table of preserveTables) assert.equal(query('upgrade_legacy', snapshot(table)), before[table], `${table}: every original value preserved`);
    assert.deepEqual(['researcher_assignments', 'researcher_ratings'].map(t => query('upgrade_legacy', snapshot(t))), dependentBefore);
    assert.equal(query('upgrade_legacy', v4Snapshot), olderStudy, 'migration does not touch v4');
    assert.equal(query('upgrade_legacy', `select string_agg(annotator_group || ':' || is_medical, ',' order by annotator_group)
      from public.role_categories_v5;`), 'family:false,healthcare:true,other:false,student:true');
    assert.equal(query('upgrade_legacy', `select array_to_string(annotator_groups, ',') || '|' || is_medical
      from public.assignments_v5 where annotator_group='care';`), 'healthcare|false', 'historical care routing remains non-medical');
    assert.equal(query('upgrade_legacy', `select annotator_role_details->>'other' from public.assignments_v5 where annotator_group='other';`), 'Volunteer');
    assert.equal(query('upgrade_legacy', `select annotator_groups is null and annotator_role_details is null
      from public.ratings_v5 where id=(select min(id) from public.ratings_v5);`), 't', 'legacy absent rating profile stays absent');
    assert.equal(query('upgrade_legacy', `select to_regprocedure('public.claim_version_v5(text,text,text,text)') is null;`), 't', 'obsolete RPC removed');
    assert.equal(query('upgrade_legacy', `select count(*) from information_schema.columns
      where table_schema='public' and table_name in ('assignment_status_v5','ratings_v5_final')
      and column_name in ('annotator_groups','annotator_role_details');`), '4', 'both views expose the new fields');
    query('upgrade_legacy', upgrade); // Reapplying must not rewrite history.
    for (const table of preserveTables) assert.equal(query('upgrade_legacy', snapshot(table)), before[table]);
    query('upgrade_legacy', `set role anon; select * from public.claim_version_v5('New multi-role',
      array['family','healthcare'], '{"family":"Daughter","healthcare":"Nurse"}', 'more_three_years');`);
    const [upId, upVersion] = latestAssignment('upgrade_legacy');
    rate('upgrade_legacy', upId, versions[upVersion][0]);
    assert.equal(query('upgrade_legacy', `select annotator_groups::text || '|' || is_medical
      from public.assignments_v5 where id='${upId}';`), '{healthcare,family}|true');
    assert.equal(query('upgrade_legacy', `select annotator_groups::text || '|' || (annotator_role_details->>'healthcare')
      from public.ratings_v5 where assignment_id='${upId}';`), '{healthcare,family}|Nurse');
    // An older browser posting legacy fields cannot falsify the saved profile.
    rate('upgrade_legacy', upId, versions[upVersion][1], "annotator_group, annotator_other='other', 'Spoofed'");
    assert.equal(query('upgrade_legacy', `select annotator_group is null and annotator_other is null from public.ratings_v5
      where assignment_id='${upId}' order by id desc limit 1;`), 't');
    // A returning legacy participant keeps their original assignment and profile.
    const [legacyId, legacyVersion] = query('upgrade_legacy', `select id || '|' || eval_version
      from public.assignments_v5 where annotator_group='other';`).split('|');
    rate('upgrade_legacy', legacyId, versions[legacyVersion][1]);
    assert.equal(query('upgrade_legacy', `select annotator_group || '|' || (annotator_role_details->>'other')
      from public.ratings_v5 where assignment_id='${legacyId}' order by id desc limit 1;`), 'other|Volunteer');
    assert.throws(() => query('upgrade_legacy', `set role anon; select * from public.claim_version_v5('Old', 'clinical', null, 'none');`));
    assert.throws(() => query('upgrade_legacy', `set role anon; select * from public.claim_version_v5('Unknown', array['clinical'], null, 'none');`));
    assert.throws(() => query('upgrade_legacy', `set role anon; select * from public.claim_version_v5('Too long', array['family'], jsonb_build_object('family', repeat('x',201)), 'none');`));
    assert.throws(() => query('upgrade_legacy', `set role anon; select * from public.ratings_v5;`));
    assert.throws(() => query('upgrade_legacy', `set role authenticated; select * from public.claim_version_v5('Not allowed', array['family'], null, 'none');`));
    assert.throws(() => query('upgrade_legacy', `set role anon; delete from public.assignments_v5;`));
    rate('upgrade_legacy', upId, versions[upVersion][2], 'source_unverifiable=true');
    assert.throws(() => rate('upgrade_legacy', upId, versions[upVersion][3], "source_unverifiable, qna_trustworthiness_attribute=true, 'Poor'"));
    query('upgrade_legacy', read('02_verify_v5.sql'));
    query('upgrade_legacy', read('03_analysis_queries.sql'));
    assert.equal(query('upgrade_legacy', dependencies + ';'), '');
    assert.equal(query('upgrade_legacy', bodies.slice(bodies.indexOf('select')) + ';'), '');

    // Full quota behavior after upgrading an empty legacy schema.
    query('postgres', 'create database upgrade_routing;');
    query('upgrade_routing', historical);
    query('upgrade_routing', load);
    query('upgrade_routing', upgrade);
    for (let i = 0; i < 15; i++) medical('upgrade_routing', i);
    for (let i = 0; i < 10; i++) nonMedical('upgrade_routing', i);
    assert.equal(held('upgrade_routing'), '3/2 3/2 3/2 3/2 3/2');
    medical('upgrade_routing');
    assert.equal(totals('upgrade_routing'), '6-5-5-5-5');
    assert.equal(query('upgrade_routing', 'select count(*) from public.capacity_alerts_v5;'), '1', 'the upgraded study raises the alert too');
    // A rerun keeps quotas the team has changed since.
    query('upgrade_routing', 'update public.study_settings_v5 set base_medical = 4;');
    query('upgrade_routing', upgrade);
    assert.equal(query('upgrade_routing', 'select base_medical from public.study_settings_v5;'), '4');

    // Already-current schemas, including populated ones, accept repeated upgrades.
    const currentBefore = Object.fromEntries(preserveTables.map(t => [t, query('multi_role',
      `select jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text from public.${t} t;`)]));
    query('multi_role', upgrade); query('multi_role', upgrade);
    for (const table of preserveTables) assert.equal(query('multi_role',
      `select jsonb_agg(to_jsonb(t) order by to_jsonb(t)::text)::text from public.${table} t;`), currentBefore[table]);
    medical('multi_role');
    const [freshId, freshVersion] = latestAssignment('multi_role');
    rate('multi_role', freshId, versions[freshVersion][0]);

    // Fail atomically for an unsupported or malformed database; never partially
    // migrate before a later constraint error.
    query('postgres', 'create database empty_upgrade;');
    assert.throws(() => query('empty_upgrade', upgrade), /Missing study_settings_v5/);
    query('empty_upgrade', 'create table public.study_settings_v5 (id boolean);');
    assert.match(query('empty_upgrade', inspect), /INCOMPLETE V5:/);
    query('postgres', 'create database broken_upgrade;');
    query('broken_upgrade', historical);
    query('broken_upgrade', load);
    query('broken_upgrade', `alter table public.role_categories_v5 drop constraint role_categories_v5_group_ck;
      insert into public.role_categories_v5 values ('unexpected', false);
      set role anon; select * from public.claim_version_v5('Unknown profile', 'unexpected', null, 'none');
      reset role;
      alter table public.assignments_v5 drop constraint assignments_v5_annotator_group_fkey;
      delete from public.role_categories_v5 where annotator_group='unexpected';`);
    const brokenBefore = query('broken_upgrade', snapshot('assignments_v5'));
    assert.throws(() => query('broken_upgrade', upgrade), /assignments_v5_groups_ck/);
    assert.equal(query('broken_upgrade', snapshot('assignments_v5')), brokenBefore);
    assert.equal(query('broken_upgrade', `select count(*) from information_schema.columns where table_schema='public'
      and table_name='assignments_v5' and column_name='annotator_groups';`), '0', 'DDL rolled back too');
    assert.equal(query('broken_upgrade', `select to_regprocedure('public.claim_version_v5(text,text,text,text)') is not null;`), 't');
    console.log('PASS: populated legacy upgrade, all original fields and routing preserved, current-schema reruns, dependent views, new multi-role signup/ratings, historical resumes, quota routing, RLS, v4 isolation, and rollback on invalid data.');
    console.log('PASS: batches (versions 6-10 generated and loaded into a running study, guarded; capacity alert once per batch at 15 people; new base slots before extras; released slots refilled first).');
    console.log('PASS: one-time setup and pair loading, base fill 3/2 then one-at-a-time balance (5-5-5-5-5 -> 6-5-5-5-5 -> 6-6-5-5-5), medical-first order, 48 h lease release, completion and late ratings, 36 concurrent claims, several roles per annotator (medical if any role is) with optional descriptions, browser-role permissions, separation from a live v4 study, verify and analysis scripts.');
  } finally {
    if (started) command('pg_ctl', ['-D', dataDir, '-m', 'fast', '-w', 'stop']);
    // Only this test's newly created cluster and logs are removed.
    if (path.dirname(temporary) === '/private/tmp' && path.basename(temporary).startsWith('medicalqa-v5-pg-'))
      fs.rmSync(temporary, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
