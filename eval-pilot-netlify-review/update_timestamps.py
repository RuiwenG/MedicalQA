#!/usr/bin/env python3
"""Update timestamps only in the frozen, ongoing ratings_v3 Netlify study.

Do NOT replace this study's data with the latest eval/web/qa_data.json: the
latest SingleAgent generation reuses UIDs for different questions/answers.
The existing 225 study pairs exactly match the preserved SingleAgent-v2 arm.
Use exact text AND source-video identity, not a shared UID, to transfer times.
Everything except t/te/ts on those 225 records is preserved, including the
1,278 inactive records. No generation, network, or Supabase calls.
"""
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = Path(__file__).resolve().parent
SOURCE_REV = "d4ad467527ce45d0af120a5ebd68345a19d39ad8"
BASELINE_PATH = "eval-pilot-netlify-review/qa_data.json"
TIMESTAMP_PATH = "eval/web/qa_data.json"
SOURCE_APPROACH = "SingleAgent-v2"
TARGET_APPROACH = "SingleAgent"
TIME_KEYS = {"t", "te", "ts"}


def read_snapshot(path):
    raw = subprocess.check_output(["git", "show", f"{SOURCE_REV}:{path}"], cwd=ROOT)
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def without_timestamps(corpus):
    return {**corpus, "pairs": [{k: v for k, v in p.items() if k not in TIME_KEYS} for p in corpus["pairs"]]}


def identity(pair):
    return pair["dataset"], pair["video"], pair["question"], pair["answer"]


def merge_timestamps(original, source, overrides):
    if overrides["source_revision"] != SOURCE_REV:
        raise ValueError("Overrides refer to a different source revision.")
    candidates = [p for p in source["pairs"] if p["approach"] == SOURCE_APPROACH]
    by_text = {identity(p): p for p in candidates}
    if len(candidates) != 225 or len(by_text) != len(candidates):
        raise ValueError("Expected exactly 225 unique historical source Q&As.")
    targets = [p for p in original["pairs"] if p["approach"] == TARGET_APPROACH]
    if len(targets) != 225 or len(original["pairs"]) != 1503:
        raise ValueError("The ongoing study's corpus changed; do not overwrite it.")
    if not set(overrides["pairs"]).issubset({p["uid"] for p in targets}):
        raise ValueError("An override targets a Q&A outside the ongoing study.")

    pairs, audit, transcripts = [], {}, {}
    for pair in original["pairs"]:
        if pair["approach"] != TARGET_APPROACH:
            pairs.append(dict(pair))
            continue
        match = by_text.get(identity(pair))
        if match is None:
            raise ValueError(f'No exact question/answer match for {pair["uid"]}; refusing UID-only matching.')
        dataset, video = pair["dataset"], str(pair["video"])
        if original["videos"][dataset][video] != source["videos"][dataset][video]:
            raise ValueError(f'Source video changed for {pair["uid"]}.')
        relative = f"{dataset}/{video}/Transcript/transcript-en.json"
        if relative not in transcripts:
            raw = (ROOT / relative).read_bytes()
            transcripts[relative] = {"sha256": hashlib.sha256(raw).hexdigest(),
                                     "end": round(max(s["end"] for s in json.loads(raw)))}
        update = {k: match[k] for k in TIME_KEYS if k in match}
        if update.get("ts") not in {"model", "aligned", "aligned-low"}:
            raise ValueError(f'Missing timestamp provenance: {pair["uid"]}')
        override = overrides["pairs"].get(pair["uid"])
        if override:
            if "t" in pair:
                raise ValueError("This timestamp-only update must not replace pre-existing source times.")
            update.update({k: override[k] for k in TIME_KEYS if k in override})
        start, end = update.get("t"), update.get("te")
        limit = transcripts[relative]["end"]
        if type(start) is not int or not 0 <= start < limit:
            raise ValueError(f'Invalid source start: {pair["uid"]}')
        if end is not None and (type(end) is not int or not start < end <= limit):
            raise ValueError(f'Invalid source end: {pair["uid"]}')
        for key in ("t", "te"):
            if key in pair and pair[key] != update.get(key):
                raise ValueError(f'Existing timestamp would change: {pair["uid"]}')
        pairs.append({**pair, **update})
        audit[pair["uid"]] = {
            "source_uid": match["uid"], "content_sha256": digest(identity(pair)),
            "previous": {k: pair[k] for k in TIME_KEYS if k in pair},
            "copied": {k: match[k] for k in TIME_KEYS if k in match},
            "final": update, **({"review_note": override["note"]} if override else {}),
        }
    result = {**original, "pairs": pairs}
    if without_timestamps(result) != without_timestamps(original):
        raise ValueError("Non-timestamp content changed.")
    return result, audit, transcripts


def main():
    original, original_hash = read_snapshot(BASELINE_PATH)
    source, source_hash = read_snapshot(TIMESTAMP_PATH)
    existing = json.loads((DEPLOY / "qa_data.json").read_text())
    if without_timestamps(existing) != without_timestamps(original):
        raise ValueError("Current deployment has changed Q&As or videos; refusing to replace it.")
    override_path = DEPLOY / "timestamp_overrides.json"
    overrides = json.loads(override_path.read_text())
    output, pairs, transcripts = merge_timestamps(original, source, overrides)
    (DEPLOY / "qa_data.json").write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    audit = {
        "schema_version": 1, "source_revision": SOURCE_REV,
        "study_version": "v3", "frozen_corpus_approach_in_source": SOURCE_APPROACH,
        "baseline_file_sha256": original_hash, "timestamp_source_file_sha256": source_hash,
        "unchanged_content_sha256": digest(without_timestamps(output)),
        "overrides_sha256": hashlib.sha256(override_path.read_bytes()).hexdigest(),
        "transcripts": transcripts, "pairs": pairs,
    }
    (DEPLOY / "timestamp_update_audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active = [p for p in output["pairs"] if p["approach"] == TARGET_APPROACH]
    print(f"Preserved all {len(output['pairs'])} records, including all {len(active)} active Q&As.")
    print(f"Timestamp sources: {dict(Counter(p['ts'] for p in active))}")
    print("Config, schema, rubric, order, and session keys were not changed.")


if __name__ == "__main__":
    main()
