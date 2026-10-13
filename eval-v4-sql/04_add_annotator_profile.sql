-- Run this script for an EXISTING v4 database before deploying the updated UI.
-- It adds three nullable columns and refreshes the latest-row view, without
-- changing any collected ratings or permissions. Safe to rerun.
-- For a brand-new database use 01_create_ratings_v4.sql instead.

begin;

do $$
begin
    if to_regclass('public.ratings_v4') is null then
        raise exception 'ratings_v4 does not exist; use 01_create_ratings_v4.sql for a new database';
    end if;
end
$$;

alter table public.ratings_v4
    add column if not exists annotator_group text,
    add column if not exists annotator_other text,
    add column if not exists dementia_care_experience text;

-- PostgreSQL expands SELECT * when a view is created. Append the columns to
-- the existing table, then replace the view so its previous column order and
-- newest-row semantics are preserved and the three new fields are available.
do $$
begin
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.ratings_v4'::regclass
                     and conname = 'ratings_v4_annotator_group_ck') then
        alter table public.ratings_v4
            add constraint ratings_v4_annotator_group_ck check (
                annotator_group is null or
                annotator_group in ('clinical', 'care', 'family', 'student', 'other')
            );
    end if;
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.ratings_v4'::regclass
                     and conname = 'ratings_v4_dementia_experience_ck') then
        alter table public.ratings_v4
            add constraint ratings_v4_dementia_experience_ck check (
                dementia_care_experience is null or
                dementia_care_experience in (
                    'none', 'less_one_year', 'one_to_three_years', 'more_three_years'
                )
            );
    end if;
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.ratings_v4'::regclass
                     and conname = 'ratings_v4_annotator_other_ck') then
        alter table public.ratings_v4
            add constraint ratings_v4_annotator_other_ck check (
                case when annotator_group = 'other' then
                    annotator_other is not null and btrim(annotator_other) <> ''
                    and char_length(annotator_other) <= 200
                else annotator_other is null end
            );
    end if;
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.ratings_v4'::regclass
                     and conname = 'ratings_v4_profile_complete_ck') then
        alter table public.ratings_v4
            add constraint ratings_v4_profile_complete_ck check (
                (annotator_group is null and dementia_care_experience is null
                 and annotator_other is null)
                or (annotator_group is not null and dementia_care_experience is not null)
            );
    end if;
end
$$;

create or replace view public.ratings_v4_final
with (security_invoker = true) as
select distinct on (session_id, qa_uid) *
from public.ratings_v4
order by session_id, qa_uid, created_at desc, id desc;

revoke all on public.ratings_v4_final from anon, authenticated;

notify pgrst, 'reload schema';

commit;
