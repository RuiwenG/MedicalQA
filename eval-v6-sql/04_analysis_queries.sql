-- Read-only operational and analysis queries for MedicalQA evaluation v6.
-- Run individual statements as needed in the Supabase SQL Editor.

-- Routing status: slots held per version, as claim_version_v6 counts them.
-- "Held" = completed, or still inside the 48-hour lease.
select * from public.version_status_v6();

-- Per batch of five versions: slots held out of 25, and the capacity alert
-- (raised at 15 people in the latest batch: prepare the next batch's Q&As).
select * from public.batch_status_v6();
select * from public.capacity_alerts_v6 order by batch;

-- Every assignment, newest first, with its progress.
select annotator, array_to_string(annotator_groups, ', ') as roles, is_medical, eval_version, routed_by,
       status, qnas_rated, claimed_at, expires_at, completed_at
from public.assignment_status_v6
order by claimed_at desc;

-- Completed annotators per version and background. The study target is
-- 3 medical and 2 non-medical completions in every version.
select eval_version,
       count(*) filter (where status = 'completed' and is_medical)     as medical_completed,
       count(*) filter (where status = 'completed' and not is_medical) as non_medical_completed,
       count(*) filter (where status = 'active')                       as in_progress,
       count(*) filter (where status = 'expired')                      as expired
from public.assignment_status_v6
group by eval_version
order by eval_version;

-- Annotators per role and dementia-care experience (completed only). Someone
-- who ticked two roles is counted under each, so the rows can sum to more than
-- the number of annotators.
select role, dementia_care_experience, count(*) as annotators
from public.assignment_status_v6, unnest(annotator_groups) as role
where status = 'completed'
group by role, dementia_care_experience
order by role, dementia_care_experience;

-- Combinations of roles as ticked (completed only).
select array_to_string(annotator_groups, ' + ') as roles, count(*) as annotators
from public.assignment_status_v6
where status = 'completed'
group by annotator_groups
order by annotators desc;

-- Ratings that arrived after their assignment's lease ran out. They are kept;
-- decide per analysis whether to include them.
select a.annotator, a.eval_version, a.status, count(*) as late_ratings
from public.ratings_v6 r
join public.assignment_status_v6 a on a.id = r.assignment_id
where r.late
group by a.annotator, a.eval_version, a.status
order by late_ratings desc;

-- Video alignment responses per Q&A, using the latest rating from each rater.
select r.eval_version, r.qa_uid,
       count(*) as raters,
       count(*) filter (where r.source_unverifiable) as video_skipped,
       count(*) filter (where r.qna_trustworthiness_attribute = 'Fully matches') as fully_matches,
       count(*) filter (where r.qna_trustworthiness_attribute = 'Partially matches') as partially_matches,
       count(*) filter (where r.qna_trustworthiness_attribute = 'Does not match') as does_not_match,
       count(*) filter (where r.qna_trustworthiness_attribute = 'Cannot determine') as cannot_determine
from public.ratings_v6_final r
group by r.eval_version, r.qa_uid
order by r.eval_version, r.qa_uid;

-- Medical vs non-medical view of each criterion, completed assignments only.
select a.is_medical,
       count(*) as ratings,
       round(avg((r.qna_standalone_binary = 'Yes')::int) * 100, 1)   as standalone_yes_pct,
       round(avg((r.qna_care_safety_binary = 'Yes')::int) * 100, 1)  as safety_concern_yes_pct,
       round(avg((r.qna_care_safety_binary = 'Not sure')::int) * 100, 1) as safety_concern_not_sure_pct,
       round(avg((r.caregiver_recommendation = 'Yes')::int) * 100, 1) as recommend_as_is_pct,
       round(avg((r.caregiver_recommendation = 'With reservations')::int) * 100, 1) as recommend_with_reservations_pct
from public.ratings_v6_final r
join public.assignment_status_v6 a on a.id = r.assignment_id
where a.status = 'completed'
group by a.is_medical
order by a.is_medical desc;
