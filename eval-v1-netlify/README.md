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
`qa_data.json` from that Git snapshot.

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

Create a separate Netlify site and upload `eval-v1-netlify.zip`. Do not deploy
it over the active v3 site. Leave the build command empty; this is a static site.

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
