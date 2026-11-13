# v5 Phase 0 deployment: 25 Q&As in five groups

This release contains the agreed Qwen3.5-9B pilot selection: five different
groups of five Q&As. Each group has three high-rated natural outputs, one
low-rated natural output and one deliberately negative output. These are
selection labels, not human-verified quality labels. The participant UI hides
them and preserves all generated wording.

The group IDs match `eval/topics/runs/review_set_qwen3.5-9b-25/` and the
27 October researcher PDF. Both regular and negative pairs use the widened
source ranges in the v5 site. Each participant's order is
shuffled using the assignment ID and remains stable on resume.

## 1. Check the database

Run `eval-v5-sql/00_check_v5_state.sql` in the intended Supabase SQL Editor.

| State | Next step |
| --- | --- |
| New v5 | Run `01_create_v5.sql` once. |
| Old single-role or multi-role v5 without batches | Run `05_upgrade_v5_multi_role.sql`, which preserves existing records. |
| Current multi-role v5 | Continue with the counts below. |
| Incomplete/unrecognized | Inspect the reported missing objects first. |

Check whether a selection or participants already exist:

```sql
select (select count(*) from public.version_pairs_v5) as pairs_loaded,
       (select count(*) from public.assignments_v5) as assignments,
       (select count(*) from public.ratings_v5) as ratings;
```

Loader 04 requires all three counts to be zero and never deletes data. If an
old placeholder was loaded, review its records first. Only clearly identified
test records should be removed manually: ratings, assignments, version pairs,
then capacity alerts. Preserve real participant records and plan a separate
study/phase rather than replacing their groups.

## 2. Rebuild the matching site and SQL

The agreed IDs are already in `eval-v5-netlify/selection.json`. From the repo:

```sh
python3 eval-v5-netlify/build_versions.py --selection eval-v5-netlify/selection.json
```

The manifest must show `provisional: false`, `n_versions: 5`,
`pairs_per_version: 5`, `total_pairs: 25`, and
`pairs_with_widened_timestamp: 25`. It records the source commit, selection
source and file hashes. Existing non-provisional group IDs/order are frozen.

## 3. Load and verify the pilot

Run the entire `eval-v5-sql/04_load_version_pairs_v5.sql` once. On an unused
v5 schema, it sets `pairs_per_version = 5` and inserts 25 group-membership
records in one transaction. An empty database with the previous value of 20
is configured automatically. Existing pairs, assignments or ratings cause an
error and roll back without changes.

Then run `02_verify_v5.sql`. Expect:

- Groups 1-5, each with five pairs and five distinct videos.
- 20 normal Qwen3.5-9B v3 pairs and 5 negative-v3 pairs.
- Five groups per batch; five pairs per group; 3 medical and 2 non-medical
  base slots per group; a 48-hour lease; capacity alert at 15 people.
- Zero malformed groups and no dependencies on earlier study objects.

`version_pairs_v5` stores membership and source identifiers. The matching
`qa_data.json` contains questions, answers and playback ranges. Each rating
also retains its displayed text and source times. No separate table is
needed for each group.

## 4. Configure and deploy Netlify

1. Extract `eval-v5-netlify.zip` or use the source folder. The ZIP has the
   site files directly at its root.
2. Check `SUPABASE_URL` and `SUPABASE_ANON_KEY` in that folder's `config.js`.
   The source and ZIP are configured for the medicalqa-eval project with its
   public anon key. Change these values only when targeting another project.
3. Upload the folder to the existing v5 Netlify site, then refresh open pages.
   Keep v1-v4 sites separate.

The package includes `transcripts.json` for all 23 selected videos. After
Standalone, reviewers can open **Read the clip transcript** beside the video
and read only the source-caption lines overlapping that clip's time range,
joined as plain text. The time range appears once in the heading, with no
per-line timestamps or full-video transcript option.
Deploy the complete package so this asset is included. This transcript-only
UI update does not require running SQL again or changing existing assignments.

Without Supabase configuration, the page runs in local-only testing mode;
ratings are not collected centrally and server quotas do not apply.

## 5. Smoke test

1. Use a clearly named test annotator. The page should promise five Q&As,
   with no provisional-selection banner.
2. Check one `rpc/claim_version_v5` request. Reload: the same assignment,
   five IDs, order and progress should resume without a second claim.
3. Check a widened clip against `qa_data.json`. The range is marked
   approximate, and the full-video link permits more context.
   Open the clip transcript and verify the same range in its heading.
   Confirm only the clip's captions appear as plain text, without per-line
   timestamps or a full-video option.
   Let the clip finish and play it again: it should restart at the displayed
   starting time using the player's own controls.
4. Submit five distinct ratings. Completion occurs after the fifth; revising
   one rating does not count as another item.
5. Inspect `version_status_v5()` and `assignment_status_v5`, then remove only
   the smoke-test records, ratings first.

## Routing and later phases

The base target is 3 medical and 2 non-medical participants per group. Once a
category fills across loaded groups, extra raters are balanced by total group
size. This is a target, not a hard cutoff. At 15 people in the newest batch,
`capacity_alerts_v5` raises the reminder to prepare groups 6-10; it does not
create those groups.

Later batches can append five more groups of the same size. Deploy the
rebuilt site before loading a new batch. Changing already assigned groups or
their size requires a separate phase plan.

## Local checks

```sh
python3 eval-v5-sql/tests/test_v5_selection.py
node eval-v5-sql/tests/test_v5_ui.cjs
node eval-v5-sql/tests/test_v5_sql.cjs
```

Run from the full repository. PostgreSQL tests use a temporary cluster;
UI tests stub Supabase. Neither contacts the live study. Rebuilding files or
creating the ZIPs does not apply SQL, deploy Netlify or push to GitHub.
