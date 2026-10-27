# v5 deployment steps (Supabase + Netlify)

Everything needed is in this repository. This file is the checklist for
putting the current v5 release live. The details behind each step are in
`eval-v5-sql/README.md` and `eval-v5-netlify/README.md`.

## What this release changes

1. **Group assignment.** Groups (versions) come in batches of five. Each group
   takes 3 medical and 2 non-medical annotators (was 4/2), so 25 people fill
   groups 1–5. After that, newcomers are extra raters in groups 1–5 (fewest
   annotators first) until groups 6–10 are loaded, which then fill first.
2. **Capacity alert.** When 15 people hold a slot in the newest batch, a row
   appears in the new table `capacity_alerts_v5` ("prepare the Q&As for
   versions 6-10"). It repeats for every batch.
3. **Bad pairs allowed.** `SingleAgent-qwen3-14b-bad-v3` pairs can be part of
   the selection. The UI no longer shows the system name anywhere (rating
   screen or end screen), so annotators cannot tell which pairs are bad.
4. **Q&As come from one selection file**, `eval-v5-netlify/selection.json`
   (added later by the team; see step 2).

## 1. Database: check the current state

In the Supabase project's **SQL Editor**, run the whole of
`eval-v5-sql/00_check_v5_state.sql`. It is read-only. The `next_step` column
says which path applies:

| `next_step` starts with | Do |
| --- | --- |
| `NEW V5` | Run `eval-v5-sql/01_create_v5.sql`, then go to step 2. |
| `MULTI-ROLE V5 WITHOUT BATCHES` or `OLD SINGLE-ROLE V5` | Run `eval-v5-sql/05_upgrade_v5_multi_role.sql`, then go to step 2. |
| `MULTI-ROLE V5` | 05 has already been run with this release; rerunning it is safe. Go to step 2. |
| `INCOMPLETE` / `UNRECOGNIZED` | Stop and check with the team. |

Do not run 01 on a database that already has v5. Script 05 keeps all existing
assignments and ratings. The first time it runs, it changes the medical quota
from 4 to 3 per group.

Then check whether Q&As are already loaded:

```sql
select (select count(*) from public.version_pairs_v5) as pairs_loaded,
       (select count(*) from public.assignments_v5)   as assignments,
       (select count(*) from public.ratings_v5)       as ratings;
```

The loader in step 3 only runs on an empty `version_pairs_v5` with no
assignments. If the provisional placeholder pairs were loaded earlier and the
only assignments are smoke tests, delete those rows from the Table Editor
first, in this order: `ratings_v5`, `assignments_v5`, `version_pairs_v5` (and
`capacity_alerts_v5`, if it has any rows). **If any assignment belongs to a
real annotator, do not delete anything; ask the team first.**

## 2. Build the site files from the selection file

Put the agreed selection at `eval-v5-netlify/selection.json`: five lists of 20
pair ids per batch, groups 1–5 first.

```json
{"versions": [
  ["ODJ_v24_SingleAgent-qwen3-14b-bad-v3_q1", "Master_v1_SingleAgent-qwen3-14b-v3_q2", "... 20 ids"],
  ["... group 2"], ["... group 3"], ["... group 4"], ["... group 5"]
]}
```

Ids have the form `<Dataset>_v<video>_SingleAgent-qwen3-14b-<v1|v2|v3|bad-v3>_q<n>`.
From the repository root:

```sh
python3 eval-v5-netlify/build_versions.py --selection eval-v5-netlify/selection.json
```

This rewrites, together:

- `eval-v5-netlify/qa_data.json`, `versions.json`, `manifest.json` (the site)
- `eval-v5-sql/04_load_version_pairs_v5.sql` (the matching database loader)

It stops with an error if a group does not have exactly 20 distinct ids, or if
an id is not a pair in this repository. Check that `eval-v5-netlify/manifest.json`
says `"provisional": false` and that the comment at the top of the 04 file
does not say PROVISIONAL. Commit the rebuilt files so the site and database stay
traceable to one commit.

## 3. Database: load the Q&As and verify

In the SQL Editor:

1. Run `eval-v5-sql/04_load_version_pairs_v5.sql` once. The last result shows
   batch 1 (versions 1-5) with 0 held of 25 slots.
2. Run `eval-v5-sql/02_verify_v5.sql` and compare each result with the
   `Expected:` comment above it. In particular: settings show 5 / 20 / 3 / 2 /
   48 h / 15; versions 1–5 have 20 Q&As each; `insert` and `claim` are `t` and
   every other privilege is `f`; both separation queries return no rows.

## 4. Netlify

1. In `eval-v5-netlify/config.js`, fill in `SUPABASE_URL` (the project URL, no
   `/rest/v1`) and `SUPABASE_ANON_KEY` (the anon / publishable key). Leave
   everything else as it is. The approach allowlist already includes the bad
   pairs.
2. Deploy the `eval-v5-netlify/` folder to the v5 Netlify site (drag and drop
   the folder, or Git deployment with publish directory `eval-v5-netlify` and
   no build command). Do not overwrite the v4 site.

## 5. Smoke test

1. Open the site: the heading says `Human Evaluation v5` and there is **no**
   provisional banner.
2. Sign up as a clearly named test annotator (for example `TEST delete me`),
   rate one Q&A. The browser's network panel shows one `rpc/claim_version_v5`
   call. Reloading with the same name resumes without a second claim.
3. In SQL Editor: `select * from public.version_status_v5();` shows the test
   annotator, and the rating is in `ratings_v5` with the right
   `assignment_id` and `eval_version`.
4. Delete the test rows from the Table Editor (`ratings_v5` first, then
   `assignments_v5`) before sharing the link.

## While the study runs

- **Watch for the alert:** check the `capacity_alerts_v5` table in the Table
  Editor, or run `select * from public.batch_status_v5();`. When an alert row
  appears, tell the team so they can prepare the next five groups, and fill in
  the row's `acknowledged_at`.
- Progress and routing queries are in `eval-v5-sql/03_analysis_queries.sql`
  (all read-only).

## Adding groups 6–10 (and later batches)

1. The team appends five more lists of 20 ids to the same
   `eval-v5-netlify/selection.json` (groups 1–5 stay exactly as they are).
2. Rebuild with the same command as in step 2. It also writes
   `eval-v5-sql/04_load_version_pairs_v5_batch2.sql`, and refuses a selection
   that changes groups 1–5.
3. **Deploy the rebuilt site first** (step 4.2, same `config.js` values), so
   the browser knows groups 6–10 before anyone is routed to them.
4. Then run `04_load_version_pairs_v5_batch2.sql` once in SQL Editor. It
   refuses to run if groups 1–5 in the database differ from the site's or
   batch 2 is already loaded. The last result shows both batches.

Batch 3 (groups 11–15) works the same way with `..._batch3.sql`.
