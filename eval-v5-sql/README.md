# MedicalQA v5 SQL: Phase 0 pilot

The current package loads 25 selected Qwen3.5-9B Q&As into five groups of five.
Each group has 3 high-rated natural pairs, 1 low-rated natural pair and 1
negative, matching `eval/topics/runs/review_set_qwen3.5-9b-25/`. Selection
labels stay out of the participant UI. Follow `../eval-v5-DEPLOY.md` for the
matching Netlify deployment.

Existing v5 tables are reused; no separate table is needed per group. Earlier
v1-v4 objects remain isolated. Files and ZIPs do not update Supabase until
run in its SQL Editor.

## New v5 database

Run these entire files in order:

1. `00_check_v5_state.sql`: confirm this is a new v5 schema.
2. `01_create_v5.sql`: create the schema once, with 5 pairs per group.
3. `04_load_version_pairs_v5.sql`: configure and load the 25 IDs, once.
4. `02_verify_v5.sql`: check counts, roles, permissions and isolation.
5. `03_analysis_queries.sql`: optional read-only progress summaries.

The loader and site data are generated together from the agreed selection:

```sh
python3 eval-v5-netlify/build_versions.py --selection eval-v5-netlify/selection.json
```

The SQL stores each pair's group, position, UID, dataset, video and approach.
The matching site holds exact Q&A text and source ranges. Rating rows retain
the displayed question, answer and source times. The 20 natural pairs use the
widened ranges; five negatives retain generated ranges.

## Existing v5 database

1. Run 00. For an old single-role or multi-role schema without batches, run
   `05_upgrade_v5_multi_role.sql` before loading the pilot. It is safe to rerun
   on the current schema and preserves existing assignments and ratings.
2. Inspect counts in `version_pairs_v5`, `assignments_v5` and `ratings_v5`.
3. If all are zero, run 04. It atomically sets `pairs_per_version = 5` even
   if yesterday's empty schema was configured for 20, then inserts 25 IDs.
4. Run 02 and deploy the matching Netlify package.

04 refuses to run when any pairs, assignments or ratings exist. It does not
replace a running study or delete data. Review any earlier placeholder/test
records before removing clearly identified test rows manually, ratings first.
Preserve real participant data and plan a separate phase instead of replacing
its groups. An aborted SQL transaction can be ended with `ROLLBACK;` before
retrying after the cause is resolved.

05 runs in one transaction under the routing advisory lock. It upgrades role
arrays/descriptions, rating validation, signup/routing, reporting views, batch
support, source-unavailable handling and browser permissions. It adds the
capacity alert and changes an old 4/2 quota to 3/2 on the first batch upgrade;
later reruns preserve configured quotas. It does not choose or replace Q&As.

Legacy role migration: clinical/care become healthcare; student, family and
other retain their labels. Historical `is_medical` classifications remain as
originally routed, including care workers previously treated as non-medical.
For historical analysis use stored `is_medical`, not a recomputed role mapping.

## Expected pilot configuration

| Setting | Value |
| --- | --- |
| Groups per batch | 5 |
| Pairs per group | 5 |
| Selected pairs | 25, distinct across groups |
| Medical base slots per group | 3 |
| Non-medical base slots per group | 2 |
| Assignment lease | 48 hours |
| Capacity alert | 15 people holding slots in newest batch |

Roles remain healthcare/student (medical) and family/other (non-medical).
Several roles can be selected; any medical role makes the signup medical.
Role descriptions are optional and limited to 200 characters each.

The base target is 25 people across five groups: 15 medical and 10 non-medical.
It is not a strict cutoff. When a category fills across loaded groups, extra
raters go to the group with the fewest total held slots, lowest number on ties.
Completed assignments retain their slot; unfinished expired assignments release
it. Late ratings remain stored and marked late.

Completion requires ratings for all five distinct Q&As in an assignment's
group. Revisions do not count twice. The database decides membership, copies
profile/group/source metadata into ratings and rejects a pair from another group.

## Objects and permissions

| Object | Purpose |
| --- | --- |
| `study_settings_v5` | Group size, quotas, lease and alert threshold |
| `role_categories_v5` | Role-to-medical mapping |
| `version_pairs_v5` | Selected IDs and source metadata by group/position |
| `assignments_v5` | Claim, profile, group, lease and completion |
| `ratings_v5` | Append-only rating revisions |
| `ratings_v5_final` | Latest rating per assignment and Q&A |
| `assignment_status_v5` | Progress and completion status |
| `claim_version_v5(...)` | Atomic group allocation |
| `version_status_v5()` | Held medical/non-medical slots per group |
| `capacity_alerts_v5` / `batch_status_v5()` | Batch-capacity reporting |

The browser's `anon` role can execute the claim function and argument-only
role validator, and insert ratings using the sequence. It cannot read the
study tables/views, change assignments, or update/delete ratings. Rating
triggers validate membership and retain the assignment's true profile. The
read-only verification script checks grants, policies, triggers and separation
from earlier study objects. The source instruction highlight and button label
wrapping need no additional SQL migration.

## Later batches

Append five more same-sized groups to the selection and rebuild. Deploy the
expanded site first, then run the generated `04_load_version_pairs_v5_batch2.sql`
for groups 6-10. It checks group size and exact earlier membership, refuses
already-loaded groups and preserves earlier assignments/ratings. Future batch
alerts behave the same way. Existing assigned group contents/size stay frozen.

## Tests and packages

From the full repository (not an extracted SQL ZIP):

```sh
python3 eval-v5-sql/tests/test_v5_selection.py
node eval-v5-sql/tests/test_v5_ui.cjs
node eval-v5-sql/tests/test_v5_sql.cjs
```

Tests cover the agreed 3/1/1 mix, matching source text/ranges and hashes,
UI assignment/resume/exports, five-rating completion, routing/concurrent
claims, future batches, legacy upgrades, permission boundaries and v4 isolation.
PostgreSQL uses an isolated temporary cluster, never Supabase.

`eval-v5-sql.zip` contains this folder plus the deployment checklist.
`eval-v5-netlify.zip` contains matching site files at its archive root.
The Netlify package is configured for medicalqa-eval with its project URL and
public anon key. Check that it points to the project where these SQL files ran.
