#!/usr/bin/env python3
"""Recover missing source-clip timestamps by aligning QA pairs to the transcript.

SingleAgent timestamps come from a ``Timestamp N:`` line the model is asked to
emit, and the model often drops or mangles it — 138 of 226 SingleAgent pairs
carry no ``time_start_sec``, so the eval site falls back to embedding the whole
video instead of the clip the pair came from.

This recovers those windows from data already on disk: no generation re-run.
Every pair is scored against its own ``Transcript/transcript-en.json`` — the
same transcript the model was given — using TF-IDF cosine similarity over
sliding windows, plus a bonus for shared 4-grams (answers frequently lift
phrasing straight from the transcript). The best-scoring window wins; ties go
to the shortest, then earliest, window.

Pairs that already have a parsed ``time_start_sec`` are left alone and used as
held-out validation instead (``--validate``).

Output is a sidecar, ``eval/web/aligned_timestamps.json``, keyed by the same
uid ``extract_qa_data.py`` builds::

    {"Master_v1_SingleAgent_q1": {"t": 57, "te": 123, "score": 0.41}, ...}

Windows scoring under ``--min-score`` also carry ``"low": true``.

The generated ``finalQA.json`` files are never modified, so model-emitted and
estimated timestamps stay distinguishable. ``extract_qa_data.py`` merges the
sidecar, letting model timestamps win, and tags estimated ones
``ts: "aligned"`` so the UI can label the clip as approximate.

Run from anywhere inside the repo::

    python eval/align_timestamps.py                  # all approaches
    python eval/align_timestamps.py --approach SingleAgent
    python eval/align_timestamps.py --validate       # accuracy vs known timestamps
"""
import argparse
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path

DATASETS = ["Master", "Teepa"]

# Candidate clip lengths, in seconds. The model-written ranges have a median
# length of 46s and a 90th percentile of 107s, so the grid brackets that.
DURATIONS = (30, 45, 60, 90, 120)

# Seconds of slack added to each side of the winning window. Costs a little
# IoU but lifts "at least half the true clip is inside the window" from 64% to
# 80% on held-out pairs, and a lead-in helps an annotator pick up the thread.
PAD_SEC = 10

# Weight on shared 4-grams, relative to the cosine score. Small on purpose:
# it breaks ties between lexically similar windows without overriding them.
ANCHOR_WEIGHT = 0.02

# Below this score the window is flagged low-confidence. Held out against known
# timestamps, the bottom quartile (mean score 0.18) lands inside the true
# segment only 47.7% of the time, against 88.3% for the top quartile — so a
# generic question with no distinctive vocabulary ("Why do symptoms vary day to
# day?") matches everywhere and lands nowhere. The UI still seeks to these, but
# leaves the clip open-ended rather than asserting a wrong 55-second window at
# an annotator who is being asked whether the Q&A matches the video.
MIN_SCORE = 0.20

# Generic English plus the filler that saturates spoken dementia-care content;
# leaving these in makes every window look equally relevant.
STOPWORDS = set(
    """a about above after again against all am an and any are aren't as at be because been before being below
    between both but by can cannot could couldn't did didn't do does doesn't doing don't down during each few for
    from further had hadn't has hasn't have haven't having he her here hers herself him himself his how i if in into
    is isn't it its itself let's me more most mustn't my myself no nor not of off on once only or other ought our
    ours ourselves out over own same shan't she should shouldn't so some such than that that's the their theirs them
    themselves then there these they this those through to too under until up very was wasn't we were weren't what
    when where which while who whom why with won't would wouldn't you your yours yourself yourselves will just also
    get gets got like really thing things lot kind way ways well okay yeah able need needs make makes made take
    takes see know think want use used using""".split()
)


def find_repo_root() -> Path:
    """Walk up from this file looking for the dataset directories."""
    for cand in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if any((cand / d).exists() for d in DATASETS):
            return cand
    raise SystemExit("Could not locate Master/ or Teepa/ — run this from inside the repo.")


def stem(token: str) -> str:
    """Crude suffix stripping — enough to match "caregiver/caregivers", "walk/walking"."""
    for suffix in ("'s", "ing", "ed", "es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9']+", text.lower())]


def content_words(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in STOPWORDS and len(t) > 2]


def load_transcript(base_dir: Path, dataset: str, video: str):
    """Return (lines, idf) for a video, or None when it has no English transcript.

    Each line keeps its own tokens and the set of 4-grams starting in it (the
    window is extended three tokens into the next line so a phrase split across
    a caption boundary still matches).
    """
    path = base_dir / dataset / str(video) / "Transcript" / "transcript-en.json"
    if not path.exists():
        return None
    segments = json.loads(path.read_text(encoding="utf-8"))
    lines = []
    for seg in segments:
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        tokens = tokenize(text)
        lines.append(
            {
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "tokens": tokens,
                "content": content_words(tokens),
                "grams": set(),
            }
        )
    if not lines:
        return None
    for i, line in enumerate(lines):
        span = line["tokens"] + (lines[i + 1]["tokens"][:3] if i + 1 < len(lines) else [])
        line["grams"] = {" ".join(span[k : k + 4]) for k in range(max(0, len(span) - 3))}

    # IDF over transcript lines: a term that shows up in every line of this
    # video says nothing about which part of it a pair came from.
    doc_freq = Counter()
    for line in lines:
        doc_freq.update(set(line["content"]))
    idf = {t: math.log(1 + len(lines) / (1 + df)) for t, df in doc_freq.items()}
    return lines, idf


def align_pair(question: str, answer: str, lines, idf, pad: int = PAD_SEC):
    """Best-matching transcript window for one pair, as (start_sec, end_sec, score)."""
    text = f"{question} {answer}"
    tokens = tokenize(text)
    query = content_words(tokens)
    if not query:
        return None

    default_idf = math.log(1 + len(lines))
    query_tf = Counter(query)
    query_vec = {t: (1 + math.log(c)) * idf.get(t, default_idf) for t, c in query_tf.items()}
    query_norm = math.sqrt(sum(v * v for v in query_vec.values())) or 1.0
    query_grams = {" ".join(tokens[k : k + 4]) for k in range(max(0, len(tokens) - 3))}
    line_anchors = [len(line["grams"] & query_grams) for line in lines]

    candidates = []
    for duration in DURATIONS:
        counts, anchors, j = Counter(), 0, 0
        for i in range(len(lines)):
            if j < i:  # a line longer than the window; restart the accumulator
                j, counts, anchors = i, Counter(), 0
            limit = lines[i]["start"] + duration
            while j < len(lines) and lines[j]["start"] < limit:
                counts.update(lines[j]["content"])
                anchors += line_anchors[j]
                j += 1
            if counts:
                window_norm = math.sqrt(
                    sum(((1 + math.log(c)) * idf.get(t, 0.0)) ** 2 for t, c in counts.items())
                ) or 1.0
                dot = sum(
                    query_vec[t] * (1 + math.log(counts[t])) * idf.get(t, 0.0)
                    for t in query_vec
                    if t in counts
                )
                score = dot / (query_norm * window_norm) + ANCHOR_WEIGHT * anchors
                start, end = lines[i]["start"], lines[j - 1]["end"]
                candidates.append((score, end - start, start, end))
            counts.subtract(lines[i]["content"])
            counts += Counter()  # drop the zero/negative entries subtract leaves behind
            anchors -= line_anchors[i]

    if not candidates:
        return None
    best = max(c[0] for c in candidates)
    score, _, start, end = min(
        (c for c in candidates if c[0] >= best),
        key=lambda c: (c[1], c[2]),  # shortest window, then earliest
    )
    return max(0, round(start) - pad), min(round(lines[-1]["end"]), round(end) + pad), score


def iter_pairs(base_dir: Path, approaches: list[str] | None):
    """Yield (uid, dataset, video, item) for every QA pair on disk."""
    for dataset in DATASETS:
        droot = base_dir / dataset
        if not droot.exists():
            continue
        for vdir in sorted(
            (d for d in droot.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: int(d.name),
        ):
            for adir in sorted(d for d in vdir.iterdir() if d.is_dir()):
                if approaches and adir.name not in approaches:
                    continue
                qa_file = adir / "QA results" / "finalQA.json"
                if not qa_file.exists():
                    continue
                items = json.loads(qa_file.read_text(encoding="utf-8"))
                for i, item in enumerate(items):
                    if not str(item.get("question", "")).strip():
                        continue
                    if not str(item.get("answer", "")).strip():
                        continue
                    uid = f"{dataset}_v{vdir.name}_{adir.name}_q{i + 1}"
                    yield uid, dataset, vdir.name, item


def has_timestamp(item: dict) -> bool:
    return isinstance(item.get("time_start_sec"), (int, float)) and isinstance(
        item.get("time_end_sec"), (int, float)
    )


def build(base_dir: Path, approaches: list[str] | None, min_score: float = MIN_SCORE) -> tuple[dict, dict]:
    """Align every pair that is missing a timestamp. Returns (sidecar, counts)."""
    cache: dict[tuple[str, str], object] = {}
    aligned, counts = {}, Counter()
    for uid, dataset, video, item in iter_pairs(base_dir, approaches):
        if has_timestamp(item):
            counts["kept (model)"] += 1
            continue
        key = (dataset, video)
        if key not in cache:
            cache[key] = load_transcript(base_dir, dataset, video)
        loaded = cache[key]
        if loaded is None:
            counts["skipped (no transcript)"] += 1
            continue
        lines, idf = loaded
        result = align_pair(item["question"], item["answer"], lines, idf)
        if result is None:
            counts["skipped (no content words)"] += 1
            continue
        start, end, score = result
        window = {"t": start, "te": end, "score": round(score, 4)}
        if score < min_score:
            window["low"] = True
            counts["aligned (low confidence)"] += 1
        else:
            counts["aligned"] += 1
        aligned[uid] = window
    return aligned, counts


def overlap_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def validate(base_dir: Path, approaches: list[str] | None) -> None:
    """Score the aligner against pairs whose timestamps are already known.

    For SingleAgent those came from the model's own ``Timestamp N:`` line, so
    they are noisy; DualAgent and MultiAgent derive theirs programmatically
    from the transcript segment the pair was generated from, which makes them
    the stronger check.
    """
    cache: dict[tuple[str, str], object] = {}
    by_approach: dict[str, list] = {}
    for uid, dataset, video, item in iter_pairs(base_dir, approaches):
        if not has_timestamp(item) or item["time_end_sec"] <= item["time_start_sec"]:
            continue
        key = (dataset, video)
        if key not in cache:
            cache[key] = load_transcript(base_dir, dataset, video)
        loaded = cache[key]
        if loaded is None:
            continue
        lines, idf = loaded
        result = align_pair(item["question"], item["answer"], lines, idf)
        if result is None:
            continue
        start, end, _ = result
        truth = (float(item["time_start_sec"]), float(item["time_end_sec"]))
        covered = max(0.0, min(truth[1], end) - max(truth[0], start)) / (truth[1] - truth[0])
        approach = uid.split("_", 2)[2].rsplit("_q", 1)[0]
        by_approach.setdefault(approach, []).append(
            (overlap_iou(truth, (start, end)), truth[0] <= (start + end) / 2 <= truth[1], covered)
        )

    print(f"{'Approach':<28} {'pairs':>6} {'mean IoU':>9} {'mid inside':>11} {'half covered':>13}")
    print("-" * 71)
    for approach, rows in sorted(by_approach.items()):
        print(
            f"{approach:<28} {len(rows):>6} {statistics.mean(r[0] for r in rows):>9.3f} "
            f"{sum(r[1] for r in rows) / len(rows):>10.1%} {sum(r[2] >= 0.5 for r in rows) / len(rows):>13.1%}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--approach",
        action="append",
        help="Limit to one approach folder (repeatable). Default: every approach.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Score the aligner against pairs that already have timestamps, and write nothing.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=MIN_SCORE,
        help=f"Flag windows below this score low-confidence (default {MIN_SCORE}). 0 disables.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Sidecar path (default: eval/web/aligned_timestamps.json next to qa_data.json).",
    )
    args = parser.parse_args()

    base_dir = find_repo_root()
    if args.validate:
        validate(base_dir, args.approach)
        return

    aligned, counts = build(base_dir, args.approach, args.min_score)
    out_path = args.out or base_dir / "eval" / "web" / "aligned_timestamps.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(aligned, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"Repo root: {base_dir}")
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB)\n")
    for label, n in sorted(counts.items()):
        print(f"  {label:<28} {n:>5}")
    print("\nRe-run eval/web/extract_qa_data.py to fold these into qa_data.json.")


if __name__ == "__main__":
    main()
