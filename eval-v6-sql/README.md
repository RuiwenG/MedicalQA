# MedicalQA v6 deployment

Both hosting folders contain the approved v6 evaluation, including the mobile
layout and **Read full answer**. They use the same 25 Q&As, source clips and
transcripts as the local preview. The two sites share the v6 assignments and
ratings in the existing Supabase project.

The 25 transcript excerpts use the source videos’ English captions and the
approved clip windows. The source provides word timestamps for 23 excerpts.
Two excerpts use whole caption cues. [Transcript review](../eval-v6-transcript-review.md)

The study uses the v5 routing rules. Each group has five Q&As and starts with
three medical and two non-medical slots. Healthcare professionals and students
count as medical. A selected medical role takes precedence when a reviewer
selects multiple roles. Once the base slots for a role category fill, the
assignment function picks the group with the fewest active or completed
assignments.

## 1. Set up Supabase

In the Supabase project configured in the hosting folders, run these files in
the SQL Editor in order.

1. [01_create_v6.sql](01_create_v6.sql) creates the v6 tables, assignment
   function and research views. It stores the six responses, video skips and
   optional notes.
2. [02_load_version_pairs_v6.sql](02_load_version_pairs_v6.sql) loads the 25
   selected Q&As into five groups of five.
3. [03_verify_v6.sql](03_verify_v6.sql) checks the selection, permissions and
   routing settings. Expect five rows with five Q&As each and zero ratings
   before testing.

The first two scripts use transactions and stop if the v6 objects or selection
already exist. [04_analysis_queries.sql](04_analysis_queries.sql) contains
read-only summaries of the collected v6 responses.

The browser calls `claim_version_v6` and sends ratings to `ratings_v6`.
`qna_care_safety_issue` stores the optional safety note and `evaluator_comment`
stores the optional explanation for a **No** recommendation. A video skip sets
`source_unverifiable` and leaves the alignment response empty. **Cannot
determine** is a separate response. `ratings_v6_final` shows the latest rating
for each Q&A and assignment.

## 2. Upload the v6 sites

- **Netlify** accepts the complete [`eval-v6-netlify`](../eval-v6-netlify/)
  folder as a manual deploy to a v6 project.
  [Netlify manual deploy instructions](https://docs.netlify.com/deploy/create-deploys/#drag-and-drop)
- **Cloudflare Pages** accepts the complete
  [`eval-v6-cloudflare`](../eval-v6-cloudflare/) folder in a v6 Direct Upload
  project.
  [Cloudflare Direct Upload instructions](https://developers.cloudflare.com/pages/get-started/direct-upload/#drag-and-drop)

Upload the folder with `index.html` at its root. Both `config.js` files already
use the existing project's public `anon` key. Apply the v6 SQL in that project
before testing a deployed site.

## 3. Check the deployed URL

Use a clearly labeled test name on the v6 site. Complete one Q&A and confirm
that its row appears in `ratings_v6_final`. On a phone, confirm that the Q&A
sits beside the rating, that **Read full answer** expands it, and that Question
2 shows its clip and transcript.

Give reviewers one v6 URL to use throughout the study. Progress in the browser
is saved separately for each hostname. Submitted ratings from both hosts go to
the shared v6 table.
