-- One-time, non-destructive setup for the MedicalQA human evaluation v4 site.
--
-- This script creates public.ratings_v4 and public.ratings_v4_final. It never
-- drops an object. If either v4 object already exists, the transaction stops so
-- rerunning setup cannot erase collected ratings.

begin;

do $$
begin
    if to_regclass('public.ratings_v4') is not null
       or to_regclass('public.ratings_v4_final') is not null then
        raise exception
            'ratings_v4 or ratings_v4_final already exists; setup aborted without changes';
    end if;
end
$$;

create table public.ratings_v4 (
    id              bigserial primary key,
    study_version   text        not null default 'v4'
        constraint ratings_v4_study_version_ck check (study_version = 'v4'),
    session_id      text        not null
        constraint ratings_v4_session_id_ck check (btrim(session_id) <> ''),
    annotator       text        not null
        constraint ratings_v4_annotator_ck check (btrim(annotator) <> ''),
    qa_uid          text        not null
        constraint ratings_v4_qa_uid_ck check (btrim(qa_uid) <> ''),
    dataset         text        not null,
    video           integer     not null
        constraint ratings_v4_video_ck check (video > 0),
    approach        text        not null,

    -- Exact generated text is retained because later corpus revisions may reuse
    -- similar identifiers while changing the content presented to evaluators.
    question_text    text       not null,
    answer_text      text       not null,
    source_start_sec integer
        constraint ratings_v4_source_start_ck check (source_start_sec >= 0),
    source_end_sec   integer
        constraint ratings_v4_source_end_ck check (
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
        constraint ratings_v4_trust_attribute_ck check (
            qna_trustworthiness_attribute is null or
            qna_trustworthiness_attribute in ('Excellent', 'Good', 'Fair', 'Poor')
        ),
    qna_trustworthiness_issue     text,
    qna_trustworthiness_binary    text
        constraint ratings_v4_trust_binary_ck check (
            qna_trustworthiness_binary is null or
            qna_trustworthiness_binary in ('Yes', 'No')
        ),

    qna_clarity_attribute         text
        constraint ratings_v4_clarity_attribute_ck check (
            qna_clarity_attribute is null or
            qna_clarity_attribute in (
                'Very easy to understand', 'Easy', 'Somewhat difficult', 'Difficult'
            )
        ),
    qna_clarity_issue             text,
    qna_clarity_binary            text
        constraint ratings_v4_clarity_binary_ck check (
            qna_clarity_binary is null or qna_clarity_binary in ('Yes', 'No')
        ),

    qna_usefulness_attribute      text
        constraint ratings_v4_usefulness_attribute_ck check (
            qna_usefulness_attribute is null or
            qna_usefulness_attribute in (
                'Highly useful', 'Useful', 'Limited useful', 'Not useful'
            )
        ),
    qna_usefulness_issue          text,
    qna_usefulness_binary         text
        constraint ratings_v4_usefulness_binary_ck check (
            qna_usefulness_binary is null or qna_usefulness_binary in ('Yes', 'No')
        ),

    qna_care_safety_issue         text,
    qna_care_safety_binary        text
        constraint ratings_v4_care_safety_binary_ck check (
            qna_care_safety_binary is null or qna_care_safety_binary in ('Yes', 'No')
        ),
    qna_standalone_issue          text,
    qna_standalone_binary         text
        constraint ratings_v4_standalone_binary_ck check (
            qna_standalone_binary is null or qna_standalone_binary in ('Yes', 'No')
        ),

    caregiver_recommendation      text
        constraint ratings_v4_recommendation_ck check (
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
        constraint ratings_v4_seconds_spent_ck check (seconds_spent >= 0),
    client_time   timestamptz,
    created_at    timestamptz not null default now(),

    -- Profile columns remain nullable for pre-profile v4 submissions. New UI
    -- sessions require the group and experience; no individual professions
    -- or unpaid-care relationship question are collected.
    annotator_group text
        constraint ratings_v4_annotator_group_ck check (
            annotator_group is null or
            annotator_group in ('clinical', 'care', 'family', 'student', 'other')
        ),
    annotator_other text,
    dementia_care_experience text
        constraint ratings_v4_dementia_experience_ck check (
            dementia_care_experience is null or
            dementia_care_experience in (
                'none', 'less_one_year', 'one_to_three_years', 'more_three_years'
            )
        ),
    constraint ratings_v4_annotator_other_ck check (
        case when annotator_group = 'other' then
            annotator_other is not null and btrim(annotator_other) <> ''
            and char_length(annotator_other) <= 200
        else annotator_other is null end
    ),
    constraint ratings_v4_profile_complete_ck check (
        (annotator_group is null and dementia_care_experience is null
         and annotator_other is null)
        or (annotator_group is not null and dementia_care_experience is not null)
    )
);

comment on table public.ratings_v4 is
    'Append-only human ratings from the MedicalQA dementia-care evaluation v4 site';

create index ratings_v4_session_idx
    on public.ratings_v4 (session_id);
create index ratings_v4_qa_uid_idx
    on public.ratings_v4 (qa_uid);
create index ratings_v4_approach_idx
    on public.ratings_v4 (dataset, approach);
create index ratings_v4_study_version_idx
    on public.ratings_v4 (study_version);
create index ratings_v4_created_at_idx
    on public.ratings_v4 (created_at desc);

alter table public.ratings_v4 enable row level security;

-- The public browser client is append-only. Resume remains in localStorage, so
-- it does not need to read rows back from Supabase.
revoke all on public.ratings_v4 from anon, authenticated;
grant insert on public.ratings_v4 to anon;
grant usage, select on sequence public.ratings_v4_id_seq to anon;

create policy ratings_v4_anon_insert
    on public.ratings_v4
    for insert
    to anon
    with check (study_version = 'v4');

-- Re-rating a Q&A appends another raw row. This view returns the newest row for
-- each browser session and Q&A while preserving the raw edit history.
create view public.ratings_v4_final
with (security_invoker = true) as
select distinct on (session_id, qa_uid) *
from public.ratings_v4
order by session_id, qa_uid, created_at desc, id desc;

revoke all on public.ratings_v4_final from anon, authenticated;

notify pgrst, 'reload schema';

commit;
