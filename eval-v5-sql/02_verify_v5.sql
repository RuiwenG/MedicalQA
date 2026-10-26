-- Read-only verification after new setup (01 and 04) or an existing-v5 upgrade (05).

-- All seven objects must be non-null.
select
    to_regclass('public.study_settings_v5')    as settings,
    to_regclass('public.role_categories_v5')   as role_categories,
    to_regclass('public.version_pairs_v5')     as version_pairs,
    to_regclass('public.assignments_v5')       as assignments,
    to_regclass('public.ratings_v5')           as raw_ratings,
    to_regclass('public.ratings_v5_final')     as final_view,
    to_regclass('public.assignment_status_v5') as assignment_status;

-- Expected: 5 versions, 20 Q&As each, 4 medical and 2 non-medical base slots,
-- and a 48-hour lease.
select * from public.study_settings_v5;

-- Expected: healthcare and student are medical; family and other are not.
select annotator_group, is_medical
from public.role_categories_v5
order by is_medical desc, annotator_group;

-- Expected: the new claim function exists and the obsolete overload is absent.
select
    to_regprocedure('public.claim_version_v5(text,text[],jsonb,text)') is not null as multi_role_claim_exists,
    to_regprocedure('public.claim_version_v5(text,text,text,text)') is null as old_claim_removed;

-- Expected: eight rows: groups (ARRAY) and role details (jsonb) on both tables
-- and both views. Upgraded databases may also retain singular legacy columns.
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public'
  and table_name in ('assignments_v5', 'ratings_v5', 'assignment_status_v5', 'ratings_v5_final')
  and column_name in ('annotator_groups', 'annotator_role_details')
order by table_name, column_name;

-- Expected: zero invalid assignments and zero invalid rating profiles.
select
    (select count(*) from public.assignments_v5
     where annotator_groups is null
        or not (cardinality(annotator_groups) between 1 and 4
                and annotator_groups <@ array['healthcare','student','family','other']::text[])
        or public.role_details_ok_v5(annotator_role_details, annotator_groups) is not true)
        as invalid_assignments,
    (select count(*) from public.ratings_v5
     where annotator_groups is not null and
        (not (cardinality(annotator_groups) between 1 and 4
              and annotator_groups <@ array['healthcare','student','family','other']::text[])
         or (annotator_role_details is not null
             and public.role_details_ok_v5(annotator_role_details, annotator_groups) is not true)))
        as invalid_rating_profiles;

-- Expected: five rows of 20, 100 distinct uids, Qwen3-14B approaches only.
select eval_version, count(*) as qnas, count(distinct (dataset, video)) as videos
from public.version_pairs_v5
group by eval_version
order by eval_version;
select approach, count(*)
from public.version_pairs_v5
group by approach
order by approach;

-- Both triggers on the ratings table must be enabled.
select tgname, tgenabled
from pg_trigger
where tgrelid = 'public.ratings_v5'::regclass and not tgisinternal
order by tgname;

-- Exactly one enabled INSERT policy for anon is expected.
select schemaname, tablename, policyname, roles, cmd, with_check
from pg_policies
where schemaname = 'public'
  and tablename = 'ratings_v5'
order by policyname;

-- Expected: insert=t, claim=t; every other value f.
select
    has_table_privilege('anon', 'public.ratings_v5', 'insert')        as anon_can_insert_ratings,
    has_table_privilege('anon', 'public.ratings_v5', 'select')        as anon_can_read_ratings,
    has_table_privilege('anon', 'public.ratings_v5', 'update')        as anon_can_update_ratings,
    has_table_privilege('anon', 'public.ratings_v5', 'delete')        as anon_can_delete_ratings,
    has_table_privilege('anon', 'public.ratings_v5_final', 'select')  as anon_can_read_final_view,
    has_table_privilege('anon', 'public.assignments_v5', 'select')    as anon_can_read_assignments,
    has_table_privilege('anon', 'public.assignments_v5', 'insert')    as anon_can_insert_assignments,
    has_table_privilege('anon', 'public.version_pairs_v5', 'select')  as anon_can_read_version_pairs,
    has_function_privilege('anon', 'public.claim_version_v5(text,text[],jsonb,text)', 'execute')
        as anon_can_claim,
    has_function_privilege('anon', 'public.version_status_v5()', 'execute')
        as anon_can_read_status;

-- Separation from earlier studies. v5 must neither depend on nor modify any
-- v1-v4 object (ratings, ratings_v3, ratings_v4, ratings_v4_final, ...).
-- Expected: no rows from either query.
-- (a) Recorded dependencies — views, foreign keys, triggers, sequences,
--     functions — from a v5 object to anything in public that is not v5.
with deps as (
    select
        coalesce(rw.ev_class::regclass::text, tg.tgrelid::regclass::text,
                 co.conrelid::regclass::text, cl.oid::regclass::text,
                 pr.oid::regprocedure::text) as v5_object,
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
select distinct v5_object, depends_on
from deps
where v5_object like '%\_v5%'
  and depends_on is not null
  and depends_on not like '%\_v5%'
  and depends_on_schema = 'public'::regnamespace
order by v5_object, depends_on;

-- (b) Function bodies are not tracked by (a), so search them for the names of
--     the earlier ratings tables.
select p.oid::regprocedure as v5_function
from pg_proc p
where p.pronamespace = 'public'::regnamespace
  and p.proname like '%\_v5%'
  and p.prosrc ~* '\m(ratings|ratings_v[1-4]|ratings_v[1-4]_final)\M';

-- Existing studies retain these counts through the upgrade. Do not delete
-- collected data. A new, unused study has zero assignments and ratings.
select
    (select count(*) from public.assignments_v5)   as assignments,
    (select count(*) from public.ratings_v5)       as raw_ratings,
    (select count(*) from public.ratings_v5_final) as final_ratings;
