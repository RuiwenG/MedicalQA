-- Read-only operational and analysis queries for MedicalQA evaluation v4.
-- Run individual statements as needed in the Supabase SQL Editor.

-- Latest raw submission.
select *
from public.ratings_v4
order by created_at desc, id desc
limit 1;

-- Completed Q&As and last activity per evaluator/session.
select
    annotator,
    session_id,
    count(*) as qnas_rated,
    min(created_at) as first_submission,
    max(created_at) as last_submission
from public.ratings_v4_final
group by annotator, session_id
order by last_submission desc;

-- Annotators per group and experience. Count browser sessions, not Q&A rows.
-- Use the latest available profile for each session, so older submissions
-- without metadata can still be grouped once that annotator resumes rating.
-- Missing profiles stay Unknown; never infer a background from a name.
with sessions as (
    select annotator, session_id, count(*) as qnas_rated
    from public.ratings_v4_final
    group by annotator, session_id
), profiles as (
    select distinct on (session_id)
        session_id, annotator_group, annotator_other, dementia_care_experience
    from public.ratings_v4
    where annotator_group is not null
    order by session_id, created_at desc, id desc
)
select
    coalesce(p.annotator_group, 'unknown') as annotator_group,
    coalesce(p.dementia_care_experience, 'unknown') as dementia_care_experience,
    count(*) as n_sessions,
    count(distinct s.annotator) as n_distinct_annotator_names,
    sum(s.qnas_rated) as qnas_rated
from sessions s
left join profiles p using (session_id)
group by p.annotator_group, p.dementia_care_experience
order by annotator_group, dementia_care_experience;

-- Export ratings with the latest known session profile for group/agreement
-- analysis. The original profile fields retain metadata at submission time.
with profiles as (
    select distinct on (session_id)
        session_id, annotator_group, annotator_other, dementia_care_experience
    from public.ratings_v4
    where annotator_group is not null
    order by session_id, created_at desc, id desc
)
select r.*,
    p.annotator_group as analysis_annotator_group,
    p.annotator_other as analysis_annotator_other,
    p.dementia_care_experience as analysis_dementia_care_experience
from public.ratings_v4_final r
left join profiles p using (session_id)
order by r.qa_uid, r.annotator;

-- Edit-history audit: rows above one indicate re-rating.
select session_id, qa_uid, count(*) as raw_versions, max(created_at) as last_edit
from public.ratings_v4
group by session_id, qa_uid
having count(*) > 1
order by last_edit desc;

-- Ordinal label distributions. Keep these categorical in the primary report.
-- Source-access failures are missing Trustworthiness assessments, not Poor.
select 'Trustworthiness' as metric, qna_trustworthiness_attribute as rating,
       count(*) as n
from public.ratings_v4_final
where source_unverifiable is distinct from true
  and qna_trustworthiness_attribute is not null
group by qna_trustworthiness_attribute
union all
select 'Clarity', qna_clarity_attribute, count(*)
from public.ratings_v4_final
group by qna_clarity_attribute
union all
select 'Usefulness', qna_usefulness_attribute, count(*)
from public.ratings_v4_final
group by qna_usefulness_attribute
order by metric, rating;

-- Report source-access missingness separately from answer quality. False does
-- not prove the video was watched; null means the older UI did not record it.
select
    count(*) as submitted_qnas,
    count(*) filter (where source_unverifiable = true) as source_access_failures,
    count(*) filter (where source_unverifiable is null) as legacy_source_status_unknown,
    count(*) filter (where source_unverifiable is distinct from true
        and qna_trustworthiness_attribute is not null) as trustworthiness_assessed
from public.ratings_v4_final;

-- Binary distributions. For Care Safety, Yes means a safety concern exists;
-- for Standalone, Yes means the Q&A stands alone.
select 'Care Safety' as metric, qna_care_safety_binary as rating, count(*) as n
from public.ratings_v4_final
group by qna_care_safety_binary
union all
select 'Standalone', qna_standalone_binary, count(*)
from public.ratings_v4_final
group by qna_standalone_binary
order by metric, rating;

-- Recommendation distribution.
select caregiver_recommendation, count(*) as n
from public.ratings_v4_final
group by caregiver_recommendation
order by n desc;

-- Q&As with independent human overlap, ready for agreement analysis.
select
    qa_uid,
    count(distinct annotator) as n_annotators,
    array_agg(distinct annotator order by annotator) as annotators
from public.ratings_v4_final
group by qa_uid
having count(distinct annotator) > 1
order by n_annotators desc, qa_uid;

-- Full overlapping rating rows. Export this result for weighted kappa,
-- Krippendorff ordinal alpha, exact/within-one agreement and adjudication.
with overlap as (
    select qa_uid
    from public.ratings_v4_final
    group by qa_uid
    having count(distinct annotator) > 1
)
select r.*
from public.ratings_v4_final r
join overlap using (qa_uid)
order by r.qa_uid, r.annotator, r.created_at;

-- Potential Trustworthiness failures or concerns for transcript adjudication.
select
    annotator, qa_uid, dataset, video, question_text, answer_text,
    qna_trustworthiness_attribute, qna_trustworthiness_issue, evaluator_comment
from public.ratings_v4_final
where source_unverifiable is distinct from true
  and (qna_trustworthiness_attribute in ('Fair', 'Poor')
       or (qna_trustworthiness_issue is not null
           and qna_trustworthiness_issue <> 'No issue'))
order by dataset, video, qa_uid, annotator;

-- Safety concerns for mandatory human review.
select
    annotator, qa_uid, dataset, video, question_text, answer_text,
    qna_care_safety_issue, evaluator_comment
from public.ratings_v4_final
where qna_care_safety_binary = 'Yes'
order by dataset, video, qa_uid, annotator;
