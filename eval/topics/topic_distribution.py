"""Codebook v2 topic distribution of whole QA sets, from the labelling models' round-2 subtopics.

Each model's subtopic (llm_label.py --level sub2) is mapped to its codebook v2 topic, and a
pair's topic is the one most models chose, as in build_review_pdf.py. A tie splits the pair
evenly between the tied topics. The models that generated a set do not label it, so the two
sets have different panels; --shared keeps only the models that labelled both.

Writes runs/topic_distribution.csv (one row per set and topic).

Usage: python eval/topics/topic_distribution.py [--shared]
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETS = {  # name: (pairs JSONL, folder of <model>_sub2.csv)
    "qwen3-14b-v3": (HERE / "pairs_qwen3-14b-v3.jsonl", HERE / "runs" / "llm_labels"),
    "qwen3.5-9b-v3": (HERE / "pairs_qwen3.5-9b-v3.jsonl", HERE / "runs" / "llm_labels_qwen35-9b"),
}
OUT = HERE / "runs" / "topic_distribution.csv"


def read_labels(folder):
    """{model: {pair_id: subtopic}}, keeping the last non-empty label per pair (reruns append)."""
    labels = {}
    for f in sorted(folder.glob("*_sub2.csv")):
        rows = {r["pair_id"]: r["label"] for r in csv.DictReader(f.open()) if r["label"]}
        labels[f.name.removesuffix("_sub2.csv")] = rows
    return labels


def distribution(pair_ids, labels, sub_to_topic):
    """Topic shares (ties split), the number of tied pairs and of pairs with no labels."""
    shares, ties, unlabelled = Counter(), 0, 0
    for p in pair_ids:
        votes = Counter(sub_to_topic[lab[p]] for lab in labels.values() if p in lab)
        if not votes:
            unlabelled += 1
            continue
        most = max(votes.values())
        top = [t for t, n in votes.items() if n == most]
        ties += len(top) > 1
        for t in top:
            shares[t] += 1 / len(top)
    return shares, ties, unlabelled


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shared", action="store_true", help="use only the models that labelled every set")
    args = ap.parse_args()
    codebook = json.loads((HERE / "codebook_v2.json").read_text())
    names = {t["id"]: t["name"] for t in codebook["topics"]}
    sub_to_topic = {s["id"]: s["parent"] for s in codebook["subtopics"]}

    sets = {}
    for name, (pairs, folder) in SETS.items():
        ids = [json.loads(line)["pair_id"] for line in pairs.read_text().splitlines()]
        sets[name] = (ids, read_labels(folder))
    if args.shared:
        common = set.intersection(*(set(labels) for _, labels in sets.values()))
        sets = {n: (ids, {m: lab for m, lab in labels.items() if m in common}) for n, (ids, labels) in sets.items()}

    rows = []
    for name, (ids, labels) in sets.items():
        shares, ties, unlabelled = distribution(ids, labels, sub_to_topic)
        print(f"{name}: {len(ids)} pairs, {len(labels)} labelling models ({', '.join(sorted(labels))}); "
              f"{ties} ties split evenly, {unlabelled} unlabelled")
        for t in names:
            rows.append({"set": name, "topic": t, "name": names[t], "pairs": round(shares[t], 1),
                         "percent": round(100 * shares[t] / max(1, len(ids) - unlabelled), 1)})

    print(f"\n{'topic':<44}" + "".join(f"{n:>16}" for n in sets))
    for t in names:
        cells = [next(r for r in rows if r["set"] == n and r["topic"] == t) for n in sets]
        print(f"{t} {names[t]:<41}" + "".join(f"{c['pairs']:>8.1f} {c['percent']:>5.1f}%" for c in cells))
    out = OUT.with_name(OUT.stem + ("_shared" if args.shared else "") + OUT.suffix)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\n-> {out.relative_to(HERE.parents[1])}")


if __name__ == "__main__":
    main()
