# MedicalQA paper

ACL-style draft. The related-works section (`sections/2_related_work.tex`) is fully written;
other sections are outlined skeletons with `\todo{}` markers and drafting notes in comments.

## Build

Locally (tectonic is installed on this machine):

```bash
cd paper && tectonic main.tex
```

Or upload this folder to Overleaf (compiles with pdfLaTeX + BibTeX; `acl.sty` and
`acl_natbib.bst` are included from github.com/acl-org/acl-style-files).

- `main.tex` uses `\usepackage[review]{acl}` — switch to `[final]` for camera-ready
  (review mode anonymizes the author block and adds line numbers).
- References: `references.bib` — 18 entries verified against publisher pages (Aug 2026).
  Note: `empowering2026` (JMIR Form Res) and `evaluatingllm2025` (Innov Aging) are the
  same study (full paper vs conference abstract).
