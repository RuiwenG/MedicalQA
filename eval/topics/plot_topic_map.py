"""Topic map of the final BERTopic structure (k = 12 consensus topics), as an image.

Every unique pair placed by a 2D UMAP of its question + answer embedding (seed 42,
for display only) and coloured by its k = 12 topic, with each topic's name on its
most central pair. The animal fluency test
sits so far from everything else that it is moved in behind a dashed break.

Writes figures/topic_map_k12.png (300 dpi) and .pdf, and figures/topic_panels_k12.png and .pdf:
one small panel per topic with its pairs highlighted, which shows each topic's real spread.

Usage: python eval/topics/plot_topic_map.py
"""
import csv
import json
from collections import Counter
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import patheffects  # noqa: E402
from umap import UMAP  # noqa: E402

from bertopic_run import dedup, embed, load_docs  # noqa: E402
from label_agreement import K12_TO_F  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "figures"
BLUE, GREY, INK, MUTED = "#2f6fd6", "#b9b6ae", "#1f2328", "#5f6670"
# Label nudges (points) where two topics' names would otherwise collide.
LABEL_SHIFT = {"F07": (-25, 30)}


def wrap(name, width=24):
    if len(name) <= width:
        return name
    cut = name.rfind(" ", 0, width)
    return name[:cut] + "\n" + name[cut + 1:]


def main():
    df, docs = load_docs("qa")
    model = "sentence-transformers/all-mpnet-base-v2"
    emb = embed(docs, SentenceTransformer(model), model, "qa")
    keep, _ = dedup(emb, 0.95)
    xy = UMAP(n_neighbors=15, n_components=2, min_dist=0.1, metric="cosine", random_state=42).fit_transform(emb[keep])
    xy = (xy - xy.min(0)) / (xy.max(0) - xy.min(0))

    topic_of = {r["pair_id"]: K12_TO_F[r["consensus_topic"]]
                for r in csv.DictReader((HERE / "runs" / "stability_k12" / "assignments.csv").open())}
    names = {t["id"]: t["name"] for t in json.loads((HERE / "codebook_v1.json").read_text())["topics"]}
    ids = [df.pair_id[i] for i in keep]
    counts = Counter(topic_of.values())

    # Squeeze the main cloud into 0-0.8 of the width and park the far outlier at 0.95.
    main_max = max(x for (x, _), p in zip(xy, ids) if topic_of[p] != "F12")
    sx = lambda x: 0.95 if x > 0.8 else x * 0.8 / main_max  # noqa: E731

    # Twelve topics: six colour-blind-checked hues (dataviz reference palette) x two marker shapes, largest first,
    # so no two topics share both colour and shape. Names are written on the plot as well, so colour is never alone.
    hues = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
    order = [t for t, _ in counts.most_common()]
    style = {t: (hues[i % 6], "o" if i < 6 else "^") for i, t in enumerate(order)}
    xs = np.array([sx(x) for x, _ in xy])
    fig, ax = plt.subplots(figsize=(13, 7.8))
    for t in order:
        mask = np.array([topic_of[p] == t for p in ids])
        colour, marker = style[t]
        ax.scatter(xs[mask], xy[mask, 1], s=14 if marker == "o" else 18, c=colour, marker=marker, alpha=0.85,
                   linewidths=0.3, edgecolors="white", zorder=2, label=f"{names[t]} ({counts[t]})")
        # Name the topic at its medoid: the pair closest to all the others, so the label sits on real data.
        pts = np.column_stack([xs[mask], xy[mask, 1]])
        mx, my = pts[np.argmin(np.linalg.norm(pts[:, None] - pts[None], axis=2).sum(1))]
        ax.annotate(wrap(names[t], 26), (mx, my), xytext=LABEL_SHIFT.get(t, (0, 10)), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8.5, color=INK, zorder=4,
                    path_effects=[patheffects.withStroke(linewidth=3, foreground="white")])
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8.5, markerscale=1.6,
              title="Topic (pairs)", title_fontsize=9, alignment="left")
    # Scale break, with a gap where the outlier's label crosses it.
    for y_from, y_to in ((-0.1, 0.48), (0.72, 1.02)):
        ax.plot([0.89, 0.89], [y_from, y_to], color=MUTED, linestyle=(0, (4, 4)), linewidth=0.9, zorder=1)
    ax.text(0.9, 1.06, "Moved in to fit:\nfar from all others", fontsize=8, color=MUTED, va="top")

    ax.text(0, 1.13, "Broad care topics crowd together; specialised topics sit at the edges",
            transform=ax.transAxes, fontsize=13, color=INK, va="bottom")
    ax.text(0, 1.04, f"{len(ids)} unique pairs, coloured by their k = 12 topic; each name sits on its topic's most "
            "central pair.\nPairs closer together have more similar wording; the axes have no units.",
            transform=ax.transAxes, fontsize=8.5, color=MUTED, va="bottom")
    ax.set_xlim(-0.04, 1.05)
    ax.set_ylim(-0.12, 1.08)
    ax.axis("off")
    OUT.mkdir(exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"topic_map_k12.{ext}", dpi=300, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT / 'topic_map_k12.png'} and .pdf")
    small_multiples(xy, ids, topic_of, names, counts, sx)


def small_multiples(xy, ids, topic_of, names, counts, sx):
    """One panel per topic: its pairs in blue over everyone else's in grey, so each topic's real spread shows."""
    order = [t for t, _ in counts.most_common()]
    fig, axes = plt.subplots(3, 4, figsize=(13, 9.5))
    xs = np.array([sx(x) for x, _ in xy])
    for ax, t in zip(axes.flat, order):
        mask = np.array([topic_of[p] == t for p in ids])
        ax.scatter(xs[~mask], xy[~mask, 1], s=2, c=GREY, alpha=0.45, linewidths=0)
        ax.scatter(xs[mask], xy[mask, 1], s=6, c=BLUE, linewidths=0)
        ax.set_title(f"{wrap(names[t], 30)}\n{counts[t]} pairs", fontsize=8.5, color=INK, loc="left")
        ax.set_xlim(-0.04, 1.02)
        ax.set_ylim(-0.04, 1.04)
        ax.axis("off")
    fig.suptitle("Each topic is a region of one continuous cloud, and some are split in two or three",
                 x=0.02, ha="left", fontsize=13, color=INK)
    fig.text(0.02, 0.935, "Blue: the topic's unique pairs. Grey: all other pairs. Same 2D layout as the topic map "
             "(UMAP, display only); the animal fluency test is moved in at the right.", fontsize=8.5, color=MUTED)
    fig.subplots_adjust(top=0.86, hspace=0.45, wspace=0.08)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"topic_panels_k12.{ext}", dpi=300, bbox_inches="tight", facecolor="white")
    print(f"wrote {OUT / 'topic_panels_k12.png'} and .pdf")


if __name__ == "__main__":
    main()
