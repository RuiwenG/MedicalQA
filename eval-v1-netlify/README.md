# MedicalQA SingleAgent v1 human evaluation

This is a separate Netlify-ready evaluator for the **first-deployed
SingleAgent corpus**. It is isolated from the active SingleAgent v3 evaluation.

## Exact provenance

- Generation/output commit: `d580199` (2026-08-02)
- First Netlify folder: `a155b46` (2026-08-06)
- First Supabase-connected Netlify package: `2323ee9` (2026-08-06)
- Historical source file: `2323ee9:eval-pilot-netlify-review/qa_data.json`
- Historical source label: `SingleAgent`
- Unambiguous evaluation label used here: `SingleAgent-v1`

The SingleAgent records in the first connected Netlify package match the
`d580199` generated outputs exactly: 487 of 487 pairs. This is the original
fixed-20 SingleAgent generation, before the four-criterion prompt revision,
dynamic Q&A count, timestamps, and Standalone requirement.

Run `python build_v1_data.py` from inside the repository to reproducibly rebuild
`qa_data.json` from that Git snapshot, then merge the checked-in timestamp sidecar.

## Timestamp-only update (2026-09-16)

All 487 original Q&As now carry `t`/`te` source-transcript alignment ranges.
The original prompt never generated timestamps: these are **approximate**
matches to each unchanged v1 Q&A, not copied v3 clips or exact ground truth.
No questions, answers, IDs, ordering, assignment cap, rubric, session keys,
Supabase configuration, or schema were changed.

- 321 automatic matches meet the existing aligner's score threshold; 166 are
  weak. The score is a lexical similarity heuristic, not a probability.
- All first-40 candidate clips were inspected against their transcript text;
  9 ranges were corrected or expanded in `timestamp_overrides.json`. This is
  not a medical-correctness review or frame-by-frame video verification.
- The first 40 include 5 weak matches. Weak matches seek to the suggested start
  and play without an end limit; the UI clearly marks them as low-confidence.
- Some answers summarize several passages. Teepa video 8, Q1 refers to both
  the opening definition and the ending comparison, so it still spans that
  whole video. Do not assume every answer has one short supporting clip.
- `aligned_timestamps.json` records transcript/corpus hashes, automatic
  similarity scores, and reviewed overrides so the update can be audited.

To regenerate (offline; no model calls), run from the repository root:

```sh
python3 eval/align_v1_timestamps.py
python3 eval-v1-netlify/build_v1_data.py
python3 eval/test_v1_timestamps.py
node eval/test_v1_timestamp_ui.cjs
```

The builder rejects a sidecar belonging to different Q&As or transcripts.
Keep both timestamp JSON files with this deployment when rebuilding.

### Updating an existing evaluation

Wait for **All ratings saved**, then redeploy this folder/ZIP to the **existing
v1 Netlify site**, retaining its address. Do not replace the v3 site or change
the evaluation's configuration. No SQL rerun or Supabase reset is required
for timestamps; the schema already has the start/end columns.

Return using the same browser/profile, evaluator name, and selections; do not
clear browser storage. UI resume is local, not fetched from Supabase. Previously
saved rows are untouched and will still have their original (usually null)
timestamp values. New submissions include the added timestamps. Record the
deployment date when analysing pre-update versus post-update ratings, because
the ease of finding video evidence has changed.

## Fixed study configuration

- Available historical Q&As: 487
- Assignment cap: 40 Q&As per evaluator
- Study value stored in each row: `v1`
- Raw Supabase table: `public.ratings_v1`
- Final/latest view: `public.ratings_v1_final`
- Rubric: the same current rubric used by v3
- Standalone: binary Yes/No with four failure reasons

## Matched first-40 assignment

The current UI sorts by Q&A position, video number, dataset, approach, and UID.
Using the same 40-pair cap gives v1 and v3 exactly the same source-video and
within-video-position distribution:

- Master videos 1-8: two Q&As from each video (16 total)
- Master videos 9-10: one Q&A from each video (2 total)
- Teepa videos 1-7: two Q&As from each video (14 total)
- Teepa videos 8-15: one Q&A from each video (8 total)

This covers all 25 videos. The question text is not identical because v1 and v3
were independently generated; the controlled match is source video and Q&A
position within that video.

## Supabase setup

1. Open the same Supabase project used by the active evaluation.
2. Open SQL Editor and start a new query.
3. Paste the complete `supabase_schema.sql` from this folder. Run this corrected
   file again even if you previously created `ratings_v1`; it is safe to rerun.
4. Click Run.
5. Verify that `ratings_v1`, `ratings_v1_final`, `ratings_v3`, and
   `ratings_v3_final` exist using the query at the bottom of the schema.

The v1 schema is non-destructive: it does not delete or change v3 ratings. If
the discarded `SingleAgent-prev` package created test rows, they remain in the
raw audit table but are excluded from `ratings_v1_final`. New rows are required
to contain `study_version = 'v1'` and `approach = 'SingleAgent-v1'`.

## Netlify deployment

For an initial deployment, create a separate Netlify site and upload
`eval-v1-netlify.zip`. For this timestamp update, redeploy to the existing v1
site as described above. Never deploy over the active v3 site. Leave the build
command empty; this is a static site.

After deployment, submit one test rating and run:

```sql
select
  id,
  created_at,
  annotator,
  qa_uid,
  approach,
  study_version,
  qna_standalone_binary,
  qna_standalone_issue
from public.ratings_v1
order by created_at desc
limit 1;
```

The newest row should contain `approach = 'SingleAgent-v1'` and
`study_version = 'v1'`.
