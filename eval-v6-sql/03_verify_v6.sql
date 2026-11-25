-- Read-only verification after 01_create_v6.sql and 02_load_version_pairs_v6.sql.

-- All eight objects must be non-null.
select
    to_regclass('public.study_settings_v6')    as settings,
    to_regclass('public.role_categories_v6')   as role_categories,
    to_regclass('public.version_pairs_v6')     as version_pairs,
    to_regclass('public.assignments_v6')       as assignments,
    to_regclass('public.ratings_v6')           as raw_ratings,
    to_regclass('public.ratings_v6_final')     as final_view,
    to_regclass('public.assignment_status_v6') as assignment_status,
    to_regclass('public.capacity_alerts_v6')   as capacity_alerts;

-- Expected: batches of 5 versions, 5 Q&As each for the pilot, 3 medical and 2 non-medical
-- base slots per version, a 48-hour lease, and the alert at 15 people.
select * from public.study_settings_v6;

-- Expected: healthcare and student are medical; family and other are not.
select annotator_group, is_medical
from public.role_categories_v6
order by is_medical desc, annotator_group;

-- Expected: the v6 claim function takes multiple roles.
select
    to_regprocedure('public.claim_version_v6(text,text[],jsonb,text)') is not null as multi_role_claim_exists;

-- Expected: eight rows: groups (ARRAY) and role details (jsonb) on both tables
-- and both views.
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name in ('assignments_v6', 'ratings_v6', 'assignment_status_v6', 'ratings_v6_final')
  and column_name in ('annotator_groups', 'annotator_role_details')
order by table_name, column_name;

-- Expected: zero invalid assignments and zero invalid rating profiles.
select
    (select count(*) from public.assignments_v6
     where annotator_groups is null
        or not (cardinality(annotator_groups) between 1 and 4
                and annotator_groups <@ array['healthcare','student','family','other']::text[])
        or public.role_details_ok_v6(annotator_role_details, annotator_groups) is not true)
        as invalid_assignments,
    (select count(*) from public.ratings_v6
     where annotator_groups is not null and
        (not (cardinality(annotator_groups) between 1 and 4
              and annotator_groups <@ array['healthcare','student','family','other']::text[])
         or (annotator_role_details is not null
             and public.role_details_ok_v6(annotator_role_details, annotator_groups) is not true)))
        as invalid_rating_profiles;

-- Expected: five versions with 5 distinct pairs each for Phase 0: 25 total.
-- Qwen3.5-9B v3: 20 normal outputs and 5 deliberately negative outputs.
select eval_version, count(*) as qnas, count(distinct (dataset, video)) as videos
from public.version_pairs_v6
group by eval_version
order by eval_version;
select approach, count(*)
from public.version_pairs_v6
group by approach
order by approach;

-- Expected: zero rows. Every loaded group must match the configured size and
-- have contiguous positions. This also works for later same-sized batches.
select vp.eval_version
from public.version_pairs_v6 vp cross join public.study_settings_v6 st
group by vp.eval_version, st.pairs_per_version
having count(*) <> st.pairs_per_version
    or min(vp.position) <> 1 or max(vp.position) <> st.pairs_per_version;

-- Both triggers on the ratings table must be enabled.
select tgname, tgenabled
from pg_trigger
where tgrelid = 'public.ratings_v6'::regclass and not tgisinternal
order by tgname;

-- Exactly one enabled INSERT policy for anon is expected.
select schemaname, tablename, policyname, roles, cmd, with_check
from pg_policies
where schemaname = 'public'
  and tablename = 'ratings_v6'
order by policyname;

-- Expected: rating insert, assignment claim, and sequence usage are true.
select
    has_table_privilege('anon', 'public.ratings_v6', 'insert')        as anon_can_insert_ratings,
    has_table_privilege('anon', 'public.ratings_v6', 'select')        as anon_can_read_ratings,
    has_table_privilege('anon', 'public.ratings_v6', 'update')        as anon_can_update_ratings,
    has_table_privilege('anon', 'public.ratings_v6', 'delete')        as anon_can_delete_ratings,
    has_table_privilege('anon', 'public.ratings_v6_final', 'select')  as anon_can_read_final_view,
    has_table_privilege('anon', 'public.assignments_v6', 'select')    as anon_can_read_assignments,
    has_table_privilege('anon', 'public.assignments_v6', 'insert')    as anon_can_insert_assignments,
    has_table_privilege('anon', 'public.version_pairs_v6', 'select')  as anon_can_read_version_pairs,
    has_function_privilege('anon', 'public.claim_version_v6(text,text[],jsonb,text)', 'execute')
        as anon_can_claim,
    has_sequence_privilege('anon', 'public.ratings_v6_id_seq', 'usage') as anon_can_allocate_rating_id,
    has_function_privilege('anon', 'public.version_status_v6()', 'execute')
        as anon_can_read_status,
    has_function_privilege('anon', 'public.batch_status_v6()', 'execute')
        as anon_can_read_batch_status,
    has_table_privilege('anon', 'public.capacity_alerts_v6', 'select')
        as anon_can_read_alerts;

-- Slots held per batch and any capacity alert raised so far.
select * from public.batch_status_v6();

-- The two dependency queries should return zero rows.
-- (a) Recorded dependencies — views, foreign keys, triggers, sequences,
--     functions — from a v6 object to anything in public that is not v6.
with deps as (
    select
        coalesce(rw.ev_class::regclass::text, tg.tgrelid::regclass::text,
                 co.conrelid::regclass::text, cl.oid::regclass::text,
                 pr.oid::regprocedure::text) as v6_object,
        coalesce(rc.oid::regclass::text, rp.oid::regprocedure::text) as depends_on,
        coalesce(rc.relnamespace, rp.pronamespace) as depends_on_schema
    from pg_depend d
    left join pg_rewrite rw    on d.classid = 'pg_rewrite'::regclass    and rw.oid = d.objid
    left join pg_trigger tg    on d.classid = 'pg_trigger'::regclass    and tg.oid = d.objid
    left join pg_constraint co on d.classid = 'pg_constraint'::regclass and co.oid = d.objid
    left join pg_class cl      on d.classid = 'pg_class'::regclass      and cl.oid = d.objid
    left join pg_proc pr       on d.classid = 'pg_proc'::regclass       and pr.oid = d.objid
    left join pg_class rc      on d.refclassid = 'pg_class'::regclass   and rc.oid = d.refobjid
    left join pg_proc rp       on d.refclassid = 'pg_proc'::regclass    and rp.oid = d.refobjid
)
select distinct v6_object, depends_on
from deps
where v6_object like '%\_v6%'
  and depends_on is not null
  and depends_on not like '%\_v6%'
  and depends_on_schema = 'public'::regnamespace
order by v6_object, depends_on;

-- (b) Function bodies are not tracked by (a), so search them for the names of
--     the earlier ratings tables.
select p.oid::regprocedure as v6_function
from pg_proc p
where p.pronamespace = 'public'::regnamespace
  and p.proname like '%\_v6%'
  and p.prosrc ~* '\m(ratings|[a-z_]+_v[1-5](_[a-z_]+)?)\M';

-- A new v6 study has zero assignments and ratings before the smoke test.
select
    (select count(*) from public.assignments_v6)   as assignments,
    (select count(*) from public.ratings_v6)       as raw_ratings,
    (select count(*) from public.ratings_v6_final) as final_ratings;
