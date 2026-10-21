-- Run for an existing v4 database before deploying the source-access option.
-- Adds one nullable flag and refreshes the latest-row view. Existing rows and
-- permissions are preserved. Safe to rerun. New databases use script 01.

begin;

do $$
begin
    if to_regclass('public.ratings_v4') is null then
        raise exception 'ratings_v4 does not exist; use 01_create_ratings_v4.sql for a new database';
    end if;
end
$$;

alter table public.ratings_v4
    add column if not exists source_unverifiable boolean;

do $$
begin
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.ratings_v4'::regclass
                     and conname = 'ratings_v4_source_unverifiable_ck') then
        alter table public.ratings_v4
            add constraint ratings_v4_source_unverifiable_ck check (
                source_unverifiable is distinct from true
                or (qna_trustworthiness_attribute is null
                    and qna_trustworthiness_issue is null
                    and qna_trustworthiness_binary is null)
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
