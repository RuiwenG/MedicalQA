# Dementia Q&A human evaluation — v4 Netlify package

This is a new, isolated v4 evaluation package built from the latest
`eval/web` UI. It does not change the frozen v3 package in
`eval-pilot-netlify-review/`.

## Study contract

- Supabase raw table: `public.ratings_v4`
- Latest-row view: `public.ratings_v4_final`
- Study version sent by the browser: `v4`
- Active approach: `SingleAgent`
- Corpus snapshot: 2,634 total records, 226 active records across 25 videos
- Fixed annotator assignment: the same first 40 deterministically ordered pairs
- Browser storage namespace: `dqa:v4:*`, isolated from earlier studies

The corpus hash and source commit are recorded in `manifest.json`.

## Fixed assignment update

Advanced options have been removed: annotators cannot change videos,
approaches, sample size, pair count,
rating criteria, or blinding. All annotators receive the same 40 Q&As in the
existing order, rate all five criteria, and see model names hidden during rating.

Existing default-session keys and progress are preserved. Previously saved
custom preferences are ignored, and separately saved custom sessions are left
untouched; they are not converted into the shared assignment. If an existing
session at the shared key contains a different assignment, the UI asks the
annotator to contact the research team without overwriting their saved progress.

The fixed-assignment change itself needs no SQL. The annotator-profile update
below does require the v4 migration before deploying the current ZIP.
Configuration, corpus, question order, and existing rating criteria are unchanged.

```sh
node eval-v4-sql/tests/test_v4_profile.cjs
```

## Annotator profile

Below the name, the welcome page has two short questions: **Your role** and
**Dementia-care experience**. Role has exactly four groups plus **Others**, with
examples in the labels. There are no individual-profession subcategories and no
separate unpaid-care relationship question. Selecting Others opens a short
required description box.

The group, optional Others description, and experience accompany each rating
and are included in CSV/JSON exports. Profiles are stored with the existing
browser session; they do not change the session key or question assignment.
Returning annotators keep their ratings and retry queue, and profiles are
restored for the matching name. Legacy sessions are asked for the two new answers
before continuing. Their pending rows receive the selected profile; ratings
already submitted are not rewritten.

**For the existing v4 site:** run `eval-v4-sql/04_add_annotator_profile.sql` and
`05_add_source_verification.sql` in the Supabase SQL Editor, then
`02_verify_ratings_v4.sql`. These migrations add nullable fields and refresh
`ratings_v4_final`, preserving collected data.
Wait for the schema refresh, then upload `eval-v4-netlify.zip` to the existing v4
Netlify site. Do not rerun `01_create_ratings_v4.sql` on an existing database.

For a new v4 database, the updated `01_create_ratings_v4.sql` already includes
these fields. Neither route changes any other study version.

## Rating layout and source-access update

The question panel uses warm yellow and the answer panel uses soft blue. At desktop widths
(1000 px and above), the Q&A stays beside the rating form in a sticky reference
panel. On narrower screens, **View Q&A** and **Back to ratings** move between
the text and the active criterion without changing any answers.

The instructions consistently ask for Standalone before opening the video,
then source verification before scoring Trustworthiness. The optional **Video
unavailable or won’t play** checkbox is only for playback/source-access problems.
An accessible source that does not support the answer still receives a normal
quality rating.

When the checkbox is selected, `source_unverifiable=true` is submitted and the
three Trustworthiness fields are null. Other criteria and the overall
recommendation remain required. The local draft retains any earlier choice
for an explicit uncheck, but database payloads, CSV and JSON exports never
present that draft as an assessed score. False means no access problem was
reported, not proof the source was watched; null is unknown for older rows.
Analysis reports access problems separately and excludes them from the
Trustworthiness score denominator. They are never counted as Poor or No.

For an existing v4 database, run `eval-v4-sql/05_add_source_verification.sql`
before deploying this UI. If the earlier profile migration has not been
applied, run script 04 first. A new database's script 01 includes both updates.
These are local package changes until the SQL and UI are deployed.

```sh
node eval-v4-sql/tests/test_v4_source.cjs
```

## Video playback update

The updated package uses `strict-origin-when-cross-origin` for YouTube embeds.
YouTube requires the embedding site's HTTP Referer; suppressing it with
`no-referrer` caused Error 153. Cross-origin requests now identify only the site
origin, without the page path or evaluator query parameters.

Each source panel displays its recorded or approximate time range, plus a link
that opens YouTube at the start time. Weak transcript matches stay open-ended.
YouTube's playback controls and timeline are explicitly enabled.

To apply this fix, upload the rebuilt `eval-v4-netlify.zip` to the existing v4
Netlify site's deploy area and refresh the page. No SQL is required. The corpus,
rating fields, session keys, and saved progress are unchanged by this update.
Source-video regression checks can be run with:

```sh
node eval/test_v4_video_ui.cjs
```

## Files

```text
eval-v4-netlify/
  index.html              static evaluation application
  config.js               v4 table, study and sampling settings
  qa_data.json            versioned corpus snapshot
  manifest.json           source commit, counts and corpus checksum
  _headers                Netlify cache and no-index headers
  extract_qa_data.py      rebuilds this folder's qa_data.json
  aligned_timestamps.json timestamp fallbacks used by the extractor
```

The database scripts live separately in `eval-v4-sql/` so dragging this folder
to Netlify cannot accidentally expose or encourage rerunning schema setup.

## Create the v4 database objects

1. In the Supabase SQL Editor, run
   `eval-v4-sql/01_create_ratings_v4.sql` exactly once.
2. Run `eval-v4-sql/02_verify_ratings_v4.sql` and check that the raw table and
   final view exist, `anon` can insert, and `anon` cannot select/update/delete.
3. Do not rerun the v3 schema. The v4 setup creates new objects and does not
   modify any v1-v3 table.

The v4 creation script deliberately refuses to run if `ratings_v4` or
`ratings_v4_final` already exists. It never drops an existing ratings table.

## Test locally

From the repository root:

```sh
python3 -m http.server 8899 --bind 127.0.0.1 --directory eval-v4-netlify
```

Open `http://127.0.0.1:8899`. Opening `index.html` through `file://` will not
work because browsers block the `qa_data.json` request.

Before collecting real ratings, submit one clearly named smoke-test session and
confirm it with:

```sql
select *
from public.ratings_v4
order by created_at desc
limit 1;
```

Remove test rows from the Supabase dashboard before launch; do not put a broad
delete statement into a reusable setup script.

## Deploy as a new Netlify site

Create a new site for v4. Do not overwrite the ongoing v3 site.

- Drag-and-drop: upload `eval-v4-netlify/` or `eval-v4-netlify.zip`.
- Git deployment: configure the new site's publish directory as
  `eval-v4-netlify` and leave the build command empty.

After deployment, verify:

1. The page heading says `Human Evaluation v4`.
2. The welcome page has no advanced options; every session contains the same 40 Q&As.
3. Trustworthiness, Clarity and Usefulness have optional problem lists.
4. `Other` opens a required text box.
5. Dictation controls appear where browser speech recognition is available.
6. A submitted row lands in `ratings_v4`, not `ratings_v3`.
7. Refreshing with the same evaluator name resumes v4 progress.
8. The role dropdown contains four groups plus Others; Others opens a description.
9. The submitted row and exports include `annotator_group`, `annotator_other`,
   and `dementia_care_experience`.

## Rebuild the corpus

Only rebuild before data collection begins:

```sh
python3 eval-v4-netlify/extract_qa_data.py
```

After rebuilding, update the counts and SHA-256 in `manifest.json`, rebuild the
ZIP, rerun `python3 eval/test_v4_package.py`, and deploy to the v4 site. Never
replace the corpus after ratings have started unless the study is explicitly
versioned again.

## Export and analyze

Use `public.ratings_v4_final` for one latest row per
`(session_id, qa_uid)`. The raw `ratings_v4` table is append-only and retains
the edit history created when an evaluator revisits a Q&A. Ready-to-run checks
and summaries are in `eval-v4-sql/03_analysis_queries.sql`. Its profile queries
use the latest known profile per session to include older rows in group analysis.
Legacy sessions without any submitted profile remain unknown, not "None" for
experience. A finished legacy session needs a subsequent rating submission or
edit before its newly entered profile will be available centrally.
