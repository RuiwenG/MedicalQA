"""BERTopic over the Qwen3-14B v3 pairs: a flat run and a two-level run.

  flat  k-means in UMAP space gives exactly --k coarse topics. (HDBSCAN with a
        large min cluster size collapsed the corpus into 2 topics.)
  hier  HDBSCAN ("leaf" selection) gives fine subtopics; Ward linkage on their
        centroids merges them into --parents top-level topics.

Near-duplicate pairs (cosine >= --dedup on the embeddings, 0.95 by default) are
collapsed before fitting: only the first copy is clustered, and each duplicate
takes its copy's topic, so every pair still gets a label. Below 0.95 the "twins"
are different videos asking about the same theme, which is topic signal.

The hier run reassigns HDBSCAN outliers to their nearest topic. Both estimate a
per-pair topic distribution (approximate_distribution) to count how many pairs
span more than one topic.

Usage:
  python eval/topics/bertopic_run.py --mode flat --k 10
  python eval/topics/bertopic_run.py --mode hier --parents 10
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from bertopic import BERTopic
from bertopic.representation import KeyBERTInspired
from hdbscan import HDBSCAN
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer
from umap import UMAP

HERE = Path(__file__).resolve().parent
PAIRS = HERE / "pairs_qwen3-14b-v3.jsonl"
# Words in nearly every pair; left in, they make every topic's keywords look alike.
DOMAIN_STOP = {"dementia", "person", "people", "caregiver", "caregivers", "care", "help",
               "helps", "important", "living", "individual", "individuals", "loved", "ones"}


def load_docs(text):
    rows = [json.loads(l) for l in PAIRS.read_text().splitlines()]
    if text == "q":
        docs = [r["question"] for r in rows]
    else:
        docs = [f"{r['question']} {r['answer']}" for r in rows]
    return pd.DataFrame(rows), docs


def embed(docs, encoder, model_name, text):
    # Keyed by the pair set too, so changing the exclusion list re-embeds.
    digest = hashlib.sha1("\n".join(docs).encode()).hexdigest()[:10]
    cache = HERE / "cache" / f"emb_{model_name.replace('/', '_')}_{text}_{digest}.npy"
    if cache.exists():
        return np.load(cache)
    cache.parent.mkdir(exist_ok=True)
    emb = encoder.encode(docs, show_progress_bar=True, normalize_embeddings=True)
    np.save(cache, emb)
    return emb


def dedup(emb, threshold):
    """Greedy: a pair whose cosine with an earlier kept pair is >= threshold maps to it.
    Returns the kept indices and, for every pair, the position of its kept copy."""
    sims = emb @ emb.T
    keep, src = [], np.empty(len(emb), dtype=int)
    for i in range(len(emb)):
        hit = next((pos for pos, k in enumerate(keep) if sims[i, k] >= threshold), None)
        if hit is None:
            src[i] = len(keep)
            keep.append(i)
        else:
            src[i] = hit
    return np.array(keep), src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["flat", "hier"], required=True)
    ap.add_argument("--text", choices=["qa", "q"], default="qa", help="embed question+answer or question only")
    ap.add_argument("--embed-model", default="sentence-transformers/all-mpnet-base-v2")
    ap.add_argument("--k", type=int, default=10, help="flat: number of topics")
    ap.add_argument("--min-cluster", type=int, default=10, help="hier: HDBSCAN min_cluster_size")
    ap.add_argument("--parents", type=int, default=10, help="hier: number of top-level topics")
    ap.add_argument("--multi-threshold", type=float, default=0.25,
                    help="a pair counts as multi-topic if its 2nd topic gets >= this share of the distribution")
    ap.add_argument("--dedup", type=float, default=0.95, help="collapse pairs at or above this cosine (1.01 = off)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    df, docs = load_docs(args.text)
    encoder = SentenceTransformer(args.embed_model)
    emb_all = embed(docs, encoder, args.embed_model, args.text)
    keep, src = dedup(emb_all, args.dedup)
    docs, emb = [docs[i] for i in keep], emb_all[keep]
    df["dup_of"] = [df.pair_id[keep[s]] if keep[s] != i else "" for i, s in enumerate(src)]

    vectorizer = CountVectorizer(stop_words=list(ENGLISH_STOP_WORDS | DOMAIN_STOP),
                                 ngram_range=(1, 2), min_df=2)
    model = BERTopic(
        embedding_model=encoder,
        umap_model=UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=args.seed),
        hdbscan_model=(KMeans(n_clusters=args.k, n_init=10, random_state=args.seed) if args.mode == "flat"
                       else HDBSCAN(min_cluster_size=args.min_cluster, metric="euclidean",
                                    cluster_selection_method="leaf", prediction_data=True)),
        vectorizer_model=vectorizer,
        representation_model=KeyBERTInspired(),
        top_n_words=10,
    )
    topics, _ = model.fit_transform(docs, emb)
    n_outliers = int(sum(t == -1 for t in topics))
    if n_outliers:
        topics = model.reduce_outliers(docs, topics, strategy="embeddings", embeddings=emb)
        model.update_topics(docs, topics=topics, vectorizer_model=vectorizer, representation_model=KeyBERTInspired())

    run = (f"flat_{args.text}_k{args.k}" if args.mode == "flat"
           else f"hier_{args.text}_mc{args.min_cluster}_p{args.parents}")
    if args.dedup <= 1:
        run += f"_dd{int(round(args.dedup * 100))}"
    out = HERE / "runs" / run
    out.mkdir(parents=True, exist_ok=True)

    info = model.get_topic_info()
    words = {t: ", ".join(w for w, _ in model.get_topic(t)[:8]) for t in info.Topic}
    topics = np.asarray(topics)
    df["topic"] = topics[src]
    df["topic_words"] = df.topic.map(words)

    # Parent level for the two-level run: cut the merge tree at --parents clusters.
    if args.mode == "hier":
        from scipy.cluster import hierarchy as sch
        topic_ids = sorted(t for t in set(topics) if t != -1)
        cent = np.vstack([emb[topics == t].mean(axis=0) for t in topic_ids])
        Z = sch.linkage(cent, method="ward")
        cut = sch.fcluster(Z, t=args.parents, criterion="maxclust")
        parent_of = dict(zip(topic_ids, cut.tolist()))
        df["parent"] = df.topic.map(parent_of)
        parent_rows = []
        for p in sorted(set(cut)):
            kids = [t for t in topic_ids if parent_of[t] == p]
            mask = df.parent == p
            parent_rows.append({"parent": p, "count": int(mask.sum()), "n_subtopics": len(kids),
                                "subtopics": " | ".join(f"{t}: {words[t]}" for t in kids)})
        pd.DataFrame(parent_rows).sort_values("count", ascending=False).to_csv(out / "parents.csv", index=False)
        (out / "hierarchy.txt").write_text(model.get_topic_tree(model.hierarchical_topics(docs)))

    # Multi-topic estimate from the per-pair topic distribution.
    dist, _ = model.approximate_distribution(docs, window=8, stride=4)
    share = dist / np.clip(dist.sum(axis=1, keepdims=True), 1e-12, None)
    top2 = np.sort(share, axis=1)[:, ::-1][:, :2][src]
    multi = top2[:, 1] >= args.multi_threshold
    df["top_share"], df["second_share"] = top2[:, 0].round(3), top2[:, 1].round(3)
    second = np.argsort(share, axis=1)[:, ::-1][:, 1][src]
    df["second_topic"] = np.where(multi, second, -1)

    reps = model.get_representative_docs()
    topic_rows = []
    for t in sorted(set(topics)):
        sub = df[df.topic == t]
        topic_rows.append({
            "topic": t, "count": len(sub), "unique": int((sub.dup_of == "").sum()), "words": words[t],
            "by_dataset": json.dumps(Counter(sub.dataset).most_common()),
            "examples": " || ".join(d.split("?")[0] + "?" for d in reps.get(t, [])[:3]),
        })
    pd.DataFrame(topic_rows).sort_values("count", ascending=False).to_csv(out / "topics.csv", index=False)
    df.drop(columns=["answer"]).to_csv(out / "assignments.csv", index=False)
    pd.crosstab(df.topic, df.dataset).to_csv(out / "topic_by_dataset.csv")

    summary = {
        "run": run, "args": vars(args), "n_pairs": len(df), "n_unique_clustered": len(keep),
        "n_topics": len(set(topics)), "hdbscan_outliers_reassigned": n_outliers,
        "multi_topic_pairs": int(multi.sum()), "multi_topic_share": round(float(multi.mean()), 3),
        "median_top_share": round(float(np.median(top2[:, 0])), 3),
    }
    if args.mode == "hier":
        summary["n_parents"] = int(len(set(cut)))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
