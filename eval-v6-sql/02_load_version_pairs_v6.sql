-- Matches the five groups in versions.json shipped with both v6 site folders.
-- Loads batch 1 (versions 1-5, 5 x 5 Q&As) of the v6 study into public.version_pairs_v6.
-- qa_data.json sha256: b2624f8a15e4e70a190397e325b3ceef01c1a723e55bf605497facae6aae9030
--
-- Run after 01_create_v6.sql and before sharing the site. Existing selections,
-- assignments, and ratings cause this transaction to stop before any write.

begin;
select pg_advisory_xact_lock(hashtext('public.claim_version_v6'));

do $$
begin
    if (select count(*) from public.study_settings_v6) <> 1 then
        raise exception 'expected one v6 study-settings row; nothing changed';
    end if;
    if exists (select 1 from public.version_pairs_v6)
       or exists (select 1 from public.assignments_v6)
       or exists (select 1 from public.ratings_v6) then
        raise exception
            'version_pairs_v6 is already loaded or assignments exist; nothing changed';
    end if;
end
$$;

-- Configure this unused study only. Existing assignments/pairs are guarded above.
update public.study_settings_v6 set pairs_per_version = 5;

insert into public.version_pairs_v6
    (eval_version, position, qa_uid, dataset, video, approach)
values
    (1, 1, 'ODJ_v10_SingleAgent-qwen3.5-9b-v3_q2', 'ODJ', 10, 'SingleAgent-qwen3.5-9b-v3'),
    (1, 2, 'Master_v9_SingleAgent-qwen3.5-9b-v3_q5', 'Master', 9, 'SingleAgent-qwen3.5-9b-v3'),
    (1, 3, 'ODJ_v11_SingleAgent-qwen3.5-9b-v3_q10', 'ODJ', 11, 'SingleAgent-qwen3.5-9b-v3'),
    (1, 4, 'SUP_v14_SingleAgent-qwen3.5-9b-v3_q12', 'SUP', 14, 'SingleAgent-qwen3.5-9b-v3'),
    (1, 5, 'Master_v1_SingleAgent-qwen3.5-9b-bad-v3_q12', 'Master', 1, 'SingleAgent-qwen3.5-9b-bad-v3'),
    (2, 1, 'SUP_v4_SingleAgent-qwen3.5-9b-v3_q31', 'SUP', 4, 'SingleAgent-qwen3.5-9b-v3'),
    (2, 2, 'GEMS_v2_SingleAgent-qwen3.5-9b-v3_q10', 'GEMS', 2, 'SingleAgent-qwen3.5-9b-v3'),
    (2, 3, 'SUP_v25_SingleAgent-qwen3.5-9b-v3_q42', 'SUP', 25, 'SingleAgent-qwen3.5-9b-v3'),
    (2, 4, 'SUP_v30_SingleAgent-qwen3.5-9b-v3_q28', 'SUP', 30, 'SingleAgent-qwen3.5-9b-v3'),
    (2, 5, 'ODJ_v30_SingleAgent-qwen3.5-9b-bad-v3_q2', 'ODJ', 30, 'SingleAgent-qwen3.5-9b-bad-v3'),
    (3, 1, 'SUP_v11_SingleAgent-qwen3.5-9b-v3_q6', 'SUP', 11, 'SingleAgent-qwen3.5-9b-v3'),
    (3, 2, 'SUP_v21_SingleAgent-qwen3.5-9b-v3_q2', 'SUP', 21, 'SingleAgent-qwen3.5-9b-v3'),
    (3, 3, 'ODJ_v21_SingleAgent-qwen3.5-9b-v3_q4', 'ODJ', 21, 'SingleAgent-qwen3.5-9b-v3'),
    (3, 4, 'SUP_v29_SingleAgent-qwen3.5-9b-v3_q4', 'SUP', 29, 'SingleAgent-qwen3.5-9b-v3'),
    (3, 5, 'Teepa_v3_SingleAgent-qwen3.5-9b-bad-v3_q5', 'Teepa', 3, 'SingleAgent-qwen3.5-9b-bad-v3'),
    (4, 1, 'GEMS_v2_SingleAgent-qwen3.5-9b-v3_q11', 'GEMS', 2, 'SingleAgent-qwen3.5-9b-v3'),
    (4, 2, 'FTD_v1_SingleAgent-qwen3.5-9b-v3_q5', 'FTD', 1, 'SingleAgent-qwen3.5-9b-v3'),
    (4, 3, 'ODJ_v16_SingleAgent-qwen3.5-9b-v3_q3', 'ODJ', 16, 'SingleAgent-qwen3.5-9b-v3'),
    (4, 4, 'SUP_v22_SingleAgent-qwen3.5-9b-v3_q25', 'SUP', 22, 'SingleAgent-qwen3.5-9b-v3'),
    (4, 5, 'ODJ_v1_SingleAgent-qwen3.5-9b-bad-v3_q3', 'ODJ', 1, 'SingleAgent-qwen3.5-9b-bad-v3'),
    (5, 1, 'Teepa_v15_SingleAgent-qwen3.5-9b-v3_q3', 'Teepa', 15, 'SingleAgent-qwen3.5-9b-v3'),
    (5, 2, 'FTD_v1_SingleAgent-qwen3.5-9b-v3_q1', 'FTD', 1, 'SingleAgent-qwen3.5-9b-v3'),
    (5, 3, 'Teepa_v1_SingleAgent-qwen3.5-9b-v3_q6', 'Teepa', 1, 'SingleAgent-qwen3.5-9b-v3'),
    (5, 4, 'SUP_v18_SingleAgent-qwen3.5-9b-v3_q34', 'SUP', 18, 'SingleAgent-qwen3.5-9b-v3'),
    (5, 5, 'ODJ_v22_SingleAgent-qwen3.5-9b-bad-v3_q1', 'ODJ', 22, 'SingleAgent-qwen3.5-9b-bad-v3');

do $$
begin
    if (select count(*) from public.version_pairs_v6) <> 25 then
        raise exception 'expected 25 version pairs';
    end if;
end
$$;

commit;
