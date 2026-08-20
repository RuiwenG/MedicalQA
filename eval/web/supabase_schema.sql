-- Current human evaluation store for the dementia Q&A eval site.
-- Paste this whole file into the Supabase SQL editor and run it once.
--
-- This creates a NEW table for the updated Q&A set + updated UI rubric:
--   public.ratings_ui_v2_qna_v2
--
-- It does not modify the legacy public.ratings table.
--
-- Design: append-only log. The browser holds the PUBLIC anon key, so `anon` is
-- granted INSERT and nothing else. A stranger with the key can add junk rows
-- (filter them out by session_id) but can never read, edit, or delete ratings.
-- Resume is powered by localStorage in the browser, so the page never needs
-- SELECT access.

create table if not exists public.ratings_ui_v2_qna_v2 (
    id              bigserial primary key,
    study_version   text        not null default 'ui_v2_qna_v2',
    session_id      text        not null,   -- one per annotator per configuration
    annotator       text        not null,
    qa_uid          text        not null,   -- stable Q&A id from qa_data.json
    dataset         text        not null,
    video           integer     not null,
    approach        text        not null,

    -- Store the exact generated Q&A text that was rated. This matters because the
    -- Q&A generation prompt/data can change between ablation runs while ids stay
    -- similar.
    question_text    text       not null,
    answer_text      text       not null,
    source_start_sec integer,
    source_end_sec   integer,

    batch           text,                   -- which assigned batch this came from
    blind           boolean     not null default true,

    -- Current v2 rubric. Values are stored as text labels exactly as annotators
    -- saw them; score/code mapping happens later during analysis.
    --   *_attribute = 4-level quality label, when that metric has one
    --   *_issue     = one or more issue/concern labels, joined with "; "
    --   *_binary    = Yes/No label
    qna_trustworthiness_attribute text,
    qna_trustworthiness_issue     text,
    qna_trustworthiness_binary    text,

    qna_clarity_attribute         text,
    qna_clarity_issue             text,
    qna_clarity_binary            text,

    qna_usefulness_attribute      text,
    qna_usefulness_issue          text,
    qna_usefulness_binary         text,

    -- Care safety is intentionally binary-only in the current UI.
    qna_care_safety_issue         text,
    qna_care_safety_binary        text,

    caregiver_recommendation      text,
    evaluator_comment             text,

    seconds_spent integer,               -- time on this Q&A, for quality checks
    client_time   timestamptz,           -- annotator's clock
    created_at    timestamptz not null default now()
);

-- If this file is re-run, keep the migration additive.
alter table public.ratings_ui_v2_qna_v2
    add column if not exists study_version                  text not null default 'ui_v2_qna_v2',
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
    add column if not exists caregiver_recommendation        text,
    add column if not exists evaluator_comment               text;

create index if not exists ratings_ui_v2_qna_v2_session_idx
    on public.ratings_ui_v2_qna_v2 (session_id);
create index if not exists ratings_ui_v2_qna_v2_qa_uid_idx
    on public.ratings_ui_v2_qna_v2 (qa_uid);
create index if not exists ratings_ui_v2_qna_v2_approach_idx
    on public.ratings_ui_v2_qna_v2 (dataset, approach);
create index if not exists ratings_ui_v2_qna_v2_study_version_idx
    on public.ratings_ui_v2_qna_v2 (study_version);

alter table public.ratings_ui_v2_qna_v2 enable row level security;

-- Public browser clients may insert only. No select/update/delete policy exists,
-- so RLS denies those actions to `anon` by default. Do not add one.
revoke all on public.ratings_ui_v2_qna_v2 from anon, authenticated;
grant insert on public.ratings_ui_v2_qna_v2 to anon;
grant usage, select on sequence public.ratings_ui_v2_qna_v2_id_seq to anon;

drop policy if exists ratings_ui_v2_qna_v2_anon_insert on public.ratings_ui_v2_qna_v2;
create policy ratings_ui_v2_qna_v2_anon_insert
    on public.ratings_ui_v2_qna_v2
    for insert
    to anon
    with check (true);


-- ---------------------------------------------------------------------------
-- Latest rating per (session, Q&A). The Previous button re-submits a Q&A, so
-- the raw table keeps an edit history; this view keeps only the final answer.
-- ---------------------------------------------------------------------------
create or replace view public.ratings_ui_v2_qna_v2_final
with (security_invoker = true) as
select distinct on (session_id, qa_uid) *
from public.ratings_ui_v2_qna_v2
order by session_id, qa_uid, created_at desc;

revoke all on public.ratings_ui_v2_qna_v2_final from anon, authenticated;


-- ---------------------------------------------------------------------------
-- Handy queries (run these in the SQL editor while annotation is in progress)
-- ---------------------------------------------------------------------------

-- Who has done how much, and when did they last submit?
--   select annotator, count(*) as qnas_rated, max(created_at) as last_seen
--   from public.ratings_ui_v2_qna_v2_final
--   group by annotator
--   order by qnas_rated desc;

-- Attribute/binary label counts by approach:
--   select approach, qna_trustworthiness_attribute, qna_trustworthiness_binary, count(*) as n
--   from public.ratings_ui_v2_qna_v2_final
--   group by approach, qna_trustworthiness_attribute, qna_trustworthiness_binary
--   order by approach, n desc;

-- Care-safety concern counts by approach:
--   select approach, qna_care_safety_issue, count(*) as n
--   from public.ratings_ui_v2_qna_v2_final
--   where qna_care_safety_issue is not null and qna_care_safety_issue <> 'No issue'
--   group by approach, qna_care_safety_issue
--   order by approach, n desc;

-- Optional comments:
--   select annotator, qa_uid, approach, evaluator_comment
--   from public.ratings_ui_v2_qna_v2_final
--   where evaluator_comment is not null
--   order by created_at desc;

-- Overlap available for inter-annotator agreement:
--   select qa_uid, count(distinct annotator) as n_annotators
--   from public.ratings_ui_v2_qna_v2_final
--   group by qa_uid
--   having count(distinct annotator) > 1;
