-- Current human evaluation store for the dementia Q&A evaluation site.
-- Paste this whole file into the Supabase SQL Editor and run it once.
--
-- WARNING: THIS IS AN INTENTIONAL v3 RESET.
-- It drops and recreates public.ratings_v3 and public.ratings_v3_final, so every
-- row already stored in those two objects is deleted. The older
-- public.ratings_ui_v2_qna_v2 and public.ratings tables are not changed.
--
-- The reset is needed because Q&A Standalone is now binary-only. The obsolete
-- qna_standalone_attribute column is deliberately removed rather than leaving
-- old-form and new-form v3 rows mixed together.
--
-- Design: append-only log. The browser holds the PUBLIC anon key, so `anon` is
-- granted INSERT and nothing else. A stranger with the key can add junk rows
-- (filter them out by session_id) but cannot read, edit, or delete ratings.
-- Resume is powered by localStorage in the browser, so the page never needs
-- SELECT access.

begin;

drop view if exists public.ratings_v3_final;
drop table if exists public.ratings_v3;

create table public.ratings_v3 (
    id              bigserial primary key,
    study_version   text        not null default 'v3',
    session_id      text        not null,   -- one per annotator per configuration
    annotator       text        not null,
    qa_uid          text        not null,   -- stable Q&A id from qa_data.json
    dataset         text        not null,
    video           integer     not null,
    approach        text        not null,

    -- Store the exact generated Q&A text that was rated. This matters because
    -- prompt/data revisions can change while ids remain similar.
    question_text    text       not null,
    answer_text      text       not null,
    source_start_sec integer,
    source_end_sec   integer,

    batch           text,
    blind           boolean     not null default true,

    -- Labels are stored exactly as displayed. Score/code mapping happens later.
    --   *_attribute = four-level quality label, when the metric has one
    --   *_issue     = issue labels joined with "; " or "No issue"
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

    -- Care Safety and Standalone are intentionally binary-only.
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

create index ratings_v3_session_idx
    on public.ratings_v3 (session_id);
create index ratings_v3_qa_uid_idx
    on public.ratings_v3 (qa_uid);
create index ratings_v3_approach_idx
    on public.ratings_v3 (dataset, approach);
create index ratings_v3_study_version_idx
    on public.ratings_v3 (study_version);

alter table public.ratings_v3 enable row level security;

-- Public browser clients may insert only. No select/update/delete policy exists,
-- so RLS denies those actions to `anon` by default. Do not add one.
revoke all on public.ratings_v3 from anon, authenticated;
grant insert on public.ratings_v3 to anon;
grant usage, select on sequence public.ratings_v3_id_seq to anon;

create policy ratings_v3_anon_insert
    on public.ratings_v3
    for insert
    to anon
    with check (true);


-- ---------------------------------------------------------------------------
-- Latest rating per (session, Q&A). The Previous button re-submits a Q&A, so
-- the raw table keeps an edit history; this view keeps only the final answer.
-- ---------------------------------------------------------------------------
create view public.ratings_v3_final
with (security_invoker = true) as
select distinct on (session_id, qa_uid) *
from public.ratings_v3
order by session_id, qa_uid, created_at desc;

revoke all on public.ratings_v3_final from anon, authenticated;

commit;


-- ---------------------------------------------------------------------------
-- Handy queries (run these separately after the reset finishes)
-- ---------------------------------------------------------------------------

-- Confirm that the reset created the raw table and final view:
--   select
--     to_regclass('public.ratings_v3') as raw_table,
--     to_regclass('public.ratings_v3_final') as final_view;

-- Check the newest raw submission:
--   select *
--   from public.ratings_v3
--   order by created_at desc
--   limit 1;

-- Who has done how much, and when did they last submit?
--   select annotator, count(*) as qnas_rated, max(created_at) as last_seen
--   from public.ratings_v3_final
--   group by annotator
--   order by qnas_rated desc;

-- Standalone pass counts by approach:
--   select approach, qna_standalone_binary, count(*) as n
--   from public.ratings_v3_final
--   group by approach, qna_standalone_binary
--   order by approach, qna_standalone_binary;

-- Standalone failure-reason counts:
--   select approach, qna_standalone_issue, count(*) as n
--   from public.ratings_v3_final
--   where qna_standalone_binary = 'No'
--   group by approach, qna_standalone_issue
--   order by approach, n desc;

-- Care-safety concern counts by approach:
--   select approach, qna_care_safety_issue, count(*) as n
--   from public.ratings_v3_final
--   where qna_care_safety_issue is not null
--     and qna_care_safety_issue <> 'No issue'
--   group by approach, qna_care_safety_issue
--   order by approach, n desc;

-- Optional comments:
--   select annotator, qa_uid, approach, evaluator_comment
--   from public.ratings_v3_final
--   where evaluator_comment is not null
--   order by created_at desc;

-- Overlap available for inter-annotator agreement:
--   select qa_uid, count(distinct annotator) as n_annotators
--   from public.ratings_v3_final
--   group by qa_uid
--   having count(distinct annotator) > 1;
