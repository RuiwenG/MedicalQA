-- Read-only operational and analysis queries for MedicalQA evaluation v5.
-- Run individual statements as needed in the Supabase SQL Editor.

-- Routing status: slots held per version, as claim_version_v5 counts them.
-- "Held" = completed, or still inside the 48-hour lease.
select * from public.version_status_v5();

-- Every assignment, newest first, with its progress.
select annotator, array_to_string(annotator_groups, ', ') as roles, is_medical, eval_version, routed_by,
       status, qnas_rated, claimed_at, expires_at, completed_at
from public.assignment_status_v5
order by claimed_at desc;

-- Completed annotators per version and background. The study target is at
-- least 4 medical and 2 non-medical completions in every version.
select eval_version,
       count(*) filter (where status = 'completed' and is_medical)     as medical_completed,
       count(*) filter (where status = 'completed' and not is_medical) as non_medical_completed,
       count(*) filter (where status = 'active')                       as in_progress,
       count(*) filter (where status = 'expired')                      as expired
from public.assignment_status_v5
group by eval_version
order by eval_version;

-- Annotators per role and dementia-care experience (completed only). Someone
-- who ticked two roles is counted under each, so the rows can sum to more than
-- the number of annotators.
select role, dementia_care_experience, count(*) as annotators
from public.assignment_status_v5, unnest(annotator_groups) as role
where status = 'completed'
group by role, dementia_care_experience
order by role, dementia_care_experience;

-- Combinations of roles as ticked (completed only).
select array_to_string(annotator_groups, ' + ') as roles, count(*) as annotators
from public.assignment_status_v5
where status = 'completed'
group by annotator_groups
order by annotators desc;

-- Ratings that arrived after their assignment's lease ran out. They are kept;
-- decide per analysis whether to include them.
select a.annotator, a.eval_version, a.status, count(*) as late_ratings
from public.ratings_v5 r
join public.assignment_status_v5 a on a.id = r.assignment_id
where r.late
group by a.annotator, a.eval_version, a.status
order by late_ratings desc;

-- Raters per Q&A and the Trustworthiness label distribution, latest row per
-- assignment and Q&A. Source-access failures are counted separately and never
-- enter the Trustworthiness denominator.
select r.eval_version, r.qa_uid,
       count(*) as raters,
       count(*) filter (where r.source_unverifiable) as source_unavailable,
       count(*) filter (where r.qna_trustworthiness_attribute in ('Excellent', 'Good')) as trust_good_or_better,
       count(r.qna_trustworthiness_attribute) as trust_rated
from public.ratings_v5_final r
group by r.eval_version, r.qa_uid
order by r.eval_version, r.qa_uid;

-- Medical vs non-medical view of each criterion, completed assignments only.
select a.is_medical,
       count(*) as ratings,
       round(avg((r.qna_standalone_binary = 'Yes')::int) * 100, 1)   as standalone_yes_pct,
       round(avg((r.qna_care_safety_binary = 'Yes')::int) * 100, 1)  as care_safety_yes_pct,
       round(avg((r.caregiver_recommendation = 'Yes')::int) * 100, 1) as recommend_as_is_pct
from public.ratings_v5_final r
join public.assignment_status_v5 a on a.id = r.assignment_id
where a.status = 'completed'
group by a.is_medical
order by a.is_medical desc;
