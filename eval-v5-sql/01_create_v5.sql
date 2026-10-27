-- One-time, non-destructive setup for the MedicalQA human evaluation v5 site.
--
-- v5 hands each annotator one fixed version of 20 Q&As. Versions are loaded in
-- batches of five (1-5 first, then 6-10, ...). Which version is decided here, in the database, by claim_version_v5(): the browser cannot
-- see how many people already hold each version, and two people arriving at the
-- same moment must not both take the last slot.
--
-- An annotator ticks every role that applies. They count as medical when any of
-- their roles is medical in role_categories_v5 (healthcare professional or
-- medical student), so a nurse who is also a family caregiver is medical. Each
-- ticked role may carry an optional description (annotator_role_details).
--
-- Routing rule (one call at a time, under a transaction advisory lock), over
-- the loaded versions only:
--   1. Base fill. A medical annotator goes to the lowest-numbered version with
--      fewer than 3 medical annotators; a non-medical one to the lowest-numbered
--      version with fewer than 2 non-medical annotators. A batch of five versions
--      therefore takes 25 people: 15 medical and 10 non-medical.
--   2. Once every loaded version has its base slots for that category, the
--      annotator is an extra rater in the version with the fewest annotators in
--      total, lowest number on a tie: 5-5-5-5-5 -> 6-5-5-5-5 -> 6-6-5-5-5 ...
--      Loading the next batch opens new base slots, which then come first.
-- Capacity alert: once 15 people hold a slot in the latest loaded batch, a row
-- is added to capacity_alerts_v5 so the team prepares the next batch's Q&As.
-- An assignment holds its slot while it is completed or still inside its
-- 48-hour lease. A lease that runs out unfinished releases the slot; ratings sent
-- for it afterwards are still stored and marked late.
--
-- This script creates the v5 objects only and never drops anything. If any of
-- them already exists, the transaction stops so a rerun cannot erase data. The
-- Q&As are loaded separately: 04_load_version_pairs_v5.sql for versions 1-5,
-- then one 04_load_version_pairs_v5_batch<N>.sql per later batch.

begin;

do $$
begin
    if to_regclass('public.study_settings_v5') is not null
       or to_regclass('public.role_categories_v5') is not null
       or to_regclass('public.version_pairs_v5') is not null
       or to_regclass('public.assignments_v5') is not null
       or to_regclass('public.ratings_v5') is not null
       or to_regclass('public.ratings_v5_final') is not null
       or to_regclass('public.assignment_status_v5') is not null
       or to_regclass('public.capacity_alerts_v5') is not null then
        raise exception
            'a v5 evaluation object already exists; setup aborted without changes';
    end if;
end
$$;

-- One row of study parameters, so the quotas and lease are not buried in code.
create table public.study_settings_v5 (
    id                 boolean     primary key default true
        constraint study_settings_v5_single_row_ck check (id),
    -- Versions per batch: versions are loaded and alerted on five at a time.
    n_versions         integer     not null default 5
        constraint study_settings_v5_versions_ck check (n_versions = 5),
    pairs_per_version  integer     not null default 20
        constraint study_settings_v5_pairs_ck check (pairs_per_version > 0),
    base_medical       integer     not null default 3
        constraint study_settings_v5_base_medical_ck check (base_medical >= 0),
    base_non_medical   integer     not null default 2
        constraint study_settings_v5_base_non_medical_ck check (base_non_medical >= 0),
    lease              interval    not null default interval '48 hours'
        constraint study_settings_v5_lease_ck check (lease > interval '0'),
    -- People holding a slot in the latest batch that raise the capacity alert.
    alert_at_held      integer     not null default 15
        constraint study_settings_v5_alert_ck check (alert_at_held > 0)
);
insert into public.study_settings_v5 default values;

-- The single place that decides which roles count as medical background.
create table public.role_categories_v5 (
    annotator_group text    primary key
        constraint role_categories_v5_group_ck check (
            annotator_group in ('healthcare', 'student', 'family', 'other')
        ),
    is_medical      boolean not null
);
insert into public.role_categories_v5 (annotator_group, is_medical) values
    ('healthcare', true),
    ('student',    true),
    ('family',     false),
    ('other',      false);

-- Optional free-text descriptions of an annotator's roles, as a JSON object
-- {role: text}: only roles the annotator ticked, each 1-200 characters.
create function public.role_details_ok_v5(details jsonb, groups text[])
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

-- The 20 Q&As of each version, loaded a batch of five versions at a time by
-- the 04 scripts. Loaded versions are never changed.
create table public.version_pairs_v5 (
    eval_version integer not null
        constraint version_pairs_v5_version_ck check (eval_version >= 1),
    position     integer not null
        constraint version_pairs_v5_position_ck check (position between 1 and 20),
    qa_uid       text    not null unique
        constraint version_pairs_v5_qa_uid_ck check (btrim(qa_uid) <> ''),
    dataset      text    not null,
    video        integer not null
        constraint version_pairs_v5_video_ck check (video > 0),
    approach     text    not null,
    primary key (eval_version, position)
);

create table public.assignments_v5 (
    id                        uuid        primary key default gen_random_uuid(),
    annotator                 text        not null
        constraint assignments_v5_annotator_ck check (
            btrim(annotator) <> '' and char_length(annotator) <= 200
        ),
    -- Every role the annotator ticked, in the page's order.
    annotator_groups          text[]      not null
        constraint assignments_v5_groups_ck check (
            cardinality(annotator_groups) between 1 and 4
            and annotator_groups <@ array['healthcare', 'student', 'family', 'other']::text[]
        ),
    annotator_role_details    jsonb       not null default '{}'::jsonb,
    dementia_care_experience  text        not null
        constraint assignments_v5_dementia_experience_ck check (
            dementia_care_experience in (
                'none', 'less_one_year', 'one_to_three_years', 'more_three_years'
            )
        ),
    -- Copied from role_categories_v5 at claim time, so a later change to the
    -- mapping never silently moves people who were already routed.
    is_medical                boolean     not null,
    eval_version              integer     not null
        constraint assignments_v5_version_ck check (eval_version >= 1),
    -- 'base' = took one of the 3 medical / 2 non-medical slots, 'balance' = an
    -- extra rater, added by the fewest-in-total rule after the base slots were full.
    routed_by                 text        not null
        constraint assignments_v5_routed_by_ck check (routed_by in ('base', 'balance')),
    claimed_at                timestamptz not null default now(),
    expires_at                timestamptz not null,
    completed_at              timestamptz,
    constraint assignments_v5_role_details_ck check (
        public.role_details_ok_v5(annotator_role_details, annotator_groups)
    )
);

create index assignments_v5_version_idx
    on public.assignments_v5 (eval_version, is_medical);

-- A flag for the research team, raised by claim_version_v5 (once per batch)
-- when study_settings_v5.alert_at_held people hold a slot in the latest loaded
-- batch: time to prepare the next batch's Q&As. Check it in the Table Editor or
-- with batch_status_v5(); set acknowledged_at once someone is on it.
create table public.capacity_alerts_v5 (
    batch            integer     primary key
        constraint capacity_alerts_v5_batch_ck check (batch >= 1),
    held_at_alert    integer     not null,
    message          text        not null,
    raised_at        timestamptz not null default now(),
    acknowledged_at  timestamptz
);

create table public.ratings_v5 (
    id              bigserial primary key,
    -- The assignment a rating belongs to. Version and lateness are filled in by
    -- the insert trigger from the assignment itself; the browser cannot set them.
    assignment_id   uuid        not null
        references public.assignments_v5 (id),
    eval_version    integer     not null
        constraint ratings_v5_eval_version_ck check (eval_version >= 1),
    late            boolean     not null default false,
    study_version   text        not null default 'v5'
        constraint ratings_v5_study_version_ck check (study_version = 'v5'),
    session_id      text        not null
        constraint ratings_v5_session_id_ck check (btrim(session_id) <> ''),
    annotator       text        not null
        constraint ratings_v5_annotator_ck check (btrim(annotator) <> ''),
    qa_uid          text        not null
        constraint ratings_v5_qa_uid_ck check (btrim(qa_uid) <> ''),
    dataset         text        not null,
    video           integer     not null
        constraint ratings_v5_video_ck check (video > 0),
    approach        text        not null,

    -- Exact generated text is retained because later corpus revisions may reuse
    -- similar identifiers while changing the content presented to evaluators.
    question_text    text       not null,
    answer_text      text       not null,
    source_start_sec integer
        constraint ratings_v5_source_start_ck check (source_start_sec >= 0),
    source_end_sec   integer
        constraint ratings_v5_source_end_ck check (
            source_end_sec >= 0
            and (source_start_sec is null or source_end_sec >= source_start_sec)
        ),

    batch           text,
    blind           boolean     not null default true,

    -- Labels are stored exactly as displayed. The three quality metrics use a
    -- four-level ordinal attribute plus an optional issue list. Their binary
    -- columns remain nullable for format compatibility and future controlled
    -- comparisons. Care Safety and Standalone are binary-only.
    qna_trustworthiness_attribute text
        constraint ratings_v5_trust_attribute_ck check (
            qna_trustworthiness_attribute is null or
            qna_trustworthiness_attribute in ('Excellent', 'Good', 'Fair', 'Poor')
        ),
    qna_trustworthiness_issue     text,
    qna_trustworthiness_binary    text
        constraint ratings_v5_trust_binary_ck check (
            qna_trustworthiness_binary is null or
            qna_trustworthiness_binary in ('Yes', 'No')
        ),

    qna_clarity_attribute         text
        constraint ratings_v5_clarity_attribute_ck check (
            qna_clarity_attribute is null or
            qna_clarity_attribute in (
                'Very easy to understand', 'Easy', 'Somewhat difficult', 'Difficult'
            )
        ),
    qna_clarity_issue             text,
    qna_clarity_binary            text
        constraint ratings_v5_clarity_binary_ck check (
            qna_clarity_binary is null or qna_clarity_binary in ('Yes', 'No')
        ),

    qna_usefulness_attribute      text
        constraint ratings_v5_usefulness_attribute_ck check (
            qna_usefulness_attribute is null or
            qna_usefulness_attribute in (
                'Highly useful', 'Useful', 'Limited useful', 'Not useful'
            )
        ),
    qna_usefulness_issue          text,
    qna_usefulness_binary         text
        constraint ratings_v5_usefulness_binary_ck check (
            qna_usefulness_binary is null or qna_usefulness_binary in ('Yes', 'No')
        ),

    qna_care_safety_issue         text,
    qna_care_safety_binary        text
        constraint ratings_v5_care_safety_binary_ck check (
            qna_care_safety_binary is null or qna_care_safety_binary in ('Yes', 'No')
        ),
    qna_standalone_issue          text,
    qna_standalone_binary         text
        constraint ratings_v5_standalone_binary_ck check (
            qna_standalone_binary is null or qna_standalone_binary in ('Yes', 'No')
        ),

    caregiver_recommendation      text
        constraint ratings_v5_recommendation_ck check (
            caregiver_recommendation is null or
            caregiver_recommendation in (
                'Yes',
                'Yes, but with minor edits (meaning unchanged)',
                'No, it needs major edits',
                'No'
            )
        ),
    evaluator_comment             text,

    seconds_spent integer
        constraint ratings_v5_seconds_spent_ck check (seconds_spent >= 0),
    client_time   timestamptz,
    created_at    timestamptz not null default now(),

    -- Profile columns are copied from the assignment by the insert trigger.
    -- The optional role descriptions are whatever the annotator chose to say.
    annotator_groups text[]
        constraint ratings_v5_annotator_groups_ck check (
            annotator_groups is null or (
                cardinality(annotator_groups) between 1 and 4
                and annotator_groups <@ array['healthcare', 'student', 'family', 'other']::text[]
            )
        ),
    annotator_role_details jsonb,
    dementia_care_experience text
        constraint ratings_v5_dementia_experience_ck check (
            dementia_care_experience is null or
            dementia_care_experience in (
                'none', 'less_one_year', 'one_to_three_years', 'more_three_years'
            )
        ),
    constraint ratings_v5_role_details_ck check (
        annotator_role_details is null
        or public.role_details_ok_v5(annotator_role_details, annotator_groups)
    ),
    constraint ratings_v5_profile_complete_ck check (
        (annotator_groups is null and dementia_care_experience is null
         and annotator_role_details is null)
        or (annotator_groups is not null and dementia_care_experience is not null)
    ),

    -- True records a source-access problem, never a failed quality rating.
    -- False means no access problem was reported; null denotes legacy data.
    source_unverifiable boolean,
    constraint ratings_v5_source_unverifiable_ck check (
        source_unverifiable is distinct from true
        or (qna_trustworthiness_attribute is null
            and qna_trustworthiness_issue is null
            and qna_trustworthiness_binary is null)
    )
);

comment on table public.ratings_v5 is
    'Append-only human ratings from the MedicalQA dementia-care evaluation v5 site';

create index ratings_v5_assignment_idx
    on public.ratings_v5 (assignment_id, qa_uid);
create index ratings_v5_qa_uid_idx
    on public.ratings_v5 (qa_uid);
create index ratings_v5_created_at_idx
    on public.ratings_v5 (created_at desc);

-- An assignment holds a slot while it is completed or its lease is still open.
create function public.assignment_holds_slot_v5(a public.assignments_v5)
returns boolean
language sql
stable
set search_path = public, pg_temp
as $$
    select a.completed_at is not null or now() < a.expires_at
$$;

-- Every rating is checked against its assignment before it is stored: the
-- assignment must exist and the Q&A must belong to that assignment's version.
-- Identity, profile, version and pair metadata are copied from the database so
-- a stale or edited browser cannot file a rating under the wrong person or
-- version. After the insert, the assignment is marked complete once every Q&A
-- of its version has at least one rating.
create function public.ratings_v5_before_insert()
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
    new.dementia_care_experience := a.dementia_care_experience;
    new.dataset := vp.dataset;
    new.video := vp.video;
    new.approach := vp.approach;
    new.late := a.completed_at is null and now() >= a.expires_at;
    new.created_at := now();
    return new;
end
$$;

create function public.ratings_v5_after_insert()
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

create trigger ratings_v5_check_assignment
    before insert on public.ratings_v5
    for each row execute function public.ratings_v5_before_insert();

create trigger ratings_v5_mark_complete
    after insert on public.ratings_v5
    for each row execute function public.ratings_v5_after_insert();

-- The routing function the browser calls once, on the landing page.
create function public.claim_version_v5(
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
    n_loaded integer;
    n_pairs integer;
    max_loaded integer;
    cur_batch integer;
    held_in_batch integer;
begin
    -- One claim at a time across the whole study; released at commit.
    perform pg_advisory_xact_lock(hashtext('public.claim_version_v5'));

    select * into st from public.study_settings_v5;
    -- Versions are loaded in whole batches (1-5, then 6-10, ...) of
    -- pairs_per_version Q&As each; routing uses the loaded versions only.
    select count(distinct vp.eval_version), count(*), coalesce(max(vp.eval_version), 0)
    into n_loaded, n_pairs, max_loaded
    from public.version_pairs_v5 vp;
    if n_loaded = 0 or n_loaded % st.n_versions <> 0 or max_loaded <> n_loaded
       or n_pairs <> n_loaded * st.pairs_per_version then
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
        select generate_series(1, n_loaded) as v
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
            select generate_series(1, n_loaded) as v
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

    -- Capacity alert: once alert_at_held people hold a slot in the latest loaded
    -- batch, flag that the next batch's Q&As need preparing. Once per batch.
    cur_batch := (chosen - 1) / st.n_versions + 1;
    if cur_batch = n_loaded / st.n_versions then
        select count(*) into held_in_batch
        from public.assignments_v5 a
        where public.assignment_holds_slot_v5(a)
          and a.eval_version between (cur_batch - 1) * st.n_versions + 1 and cur_batch * st.n_versions;
        if held_in_batch >= st.alert_at_held then
            insert into public.capacity_alerts_v5 (batch, held_at_alert, message)
            values (cur_batch, held_in_batch, format(
                '%s people hold a slot in versions %s-%s: prepare the Q&As for versions %s-%s',
                held_in_batch, (cur_batch - 1) * st.n_versions + 1, cur_batch * st.n_versions,
                cur_batch * st.n_versions + 1, (cur_batch + 1) * st.n_versions))
            on conflict (batch) do nothing;
        end if;
    end if;

    return query select new_id, chosen, new_expiry;
end
$$;

-- Researchers' view of every assignment and whether it still holds its slot.
create view public.assignment_status_v5
with (security_invoker = true) as
select
    a.*,
    case
        when a.completed_at is not null then 'completed'
        when now() < a.expires_at then 'active'
        else 'expired'
    end as status,
    (select count(distinct r.qa_uid) from public.ratings_v5 r
     where r.assignment_id = a.id) as qnas_rated
from public.assignments_v5 a;

-- Counts per version by category, as the routing function sees them.
create function public.version_status_v5()
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
    from generate_series(1, (select greatest(st.n_versions, coalesce(max(vp.eval_version), 0))
                             from public.study_settings_v5 st
                             left join public.version_pairs_v5 vp on true
                             group by st.n_versions)) as v(v)
    left join public.assignment_status_v5 s on s.eval_version = v.v
    group by v.v
    order by v.v
$$;

-- Per batch of five versions: slots held against the 25 base slots, and the
-- capacity alert if it has been raised.
create function public.batch_status_v5()
returns table (
    batch integer,
    versions text,
    medical_held bigint,
    non_medical_held bigint,
    total_held bigint,
    base_slots integer,
    alert_raised_at timestamptz,
    alert_message text
)
language sql
stable
security invoker
set search_path = public, pg_temp
as $$
    select b.batch,
           b.first_version || '-' || b.last_version,
           b.medical_held, b.non_medical_held, b.total_held,
           b.n * (st.base_medical + st.base_non_medical),
           al.raised_at, al.message
    from (select (v.eval_version - 1) / st.n_versions + 1 as batch,
                 min(v.eval_version) as first_version, max(v.eval_version) as last_version,
                 count(*)::integer as n,
                 sum(v.medical_held)::bigint as medical_held,
                 sum(v.non_medical_held)::bigint as non_medical_held,
                 sum(v.total_held)::bigint as total_held
          from public.version_status_v5() v
          cross join public.study_settings_v5 st
          group by 1) b
    cross join public.study_settings_v5 st
    left join public.capacity_alerts_v5 al on al.batch = b.batch
    order by b.batch
$$;

-- Re-rating a Q&A appends another raw row. This view returns the newest row for
-- each assignment and Q&A while preserving the raw edit history.
create view public.ratings_v5_final
with (security_invoker = true) as
select distinct on (assignment_id, qa_uid) *
from public.ratings_v5
order by assignment_id, qa_uid, created_at desc, id desc;

-- Permissions. Supabase grants new tables and functions to anon and
-- authenticated by default, so everything is revoked first and only two things
-- are handed back: calling claim_version_v5 and inserting ratings.
alter table public.study_settings_v5 enable row level security;
alter table public.role_categories_v5 enable row level security;
alter table public.version_pairs_v5 enable row level security;
alter table public.assignments_v5 enable row level security;
alter table public.ratings_v5 enable row level security;
alter table public.capacity_alerts_v5 enable row level security;

revoke all on public.study_settings_v5, public.role_categories_v5,
    public.version_pairs_v5, public.assignments_v5, public.ratings_v5,
    public.ratings_v5_final, public.assignment_status_v5, public.capacity_alerts_v5
    from anon, authenticated;
revoke all on function public.claim_version_v5(text, text[], jsonb, text),
    public.version_status_v5(),
    public.batch_status_v5(),
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

create policy ratings_v5_anon_insert
    on public.ratings_v5
    for insert
    to anon
    with check (study_version = 'v5');

notify pgrst, 'reload schema';

commit;
