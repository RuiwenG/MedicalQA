# v4 evaluation SQL

These scripts create and inspect the database objects used by
`eval-v4-netlify/`.

For a **brand-new v4 database**, run in the Supabase SQL Editor:

1. `01_create_ratings_v4.sql` — one-time, non-destructive creation.
2. `02_verify_ratings_v4.sql` — read-only structure and permission checks.
3. `03_analysis_queries.sql` — read-only progress, quality and agreement inputs.

For the **existing v4 database**, apply the approved annotator form with:

1. `04_add_annotator_profile.sql` — adds the three nullable profile columns and
   refreshes the final view. It preserves collected rows and is safe to rerun.
2. `05_add_source_verification.sql` — adds the source-access flag and ensures
   an unavailable source cannot also store a Trustworthiness assessment.
3. `02_verify_ratings_v4.sql` — confirms columns in both table and view,
   profile constraints, and the existing insert-only browser permissions.
4. Deploy the rebuilt v4 Netlify ZIP after the database schema refresh.

## Source-access failures

The nullable boolean `source_unverifiable` records **Video unavailable or won’t
play**. True requires all three Trustworthiness fields to be null. It does
not prevent ratings of other criteria. False means no source-access issue was
reported; null is unknown for legacy submissions. Neither value proves the
video was watched. Do not encode unavailable evidence as Poor or No.

Script 03 excludes flagged rows from Trustworthiness label distributions and
reports their count separately. Other metrics retain their own denominators.
Apply script 04 before 05 if profile fields have not yet been added. Script 01
includes both updates for a new database. Both migrations preserve old rows.

Do not rerun the creation script on an existing database. The migration touches
only `public.ratings_v4` and `public.ratings_v4_final` and never changes v1-v3.

## Profile fields

| Column | Stored values |
| --- | --- |
| `annotator_group` | `clinical`, `care`, `family`, `student`, `other` |
| `annotator_other` | Short description when group is `other`; otherwise null |
| `dementia_care_experience` | `none`, `less_one_year`, `one_to_three_years`, `more_three_years` |

The four named groups mean clinical professionals, professional care/support
workers, family/unpaid caregivers, and healthcare students. No profession-level
subcategories or separate unpaid-care relationship answers are stored.

Profile columns are nullable to preserve pre-profile submissions. A supplied
profile must contain a valid group and experience. Others requires a nonblank
description of at most 200 characters. A blank legacy profile means **unknown**,
not no dementia-care experience.

The analysis queries use the most recently submitted profile per browser session
so earlier rows can be grouped after an annotator resumes. They count distinct
names separately from sessions; names alone are not a verified unique-person ID.
Existing submitted rows remain unchanged. Profiles entered by finished legacy
annotators become available centrally only after a subsequent rating submission
or edit.

Offline UI/session/payload/export checks:

```sh
node eval-v4-sql/tests/test_v4_profile.cjs
```

With local PostgreSQL tools installed, the migration/constraint/permission test
creates and removes its own isolated temporary cluster; it never uses Supabase:

```sh
node eval-v4-sql/tests/test_v4_sql.cjs
```

Optional real-browser verification, with Playwright installed (external requests
are intercepted and rating submissions are mocked):

```sh
node eval-v4-sql/tests/test_v4_browser.cjs
```

The creation script is intentionally separate from v3. It creates `ratings_v4` and
`ratings_v4_final`, and refuses to proceed if either name already exists. It
does not drop or modify earlier ratings.

The browser role receives only `INSERT` on the raw table and sequence access
needed for the identity column. It receives no `SELECT`, `UPDATE`, or `DELETE`
access and no access to the final view. Analysis should be run by an authorized
Supabase dashboard or database user.
