# MedicalQA human evaluation v5: Phase 0

This package contains 25 Qwen3.5-9B Q&As in five groups of five. Each group has
three high-rated natural pairs, one low-rated natural pair and one deliberate
negative. The UI hides selection labels, model names and negative targets.
The selected answers are unchanged, and the five evaluation criteria remain.

The selection is no longer provisional. `selection.json` records the IDs
from `eval/topics/runs/review_set_qwen3.5-9b-25/` and the 27 October review PDF.
Follow `../eval-v5-DEPLOY.md` for the database and deployment sequence.

## Build matching site and SQL files

From the repository root:

```sh
python3 eval-v5-netlify/build_versions.py --selection eval-v5-netlify/selection.json
```

This writes `qa_data.json`, `transcripts.json`, `versions.json`, `manifest.json`, and the matching
`eval-v5-sql/04_load_version_pairs_v5.sql` together. Expect a non-provisional
manifest with 5 versions, 5 pairs per version, 25 total pairs and 23 videos.

All 25 pairs use the widened ranges from the Qwen3.5-9B v3 and bad-v3
files under `eval/widened_timestamps/`. The player and transcript panel use
`t`/`te`, and the UI marks these ranges as approximate (`ts: widened`).
The original times are recorded as `t_model`/`te_model`.
Widening is not independent verification of answer content.

The builder supports earlier Qwen3-14B approaches and equal-sized groups up
to 20 pairs for compatibility. Existing non-provisional group IDs/order cannot
be changed. Later batches must preserve earlier groups and their size.
`--provisional` remains available for separate testing folders; it cannot
replace this folder's agreed selection.

## Assignment and completion

Annotators enter their name, every applicable role, optional role descriptions
and dementia-care experience. Healthcare professionals and medical students
count as medical; family/friend caregivers and others count as non-medical.
Any medical role makes a multi-role profile medical. All roles and descriptions
are retained with ratings.

Supabase fills 3 medical and 2 non-medical base slots per group, then balances
extra raters when that category is full across all loaded groups. Five people
is a target, not a hard cutoff. The alert at 15 people in the newest batch
reminds the team to prepare the next five groups; it does not load them.

Each participant receives one group of five pairs in an order seeded by the
assignment ID. The same browser/name resumes its assignment and order. Another
browser/device can create a separate claim. An unfinished assignment holds a
slot for 48 hours. Completion requires five distinct pair ratings; revisions
do not count twice, and late ratings remain stored with a late flag.

The browser writes only `dqa:v5:*` storage and does not touch v1-v4 data.
Saved sessions containing a different selected set are not resumed here.

## Video and rating behaviour

Standalone comes before the video. The highlighted instruction then asks
annotators to watch the source before rating Trustworthiness. The player shows
the range; the full-video link starts there without an end limit. An unavailable
source leaves Trustworthiness unscored while retaining the other ratings.
The existing wording, label wrapping and rating payload formats are preserved.

When a timestamped clip ends, the player prepares the same start/end range for
another play instead of replaying from 0:00, using the player's own controls.
The YouTube player API is loaded when the video is opened.

The collapsible **Read the clip transcript** panel appears alongside the video,
after Standalone. It shows all source-caption lines overlapping the clip's
start/end range as continuous plain text, with the time range once in the
heading. There are no per-line timestamps or full-video transcript option.
Text comes from the selected video's
canonical `Transcript/transcript-en.json`; only start, end and text are packaged
in `transcripts.json`. Missing transcripts show a message without blocking the
study. A missing or unbounded clip range never falls back to the whole video's
text. Reviewers should still follow the existing video and Trustworthiness rules.

`version_pairs_v5` stores group IDs and source metadata. `qa_data.json` holds
only the selected questions/answers/video links. Submitted ratings retain the
exact displayed wording and playback times for analysis.

## Preview and deploy

```sh
python3 -m http.server 8899 --bind 127.0.0.1 --directory eval-v5-netlify
```

Open `http://localhost:8899`. The source and ZIP are configured with the
medicalqa-eval project's URL and public anon key, so signup and rating
submission contact that project's v5 backend, including during local preview.
For local-only testing, clear both Supabase values in a separate copy of
`config.js`; `?version=2` through `?version=5` then select other groups.

For deployment, use SQL 00 to inspect the database, 01 for a new schema or 05
for an existing-v5 profile/routing upgrade, then 04 on an unused study and 02
to verify. Confirm the Supabase project URL and anon key in the extracted config,
upload to the v5 Netlify site, refresh open pages and smoke-test five ratings.
The loader refuses existing pairs/assignments/ratings without deleting them.

`eval-v5-netlify.zip` has this folder's files directly at the archive root.
Include `transcripts.json` when uploading either hosting package. The transcript
addition needs no Supabase migration or changes to existing assignments.
`eval-v5-sql.zip` contains matching SQL and deployment instructions. These
packages do not change Supabase or Netlify until applied/uploaded.

## Validation

```sh
python3 eval-v5-sql/tests/test_v5_selection.py
node eval-v5-sql/tests/test_v5_ui.cjs
node eval-v5-sql/tests/test_v5_sql.cjs
```

Run from the full repository. Tests use stubbed UI requests and a temporary
PostgreSQL cluster; no test contacts the live study.
