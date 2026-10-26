-- Upgrade an EXISTING MedicalQA v5 database to the current multi-role UI.
-- Run the whole file in the Supabase SQL Editor as the database owner.
-- For a NEW v5 database use 01_create_v5.sql instead. Do not run 04 again.
--
-- Tested starting points: original single-role v5 (a37c12a) and current v5.
-- Safe to rerun. One transaction: an error rolls back the entire upgrade.
-- No ratings, assignments, version pairs, or study settings are deleted.
-- Legacy profile columns are retained for audit. Historical is_medical values
-- stay as originally routed; new healthcare/student signups count as medical.
-- Refresh/redeploy the latest v5 UI after this upgrade (old claim RPC removed).

begin;
set local lock_timeout = '10s';
set local statement_timeout = '120s';
select pg_advisory_xact_lock(hashtext('public.claim_version_v5'));

do $$
declare
    object_name text;
begin
    foreach object_name in array array['study_settings_v5', 'role_categories_v5',
        'version_pairs_v5', 'assignments_v5', 'ratings_v5',
        'assignment_status_v5', 'ratings_v5_final'] loop
        if to_regclass('public.' || object_name) is null then
            raise exception 'Missing %. For a new v5 database run 01_create_v5.sql; incomplete setups require inspection.', object_name;
        end if;
    end loop;
    if exists (select 1 from public.role_categories_v5
               where annotator_group not in ('clinical', 'care', 'healthcare', 'student', 'family', 'other')) then
        raise exception 'Unknown v5 role categories; upgrade aborted without changes';
    end if;
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'assignments_v5'
                     and column_name in ('annotator_group', 'annotator_groups')) then
        raise exception 'Unrecognized v5 profile schema; upgrade aborted without changes';
    end if;
end
$$;

lock table public.assignments_v5, public.ratings_v5, public.role_categories_v5
    in access exclusive mode;

-- Retain the old single-role columns and their original values, but new
-- assignments do not have to populate them. Detach the legacy FK before
-- replacing its two obsolete category codes in the lookup table.
alter table public.assignments_v5
    drop constraint if exists assignments_v5_annotator_group_fkey;
do $$
begin
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'assignments_v5'
                 and column_name = 'annotator_group') then
        alter table public.assignments_v5 alter column annotator_group drop not null;
    end if;
end
$$;

alter table public.assignments_v5
    add column if not exists annotator_groups text[],
    add column if not exists annotator_role_details jsonb not null default '{}'::jsonb;
alter table public.ratings_v5
    add column if not exists annotator_groups text[],
    add column if not exists annotator_role_details jsonb;

-- JSON access works on both old and new row types. Only missing multi-role
-- profiles are backfilled. Original singular columns remain byte-for-byte.
-- The mapping matches the UI's LEGACY_GROUP mapping.
update public.assignments_v5 a
set annotator_groups = array[case to_jsonb(a)->>'annotator_group'
        when 'clinical' then 'healthcare' when 'care' then 'healthcare'
        else to_jsonb(a)->>'annotator_group' end],
    annotator_role_details = case
        when to_jsonb(a)->>'annotator_group' = 'other'
             and nullif(btrim(to_jsonb(a)->>'annotator_other'), '') is not null
        then jsonb_build_object('other', btrim(to_jsonb(a)->>'annotator_other'))
        else '{}'::jsonb end
where a.annotator_groups is null and to_jsonb(a)->>'annotator_group' is not null;

update public.ratings_v5 r
set annotator_groups = array[case to_jsonb(r)->>'annotator_group'
        when 'clinical' then 'healthcare' when 'care' then 'healthcare'
        else to_jsonb(r)->>'annotator_group' end],
    annotator_role_details = case
        when to_jsonb(r)->>'annotator_group' = 'other'
             and nullif(btrim(to_jsonb(r)->>'annotator_other'), '') is not null
        then jsonb_build_object('other', btrim(to_jsonb(r)->>'annotator_other'))
        else '{}'::jsonb end
where r.annotator_groups is null and to_jsonb(r)->>'annotator_group' is not null;

alter table public.assignments_v5
    alter column annotator_groups set not null,
    alter column annotator_role_details set default '{}'::jsonb,
    alter column annotator_role_details set not null;

alter table public.role_categories_v5 drop constraint if exists role_categories_v5_group_ck;
delete from public.role_categories_v5 where annotator_group in ('clinical', 'care');
insert into public.role_categories_v5 (annotator_group, is_medical) values
    ('healthcare', true), ('student', true), ('family', false), ('other', false)
on conflict (annotator_group) do update set is_medical = excluded.is_medical;
alter table public.role_categories_v5 add constraint role_categories_v5_group_ck
    check (annotator_group in ('healthcare', 'student', 'family', 'other'));

create or replace function public.role_details_ok_v5(details jsonb, groups text[])
returns boolean
language sql
immutable
as $$
    select jsonb_typeof(details) = 'object'
       and not exists (
           select 1 from jsonb_each(details) d
           where not d.key = any(groups)
              or jsonb_typeof(d.value) <> 'string'
              or btrim(d.value #>> '{}') = ''
              or char_length(d.value #>> '{}') > 200
       )
$$;

-- Apply the same profile constraints as current 01_create_v5.sql. Existing
-- data is validated now; malformed data aborts the transaction, never erased.
alter table public.assignments_v5
    drop constraint if exists assignments_v5_groups_ck,
    drop constraint if exists assignments_v5_role_details_ck,
    add constraint assignments_v5_groups_ck check (
        cardinality(annotator_groups) between 1 and 4
        and annotator_groups <@ array['healthcare', 'student', 'family', 'other']::text[]),
    add constraint assignments_v5_role_details_ck check (
        public.role_details_ok_v5(annotator_role_details, annotator_groups));
alter table public.ratings_v5
    drop constraint if exists ratings_v5_annotator_groups_ck,
    drop constraint if exists ratings_v5_role_details_ck,
    drop constraint if exists ratings_v5_profile_complete_ck,
    add constraint ratings_v5_annotator_groups_ck check (
        annotator_groups is null or (cardinality(annotator_groups) between 1 and 4
        and annotator_groups <@ array['healthcare', 'student', 'family', 'other']::text[])),
    add constraint ratings_v5_role_details_ck check (
        annotator_role_details is null
        or public.role_details_ok_v5(annotator_role_details, annotator_groups)),
    add constraint ratings_v5_profile_complete_ck check (
        (annotator_groups is null and dementia_care_experience is null and annotator_role_details is null)
        or (annotator_groups is not null and dementia_care_experience is not null));

-- Source access remains independent of the Trustworthiness score.
alter table public.ratings_v5 add column if not exists source_unverifiable boolean;
alter table public.ratings_v5
    drop constraint if exists ratings_v5_source_unverifiable_ck,
    add constraint ratings_v5_source_unverifiable_ck check (
        source_unverifiable is distinct from true
        or (qna_trustworthiness_attribute is null and qna_trustworthiness_issue is null
            and qna_trustworthiness_binary is null));

-- Remove the obsolete overload so old signup requests cannot keep creating
-- single-role assignments. No CASCADE: unexpected dependencies abort safely.
drop function if exists public.claim_version_v5(text, text, text, text);

create or replace function public.assignment_holds_slot_v5(a public.assignments_v5)
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
    select a.completed_at is not null or now() < a.expires_at
$$;

create or replace function public.ratings_v5_before_insert()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    a public.assignments_v5;
    vp public.version_pairs_v5;
begin
    select * into a from public.assignments_v5 where id = new.assignment_id;
    if not found then
        raise exception 'unknown v5 assignment' using errcode = '23503';
    end if;
    select * into vp from public.version_pairs_v5
    where eval_version = a.eval_version and qa_uid = new.qa_uid;
    if not found then
        raise exception 'Q&A % is not part of version %', new.qa_uid, a.eval_version
            using errcode = '23514';
    end if;
    new.eval_version := a.eval_version;
    new.session_id := a.id::text;
    new.annotator := a.annotator;
    new.annotator_groups := a.annotator_groups;
    new.annotator_role_details := a.annotator_role_details;
    -- Keep retained legacy fields tied to the assignment, never client input.
    -- jsonb_populate_record ignores these keys on a fresh multi-role schema.
    new := jsonb_populate_record(new, jsonb_build_object(
        'annotator_group', to_jsonb(a)->'annotator_group',
        'annotator_other', to_jsonb(a)->'annotator_other'));
    new.dementia_care_experience := a.dementia_care_experience;
    new.dataset := vp.dataset;
    new.video := vp.video;
    new.approach := vp.approach;
    new.late := a.completed_at is null and now() >= a.expires_at;
    new.created_at := now();
    return new;
end
$$;

create or replace function public.ratings_v5_after_insert()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
    update public.assignments_v5 a
    set completed_at = now()
    where a.id = new.assignment_id
      and a.completed_at is null
      and (select count(distinct r.qa_uid) from public.ratings_v5 r
           where r.assignment_id = a.id)
          >= (select count(*) from public.version_pairs_v5 vp
              where vp.eval_version = a.eval_version);
    return null;
end
$$;

create or replace function public.claim_version_v5(
    p_name text,
    p_groups text[],
    p_role_details jsonb,
    p_experience text
)
returns table (assignment_id uuid, eval_version integer, expires_at timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
#variable_conflict use_column
declare
    st public.study_settings_v5;
    medical boolean;
    roles text[];
    quota integer;
    chosen integer;
    how text;
    details jsonb;
    new_id uuid;
    new_expiry timestamptz;
begin
    -- One claim at a time across the whole study; released at commit.
    perform pg_advisory_xact_lock(hashtext('public.claim_version_v5'));

    select * into st from public.study_settings_v5;
    if (select count(*) from public.version_pairs_v5) <> st.n_versions * st.pairs_per_version then
        raise exception 'v5 Q&A versions are not loaded yet' using errcode = '55000';
    end if;
    if p_name is null or btrim(p_name) = '' then
        raise exception 'a name is required' using errcode = '22023';
    end if;
    -- Known roles only, each once, in the page's order.
    if exists (select 1 from unnest(coalesce(p_groups, '{}')) g
               where g is null or g not in (select annotator_group from public.role_categories_v5)) then
        raise exception 'unknown role in %', p_groups using errcode = '22023';
    end if;
    select array_agg(rc.annotator_group order by array_position(
               array['healthcare', 'student', 'family', 'other'], rc.annotator_group)),
           bool_or(rc.is_medical)
    into roles, medical
    from public.role_categories_v5 rc
    where rc.annotator_group = any(p_groups);
    if roles is null then
        raise exception 'at least one role is required' using errcode = '22023';
    end if;
    -- Optional descriptions: kept for ticked roles only, trimmed, blanks dropped.
    if p_role_details is not null and jsonb_typeof(p_role_details) <> 'object' then
        raise exception 'role details must be a JSON object' using errcode = '22023';
    end if;
    select coalesce(jsonb_object_agg(d.key, btrim(d.value #>> '{}')), '{}'::jsonb) into details
    from jsonb_each(coalesce(p_role_details, '{}'::jsonb)) d
    where d.key = any(roles) and jsonb_typeof(d.value) = 'string' and btrim(d.value #>> '{}') <> '';
    if not public.role_details_ok_v5(details, roles) then
        raise exception 'each role description must be at most 200 characters' using errcode = '22023';
    end if;
    quota := case when medical then st.base_medical else st.base_non_medical end;

    with versions as (
        select generate_series(1, st.n_versions) as v
    ), held as (
        select a.eval_version, a.is_medical
        from public.assignments_v5 a
        where public.assignment_holds_slot_v5(a)
    ), counts as (
        select v.v,
               count(h.eval_version) filter (where h.is_medical = medical) as same_category,
               count(h.eval_version) as total
        from versions v left join held h on h.eval_version = v.v
        group by v.v
    )
    select c.v into chosen
    from counts c where c.same_category < quota
    order by c.v limit 1;

    if chosen is not null then
        how := 'base';
    else
        how := 'balance';
        with versions as (
            select generate_series(1, st.n_versions) as v
        ), held as (
            select a.eval_version
            from public.assignments_v5 a
            where public.assignment_holds_slot_v5(a)
        )
        select v.v into chosen
        from versions v left join held h on h.eval_version = v.v
        group by v.v
        order by count(h.eval_version), v.v
        limit 1;
    end if;

    new_expiry := now() + st.lease;
    insert into public.assignments_v5 (
        annotator, annotator_groups, annotator_role_details, dementia_care_experience,
        is_medical, eval_version, routed_by, expires_at
    ) values (
        btrim(p_name), roles, details, p_experience,
        medical, chosen, how, new_expiry
    ) returning id into new_id;

    return query select new_id, chosen, new_expiry;
end
$$;

-- Append new profile columns to the views without dropping them or changing
-- their existing column order (including status and qnas_rated). This preserves
-- dependent researcher views and grants. PostgreSQL CREATE OR REPLACE VIEW:
-- https://www.postgresql.org/docs/current/sql-createview.html
-- Historical single-role columns stay available in upgraded databases.
do $$
declare
    projection text;
    extra_columns text;
begin
    select string_agg(case attname
        when 'status' then $expr$case when a.completed_at is not null then 'completed'
            when now() < a.expires_at then 'active' else 'expired' end as status$expr$
        when 'qnas_rated' then '(select count(distinct r.qa_uid) from public.ratings_v5 r where r.assignment_id = a.id) as qnas_rated'
        else format('a.%I', attname) end, ', ' order by attnum)
    into projection
    from pg_attribute where attrelid = 'public.assignment_status_v5'::regclass
      and attnum > 0 and not attisdropped;
    select string_agg(format('a.%I', a.attname), ', ' order by a.attnum)
    into extra_columns
    from pg_attribute a
    where a.attrelid = 'public.assignments_v5'::regclass
      and a.attnum > 0 and not a.attisdropped
      and not exists (select 1 from pg_attribute v
                      where v.attrelid = 'public.assignment_status_v5'::regclass
                        and v.attnum > 0 and not v.attisdropped and v.attname = a.attname);
    execute 'create or replace view public.assignment_status_v5 with (security_invoker = true) as select '
        || projection || case when extra_columns is null then '' else ', ' || extra_columns end
        || ' from public.assignments_v5 a';
end
$$;

create or replace view public.ratings_v5_final
with (security_invoker = true) as
select distinct on (assignment_id, qa_uid) *
from public.ratings_v5
order by assignment_id, qa_uid, created_at desc, id desc;

-- Restore the two standard rating triggers and browser permissions. The policy
-- is replaced by name only; no earlier study or researcher grant is changed.
drop trigger if exists ratings_v5_check_assignment on public.ratings_v5;
create trigger ratings_v5_check_assignment before insert on public.ratings_v5
    for each row execute function public.ratings_v5_before_insert();
drop trigger if exists ratings_v5_mark_complete on public.ratings_v5;
create trigger ratings_v5_mark_complete after insert on public.ratings_v5
    for each row execute function public.ratings_v5_after_insert();

create or replace function public.version_status_v5()
returns table (
    eval_version integer,
    medical_held bigint,
    non_medical_held bigint,
    total_held bigint,
    completed bigint,
    active bigint,
    expired bigint
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
    select v.v,
           count(*) filter (where s.status <> 'expired' and s.is_medical),
           count(*) filter (where s.status <> 'expired' and not s.is_medical),
           count(*) filter (where s.status <> 'expired'),
           count(*) filter (where s.status = 'completed'),
           count(*) filter (where s.status = 'active'),
           count(*) filter (where s.status = 'expired')
    from generate_series(1, 5) as v(v)
    left join public.assignment_status_v5 s on s.eval_version = v.v
    group by v.v
    order by v.v
$$;

alter table public.study_settings_v5 enable row level security;
alter table public.role_categories_v5 enable row level security;
alter table public.version_pairs_v5 enable row level security;
alter table public.assignments_v5 enable row level security;
alter table public.ratings_v5 enable row level security;

revoke all on public.study_settings_v5, public.role_categories_v5,
    public.version_pairs_v5, public.assignments_v5, public.ratings_v5,
    public.ratings_v5_final, public.assignment_status_v5
    from anon, authenticated;
revoke all on function public.claim_version_v5(text, text[], jsonb, text),
    public.version_status_v5(),
    public.assignment_holds_slot_v5(public.assignments_v5),
    public.ratings_v5_before_insert(),
    public.ratings_v5_after_insert(),
    public.role_details_ok_v5(jsonb, text[])
    from public, anon, authenticated;

grant insert on public.ratings_v5 to anon;
grant usage, select on sequence public.ratings_v5_id_seq to anon;
grant execute on function public.claim_version_v5(text, text[], jsonb, text) to anon;
-- The role-details check runs inside the ratings_v5 constraint on every insert
-- by anon. It reads only its two arguments, so executing it exposes nothing.
grant execute on function public.role_details_ok_v5(jsonb, text[]) to anon;

drop policy if exists ratings_v5_anon_insert on public.ratings_v5;
create policy ratings_v5_anon_insert
    on public.ratings_v5
    for insert
    to anon
    with check (study_version = 'v5');

notify pgrst, 'reload schema';
commit;

-- Success summary. Follow with 02_verify_v5.sql for the full read-only checks.
select 'v5 multi-role upgrade complete' as result,
       (select count(*) from public.assignments_v5) as assignments_preserved,
       (select count(*) from public.ratings_v5) as ratings_preserved,
       (select count(*) from public.version_pairs_v5) as version_pairs_preserved;
