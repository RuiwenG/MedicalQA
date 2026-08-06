# Dementia QA — Human Evaluation Site

A public, shareable version of `eval/QA_Educational_Eval_UI.ipynb`. Same four QA-pair
metrics, same pilot form, same QA pairs — but an annotator only needs a link and
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

`qa_data.json` holds the question and answer for all 1,587 pairs (source
segments are stripped so every approach is judged on the same footing), plus a
`videos` map of source URLs read from `test_dataset.csv` (Master) and
`teepa.csv` (Teepa). Regenerate it whenever results change:

```
python eval/web/extract_qa_data.py
```

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

## 2. Set up Supabase (about 15 minutes, free)

1. Create a project at [supabase.com](https://supabase.com).
2. SQL Editor → paste all of `supabase_schema.sql` → Run.
3. Project Settings → Data API → copy the **Project URL**.
   Project Settings → API keys → copy the **anon / public** key.
4. Paste both into `config.js`.

Verify the lockdown before sharing the link. In the browser console on the live
site, `SELECT` must fail:

```js
await fetch(`${EVAL_CONFIG.SUPABASE_URL}/rest/v1/ratings?select=*`,
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

Send everyone the **same** URL. Each person types their name, and the name hashes
to a stable batch — so different people get different batches, and the same
person always returns to their own.

To pin someone to a specific batch:

```
https://your-site.netlify.app/?a=Jane%20Doe&batch=3
```

| Parameter | Effect |
| --- | --- |
| `a` or `annotator` | pre-fills the name field |
| `batch` | forces a batch (1-based) instead of hashing the name |

### How batches are built

Everything is derived from `SEED`, so it is reproducible and needs no server:

1. All 1,587 pairs are shuffled once with the shared seed.
2. The first `ANCHOR_SIZE` (default 20) become the **anchor set**, given to
   every annotator. This overlap is what makes inter-annotator agreement
   computable — without it, no two people rate the same pair.
3. The remaining pairs are dealt out by stride across `BATCHES` (default 8),
   which keeps datasets and approaches balanced within every batch.

With the defaults each batch is ~216 pairs (roughly 1.5–2 hours). Every pair is
assigned exactly once except the shared anchors. For a short pilot, set
`MAX_PAIRS: 40` in `config.js`.

**Everyone must share one seed.** Change it and the batches stop lining up and
the anchor set is no longer shared.

### Choosing videos and approaches by hand

**Advanced options → "Choose videos and approaches myself"** replaces the
automatic batch with a direct picker: datasets, then a chip per video grouped by
dataset (with **All** / **None** per dataset), then approaches, then metrics,
blind/shuffle, and sample N per video+approach. Counts update live — each
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

Supabase → Table Editor → `ratings_final` → Export CSV. Or straight into pandas:

```python
import pandas as pd
# Settings -> Database -> Connection string (use the pooler URI)
df = pd.read_sql("select * from ratings_final", "postgresql://...")
df.groupby(["approach", "qa_alignment_binary"]).size().rename("n").reset_index()
# Values are text labels. Apply the agreed score/code mapping after collection.
```

`ratings` is append-only, so a pair re-rated via the Previous button appears
more than once. The `ratings_final` view keeps only the latest per
`(session_id, qa_uid)` — use it for analysis and keep `ratings` as the audit
trail.

Downloaded CSVs use the same column names the notebook writes to Excel, so
dropping them into `eval/results/` lets the notebook's aggregation cell pick
them up alongside the `.xlsx` files.

The pilot output stores, for each metric, an attribute label (`*_attribute`),
one or more problem labels (`*_error`, joined with `; `), and a Yes/No label
(`*_binary`), plus the overall `caregiver_recommendation`. These are text
labels only; score/code mapping happens later during analysis.

For inter-annotator agreement, restrict to the anchor pairs:

```sql
select qa_uid, count(distinct annotator) as n
from ratings_final group by qa_uid having count(distinct annotator) > 1;
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
