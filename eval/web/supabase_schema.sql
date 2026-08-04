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

    -- 1-5 Likert, null when the annotator deselected that metric
    q_fluency     smallint check (q_fluency     between 1 and 5),
    a_fluency     smallint check (a_fluency     between 1 and 5),
    q_clarity     smallint check (q_clarity     between 1 and 5),
    a_clarity     smallint check (a_clarity     between 1 and 5),
    qa_alignment  smallint check (qa_alignment  between 1 and 5),
    q_edu_value   smallint check (q_edu_value   between 1 and 5),
    a_edu_value   smallint check (a_edu_value   between 1 and 5),
    standalone    smallint check (standalone    between 1 and 5),

    seconds_spent integer,               -- time on this pair, for quality checks
    client_time   timestamptz,           -- annotator's clock
    created_at    timestamptz not null default now()
);

-- Analysis queries filter by these constantly.
create index if not exists ratings_session_idx on public.ratings (session_id);
create index if not exists ratings_qa_uid_idx  on public.ratings (qa_uid);
create index if not exists ratings_approach_idx on public.ratings (dataset, approach);

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

-- Mean score per approach:
--   select approach,
--          round(avg(q_fluency), 2)    as q_fluency,
--          round(avg(a_fluency), 2)    as a_fluency,
--          round(avg(q_clarity), 2)    as q_clarity,
--          round(avg(a_clarity), 2)    as a_clarity,
--          round(avg(qa_alignment), 2) as qa_alignment,
--          round(avg(q_edu_value), 2)  as q_edu_value,
--          round(avg(a_edu_value), 2)  as a_edu_value,
--          round(avg(standalone), 2)   as standalone,
--          count(*)                    as n
--   from public.ratings_final group by approach order by approach;

-- Pairs rated by more than one annotator (the inter-annotator agreement set):
--   select qa_uid, count(distinct annotator) as n_annotators
--   from public.ratings_final group by qa_uid having count(distinct annotator) > 1;
