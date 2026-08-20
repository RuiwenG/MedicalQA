# Dementia Q&A — Human Evaluation Site

A public, shareable version of `eval/QA_Educational_Eval_UI.ipynb`. Same four Q&A
metrics, same pilot form, same Q&As — but an annotator only needs a link and
a browser, and their ratings land in a database instead of a spreadsheet you
have to collect by email.

```
eval/web/
  index.html            the whole app — inline CSS + vanilla JS, no build step
  config.js             Supabase URL/key + study settings (edit this)
  qa_data.json          generated: { pairs: [...], videos: {...} }
  extract_qa_data.py    regenerates qa_data.json from the repo + dataset CSVs
  supabase_schema.sql   run once in the Supabase SQL editor
```

`qa_data.json` holds the question and answer for all 1,052 pairs (source
segments are stripped so every approach is judged on the same footing), plus a
`videos` map of source URLs read from `test_dataset.csv` (Master) and
`teepa.csv` (Teepa). Regenerate it whenever results change:

```
python eval/web/extract_qa_data.py
```

### Excluded approaches

`EXCLUDE_APPROACHES` in `config.js` drops approaches from the corpus at load
time, before the shared ordered set is built. DualAgent and RAG are currently excluded, leaving **452
pairs** across `MultiAgent-LLMChunking` and `SingleAgent`. Excluded pairs never
enter the shared ordered set, the advanced picker, or any count — annotators
cannot opt back in. `qa_data.json` is untouched, so emptying the list restores
them without regenerating anything.

Both `config.js` files must list the same exclusions, otherwise the pilot and
the study rate different corpora.

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

The current UI writes to `ratings_ui_v2_qna_v2`, configured through
`RATINGS_TABLE` in `config.js`. The earlier `ratings` table is left untouched for
old pilot data.

## 2. Set up Supabase (about 15 minutes, free)

1. Create a project at [supabase.com](https://supabase.com).
2. SQL Editor → paste all of `supabase_schema.sql` → Run. This creates the new
   `ratings_ui_v2_qna_v2` table and `ratings_ui_v2_qna_v2_final` view.
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

The repo root has a `netlify.toml` setting `publish = "eval/web"`, so:

1. Netlify → Add new site → Import an existing project → pick this repo.
2. Leave build command empty. Deploy.

To redeploy after regenerating results, run `python eval/web/extract_qa_data.py`,
commit `qa_data.json`, and push.

Drag-and-drop also works — drop the `eval/web` folder onto the Netlify dashboard.

Locally: `cd eval/web && python -m http.server 8899`, then open
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

When a QA pair has `time_start_sec` and `time_end_sec`, the player is embedded
as a clip for that segment. Some systems do not currently store timestamps; for
those pairs, the UI embeds the full source video and says the exact segment is
unavailable.

## 5. Getting the results out

Supabase → Table Editor → `ratings_ui_v2_qna_v2_final` → Export CSV. Or straight into pandas:

```python
import pandas as pd
# Settings -> Database -> Connection string (use the pooler URI)
df = pd.read_sql("select * from ratings_ui_v2_qna_v2_final", "postgresql://...")
df.groupby(["approach", "qna_trustworthiness_binary"]).size().rename("n").reset_index()
# Values are text labels. Apply the agreed score/code mapping after collection.
```

`ratings_ui_v2_qna_v2` is append-only, so a Q&A re-rated via the Previous button
appears more than once. The `ratings_ui_v2_qna_v2_final` view keeps only the
latest per `(session_id, qa_uid)` — use it for analysis and keep the raw table
as the audit trail.

Downloaded CSVs use the same column names the notebook writes to Excel, so
dropping them into `eval/results/` lets the notebook's aggregation cell pick
them up alongside the `.xlsx` files.

The current output stores, for each applicable metric, an attribute label
(`*_attribute`), one or more issue labels (`*_issue`, joined with `; `), and a
Yes/No label (`*_binary`). Q&A Care Safety is binary-only, and the table also
stores `caregiver_recommendation`, `evaluator_comment`, and the exact
`question_text` / `answer_text` that was rated. These are text labels only;
score/code mapping happens later during analysis.

For inter-annotator agreement, use Q&As rated by more than one annotator:

```sql
select qa_uid, count(distinct annotator) as n
from ratings_ui_v2_qna_v2_final group by qa_uid having count(distinct annotator) > 1;
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
