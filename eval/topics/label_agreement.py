"""Agreement between the LLM topic labellers (and BERTopic), at both levels.

Reads runs/llm_labels/<model>_<level>.csv (llm_label.py) and adds BERTopic's own
labels as one more rater, mapped to codebook v1 ids:
  flat  the k = 12 consensus topic (runs/stability_k12)
  sub   the HDBSCAN subtopic (runs/hier_qa_mc10_p10_dd95); the animal fluency
        pairs, which HDBSCAN spread over other subtopics, have no BERTopic subtopic

Levels: flat and sub (round 1, labelled separately) and, once round 2 has run,
sub2 with flat2 derived from it.

Writes runs/llm_labels/analysis/agreement_<level>.csv (pairwise Cohen's kappa and percent
agreement), majority_<level>.csv (per pair: the majority label and its share)
and agreement_summary.json (Fleiss' kappa across the LLMs, each rater's agreement
with the majority of the others, and how often a model's subtopic sits under its
own flat label).

Usage: python eval/topics/label_agreement.py
"""
import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
LABELS = HERE / "runs" / "llm_labels"
OUT = LABELS / "analysis"
CODEBOOK = json.loads((HERE / "codebook_v1.json").read_text())
PAIRS = [json.loads(line)["pair_id"] for line in (HERE / "pairs_qwen3-14b-v3.jsonl").read_text().splitlines()]

# BERTopic topic and subtopic numbers -> codebook v1 ids.
K12_TO_F = {"2": "F01", "6": "F02", "5": "F03", "4": "F04", "1": "F05", "0": "F06",
            "3": "F07", "7": "F08", "9": "F09", "11": "F10", "8": "F11", "10": "F12"}
SUB_NAME_TO_ID = {s["name"]: s["id"] for s in CODEBOOK["subtopics"]}
HDBSCAN_TO_S = {"5": "S01", "3": "S02", "17": "S03", "13": "S04", "23": "S05", "29": "S06", "15": "S07",
                "30": "S08", "9": "S09", "27": "S10", "24": "S11", "2": "S12", "7": "S13", "22": "S14",
                "21": "S15", "1": "S16", "14": "S17", "0": "S18", "19": "S19", "16": "S20", "20": "S21",
                "12": "S22", "31": "S23", "6": "S24", "25": "S25", "10": "S26", "18": "S27", "28": "S28",
                "11": "S29", "26": "S30", "4": "S31", "8": "S32"}
PARENT = {s["id"]: s["parent"] for s in CODEBOOK["subtopics"]}


def read_llm(level):
    """{model: {pair_id: label}}, keeping the last non-empty label per pair (reruns append).
    flat2 is not labelled directly: it is each round-2 subtopic's parent topic."""
    if level == "flat2":
        return {m: {p: PARENT[s] for p, s in lab.items()} for m, lab in read_llm("sub2").items()}
    raters = {}
    for f in sorted(LABELS.glob(f"*_{level}.csv")):
        model = f.name[: -len(f"_{level}.csv")]
        labels = {}
        for r in csv.DictReader(f.open()):
            if r["label"]:
                labels[r["pair_id"]] = r["label"]
        raters[model] = labels
    return raters


def read_bertopic(level):
    if level in ("flat", "flat2"):
        rows = csv.DictReader((HERE / "runs" / "stability_k12" / "assignments.csv").open())
        return {r["pair_id"]: K12_TO_F[r["consensus_topic"]] for r in rows}
    rows = csv.DictReader((HERE / "runs" / "hier_qa_mc10_p10_dd95" / "assignments.csv").open())
    return {r["pair_id"]: HDBSCAN_TO_S[r["topic"]] for r in rows}


def cohen(a, b):
    cats = sorted(set(a) | set(b))
    idx = {c: i for i, c in enumerate(cats)}
    m = np.zeros((len(cats), len(cats)))
    for x, y in zip(a, b):
        m[idx[x], idx[y]] += 1
    n = m.sum()
    po = np.trace(m) / n
    pe = (m.sum(0) * m.sum(1)).sum() / n**2
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def fleiss(table):
    """table: pairs x categories counts, same number of raters per pair."""
    n = table.sum(1)[0]
    p_j = table.sum(0) / table.sum()
    P_i = ((table**2).sum(1) - n) / (n * (n - 1))
    pe = (p_j**2).sum()
    return (P_i.mean() - pe) / (1 - pe)


def analyse(level):
    llm = read_llm(level)
    raters = dict(llm)
    raters["bertopic"] = read_bertopic(level)
    ids = [o["id"] for o in (CODEBOOK["topics"] if level in ("flat", "flat2") else CODEBOOK["subtopics"])]

    rows = []
    for a, b in combinations(raters, 2):
        common = [p for p in PAIRS if p in raters[a] and p in raters[b]]
        la, lb = [raters[a][p] for p in common], [raters[b][p] for p in common]
        rows.append({"rater_a": a, "rater_b": b, "pairs": len(common),
                     "agreement": round(float(np.mean([x == y for x, y in zip(la, lb)])), 3),
                     "kappa": round(float(cohen(la, lb)), 3)})
    with (OUT / f"agreement_{level}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Fleiss over the LLMs, on pairs every LLM labelled.
    full = [p for p in PAIRS if all(p in lab for lab in llm.values())]
    table = np.array([[sum(lab[p] == c for lab in llm.values()) for c in ids] for p in full])
    majority = []
    for p in PAIRS:
        votes = Counter(lab[p] for lab in llm.values() if p in lab)
        if votes:
            top, n = votes.most_common(1)[0]
            majority.append({"pair_id": p, "majority": top, "share": round(n / sum(votes.values()), 3),
                             "raters": sum(votes.values())})
    with (OUT / f"majority_{level}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pair_id", "majority", "share", "raters"])
        w.writeheader()
        w.writerows(majority)

    # Each rater against the majority of the LLMs other than itself.
    vs_majority = {}
    for name, lab in raters.items():
        hits = tot = 0
        for p in PAIRS:
            if p not in lab:
                continue
            votes = Counter(o[p] for m, o in llm.items() if m != name and p in o)
            if votes:
                tot += 1
                hits += lab[p] == votes.most_common(1)[0][0]
        vs_majority[name] = round(hits / tot, 3) if tot else None

    shares = np.array([m["share"] for m in majority])
    return {"raters": list(raters), "llm_pairs_all_labelled": len(full),
            "fleiss_kappa_llms": round(float(fleiss(table)), 3) if len(full) else None,
            "agreement_with_majority_of_other_llms": vs_majority,
            "pairs_with_majority_share_ge_0.75": int((shares >= 0.75).sum()),
            "pairs_unanimous": int((shares == 1).sum()),
            "labelled_per_rater": {k: len(v) for k, v in raters.items()}}


def main():
    OUT.mkdir(exist_ok=True)
    # Round 1: flat and sub labelled separately. Round 2: sub2, with flat2 derived from it.
    levels = ["flat", "sub"] + (["sub2", "flat2"] if any(LABELS.glob("*_sub2.csv")) else [])
    summary = {level: analyse(level) for level in levels}
    # Does each model's subtopic sit under its own flat label?
    flat, sub = read_llm("flat"), read_llm("sub")
    summary["subtopic_under_own_flat_label"] = {
        m: round(float(np.mean([PARENT[sub[m][p]] == flat[m][p] for p in PAIRS if p in sub[m] and p in flat[m]])), 3)
        for m in flat if m in sub}
    if "sub2" in levels:
        summary["round1_vs_round2_same_label"] = {
            lv: {m: round(float(np.mean([a[p] == b[m][p] for p in PAIRS if p in a and p in b[m]])), 3)
                 for m, a in read_llm(r1).items() if m in b}
            for lv, r1, b in (("sub", "sub", read_llm("sub2")), ("flat", "flat", read_llm("flat2")))}
        summary["secondary_share_sub2"] = {
            f.name[: -len("_sub2.csv")]: round(float(np.mean([bool(r["secondary"]) for r in csv.DictReader(f.open())])), 3)
            for f in sorted(LABELS.glob("*_sub2.csv"))}
    (OUT / "agreement_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
