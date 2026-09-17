# V1 provenance audit before Netlify redeployment

Checked on 2026-09-16 against the supplied `Prompt Design-2.pdf`, every
SingleAgent result-file history reachable from the local Git refs, and the
current `eval-v1-netlify` package. This audit does not run generation or modify
deployed sites or Supabase.

## Findings

The PDF's version 1 Single-Agent section is on pages 2–3. It specifies a fixed
20 Q&A pairs, four caregiver-focused criteria, and no Standalone criterion or
timestamp output. The closest implemented prompt was introduced in commit
`25941720cca5e38fd9d7497e64d5b03062f1cbbc` on August 5.

It is not a verbatim PDF match: the PDF asks for a mix of primarily instructive
and primarily supportive pairs, with every pair satisfying criteria 1 and 2.
The committed coverage rule instead says every pair should cover all four
criteria. No occurrence of the PDF's `Aim for a mix` wording was found in the
available SingleQA Git history. This difference must be disclosed when calling
historical outputs the implemented version 1.

The earlier statement that all 488 outputs in `edfea94` were generated using
the version 1 prompt was incorrect. A checkout of a commit includes unchanged
files from earlier generations.

| Files in `edfea9466270df17e1ef15e944a26ccf3469ad3e` | Q&As | Last output change | Evidence |
| --- | ---: | --- | --- |
| Master videos 1–10 | 200 (20/video) | `edfea94`, August 5 | All ten files changed after the four-criterion prompt was introduced. |
| Teepa videos 1–15 | 288 | `ffb2cf1`, August 2 | All fifteen files are identical to the older pre-four-criterion outputs. |

The next committed Teepa generation is `622e7c6` on August 17 (127 pairs).
At that commit the prompt already uses dynamic `k` and timestamps. It cannot
substitute for fixed-20 version 1 results. No separate Teepa generation under
the fixed-20 four-criterion prompt was found in the available Git history.

## Current package and study impact

`eval-v1-netlify/build_v1_data.py` currently reads the first Supabase-connected
Netlify snapshot at `2323ee9`, containing 487 SingleAgent pairs matching the
older generation. The package writes to `ratings_v1` with `study_version=v1`.

A verified historical implemented-v1 package can currently include the 200
Master pairs. Changing the first-40 assignment to Master only would mean four
pairs per Master video. Only 18 of those source-video/Q&A-index positions occur
in the existing 25-video first-40 assignment. Those 18 are Master videos 1–8,
questions 1–2, plus Master videos 9–10, question 1. Matching positions does not
mean the question or answer text is identical across generations.

The available choices are:

1. Use the verified Master-only implemented-v1 corpus and explicitly revise
   the comparison sample to those videos.
2. Use the full historical 488-pair snapshot, explicitly described as a mixture
   of two prompts; it cannot be reported as an all-v1 prompt evaluation.
3. Recover missing Teepa outputs from an external archive, or generate new
   Teepa outputs with an explicitly pinned v1 prompt and generation settings.
   Newly generated outputs must be described as a new run, not recovered
   historical outputs.

The deployment payload and ZIP have not been replaced pending this study-scope
choice. The active v3 evaluation is unchanged.

## Required checks when rebuilding

- Preserve the original question and answer text exactly.
- Record the source commit, source path, and content digest for every source
  file so a commit snapshot is never mistaken for a generation event.
- Use a new corpus identifier in the stored study version, QA identifiers, and
  browser session key. Old ratings and pending browser uploads must not become
  ratings of different text after redeployment.
- Preserve earlier raw `ratings_v1` rows; filter the final view to the new corpus.
- Verify source video links, Standalone's binary rubric, selection ordering,
  and complete equality between ZIP contents and the folder.

## Reproduce the critical checks

Run from the MedicalQA repository:

```sh
git show --stat edfea94
git log --all --date=short --format='%h %ad %s' -- 'Teepa/*/SingleAgent/QA results/finalQA.json'
git diff ffb2cf1 edfea94 -- 'Teepa/*/SingleAgent/QA results/finalQA.json'
git show 622e7c6:SingleQA/config/settings.py
git log --all -S 'Aim for a mix' -- SingleQA/config/settings.py
```

Source fingerprints (SHA-256):

- Supplied PDF: `36bfd90ede87c8043489f1944d0f7e304d5428b2c30fe7d9474292d91e5d26b1`
- `edfea94:SingleQA/config/settings.py`: `d875324f199dfc65f547e83f0ba7cf6097b08ff8af04eafe6ba481caa4a98ec9`
