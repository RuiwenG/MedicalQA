#!/usr/bin/env python3
"""Fleiss' kappa across the three LLM judges, computed the way the human
inter-annotator figure was: nominal four-point ratings, the judges as raters,
plus a pooled value that treats every Q&A x metric combination as its own unit.

Each judge ran three times, so every figure is computed for each of the 27 ways
of taking one run from each judge; the mean is reported with the lowest and
highest combination. Because the human figure rests on only 20 shared pairs,
the script also redraws random 20-pair samples to show how much Fleiss' kappa
moves at that size.

    python eval/llm_judge/fleiss_judges.py              # all prompt versions
    python eval/llm_judge/fleiss_judges.py --arms v3    # v3 only, as in the human study
"""
import argparse
import csv
import itertools
import json
import os
import random
import statistics as st
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RES = REPO / "eval" / "results"
ARMS = {"SingleAgent-v1", "SingleAgent-v2", "SingleAgent"}
ARM_OF = {"v1": "SingleAgent-v1", "v2": "SingleAgent-v2", "v3": "SingleAgent"}

# The judge runs this analysis covers (see the comparison page for provenance).
DS = "Master-Teepa_deepseek-v4-pro_full_Eval_"
GO = "Master-Teepa_openai-gpt-oss-120b_full_Eval_"
GM = "Master-Teepa_google-gemma-4-31b-it_full_Eval_"
RUNS = {
    "DeepSeek": [[(DS + "20260909_154820.csv", {"SingleAgent-v1"}),
                  (DS + "20260909_005804.csv", {"SingleAgent-v2"}),
                  (DS + "20260909_131051.csv", {"SingleAgent"})],
                 [(DS + "20260909_173408.csv", None)],
                 [(DS + "20260911_152538.csv", None)]],
    "gpt-oss": [[(GO + "20261001_214603.csv", None)],
                [(GO + "20261001_223931.csv", None)],
                [(GO + "20261001_224310.csv", None)]],
    "Gemma 4": [[(GM + "20261002_114123.csv", None)],
                [(GM + "20261002_120506_70408.csv", None)],
                [(GM + "RUN3_20261002_113714.csv", None)]],
}

# Human figure's metrics first; Standalone is reported but kept out of the
# headline pool so the pooled value is comparable with the human one.
METRICS = {
    "Trustworthiness": ("Q&A Trustworthiness Attribute", ["Excellent", "Good", "Fair", "Poor"]),
    "Clarity": ("Q&A Clarity Attribute", ["Very easy to understand", "Easy", "Somewhat difficult", "Difficult"]),
    "Usefulness": ("Q&A Usefulness Attribute", ["Highly useful", "Useful", "Limited useful", "Not useful"]),
    "Caregiver recommendation": ("Caregiver Recommendation",
                                 ["Yes", "Yes, but with minor edits (meaning unchanged)", "No, it needs major edits", "No"]),
    "Standalone": ("Q&A Standalone Attribute", ["Fully standalone", "Mostly standalone", "Somewhat dependent", "Not standalone"]),
}
HUMAN_SET = ["Trustworthiness", "Clarity", "Usefulness", "Caregiver recommendation"]
# From the human inter-annotator figure (20 shared pairs, 3 raters each).
HUMAN = {"Trustworthiness": 0.157, "Clarity": 0.011, "Usefulness": 0.150,
         "Caregiver recommendation": -0.140, "Pooled": 0.088}


def load_run(parts):
    rows = {}
    for name, keep in parts:
        for r in csv.DictReader(open(RES / name, encoding="utf-8")):
            if r["Approach"] in ARMS and (keep is None or r["Approach"] in keep):
                rows[r["QA ID"]] = r
    return rows


def fleiss(subjects):
    """subjects: list of tuples, one category per rater."""
    m = len(subjects[0]); n = len(subjects)
    tot = Counter(); p_sum = 0.0
    for t in subjects:
        c = Counter(t); tot.update(c)
        p_sum += (sum(v * v for v in c.values()) - m) / (m * (m - 1))
    p_bar = p_sum / n
    p_e = sum((v / (n * m)) ** 2 for v in tot.values())
    return None if p_e >= 0.999999 else (p_bar - p_e) / (1 - p_e)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arms", nargs="+", choices=sorted(ARM_OF), default=sorted(ARM_OF),
                    help="Prompt versions to include (the human study rated v3 only)")
    args = ap.parse_args()
    keep = {ARM_OF[a] for a in args.arms}
    runs = {j: [load_run(p) for p in parts] for j, parts in RUNS.items()}
    uids = sorted(set.intersection(*[set(r) for rs in runs.values() for r in rs]))
    first = runs[next(iter(runs))][1]
    uids = [u for u in uids if first[u]["Approach"] in keep]
    judges = list(runs)

    def level(run, u, metric):
        col, scale = METRICS[metric]
        v = run[u][col]
        return scale.index(v) if v in scale else None

    combos = list(itertools.product(range(3), repeat=len(judges)))

    def subjects_for(metric, combo, pool=uids):
        out = []
        for u in pool:
            t = tuple(level(runs[j][i], u, metric) for j, i in zip(judges, combo))
            if None not in t:
                out.append(t)
        return out

    def summ(v):
        v = [x for x in v if x is not None]
        return {"mean": st.mean(v), "min": min(v), "max": max(v)}

    arms_label = "+".join(sorted(args.arms))
    suffix = "" if len(args.arms) == 3 else "_" + "".join(sorted(args.arms))
    result = {"n_pairs": len(uids), "arms": sorted(args.arms), "judges": judges, "metrics": {}, "human": HUMAN}
    for metric in METRICS:
        result["metrics"][metric] = summ([fleiss(subjects_for(metric, c)) for c in combos])

    def pooled(metric_set, combo, pool=uids):
        subj = []
        for m in metric_set:
            subj += subjects_for(m, combo, pool)
        return fleiss(subj)

    result["metrics"]["Pooled"] = summ([pooled(HUMAN_SET, c) for c in combos])
    result["pooled_with_standalone"] = summ([pooled(list(METRICS), c) for c in combos])

    # How far does Fleiss' kappa move on 20 pairs? Redraw 20-pair samples.
    rng = random.Random(42)
    draws = {m: [] for m in HUMAN_SET + ["Pooled"]}
    for _ in range(2000):
        pool = rng.sample(uids, 20); c = rng.choice(combos)
        for m in HUMAN_SET:
            k = fleiss(subjects_for(m, c, pool)) if subjects_for(m, c, pool) else None
            if k is not None: draws[m].append(k)
        k = pooled(HUMAN_SET, c, pool)
        if k is not None: draws["Pooled"].append(k)
    def pct(v, q): v = sorted(v); return v[min(len(v) - 1, int(q * len(v)))]
    result["n20_interval"] = {m: {"lo": pct(v, .025), "hi": pct(v, .975), "defined": len(v)} for m, v in draws.items()}

    out_dir = REPO / "eval" / "llm_judge"
    json.dump(result, open(out_dir / f"fleiss_judges{suffix}.json", "w"), indent=1)

    print(f"Fleiss' kappa, 3 judges as raters, nominal four-point ratings, {len(uids)} pairs ({arms_label})")
    print(f"{'metric':<26}{'LLM judges':>11}{'range (27 run combos)':>24}{'human (20 pairs)':>18}{'LLM on 20 pairs, 95%':>24}")
    for m in HUMAN_SET + ["Pooled", "Standalone"]:
        s = result["metrics"][m]; h = HUMAN.get(m); iv = result["n20_interval"].get(m)
        hs = f"{h:.3f}" if h is not None else "—"
        ivs = f"{iv['lo']:.2f} to {iv['hi']:.2f}" if iv else "—"
        print(f"{m:<26}{s['mean']:>11.3f}{s['min']:>12.3f} – {s['max']:<9.3f}{hs:>18}{ivs:>24}")
    pw = result["pooled_with_standalone"]
    print(f"\npooled including Standalone: {pw['mean']:.3f} ({pw['min']:.3f} – {pw['max']:.3f})")
    plot(result, out_dir, suffix)


def plot(result, out_dir, suffix=""):
    which = "v3 prompt" if result["arms"] == ["v3"] else ", ".join(result["arms"]) + " prompts"
    import tempfile
    os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "mplcache"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    BLUE, ORANGE, PURPLE, GREY = "#2f6fad", "#c86427", "#6a5b9e", "#9a9a9a"
    labels = HUMAN_SET + ["Pooled"]
    shown = {"Caregiver recommendation": "Caregiver recommendation", "Pooled": "Pooled (all metrics)"}

    # 1. LLM judges alone, in the same layout as the human figure
    fig, ax = plt.subplots(figsize=(10, 6.2))
    ys = list(range(len(labels)))[::-1]
    for y, m in zip(ys, labels):
        s = result["metrics"][m]; v = s["mean"]
        color = PURPLE if m == "Pooled" else (BLUE if v >= 0 else ORANGE)
        ax.barh(y, v, height=0.62, color=color)
        ax.plot([s["min"], s["max"]], [y, y], color="#222", lw=1.2)
        ax.text(max(v, s["max"]) + 0.015, y, f"{v:.3f}", va="center",
                fontweight="bold" if m == "Pooled" else "normal", fontsize=12)
    ax.set_yticks(ys, [shown.get(m, m) for m in labels], fontsize=12)
    ax.axvline(0, color=GREY, lw=1)
    ax.set_xlim(-0.6, 0.6); ax.grid(axis="x", color="#e3e3e3"); ax.set_axisbelow(True)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.set_xlabel("Fleiss' κ  (0 = chance-level agreement; higher is better)", fontsize=12)
    fig.text(0.1, 0.96, "LLM judge agreement", fontsize=18, fontweight="bold")
    fig.text(0.1, 0.915, f"Fleiss' kappa on {result['n_pairs']} shared Q&A pairs, {which} (3 judges each); nominal four-point ratings",
             fontsize=12, color="#555")
    fig.text(0.1, 0.02, "Bar: mean over the 27 ways of taking one run from each judge; line: lowest to highest.\n"
             "Pooled treats each Q&A × metric combination as a separate rating unit.", fontsize=9.5, color="#666")
    fig.subplots_adjust(left=0.27, right=0.95, top=0.86, bottom=0.17)
    fig.savefig(out_dir / f"fleiss_llm_judges{suffix}.png", dpi=200)
    plt.close(fig)

    # 2. Human vs LLM judges, side by side
    fig, ax = plt.subplots(figsize=(10, 6.6))
    ys = list(range(len(labels)))[::-1]; h = 0.36
    for y, m in zip(ys, labels):
        hv = HUMAN[m]; lv = result["metrics"][m]["mean"]; iv = result["n20_interval"][m]
        ax.barh(y + h / 2, hv, height=h, color="#b9b9b9", label="Human annotators (20 pairs, v3 prompt)" if y == ys[0] else None)
        ax.barh(y - h / 2, lv, height=h, color=PURPLE if m == "Pooled" else BLUE,
                label=f"LLM judges ({result['n_pairs']} pairs, {which})" if y == ys[0] else None)
        ax.plot([iv["lo"], iv["hi"]], [y - h / 2] * 2, color="#222", lw=1.1)
        ax.text(hv + (0.015 if hv >= 0 else -0.015), y + h / 2, f"{hv:.3f}", va="center",
                ha="left" if hv >= 0 else "right", fontsize=10.5, color="#555")
        ax.text(max(lv, iv["hi"]) + 0.015, y - h / 2, f"{lv:.3f}", va="center", fontsize=10.5,
                fontweight="bold" if m == "Pooled" else "normal")
    ax.set_yticks(ys, [shown.get(m, m) for m in labels], fontsize=12)
    ax.axvline(0, color=GREY, lw=1)
    ax.set_xlim(-0.6, 0.8); ax.grid(axis="x", color="#e3e3e3"); ax.set_axisbelow(True)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    ax.set_xlabel("Fleiss' κ  (0 = chance-level agreement; higher is better)", fontsize=12)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=10.5)
    fig.text(0.1, 0.96, "Human annotators vs LLM judges", fontsize=18, fontweight="bold")
    fig.text(0.1, 0.915, "Fleiss' kappa, 3 raters, nominal four-point ratings", fontsize=12, color="#555")
    fig.subplots_adjust(top=0.82)
    fig.text(0.1, 0.02, "Line on each LLM bar: 95% range of the LLM value when recomputed on random 20-pair samples,\n"
             "the size of the human study, drawn from the same prompt version. The 20 human pairs are not identified, so the two may differ.", fontsize=9.5, color="#666")
    fig.subplots_adjust(left=0.27, right=0.95, top=0.82, bottom=0.17)
    fig.savefig(out_dir / f"fleiss_human_vs_llm{suffix}.png", dpi=200)
    plt.close(fig)
    print(f"\nfigures: {out_dir / f'fleiss_llm_judges{suffix}.png'}\n         {out_dir / f'fleiss_human_vs_llm{suffix}.png'}")


if __name__ == "__main__":
    main()
