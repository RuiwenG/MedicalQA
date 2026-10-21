-- Read-only verification after script 01, or scripts 04 and 05 on an existing
-- database that needs the profile and source-access updates.

-- Both values must be non-null.
select
    to_regclass('public.ratings_v4') as raw_table,
    to_regclass('public.ratings_v4_final') as final_view;

-- Confirm the stored schema and ordering.
select ordinal_position, column_name, data_type, is_nullable, column_default
from information_schema.columns
where table_schema = 'public'
  and table_name = 'ratings_v4'
order by ordinal_position;

-- Expected: all three fields exist in both the raw table and final view.
select table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in ('ratings_v4', 'ratings_v4_final')
  and column_name in (
      'annotator_group', 'annotator_other', 'dementia_care_experience'
  )
order by table_name, column_name;

-- The source-access flag must also exist in both objects.
select table_name, column_name, data_type, is_nullable
from information_schema.columns
where table_schema = 'public'
  and table_name in ('ratings_v4', 'ratings_v4_final')
  and column_name = 'source_unverifiable'
order by table_name;

-- A source-access problem cannot also contain a Trustworthiness score.
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.ratings_v4'::regclass
  and conname = 'ratings_v4_source_unverifiable_ck';

-- Check allowed group/experience values and Others description requirements.
select conname, pg_get_constraintdef(oid) as definition
from pg_constraint
where conrelid = 'public.ratings_v4'::regclass
  and conname in (
      'ratings_v4_annotator_group_ck', 'ratings_v4_dementia_experience_ck',
      'ratings_v4_annotator_other_ck', 'ratings_v4_profile_complete_ck'
  )
order by conname;

-- Exactly one enabled INSERT policy for anon is expected.
select schemaname, tablename, policyname, roles, cmd, qual, with_check
from pg_policies
where schemaname = 'public'
  and tablename = 'ratings_v4'
order by policyname;

-- Expected: insert=t; select/update/delete=f; sequence usage=t.
select
    has_table_privilege('anon', 'public.ratings_v4', 'insert') as anon_can_insert,
    has_table_privilege('anon', 'public.ratings_v4', 'select') as anon_can_select,
    has_table_privilege('anon', 'public.ratings_v4', 'update') as anon_can_update,
    has_table_privilege('anon', 'public.ratings_v4', 'delete') as anon_can_delete,
    has_sequence_privilege('anon', 'public.ratings_v4_id_seq', 'usage')
        as anon_can_use_id_sequence,
    has_table_privilege('anon', 'public.ratings_v4_final', 'select')
        as anon_can_select_final_view;

-- Before launch both counts should be zero. After the smoke test, the final
-- count can be smaller than raw_count only when a Q&A has been re-rated.
select
    (select count(*) from public.ratings_v4) as raw_count,
    (select count(*) from public.ratings_v4_final) as final_count;

-- Run after a browser smoke test. The returned row must say study_version=v4.
select *
from public.ratings_v4
order by created_at desc, id desc
limit 1;
