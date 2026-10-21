// Integration test against a new, isolated local PostgreSQL cluster.
// Requires initdb, pg_ctl and psql on PATH; never connects to an existing DB.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const sqlFolder = path.resolve(__dirname, '..');
const temporary = fs.mkdtempSync('/private/tmp/medicalqa-v4-pg-');
const dataDir = path.join(temporary, 'data');
let started = false;
const command = (name, args, input) => execFileSync(name, args,
  {input, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 4 * 1024 * 1024});
const query = (database, sql) => command('psql', ['-h', temporary, '-p', '55439',
  '-d', database, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1'], sql).trim();
const setup = fs.readFileSync(path.join(sqlFolder, '01_create_ratings_v4.sql'), 'utf8');
const migrate = fs.readFileSync(path.join(sqlFolder, '04_add_annotator_profile.sql'), 'utf8');
const sourceMigrate = fs.readFileSync(path.join(sqlFolder, '05_add_source_verification.sql'), 'utf8');
const oldSetup = setup.replace(/created_at    timestamptz not null default now\(\),[\s\S]*?\n\);/,
  'created_at    timestamptz not null default now()\n);');
const columnNames = database => query(database, `select string_agg(column_name, ',' order by ordinal_position)
  from information_schema.columns where table_schema='public' and table_name='ratings_v4_final';`);
const insert = (database, uid, profile = '') => query(database, `insert into public.ratings_v4
  (session_id, annotator, qa_uid, dataset, video, approach, question_text, answer_text${profile ? ', annotator_group, annotator_other, dementia_care_experience' : ''})
  values ('offline-session', 'Offline test', '${uid}', 'Master', 1, 'SingleAgent', 'Question', 'Answer'${profile ? ', '+profile : ''});`);

try {
  command('initdb', ['-D', dataDir, '--no-locale', '--encoding=UTF8', '--auth-local=trust', '--auth-host=reject']);
  command('pg_ctl', ['-D', dataDir, '-l', path.join(temporary, 'postgres.log'),
    '-o', `-F -h '' -k ${temporary} -p 55439`, '-w', 'start']);
  started = true;
  query('postgres', 'create role anon; create role authenticated; create database fresh; create database upgraded;');
  query('fresh', setup);
  query('upgraded', oldSetup);
  insert('upgraded', 'legacy-pair');
  insert('upgraded', 'legacy-pair');
  const snapshotQuery = `select jsonb_agg(to_jsonb(r) - 'source_unverifiable' - 'annotator_group' - 'annotator_other'
    - 'dementia_care_experience' order by id)::text from public.ratings_v4 r;`;
  const originalRows = query('upgraded', snapshotQuery);
  query('upgraded', migrate);
  query('upgraded', migrate);
  query('upgraded', sourceMigrate);
  query('upgraded', sourceMigrate);
  assert.equal(query('upgraded', snapshotQuery), originalRows, 'migration preserves every original field and row');
  assert.equal(query('upgraded', 'select count(*) from public.ratings_v4;'), '2');
  assert.equal(query('upgraded', 'select count(*) from public.ratings_v4_final;'), '1');
  assert.equal(query('upgraded', 'select id from public.ratings_v4_final;'), '2', 'newest-row ordering stays intact');
  assert.equal(columnNames('fresh'), columnNames('upgraded'), 'fresh and upgraded view column order matches');
  assert.ok(columnNames('upgraded').endsWith('annotator_group,annotator_other,dementia_care_experience,source_unverifiable'));
  assert.throws(() => query('fresh', setup), 'fresh setup still refuses to overwrite existing objects');
  assert.equal(query('fresh', 'select count(*) from public.ratings_v4;'), '0');

  for (const database of ['fresh', 'upgraded']) {
    for (const group of ['clinical', 'care', 'family', 'student', 'other']) {
      for (const experience of ['none', 'less_one_year', 'one_to_three_years', 'more_three_years']) {
        insert(database, group+'-'+experience, `'${group}', ${group === 'other' ? "'Research volunteer'" : 'null'}, '${experience}'`);
      }
    }
    for (const badProfile of [
      "'doctor', null, 'none'", "'student', null, 'unknown'", "'other', null, 'none'",
      "'other', '   ', 'none'", "'clinical', 'Stale text', 'none'", "'family', null, null",
      "null, null, 'none'", "null, 'Unexpected text', null", "'other', repeat('x',201), 'none'",
    ]) assert.throws(() => insert(database, 'invalid', badProfile), badProfile);
    insert(database, 'legacy-null');
    query(database, `insert into public.ratings_v4
      (session_id,annotator,qa_uid,dataset,video,approach,question_text,answer_text,source_unverifiable,qna_clarity_attribute)
      values ('source-test','Source test','unavailable','Master',1,'SingleAgent','Q','A',true,'Easy');`);
    assert.equal(query(database, `select source_unverifiable::text || '|' ||
      (qna_trustworthiness_attribute is null)::text from public.ratings_v4 where qa_uid='unavailable';`), 'true|true');
    for (const column of ['qna_trustworthiness_attribute', 'qna_trustworthiness_issue', 'qna_trustworthiness_binary']) {
      const value = column.endsWith('_attribute') ? 'Poor' : column.endsWith('_binary') ? 'No' : 'Contradiction';
      assert.throws(() => query(database, `update public.ratings_v4 set ${column}='${value}' where qa_uid='unavailable';`),
        'a source-access failure cannot store a Trustworthiness assessment');
    }
    const permissions = query(database, `select
      has_table_privilege('anon','public.ratings_v4','insert'),
      has_table_privilege('anon','public.ratings_v4','select'),
      has_table_privilege('anon','public.ratings_v4','update'),
      has_table_privilege('anon','public.ratings_v4','delete'),
      has_table_privilege('anon','public.ratings_v4_final','select');`);
    assert.equal(permissions, 't|f|f|f|f');
    assert.equal(query(database, `select count(*) from pg_policies where tablename='ratings_v4'
      and cmd='INSERT' and roles=array['anon']::name[];`), '1');
    assert.match(query(database, `select reloptions::text from pg_class where oid='public.ratings_v4_final'::regclass;`), /security_invoker=true/);
    query(database, fs.readFileSync(path.join(sqlFolder, '02_verify_ratings_v4.sql'), 'utf8'));
    query(database, fs.readFileSync(path.join(sqlFolder, '03_analysis_queries.sql'), 'utf8'));
    query(database, `set role anon; insert into public.ratings_v4
      (session_id,annotator,qa_uid,dataset,video,approach,question_text,answer_text,annotator_group,dementia_care_experience)
      values('anon-smoke','Offline anon','anon-pair','Master',1,'SingleAgent','Q','A','student','none'); reset role;`);
    assert.throws(() => query(database, 'set role anon; select * from public.ratings_v4;'));
  }
  console.log('PASS: fresh setup and repeated profile/source migrations, row preservation, latest-row view, profile and source constraints, insert-only permissions/RLS, verification and analysis queries.');
} finally {
  if (started) command('pg_ctl', ['-D', dataDir, '-m', 'fast', '-w', 'stop']);
  // Only this test's newly created cluster and logs are removed.
  if (path.dirname(temporary) === '/private/tmp' && path.basename(temporary).startsWith('medicalqa-v4-pg-'))
    fs.rmSync(temporary, {recursive: true, force: true});
}
