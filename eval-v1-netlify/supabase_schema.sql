-- Separate Supabase store for the first-deployed SingleAgent v1 evaluation.
-- Paste this whole file into the Supabase SQL Editor and run it once.
--
-- This creates:
--   public.ratings_v1
--   public.ratings_v1_final
--
-- Expected rows:
--   study_version = 'v1'
--   approach      = 'SingleAgent-v1'
--
-- It does NOT drop, reset, or modify public.ratings_v3 or
-- public.ratings_v3_final. The active v3 evaluation remains untouched.

create table if not exists public.ratings_v1 (
    id              bigserial primary key,
    study_version   text        not null default 'v1',
    session_id      text        not null,
    annotator       text        not null,
    qa_uid          text        not null,
    dataset         text        not null,
    video           integer     not null,
    approach        text        not null,

    question_text    text       not null,
    answer_text      text       not null,
    source_start_sec integer,
    source_end_sec   integer,

    batch           text,
    blind           boolean     not null default true,

    qna_trustworthiness_attribute text,
    qna_trustworthiness_issue     text,
    qna_trustworthiness_binary    text,

    qna_clarity_attribute         text,
    qna_clarity_issue             text,
    qna_clarity_binary            text,

    qna_usefulness_attribute      text,
    qna_usefulness_issue          text,
    qna_usefulness_binary         text,

    -- Care Safety and Standalone use only Yes/No plus an issue field.
    qna_care_safety_issue         text,
    qna_care_safety_binary        text,
    qna_standalone_issue          text,
    qna_standalone_binary         text,

    caregiver_recommendation      text,
    evaluator_comment             text,

    seconds_spent integer,
    client_time   timestamptz,
    created_at    timestamptz not null default now()
);

-- Safe to re-run if setup was interrupted: add any missing current columns
-- without deleting ratings already collected in the v1 table.
alter table public.ratings_v1
    add column if not exists study_version                  text not null default 'v1',
    add column if not exists question_text                   text not null default '',
    add column if not exists answer_text                     text not null default '',
    add column if not exists source_start_sec                integer,
    add column if not exists source_end_sec                  integer,
    add column if not exists qna_trustworthiness_attribute   text,
    add column if not exists qna_trustworthiness_issue       text,
    add column if not exists qna_trustworthiness_binary      text,
    add column if not exists qna_clarity_attribute           text,
    add column if not exists qna_clarity_issue               text,
    add column if not exists qna_clarity_binary              text,
    add column if not exists qna_usefulness_attribute        text,
    add column if not exists qna_usefulness_issue            text,
    add column if not exists qna_usefulness_binary           text,
    add column if not exists qna_care_safety_issue           text,
    add column if not exists qna_care_safety_binary          text,
    add column if not exists qna_standalone_issue            text,
    add column if not exists qna_standalone_binary           text,
    add column if not exists caregiver_recommendation        text,
    add column if not exists evaluator_comment               text;

-- Enforce the corrected study identity for every new submission. NOT VALID
-- keeps this migration safe if an earlier test used the incorrect
-- SingleAgent-prev package: old rows remain in the raw audit table, but all new
-- rows must use the first-deployed SingleAgent-v1 corpus.
alter table public.ratings_v1
    drop constraint if exists ratings_v1_study_version_only;
alter table public.ratings_v1
    add constraint ratings_v1_study_version_only
    check (study_version = 'v1') not valid;

alter table public.ratings_v1
    drop constraint if exists ratings_v1_approach_only;
alter table public.ratings_v1
    add constraint ratings_v1_approach_only
    check (approach = 'SingleAgent-v1') not valid;

create index if not exists ratings_v1_session_idx
    on public.ratings_v1 (session_id);
create index if not exists ratings_v1_qa_uid_idx
    on public.ratings_v1 (qa_uid);
create index if not exists ratings_v1_approach_idx
    on public.ratings_v1 (dataset, approach);
create index if not exists ratings_v1_study_version_idx
    on public.ratings_v1 (study_version);

alter table public.ratings_v1 enable row level security;

-- The public evaluator site can insert, but cannot read, edit, or delete rows.
revoke all on public.ratings_v1 from anon, authenticated;
grant insert on public.ratings_v1 to anon;
grant usage, select on sequence public.ratings_v1_id_seq to anon;

drop policy if exists ratings_v1_anon_insert on public.ratings_v1;
create policy ratings_v1_anon_insert
    on public.ratings_v1
    for insert
    to anon
    with check (true);

-- Keep the newest answer when an evaluator goes back and rerates a Q&A. The
-- filter prevents any test submissions from the discarded SingleAgent-prev
-- package from entering the corrected v1 analysis dataset.
drop view if exists public.ratings_v1_final;
create view public.ratings_v1_final
with (security_invoker = true) as
select distinct on (session_id, qa_uid) *
from public.ratings_v1
where study_version = 'v1'
  and approach = 'SingleAgent-v1'
order by session_id, qa_uid, created_at desc;

revoke all on public.ratings_v1_final from anon, authenticated;


-- Verify that v1 and v3 both still exist after setup:
--   select
--     to_regclass('public.ratings_v1') as v1_table,
--     to_regclass('public.ratings_v1_final') as v1_final_view,
--     to_regclass('public.ratings_v3') as v3_table,
--     to_regclass('public.ratings_v3_final') as v3_final_view;

-- Check the newest v1 submission:
--   select
--     id, created_at, annotator, qa_uid, approach,
--     qna_standalone_binary, qna_standalone_issue
--   from public.ratings_v1
--   order by created_at desc
--   limit 1;

-- Confirm that the corrected final view contains only SingleAgent-v1:
--   select study_version, approach, count(*) as rows
--   from public.ratings_v1_final
--   group by study_version, approach;

-- Find any preserved test rows from the discarded package. They remain only
-- in the raw audit table and are excluded from ratings_v1_final:
--   select study_version, approach, count(*) as rows
--   from public.ratings_v1
--   where study_version <> 'v1' or approach <> 'SingleAgent-v1'
--   group by study_version, approach;

-- Count final v1 ratings per evaluator:
--   select annotator, count(*) as qnas_rated, max(created_at) as last_seen
--   from public.ratings_v1_final
--   group by annotator
--   order by qnas_rated desc;
