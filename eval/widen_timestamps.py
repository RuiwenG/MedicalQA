#!/usr/bin/env python3
"""Widen too-short source-clip timestamps by growing them over related transcript lines.

The generation prompt asks for "the start-end range of the transcript section that
pair is drawn from", and the models often cite only the one line that prompted the
question (a few seconds), while the answer draws on the passage around it. This
grows each cited span, never moving or shrinking it:

1. Every transcript line is embedded with ~CTX seconds of its neighbours on each
   side (single lines are often "Yes, absolutely."), using all-mpnet-base-v2, the
   encoder of the topic model and eval/topics/timestamp_check.py.
2. The span's anchor similarity is the best similarity between the question+answer
   and the lines the model cited.
3. Growth, one line at a time: the line just before or just after the span,
   whichever is more similar to the pair, is appended while its similarity is at
   least max(MIN_SIM, REL x anchor) and the span stays within MAX seconds. It stops
   when neither neighbour matches the pair's context.

There is no minimum length: a span with no related lines around it stays as the
model cited it, which itself says the answer has no longer passage behind it. The
generated finalQA.json files are never modified. Videos in eval/excluded_videos.json are
skipped, as in the judge inputs; the result is a sidecar keyed by pair uid
(``<Dataset>_v<video>_<approach>_q<n>``, as in the judge inputs and the site):

    {"FTD_v1_SingleAgent-qwen3.5-9b-v3_q1": {"t": 0, "te": 41, "t_model": 3,
     "te_model": 18, "method": "grown", "sim_model": 0.52, "sim_widened": 0.61}, ...}

``method`` is ``kept`` (no neighbour matched) or ``grown``. ``sim_*`` is the similarity between the pair and the whole
clip's text, before and after, for the report.

    python eval/widen_timestamps.py --approach SingleAgent-qwen3.5-9b-v3
      -> eval/widened_timestamps/SingleAgent-qwen3.5-9b-v3.json, ..._report.md, ..._spot_check.md
"""
import argparse
import json
import math
import random
import statistics
from pathlib import Path

import numpy as np

from exclusions import is_excluded

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "eval" / "widened_timestamps"
DATASETS = ["Master", "Teepa", "FTD", "GEMS", "ODJ", "SUP"]
ENCODER = "sentence-transformers/all-mpnet-base-v2"
CTX, MAX, REL, MIN_SIM = 7.5, 120, 0.85, 0.30

_ENCODER = None


def encoder():
    global _ENCODER
    if _ENCODER is None:
        from sentence_transformers import SentenceTransformer
        _ENCODER = SentenceTransformer(ENCODER)
    return _ENCODER


def embed(texts):
    return encoder().encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False)


def transcript(vdir):
    """[(start, end, text)] of the transcript the generator was given (same rules as the runner)."""
    matches = sorted((vdir / "Transcript").glob("transcript*.json"))
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one transcript*.json in {vdir / 'Transcript'}")
    lines = []
    for seg in json.loads(matches[0].read_text(encoding="utf-8")):
        text = (seg.get("text") or seg.get("transcript") or "").strip()
        start = seg.get("start")
        end = seg.get("end")
        if end is None:
            end = start + seg.get("duration", 0)
        if text:
            lines.append((float(start), float(end), text))
    return lines


def line_embeddings(lines):
    """Each line embedded together with the lines within CTX seconds of it."""
    mids = [(s + e) / 2 for s, e, _ in lines]
    texts = [" ".join(t for (s, e, t), m in zip(lines, mids) if abs(m - mids[i]) <= CTX) for i in range(len(lines))]
    return embed(texts)


def cited_lines(lines, start, end):
    """Indices of the lines overlapping [start, end]; the nearest line when none do."""
    idx = [i for i, (s, e, _) in enumerate(lines) if s < end and e > start]
    if idx:
        return idx[0], idx[-1]
    nearest = min(range(len(lines)), key=lambda i: abs((lines[i][0] + lines[i][1]) / 2 - (start + end) / 2))
    return nearest, nearest


def widen(lines, sims, start, end):
    """(t, te, method) for a cited [start, end]; never narrower than the citation."""
    lo, hi = cited_lines(lines, start, end)
    cited = (lo, hi)
    anchor = float(sims[lo:hi + 1].max())
    bar = max(MIN_SIM, REL * anchor)

    def span(a, b):
        return max(end, math.ceil(lines[b][1])) - min(start, math.floor(lines[a][0]))

    # Grow over related lines, the more similar neighbour first.
    while True:
        options = [(sims[i], i) for i in (lo - 1, hi + 1) if 0 <= i < len(lines)]
        options = [(s, i) for s, i in sorted(options, reverse=True)
                   if s >= bar and span(min(lo, i), max(hi, i)) <= MAX]
        if not options:
            break
        i = options[0][1]
        lo, hi = min(lo, i), max(hi, i)
    t, te = min(start, math.floor(lines[lo][0])), max(end, math.ceil(lines[hi][1]))
    # A kept span may still move a second or two out to the edges of the lines it cites.
    return t, te, "kept" if (lo, hi) == cited else "grown"


def clip_text(lines, t, te):
    return " ".join(x for s, e, x in lines if s < te and e > t) or "."


def pct(values, q):
    return sorted(values)[min(len(values) - 1, int(q * len(values)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--approach", required=True, help="output folder inside each video, e.g. SingleAgent-qwen3.5-9b-v3")
    ap.add_argument("--spot-check", type=int, default=30, help="pairs in the spot-check sheet (default 30)")
    args = ap.parse_args()

    result, rows = {}, []
    for ds in DATASETS:
        for vdir in sorted((p for p in (REPO / ds).iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
            final = vdir / args.approach / "QA results" / "finalQA.json"
            if is_excluded(ds, vdir.name) or not final.exists():
                continue
            pairs = json.loads(final.read_text(encoding="utf-8"))
            lines = transcript(vdir)
            line_emb = line_embeddings(lines)
            qa_emb = embed([p["question"].strip() + " " + p["answer"].strip() for p in pairs])
            spans = []
            for n, (p, q) in enumerate(zip(pairs, qa_emb), 1):
                s, e = p.get("time_start_sec"), p.get("time_end_sec")
                if s is None or e is None:
                    continue
                s, e = int(s), int(e)
                t, te, method = widen(lines, line_emb @ q, s, e)
                spans.append((n, s, e, t, te, method, q))
            clips = embed([clip_text(lines, s, e) for _, s, e, *_ in spans] +
                          [clip_text(lines, t, te) for _, _, _, t, te, *_ in spans])
            for k, (n, s, e, t, te, method, q) in enumerate(spans):
                uid = f"{ds}_v{vdir.name}_{args.approach}_q{n}"
                rec = {"t": t, "te": te, "t_model": s, "te_model": e, "method": method,
                       "sim_model": round(float(clips[k] @ q), 3),
                       "sim_widened": round(float(clips[len(spans) + k] @ q), 3)}
                result[uid] = rec
                rows.append((uid, ds, pairs[n - 1], rec, lines))
            print(f"{ds}/{vdir.name}: {len(spans)} pairs", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{args.approach}.json"
    out.write_text(json.dumps({"encoder": ENCODER, "params": {"ctx": CTX, "max": MAX, "rel": REL,
                                                              "min_sim": MIN_SIM},
                               "spans": result}, indent=1) + "\n", encoding="utf-8")

    # Report: span lengths and clip similarity, before and after, overall and per dataset.
    def summary(recs):
        before = [r["te_model"] - r["t_model"] for r in recs]
        after = [r["te"] - r["t"] for r in recs]
        return (len(recs), statistics.median(before), statistics.median(after),
                100 * sum(b < 10 for b in before) / len(recs), 100 * sum(a < 10 for a in after) / len(recs),
                max(after), statistics.mean(r["sim_model"] for r in recs), statistics.mean(r["sim_widened"] for r in recs),
                100 * sum(r["sim_widened"] < r["sim_model"] - 0.05 for r in recs) / len(recs))
    recs = list(result.values())
    methods = {m: sum(r["method"] == m for r in recs) for m in ("kept", "grown")}
    report = [f"# Widened timestamps: {args.approach}", "",
              f"Encoder `{ENCODER}`; context ±{CTX}s per line; growth while similarity ≥ max({MIN_SIM}, {REL} × anchor) "
              f"and span ≤ {MAX}s; no minimum length.", "",
              f"Methods: {methods['kept']} kept, {methods['grown']} grown (of {len(recs)}).", "",
              "| Set | Pairs | Median before | Median after | <10s before | <10s after | Longest after "
              "| Clip similarity before | after | Similarity dropped >0.05 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for name, sel in [("All", recs)] + [(ds, [r for uid, d, _, r, _ in rows if d == ds]) for ds in DATASETS]:
        if sel:
            n, mb, ma, sb, sa, mx, simb, sima, drop = summary(sel)
            report.append(f"| {name} | {n} | {mb:.0f}s | {ma:.0f}s | {sb:.0f}% | {sa:.0f}% | {mx}s "
                          f"| {simb:.3f} | {sima:.3f} | {drop:.0f}% |")
    after = [r["te"] - r["t"] for r in recs]
    report += ["", "Span length after, percentiles: " +
               ", ".join(f"p{int(q * 100)} {pct(after, q)}s" for q in (0.1, 0.25, 0.5, 0.75, 0.9))]
    (OUT_DIR / f"{args.approach}_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    # Spot check: mostly pairs whose cited span was under 10 s, two thirds of them from SUP.
    rng = random.Random(0)
    short = [x for x in rows if x[3]["te_model"] - x[3]["t_model"] < 10]
    sup = [x for x in short if x[1] == "SUP"]
    other = [x for x in short if x[1] != "SUP"]
    k_sup = min(len(sup), args.spot_check * 2 // 3)
    pick = rng.sample(sup, k_sup) + rng.sample(other, min(len(other), args.spot_check - k_sup))
    clock = lambda s: f"{s // 60}:{s % 60:02d}"
    sheet = [f"# Spot check: {args.approach}", "",
             "For each pair: does the widened clip cover what the answer draws on, without much unrelated talk? "
             "Mark ✓ (good), + (should be longer), − (too long).", ""]
    for i, (uid, ds, p, r, lines) in enumerate(pick, 1):
        sheet += [f"## {i}. {uid} — {r['method']}", "",
                  f"**Q:** {p['question'].strip()}", "", f"**A:** {p['answer'].strip()}", "",
                  f"**Model span {clock(r['t_model'])}–{clock(r['te_model'])} ({r['te_model'] - r['t_model']}s), "
                  f"widened {clock(r['t'])}–{clock(r['te'])} ({r['te'] - r['t']}s); "
                  f"clip similarity {r['sim_model']:.2f} → {r['sim_widened']:.2f}**", ""]
        for s, e, text in lines:
            if s < r["te"] and e > r["t"]:
                mark = "▶" if s < r["te_model"] and e > r["t_model"] else "·"
                sheet.append(f"- {mark} [{clock(int(s))}] {text}")
        sheet += ["", "Verdict: ", ""]
    (OUT_DIR / f"{args.approach}_spot_check.md").write_text("\n".join(sheet) + "\n", encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {out.relative_to(REPO)} and its _report.md, _spot_check.md")


if __name__ == "__main__":
    main()
