#!/usr/bin/env python3
"""Align the original 487 Netlify v1 Q&As, never the newer SingleAgent outputs.

Uses the existing offline transcript-window aligner. No Q&A generation, API
calls, Supabase writes, or changes to v3. Results are approximate, not manually
verified ground truth. Rebuild the deployment with build_v1_data.py afterwards.
"""

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

from align_timestamps import ANCHOR_WEIGHT, DURATIONS, MIN_SCORE, PAD_SEC, align_pair, load_transcript


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("v1_builder", ROOT / "eval-v1-netlify/build_v1_data.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def main() -> None:
    corpus = builder.load_historical_corpus(ROOT)
    groups = {}
    for pair in corpus["pairs"]:
        groups.setdefault((pair["dataset"], pair["video"]), []).append(pair)
    windows, transcripts, counts, video_ends = {}, {}, Counter(), {}
    for (dataset, video), pairs in groups.items():
        loaded = load_transcript(ROOT, dataset, str(video))
        if loaded is None:
            raise ValueError(f"Missing transcript for {dataset}/{video}")
        relative = f"{dataset}/{video}/Transcript/transcript-en.json"
        transcripts[relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        lines, idf = loaded
        video_ends[(dataset, video)] = round(lines[-1]["end"])
        for pair in pairs:
            result = align_pair(pair["question"], pair["answer"], lines, idf)
            if result is None:
                raise ValueError(f'No alignment for {pair["uid"]}')
            start, end, score = result
            if not 0 <= start < end <= round(lines[-1]["end"]):
                raise ValueError(f'Invalid clip for {pair["uid"]}')
            low = score < MIN_SCORE
            windows[pair["uid"]] = {"t": start, "te": end, "score": round(score, 6), "low": low}
            counts["weak" if low else "aligned"] += 1
        print(f"{dataset}/{video}: {len(pairs)} aligned ({len(windows)}/487)", flush=True)

    override_path = ROOT / "eval-v1-netlify/timestamp_overrides.json"
    overrides = json.loads(override_path.read_text(encoding="utf-8"))
    if overrides["source_revision"] != builder.SOURCE_REV:
        raise ValueError("Timestamp overrides target a different corpus.")
    by_uid = {p["uid"]: p for p in corpus["pairs"]}
    for uid, override in overrides["pairs"].items():
        pair = by_uid[uid]  # fail on an unexpected UID
        start, end = override["t"], override["te"]
        if not (type(start) is int and type(end) is int and
                0 <= start < end <= video_ends[(pair["dataset"], pair["video"])]):
            raise ValueError(f"Invalid reviewed range: {uid}")
        windows[uid]["automatic_range"] = {key: windows[uid][key] for key in ("t", "te")}
        windows[uid].update(override)

    sidecar = {
        "schema_version": 1,
        "source_revision": builder.SOURCE_REV,
        "corpus_sha256": builder.corpus_digest(corpus),
        "method": "TF-IDF transcript windows plus shared 4-grams; approximate, not ground truth",
        "aligner_sha256": hashlib.sha256((ROOT / "eval/align_timestamps.py").read_bytes()).hexdigest(),
        "parameters": {"durations_sec": DURATIONS, "padding_sec": PAD_SEC,
                       "anchor_weight": ANCHOR_WEIGHT, "weak_below": MIN_SCORE},
        "transcripts": transcripts,
        "overrides_sha256": hashlib.sha256(override_path.read_bytes()).hexdigest(),
        "pairs": windows,
    }
    builder.merge_timestamps(corpus, sidecar, ROOT)  # validate before publishing
    destination = ROOT / "eval-v1-netlify/aligned_timestamps.json"
    destination.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {destination}: {dict(counts)}", flush=True)


if __name__ == "__main__":
    main()
