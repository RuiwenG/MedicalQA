# Dementia Q&A — Human Evaluation Site

## Existing v3 study: timestamp-only update (2026-09-16)

**Do not rerun `supabase_schema.sql`. It drops the existing `ratings_v3` table
and would delete collected ratings. No SQL is needed for this update.**

This package intentionally retains the **225 Q&As already being evaluated**,
their text, IDs, order, 40-pair assignment, rubric, session keys, and database
settings. All 1,278 inactive records are also unchanged. The latest generated
`SingleAgent` corpus has 226 different Q&As and reuses IDs; replacing this
package with `eval/web` would therefore mix different content into existing
sessions. Do not copy the latest corpus over the ongoing study.

Instead, every active Q&A was matched by exact question, answer, dataset,
video, and video URL to the preserved `SingleAgent-v2` snapshot in the pulled
repository. That internal snapshot name does **not** rename this human study:
the UI still uses `SingleAgent`, study `v3`, and `ratings_v3`.

- 162 existing model-written timestamp ranges are unchanged.
- 63 previously missing ranges were recovered from matching transcript
  alignments: 54 bounded approximate clips and 9 weak/open-ended suggestions.
- The first 40 retain their exact identities/order: 33 existing ranges plus
  7 newly timestamped Q&As. Those 7 were inspected against transcript text;
  Teepa 13, Q1 was corrected and made open-ended because its examples and
  broader discussion occur in different passages.
- Estimated times are labelled approximate. Native timestamps are recorded
  model output, not a guarantee that every claim is supported by the clip.
- Catherine's September 16 recovery of 22 native timestamps belongs to the
  newer generated corpus, so those times were not copied onto different Q&As.

### Upload without resetting progress

1. Wait for the existing UI to say **All ratings saved**.
2. Upload **this folder or `eval-pilot-netlify-review.zip` to the existing v3
   Netlify site**. Keep its current address. Do not upload the v1 package there.
3. Return in the same browser/profile with the same evaluator name and
   selections. Do not clear browser storage or use a new deployment-preview
   URL to resume an existing session.
4. No Supabase changes or SQL rerun. Existing saved rows remain untouched;
   subsequent submissions carry the added timestamps.

Progress recovery is browser-local; it is not downloaded from Supabase.
Record when the update is deployed for analysis, because navigating to video
evidence becomes easier even though the Q&As and rubric are unchanged.

To reproducibly rebuild only this frozen study's timestamps from the pinned
Git snapshot, run from the repository root:

```sh
python3 eval-pilot-netlify-review/update_timestamps.py
python3 eval/test_v3_timestamps.py
node eval/test_v3_timestamp_ui.cjs
```

`timestamp_update_audit.json` records source IDs, old/new timestamp metadata,
content and transcript hashes, and the reviewed correction in
`timestamp_overrides.json`. The updater rejects changed question/answer text,
changed source videos, missing matches, and changes to existing timestamps.

---

A public, shareable version of `eval/QA_Educational_Eval_UI.ipynb`. Same five Q&A
metrics, same pilot form, same Q&As — but an annotator only needs a link and
a browser, and their ratings land in a database instead of a spreadsheet you
have to collect by email.

```
eval-pilot-netlify-review/
  index.html            the whole app — inline CSS + vanilla JS, no build step
  config.js             Supabase URL/key + study settings (edit this)
  qa_data.json          generated: { pairs: [...], videos: {...} }
  update_timestamps.py  updates timestamps in the pinned, frozen study only
  extract_qa_data.py    legacy extractor; do not run for the ongoing study
  supabase_schema.sql   destructive initial setup; DO NOT rerun during collection
```

`qa_data.json` holds 1,503 current and historical question-answer pairs, their
available source timestamps, and a `videos` map of URLs read from
`test_dataset.csv` (Master) and `teepa.csv` (Teepa). The active allowlist keeps
only the 225 frozen `SingleAgent` pairs in the human study. Rebuild timestamps
without replacing the corpus:

```
python3 eval-pilot-netlify-review/update_timestamps.py
```

### Active approach

`ONLY_APPROACHES: ["SingleAgent"]` in `config.js` drops every other approach
from the corpus at load time, before the shared ordered set is built. Excluded
pairs never enter the shared set, the advanced picker, or any count — annotators
cannot opt back in. `qa_data.json` is untouched, so changing the allowlist can
restore a comparison arm without regenerating the data file.

Do not change this package's `config.js` during the timestamp-only update.
Matching allowlists alone do not guarantee matching Q&A text across generations.

---

## 1. How ratings are stored

Three layers, so no single failure loses work:

1. **`localStorage`** holds the full session and is written after every rating.
   This is what powers resume — the browser never needs read access to the
   database, which is why the database key can be locked down so tightly.
2. **Supabase** receives one row per rated pair, fire-and-forget. Failures are
   queued and retried when the tab regains focus or the network returns.
3. **Download CSV / JSON** is always available on the finish screen, so even a
   completely misconfigured backend cannot lose an annotator's work.

The anon key is public — it ships in the page source. That is safe because the
`ratings` table grants `anon` **INSERT and nothing else**: no SELECT, no UPDATE,
no DELETE. Worst case a stranger inserts junk rows, which you filter out by
   `session_id`. Nobody can read or destroy real ratings.

The current UI writes to `ratings_v3`, configured through `RATINGS_TABLE` in
`config.js`. The current `supabase_schema.sql` intentionally resets that v3
table for the binary-only Standalone form. It does not change
`ratings_ui_v2_qna_v2` or the older `ratings` table.

## 2. Set up Supabase (about 15 minutes, free)

**Initial setup only. Skip this section for the ongoing v3 study.**

1. Create a project at [supabase.com](https://supabase.com).
2. SQL Editor → paste all of `supabase_schema.sql` → Run. This drops and
   recreates `ratings_v3` and `ratings_v3_final`, deleting any earlier v3 test
   rows. The SQL is wrapped in a transaction so a setup error rolls back the
   reset.
3. Project Settings → Data API → copy the **Project URL**.
   Project Settings → API keys → copy the **anon / public** key.
4. Paste both into `config.js`.

Verify the lockdown before sharing the link. In the browser console on the live
site, `SELECT` must fail:

```js
await fetch(`${EVAL_CONFIG.SUPABASE_URL}/rest/v1/${EVAL_CONFIG.RATINGS_TABLE}?select=*`,
  { headers: { apikey: EVAL_CONFIG.SUPABASE_ANON_KEY } }).then(r => r.json())
// expected: [] or a permission error — never actual rating rows
```

> **This check only proves something once the table has rows in it.** On an
> empty table, "you have no permission" and "there is nothing here" both come
> back as `[]`. Run it once, then run it **again after a few real ratings have
> landed** — that second run is the one that actually proves the lockdown.

> **Free-tier gotcha:** a Supabase project pauses after about a week with no
> activity and needs a manual restore from the dashboard. Irrelevant while
> annotation is running, but un-pause it before sending links after a quiet
> stretch.

Leaving the two values empty is a supported mode: everything works, a yellow
"local-only" banner appears, and annotators must download and email their CSV.

## 3. Deploy to Netlify

For this ongoing study, upload `eval-pilot-netlify-review/` or its matching ZIP
to the existing v3 site's deploy area. Do not create a new site or change the
address. The repo's default `netlify.toml` publishes `eval/web`, which now has
different Q&As; do not switch this ongoing study to that publish directory.

Locally: `cd eval-pilot-netlify-review && python -m http.server 8899`, then open
<http://localhost:8899>. Opening `index.html` directly will not work —
`file://` blocks the `fetch` of `qa_data.json`.

## 4. Sharing links with annotators

Send everyone the **same** URL. Each person types their name only to label their
ratings and resume progress. The name does not change which Q&As they receive.
In automatic mode, every evaluator receives the same ordered Q&A list.

| Parameter | Effect |
| --- | --- |
| `a` or `annotator` | pre-fills the name field |

### How the shared set is built

Automatic mode is deterministic and uses no shuffle:

1. Load all non-excluded Q&As.
2. Sort them by Q&A number, video, dataset, approach, and Q&A id. This is fixed
   and repeatable, but not randomized.
3. Apply `MAX_PAIRS` if configured.

For the short Netlify pilot, `MAX_PAIRS: 40` means everyone receives the same
first 40 ordered pairs. Set `MAX_PAIRS: 0` to make everyone rate all visible
pairs.

### Choosing videos and approaches by hand

**Advanced options → "Choose videos and approaches myself"** replaces the
automatic shared set with a direct picker: datasets, then a chip per video
grouped by dataset (with **All** / **None** per dataset), then approaches, then
metrics, blinding, and sample N per video+approach. Counts update live — each
approach shows how many pairs it contributes under the current video selection,
and a summary line gives the session total and a rough time estimate.

The **⚙ Options** button on the rating screen returns here at any point. Your
selections and name are remembered, and progress for that exact configuration is
restored, so you can go back, adjust, and carry on. Changing the selection starts
a separate session — the old one stays in `localStorage` and reappears if you
select the same options again.

### Embedded source video

Each video source URL is pulled from `test_dataset.csv` (Master) and `teepa.csv`
(Teepa) by the extract script:

- a small ▶ next to every video in the picker, so you can preview before choosing;
- an embedded YouTube player on each rating screen.

The deployment's `t`/`te` fields set the clip start/end. All 225 active pairs
now have a start. Approximate alignments are labelled; weak matches and any
start-only ranges play on without an end limit. The source-video link also
lets an evaluator explore beyond a suggested clip.

## 5. Getting the results out

Supabase → Table Editor → `ratings_v3_final` → Export CSV. Or straight into pandas:

```python
import pandas as pd
# Settings -> Database -> Connection string (use the pooler URI)
df = pd.read_sql("select * from ratings_v3_final", "postgresql://...")
df.groupby(["approach", "qna_trustworthiness_binary"]).size().rename("n").reset_index()
# Values are text labels. Apply the agreed score/code mapping after collection.
```

`ratings_v3` is append-only, so a Q&A re-rated via the Previous button
appears more than once. The `ratings_v3_final` view keeps only the
latest per `(session_id, qa_uid)` — use it for analysis and keep the raw table
as the audit trail.

The three scaled metrics store an attribute label (`*_attribute`). Every metric
stores a Yes/No label (`*_binary`) and an issue field (`*_issue`, joined with
`; `). Q&A Standalone and Q&A Care Safety are binary-only, so they have no
attribute column. The table also stores `caregiver_recommendation`,
`evaluator_comment`, and the exact `question_text` / `answer_text` that was
rated. These are text labels only; score/code mapping happens during analysis.

For inter-annotator agreement, use Q&As rated by more than one annotator:

```sql
select qa_uid, count(distinct annotator) as n
from ratings_v3_final group by qa_uid having count(distinct annotator) > 1;
```

## 6. Notes

- **Blinding.** The approach name is hidden while rating and only revealed in
  the summary at the end. It is always recorded in the stored row.
- **Markdown is shown raw.** Several pipelines emit `##` and `**bold**` in
  answers. That is deliberate — formatting noise is part of answer quality and
  should count against the relevant accessibility or usefulness judgments.
- **Keyboard.** `Y` marks the highlighted criterion as Yes, `N` marks it as
  No and shows problem types, and `Enter` submits the pair.
- **`seconds_spent`** is recorded per pair. Useful for spotting an annotator who
  clicked through 200 pairs in ten minutes.
- **Privacy.** Annotators type a name, not an email. Nothing else about them is
  collected, and the site sends no analytics. If this feeds a publication, check
  whether your IRB needs a consent line added to the welcome screen.
