-- Human evaluation store for the dementia QA eval site.
-- Paste this whole file into the Supabase SQL editor and run it once.
--
-- Design: append-only log. The browser holds the PUBLIC anon key, so `anon` is
-- granted INSERT and nothing else. A stranger with the key can add junk rows
-- (filter them out by session_id) but can never read, edit, or delete ratings.
-- Resume is powered by localStorage in the browser, so the page never needs
-- SELECT access.

create table if not exists public.ratings (
    id           bigserial primary key,
    session_id   text        not null,   -- one per annotator per configuration
    annotator    text        not null,
    qa_uid       text        not null,   -- e.g. Teepa_v3_RAG_q12
    dataset      text        not null,
    video        integer     not null,
    approach     text        not null,
    batch        text,                   -- which assigned batch this came from
    blind        boolean     not null default true,

    -- Pilot form, per metric. Values are stored as text labels exactly as the
    -- annotator saw them; score/code mapping happens later during analysis.
    --   *_attribute = attribute label
    --   *_error     = one or more problem labels, joined with "; "
    --   *_binary    = Yes/No label
    -- Null is used when the annotator deselected that metric.
    qa_alignment_attribute      text,
    qa_alignment_error          text,
    qa_alignment_binary         text,
    qa_accessibility_attribute  text,
    qa_accessibility_error      text,
    qa_accessibility_binary     text,
    qa_edu_actionable_attribute text,
    qa_edu_actionable_error     text,
    qa_edu_actionable_binary    text,
    qa_mental_health_attribute  text,
    qa_mental_health_error      text,
    qa_mental_health_binary     text,
    caregiver_recommendation    text,

    seconds_spent integer,               -- time on this pair, for quality checks
    client_time   timestamptz,           -- annotator's clock
    created_at    timestamptz not null default now()
);

-- Analysis queries filter by these constantly.
create index if not exists ratings_session_idx on public.ratings (session_id);
create index if not exists ratings_qa_uid_idx  on public.ratings (qa_uid);
create index if not exists ratings_approach_idx on public.ratings (dataset, approach);

-- If the table already existed with an older metric schema, these keep the
-- migration additive. Old columns can remain unused until the study data is
-- exported or the table is rebuilt.
alter table public.ratings
    add column if not exists qa_alignment_attribute      text,
    add column if not exists qa_alignment_error          text,
    add column if not exists qa_alignment_binary         text,
    add column if not exists qa_accessibility_attribute  text,
    add column if not exists qa_accessibility_error      text,
    add column if not exists qa_accessibility_binary     text,
    add column if not exists qa_edu_actionable_attribute text,
    add column if not exists qa_edu_actionable_error     text,
    add column if not exists qa_edu_actionable_binary    text,
    add column if not exists qa_mental_health_attribute  text,
    add column if not exists qa_mental_health_error      text,
    add column if not exists qa_mental_health_binary     text,
    add column if not exists caregiver_recommendation    text;

alter table public.ratings drop constraint if exists ratings_qa_alignment_check;
alter table public.ratings drop constraint if exists ratings_qa_accessibility_check;
alter table public.ratings drop constraint if exists ratings_qa_edu_actionable_check;
alter table public.ratings drop constraint if exists ratings_qa_mental_health_check;
alter table public.ratings drop constraint if exists ratings_qa_alignment_attribute_check;
alter table public.ratings drop constraint if exists ratings_qa_alignment_severity_check;
alter table public.ratings drop constraint if exists ratings_qa_alignment_binary_check;
alter table public.ratings drop constraint if exists ratings_qa_accessibility_attribute_check;
alter table public.ratings drop constraint if exists ratings_qa_accessibility_severity_check;
alter table public.ratings drop constraint if exists ratings_qa_accessibility_binary_check;
alter table public.ratings drop constraint if exists ratings_qa_edu_actionable_attribute_check;
alter table public.ratings drop constraint if exists ratings_qa_edu_actionable_severity_check;
alter table public.ratings drop constraint if exists ratings_qa_edu_actionable_binary_check;
alter table public.ratings drop constraint if exists ratings_qa_mental_health_attribute_check;
alter table public.ratings drop constraint if exists ratings_qa_mental_health_severity_check;
alter table public.ratings drop constraint if exists ratings_qa_mental_health_binary_check;
alter table public.ratings drop constraint if exists ratings_caregiver_recommendation_check;

alter table public.ratings
    alter column qa_alignment_attribute type text using qa_alignment_attribute::text,
    alter column qa_alignment_binary type text using qa_alignment_binary::text,
    alter column qa_accessibility_attribute type text using qa_accessibility_attribute::text,
    alter column qa_accessibility_binary type text using qa_accessibility_binary::text,
    alter column qa_edu_actionable_attribute type text using qa_edu_actionable_attribute::text,
    alter column qa_edu_actionable_binary type text using qa_edu_actionable_binary::text,
    alter column qa_mental_health_attribute type text using qa_mental_health_attribute::text,
    alter column qa_mental_health_binary type text using qa_mental_health_binary::text,
    alter column caregiver_recommendation type text using caregiver_recommendation::text;

alter table public.ratings enable row level security;

-- The ONLY policy. No select/update/delete policy exists, so RLS denies them
-- to `anon` by default. Do not add one.
drop policy if exists ratings_anon_insert on public.ratings;
create policy ratings_anon_insert
    on public.ratings
    for insert
    to anon
    with check (true);


-- ---------------------------------------------------------------------------
-- Latest rating per (session, QA pair). The Previous button re-submits a pair,
-- so the raw table keeps an edit history; this view keeps only the final answer.
-- ---------------------------------------------------------------------------
create or replace view public.ratings_final as
select distinct on (session_id, qa_uid) *
from public.ratings
order by session_id, qa_uid, created_at desc;


-- ---------------------------------------------------------------------------
-- Handy queries (run these in the SQL editor while annotation is in progress)
-- ---------------------------------------------------------------------------

-- Who has done how much, and when did they last submit?
--   select annotator, count(*) as pairs_rated, max(created_at) as last_seen
--   from public.ratings_final group by annotator order by pairs_rated desc;

-- Attribute/binary label counts by approach:
--   select approach, qa_alignment_attribute, qa_alignment_binary, count(*) as n
--   from public.ratings_final
--   group by approach, qa_alignment_attribute, qa_alignment_binary
--   order by approach, n desc;

-- Error taxonomy counts by approach:
--   select approach, qa_alignment_error, count(*) as n
--   from public.ratings_final
--   group by approach, qa_alignment_error
--   order by approach, n desc;

-- Numeric mappings/correlations should be done in the analysis notebook or
-- script after collection, using the agreed score map for these text labels.

-- Pairs rated by more than one annotator (the inter-annotator agreement set):
--   select qa_uid, count(distinct annotator) as n_annotators
--   from public.ratings_final group by qa_uid having count(distinct annotator) > 1;
