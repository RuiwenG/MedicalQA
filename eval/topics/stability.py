"""How stable are the flat BERTopic topics across random seeds and pair order?

A single run is reproducible, but another seed or a shuffled pair order moves
about half the pairs (adjusted Rand ~0.55 against seed 42). This script runs the
flat clustering --runs times per k, each with its own seed and a shuffled order,
and reports:

  base_topics.csv   each seed-42 topic: how well it reappears in the other runs
                    (best Jaccard overlap per run; "stable" if >= 0.5 in every run)
  consensus.csv     topics from all runs together: pairs that land together in most
                    runs are grouped (average linkage on the co-association matrix),
                    with keywords, example questions and the closest seed-42 topic
  assignments.csv   per pair: seed-42 topic, consensus topic, and how often the pair
                    stays with its seed-42 topic mates

Usage:
  python eval/topics/stability.py --k 8 10 12 14 --runs 10
"""
import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from bertopic import BERTopic
from bertopic.cluster import BaseCluster
from bertopic.dimensionality import BaseDimensionalityReduction
from bertopic.representation import KeyBERTInspired
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer
from sklearn.metrics import adjusted_rand_score
from umap import UMAP

from bertopic_run import DOMAIN_STOP, dedup, embed, load_docs

HERE = Path(__file__).resolve().parent
BASE_SEED = 42


def cluster(emb, k, seed):
    reduced = UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine",
                   random_state=seed).fit_transform(emb)
    return KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(reduced)


def jaccard(a, b):
    return len(a & b) / len(a | b)


def best_overlap(members, labels):
    """Best Jaccard between one set of pair indices and any cluster in labels."""
    return max(jaccard(members, set(np.flatnonzero(labels == t))) for t in np.unique(labels))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, nargs="+", default=[8, 10, 12, 14])
    ap.add_argument("--runs", type=int, default=10, help="extra runs per k besides seed 42")
    ap.add_argument("--embed-model", default="sentence-transformers/all-mpnet-base-v2")
    ap.add_argument("--dedup", type=float, default=0.95)
    args = ap.parse_args()

    df, docs = load_docs("qa")
    encoder = SentenceTransformer(args.embed_model)
    emb_all = embed(docs, encoder, args.embed_model, "qa")
    keep, src = dedup(emb_all, args.dedup)
    emb, udocs = emb_all[keep], [docs[i] for i in keep]
    uq = df.iloc[keep].reset_index(drop=True)
    n = len(keep)
    vectorizer = CountVectorizer(stop_words=list(ENGLISH_STOP_WORDS | DOMAIN_STOP), ngram_range=(1, 2), min_df=2)

    for k in args.k:
        out = HERE / "runs" / f"stability_k{k}"
        out.mkdir(parents=True, exist_ok=True)
        base = cluster(emb, k, BASE_SEED)
        runs = []
        for r in range(args.runs):
            perm = np.random.default_rng(r).permutation(n)
            lab = np.empty(n, dtype=int)
            lab[perm] = cluster(emb[perm], k, r)
            runs.append(lab)
        ari = [adjusted_rand_score(base, lab) for lab in runs]

        # Seed-42 topics: does each come back?
        base_rows = []
        for t in range(k):
            members = set(np.flatnonzero(base == t))
            overlaps = [best_overlap(members, lab) for lab in runs]
            base_rows.append({"base_topic": t, "unique_pairs": len(members),
                              "mean_jaccard": round(float(np.mean(overlaps)), 3),
                              "min_jaccard": round(float(np.min(overlaps)), 3),
                              "runs_matched": int(sum(o >= 0.5 for o in overlaps)),
                              "stable": all(o >= 0.5 for o in overlaps)})

        # Consensus: how often each two pairs land together, over all runs including seed 42.
        co = np.zeros((n, n), dtype=np.float32)
        for lab in [base] + runs:
            co += lab[:, None] == lab[None, :]
        co /= len(runs) + 1
        dist = 1 - co
        np.fill_diagonal(dist, 0)
        cons = fcluster(linkage(squareform(dist, checks=False), method="average"), t=k, criterion="maxclust") - 1

        # Keywords for the consensus topics, using the labels as given (no re-clustering).
        tm = BERTopic(embedding_model=encoder, umap_model=BaseDimensionalityReduction(), hdbscan_model=BaseCluster(),
                      vectorizer_model=vectorizer, representation_model=KeyBERTInspired())
        tm_topics, _ = tm.fit_transform(udocs, emb, y=cons)
        # BERTopic renumbers topics by size, so look each consensus label's topic up through its pairs.
        tm_id = {c: Counter(np.asarray(tm_topics)[cons == c]).most_common(1)[0][0] for c in set(cons)}

        # How often a pair stays with the pairs it shared a seed-42 topic with.
        mates = base[:, None] == base[None, :]
        np.fill_diagonal(mates, False)
        stay = np.array([co[i, mates[i]].mean() if mates[i].any() else 1.0 for i in range(n)])

        cons_rows = []
        for c in sorted(set(cons)):
            members = set(np.flatnonzero(cons == c))
            full = [i for i, s in enumerate(src) if cons[s] == c]
            vids = Counter(f"{df.dataset[i]} {df.video[i]}" for i in full)
            match = max(range(k), key=lambda t: jaccard(members, set(np.flatnonzero(base == t))))
            mi = np.array(sorted(members))
            # Examples: the pairs that sit most firmly in the topic (highest mean co-association).
            core = mi[np.argsort(-co[np.ix_(mi, mi)].mean(axis=1))[:5]]
            cons_rows.append({"consensus_topic": int(c), "pairs": len(full), "unique_pairs": len(members),
                              "videos": len(vids), "top_video": vids.most_common(1)[0][0],
                              "top_video_share": round(vids.most_common(1)[0][1] / len(full), 3),
                              "closest_base_topic": match,
                              "jaccard_with_base": round(jaccard(members, set(np.flatnonzero(base == match))), 3),
                              "cohesion": round(float(co[np.ix_(mi, mi)].mean()), 3),
                              "words": ", ".join(w for w, _ in tm.get_topic(tm_id[c])[:8]),
                              "examples": " || ".join(uq.question[i] for i in core)})

        pd.DataFrame(base_rows).to_csv(out / "base_topics.csv", index=False)
        pd.DataFrame(cons_rows).sort_values("pairs", ascending=False).to_csv(out / "consensus.csv", index=False)
        pd.DataFrame({"pair_id": df.pair_id, "dataset": df.dataset, "video": df.video,
                      "base_topic": base[src], "consensus_topic": cons[src],
                      "stays_with_mates": stay[src].round(3)}).to_csv(out / "assignments.csv", index=False)
        summary = {"k": k, "runs": args.runs, "unique_pairs": n,
                   "ari_vs_seed42": {"mean": round(float(np.mean(ari)), 3), "min": round(float(np.min(ari)), 3),
                                     "max": round(float(np.max(ari)), 3)},
                   "stable_base_topics": int(sum(r["stable"] for r in base_rows)),
                   "pairs_staying_with_mates_mean": round(float(stay.mean()), 3),
                   "pairs_with_stay_ge_0.8": int((stay >= 0.8).sum())}
        (out / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
