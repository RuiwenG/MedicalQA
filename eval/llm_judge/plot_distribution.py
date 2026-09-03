#!/usr/bin/env python3
"""Ordinal rating distribution chart for an LLM-judge CSV, styled to match the
human-eval slide (same rows, same 4..1 score colors, same label format)."""
import csv
import sys
from collections import Counter
from pathlib import Path

import matplotlib.pyplot as plt

CSV_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else
                "eval/results/Master-Teepa_deepseek-v4-pro_pilot40_Eval_20260807_120444.csv")

# label -> score, strongest first (index 0 = score 4), mirroring the site form
ROWS = [
    ("Alignment / trust", "QA Alignment/Trustworthiness Attribute",
     ["Excellent", "Good", "Fair", "Poor"]),
    ("Accessibility", "QA Accessibility Attribute",
     ["Very easy to understand", "Easy", "Somewhat difficult", "Difficult"]),
    ("Educational / actionable", "QA Educational/Actionable Value Attribute",
     ["Highly actionable", "Actionable", "Limited usefulness", "Not useful"]),
    ("Mental-health value", "QA Mental Health Value Attribute",
     ["Highly supportive", "Supportive", "Limited support", "Unsupportive / potentially harmful"]),
    ("Caregiver recommendation", "Caregiver Recommendation",
     ["Yes", "Yes, but with minor edits", "Only after major revisions", "No"]),
]
COLORS = ["#2f6da4", "#7fbfe5", "#e2a63d", "#d0622b"]      # score 4 -> 1
LABEL_INK = ["white", "#1a1a1a", "#1a1a1a", "white"]        # per segment bg

rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
n_total = len(rows)
model = rows[0]["Annotator"]

fig, ax = plt.subplots(figsize=(12.6, 7.0), dpi=200)
fig.patch.set_facecolor("white")
ax.set_facecolor("white")

ypos = range(len(ROWS) - 1, -1, -1)
for y, (title, col, order) in zip(ypos, ROWS):
    counts = Counter(r[col] for r in rows)
    n = sum(counts.get(lbl, 0) for lbl in order)
    left = 0.0
    for score_i, lbl in enumerate(order):
        c = counts.get(lbl, 0)
        if not c:
            continue
        pct = 100.0 * c / n
        ax.barh(y, pct, left=left, height=0.62, color=COLORS[score_i],
                edgecolor="white", linewidth=2)
        if pct >= 5:
            ax.text(left + pct / 2, y, f"{pct:.0f}%\n(n={c})",
                    ha="center", va="center", fontsize=11.5,
                    color=LABEL_INK[score_i], linespacing=1.4)
        left += pct

ax.set_yticks(list(ypos))
ax.set_yticklabels([r[0] for r in ROWS], fontsize=14)
ax.set_xlim(0, 100)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.tick_params(axis="x", labelsize=13)
ax.set_xlabel("Share of ratings (%)", fontsize=13.5)
ax.set_axisbelow(True)
ax.grid(axis="x", color="#d9d9d9", linewidth=0.8)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.tick_params(length=0)

ax.set_title("Complete ordinal rating distributions",
             fontsize=19, fontweight="bold", loc="left", pad=28)
scope = ("same 40-pair batch as the human pilot" if n_total <= 60
         else "full corpus, all four generation approaches")
ax.text(0, 1.045, f"LLM judge ({model}); {scope}, n={n_total}. "
        "Scores are ordered from 4 (best) to 1 (worst).",
        transform=ax.transAxes, fontsize=12.5, color="#555555")

handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in COLORS]
ax.legend(handles, [f"Score {s}" for s in (4, 3, 2, 1)],
          loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4,
          frameon=False, fontsize=13, handlelength=1.4, handleheight=1.1)

fig.tight_layout()
out = Path("eval/llm_judge") / (CSV_PATH.stem.replace("_Eval_", "_dist_") + ".png")
fig.savefig(out, bbox_inches="tight", facecolor="white")
print(out)
