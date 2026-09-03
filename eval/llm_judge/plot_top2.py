#!/usr/bin/env python3
"""Top-2 (score>=3) by generation approach with Wilson 95% CIs, styled to match
the human-eval slide: dot + CI per approach per metric, delta annotations."""
import csv
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt

CSV_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else
                "eval/results/Master-Teepa_deepseek-v4-pro_pilot40_Eval_20260807_120444.csv")

ROWS = [  # (row label, csv column, top-2 label values = scores 4 and 3)
    ("Alignment / trust", "QA Alignment/Trustworthiness Attribute", {"Excellent", "Good"}),
    ("Accessibility", "QA Accessibility Attribute", {"Very easy to understand", "Easy"}),
    ("Educational / actionable", "QA Educational/Actionable Value Attribute", {"Highly actionable", "Actionable"}),
    ("Mental-health value", "QA Mental Health Value Attribute", {"Highly supportive", "Supportive"}),
    ("Caregiver recommendation", "Caregiver Recommendation", {"Yes", "Yes, but with minor edits"}),
]
APPROACHES = [("MultiAgent-LLMChunking", "Multi-agent", "#2e73b4"),
              ("SingleAgent", "Single-agent", "#d0622b")]


def wilson(k, n, z=1.959964):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return 100 * p, 100 * max(0, centre - half), 100 * min(1, centre + half)


rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
model = rows[0]["Annotator"]

fig, ax = plt.subplots(figsize=(13.2, 7.6), dpi=200)
fig.patch.set_facecolor("white")
ax.set_facecolor("white")

OFF = 0.16
for yi, (label, col, top2) in enumerate(ROWS):
    y0 = len(ROWS) - 1 - yi
    pts = []
    for j, (app, _, color) in enumerate(APPROACHES):
        sub = [r for r in rows if r["Approach"] == app]
        n = len(sub)
        k = sum(1 for r in sub if r[col] in top2)
        p, lo, hi = wilson(k, n)
        y = y0 + (OFF if j == 0 else -OFF)
        ax.plot([lo, hi], [y, y], color=color, lw=2, solid_capstyle="butt", zorder=3)
        for xw in (lo, hi):
            ax.plot([xw, xw], [y - 0.045, y + 0.045], color=color, lw=2, zorder=3)
        ax.plot(p, y, "o", color=color, ms=9, zorder=4)
        ha, dx = ("center", 0) if p < 88 else ("right", 4)
        ax.annotate(f"{p:.0f}% (n={n})", (p, y), textcoords="offset points",
                    xytext=(dx, 8), ha=ha, fontsize=11.5, color="#333333")
        pts.append(p)
    ax.plot(sorted(pts), [y0, y0], color="#cccccc", lw=1, zorder=1)
    import matplotlib.transforms as mtransforms
    tr = mtransforms.blended_transform_factory(ax.transAxes, ax.transData)
    ax.text(1.03, y0, f"Δ {pts[0] - pts[1]:+.1f} pp", transform=tr,
            fontsize=12.5, color="#444444", va="center", ha="left")

ax.set_yticks(range(len(ROWS) - 1, -1, -1))
ax.set_yticklabels([r[0] for r in ROWS], fontsize=14)
ax.set_ylim(-0.6, len(ROWS) - 0.25)
ax.set_xlim(0, 100)
ax.set_xticks([0, 20, 40, 60, 80, 100])
ax.tick_params(axis="x", labelsize=13)
ax.set_xlabel("Top-2 ratings (%) with Wilson 95% CI", fontsize=13.5)
ax.set_axisbelow(True)
ax.grid(axis="x", color="#dddddd", linewidth=0.9)
for spine in ax.spines.values():
    spine.set_visible(False)
ax.tick_params(length=0)

ax.set_title("Top-2 performance by generation approach",
             fontsize=19, fontweight="bold", loc="left", pad=46)
ax.text(0, 1.085, f"LLM judge ({model}); same 40-pair batch as the human pilot. Top-2 means score ≥3.",
        transform=ax.transAxes, fontsize=12.5, color="#555555")
ax.text(0, 1.035, "Delta is multi-agent minus single-agent; intervals are wide at this sample size.",
        transform=ax.transAxes, fontsize=12.5, color="#555555")

handles = [plt.Line2D([], [], color=c, marker="o", ms=9, lw=2)
           for _, _, c in APPROACHES]
ax.legend(handles, [lbl for _, lbl, _ in APPROACHES],
          loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2,
          frameon=False, fontsize=13.5)

fig.tight_layout()
out = Path("eval/llm_judge") / (CSV_PATH.stem.replace("_Eval_", "_top2_") + ".png")
fig.savefig(out, bbox_inches="tight", facecolor="white")
print(out)
