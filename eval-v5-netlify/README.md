# Dementia Q&A human evaluation — v5 Netlify package

v5 gives each annotator **one of five versions of 20 Q&As**, and the database
decides which version. The aim is an equal number of annotators per version,
with at least 4 medical and 2 non-medical annotators in each. It is a new,
isolated package built from the v4 UI. It does not change `eval-v4-netlify/`,
`eval-v4-sql/` or any earlier study.

## Study contract

- Supabase raw table: `public.ratings_v5`
- Latest-row view: `public.ratings_v5_final`
- Version routing: `public.claim_version_v5(...)`, called once per new annotator
- Study version sent by the browser: `v5`
- Generator: Qwen3-14B only (`SingleAgent-qwen3-14b-v1`, `-v2`, `-v3`)
- Corpus: 100 Q&As in 5 versions of 20 (`qa_data.json`, `versions.json`)
- Browser storage namespace: `dqa:v5:*`, isolated from earlier studies

**The current selection is PROVISIONAL.** `manifest.json` says
`"provisional": true` and the welcome page shows a warning banner. It is a
placeholder for testing: four pairs from each of the original 25 videos, spread
over the three prompt versions. Replace it with the agreed selection before
launch (see "Build the versions").

## Landing page and routing

The annotator enters their **name**, **role** (the four v4 groups plus
Others) and **dementia-care experience**, then presses **Continue**. The page
calls `claim_version_v5`, which records an assignment and returns its version.
The annotator then rates that version's 20 Q&As in their own shuffled order,
seeded by the assignment id, so a reload keeps the same order.

Clinical professionals and healthcare students count as **medical**;
professional care workers, family caregivers and Others count as
**non-medical**. That mapping lives in one place, the database table
`role_categories_v5`.

The routing rule:

1. **Base fill.** A medical annotator goes to the lowest-numbered version with
   fewer than 4 medical annotators. A non-medical annotator goes to the
   lowest-numbered version with fewer than 2 non-medical.
2. **After the base.** Once every version has its base slots for that category,
   anyone goes to the version with the fewest annotators in total, lowest
   number on a tie: 6-6-6-6-6 → 7-6-6-6-6 → 7-7-6-6-6 …

If annotators arrive out of order, for example all medical first, the extras
spread one at a time while the non-medical base slots stay reserved. The
versions are never more than one or two apart, and the balance rule evens them
out.

**Drop-outs.** An assignment holds its slot for 48 hours. If it is not finished
by then, the slot is released to the next annotator. A finished assignment
keeps its slot for good. Ratings sent after the 48 hours are still stored, with
`late = true`. "Finished" is worked out from the ratings: every one of the
version's 20 Q&As has a rating.

**Returning annotators.** The browser remembers its assignments. Typing the
same name on the same device and browser resumes the same version, order and
progress without a new claim. **On another device or browser the annotator is
treated as new** and gets a fresh assignment. The two are never merged, and
the abandoned one releases its slot after 48 hours. Ask annotators to use one
device. The profile chosen at the first Continue is the one their ratings are
filed under.

## Separation from v1–v4

v5 creates only new objects (`*_v5`), and none of them references or modifies
a v1–v4 table, view, sequence, function, trigger or grant. Script 02 checks
this, and the SQL test creates v5 next to a populated v4 study and confirms v4
is unchanged. The browser only reads and writes `dqa:v5:*` keys, so it cannot
pick up a v4 session saved in the same browser.

v5 can therefore go into **the existing v4 Supabase project or a brand-new
one**. For a new project, the only change on the site side is `config.js`:

- `SUPABASE_URL` — the new project's URL
- `SUPABASE_ANON_KEY` — the new project's anon (publishable) key

Both are empty in the repository, which runs the page in local-only mode.

## Files

```text
eval-v5-netlify/
  index.html          static evaluation application
  config.js           Supabase project, v5 table/function names, approach allowlist
  qa_data.json        the 100 selected Q&As and their video links (generated)
  versions.json       the 20 uids of each version (generated)
  manifest.json       source commit, counts, provisional flag, SHA-256s (generated)
  build_versions.py   builds the four generated files plus eval-v5-sql/04
  _headers            Netlify cache, no-index and referrer headers
```

## Build the versions

Write the agreed selection as JSON, five lists of 20 Qwen3-14B uids
(`<Dataset>_v<video>_SingleAgent-qwen3-14b-v<1|2|3>_q<n>`):

```json
{"versions": [["Master_v1_SingleAgent-qwen3-14b-v3_q2", "..."], [], [], [], []]}
```

Then, from the repository root:

```sh
python3 eval-v5-netlify/build_versions.py --selection selection.json
```

This rewrites `qa_data.json`, `versions.json`, `manifest.json` and
`eval-v5-sql/04_load_version_pairs_v5.sql` together. It refuses a selection
that is not 5 × 20 distinct Qwen3-14B pairs from this repo. The placeholder is
rebuilt with `--provisional`. Never change the versions after the first
annotator has been assigned; a new selection means a new study version.

## Test locally

```sh
python3 -m http.server 8899 --bind 127.0.0.1 --directory eval-v5-netlify
```

Open `http://127.0.0.1:8899`. With `config.js` empty the page runs in
local-only mode: no quotas, version 1 (or `?version=3` and so on), ratings
kept in the browser and exported at the end.

```sh
node eval-v5-sql/tests/test_v5_ui.cjs    # landing page, claim, order, resume, payloads
node eval-v5-sql/tests/test_v5_sql.cjs   # routing, permissions, separation (local PostgreSQL)
```

## Deploy

1. Build the agreed selection (above) and check that `manifest.json` says
   `"provisional": false`.
2. In the Supabase SQL Editor, run `eval-v5-sql/01_create_v5.sql` once,
   `eval-v5-sql/04_load_version_pairs_v5.sql` once, then
   `eval-v5-sql/02_verify_v5.sql` (see `eval-v5-sql/README.md`).
3. Fill in `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `config.js`.
4. Create a **new** Netlify site and upload `eval-v5-netlify/`, or use Git
   deployment with publish directory `eval-v5-netlify` and no build command. Do
   not overwrite the v4 site.
5. Smoke test with one clearly named annotator, confirm the assignment and
   rating rows, then delete them from the dashboard before launch.

After deployment, verify:

1. The heading says `Human Evaluation v5` and there is no provisional banner.
2. Continue gets a version; the browser's network panel shows one
   `rpc/claim_version_v5` call.
3. A reload with the same name resumes without a second claim.
4. A submitted row lands in `ratings_v5` with the right `assignment_id` and
   `eval_version`.
5. `select * from public.version_status_v5();` shows the test annotator.
