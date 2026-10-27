-- Read-only: run first in the intended Supabase project to choose your path.
-- Does not query or display annotator names, descriptions, or ratings.
with objects(name) as (
    values ('study_settings_v5'), ('role_categories_v5'), ('version_pairs_v5'),
           ('assignments_v5'), ('ratings_v5'), ('assignment_status_v5'), ('ratings_v5_final')
), state as (
    select count(*) filter (where to_regclass('public.' || name) is not null) as found
    from objects
)
select case
    when found = 0 then 'NEW V5: run 01_create_v5.sql; load the agreed selection with 04, then run 02.'
    when found <> 7 then 'INCOMPLETE V5: inspect missing objects before making changes.'
    when to_regprocedure('public.claim_version_v5(text,text[],jsonb,text)') is not null
         and to_regclass('public.capacity_alerts_v5') is null
        then 'MULTI-ROLE V5 WITHOUT BATCHES: run 05_upgrade_v5_multi_role.sql (3/2 quotas, batches, capacity alert), then 02_verify_v5.sql.'
    when to_regprocedure('public.claim_version_v5(text,text[],jsonb,text)') is not null
        then 'MULTI-ROLE V5: run 02_verify_v5.sql; 05 is also safe to rerun to synchronize this release.'
    when to_regprocedure('public.claim_version_v5(text,text,text,text)') is not null
        then 'OLD SINGLE-ROLE V5: run 05_upgrade_v5_multi_role.sql, then 02_verify_v5.sql. Do not rerun 01 or 04.'
    else 'UNRECOGNIZED V5: inspect the schema before making changes.'
end as next_step, found as objects_found
from state;

select table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in ('assignments_v5', 'ratings_v5', 'assignment_status_v5', 'ratings_v5_final')
  and column_name in ('annotator_group', 'annotator_other', 'annotator_groups', 'annotator_role_details')
order by table_name, ordinal_position;

select p.oid::regprocedure as claim_function, pg_get_function_arguments(p.oid) as arguments
from pg_proc p
where p.pronamespace = 'public'::regnamespace and p.proname = 'claim_version_v5';
