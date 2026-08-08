#!/usr/bin/env python3
"""Create publication-ready plots from the human-evaluation analysis tables.

The script reads ``eval/results/analysis/ratings_scored.csv`` and the summary
tables written by ``eval/analyze_ratings.py``. It deliberately visualizes the
ordinal ratings as distributions, medians, and Top-2 rates rather than means.

Run from anywhere inside the repository:

    python eval/plot_results.py
    python eval/plot_results.py --analysis-dir eval/results/analysis
"""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

# Keep Matplotlib/font caches in a writable location on shared systems.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "medicalqa-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "medicalqa-cache"))

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


METRICS = [
    {
        "short": "Alignment / trust",
        "full": "QA Alignment/Trustworthiness",
        "attribute": "qa_alignment_attribute",
        "score": "qa_alignment_score",
    },
    {
        "short": "Accessibility",
        "full": "QA Accessibility",
        "attribute": "qa_accessibility_attribute",
        "score": "qa_accessibility_score",
    },
    {
        "short": "Educational / actionable",
        "full": "QA Educational/Actionable Value",
        "attribute": "qa_edu_actionable_attribute",
        "score": "qa_edu_actionable_score",
    },
    {
        "short": "Mental-health value",
        "full": "QA Mental Health Value",
        "attribute": "qa_mental_health_attribute",
        "score": "qa_mental_health_score",
    },
    {
        "short": "Caregiver recommendation",
        "full": "Caregiver recommendation",
        "attribute": "caregiver_recommendation",
        "score": "recommendation_score",
    },
]

SHORT_BY_FULL = {metric["full"]: metric["short"] for metric in METRICS}
APPROACH_LABELS = {
    "MultiAgent-LLMChunking": "Multi-agent",
    "SingleAgent": "Single-agent",
}

# Okabe-Ito-inspired palette. Tier colors run from strongest to weakest.
TIER_COLORS = {4: "#0072B2", 3: "#56B4E9", 2: "#E69F00", 1: "#D55E00"}
APPROACH_COLORS = {
    "MultiAgent-LLMChunking": "#0072B2",
    "SingleAgent": "#D55E00",
}
METRIC_COLORS = ["#0072B2", "#009E73", "#E69F00", "#CC79A7"]


def find_repo_root() -> Path:
    """Find the project root without requiring a particular working directory."""
    for candidate in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if (candidate / "eval" / "analyze_ratings.py").exists():
            return candidate
    raise SystemExit("Could not locate the repository root.")


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Return a Wilson 95% interval as percentages."""
    if total == 0:
        return (float("nan"), float("nan"))
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    half = z * np.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return 100 * (centre - half), 100 * (centre + half)


def setup_style() -> None:
    sns.set_theme(style="whitegrid", context="notebook")
    plt.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 240,
            "savefig.bbox": "tight",
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.titlesize": 13,
            "axes.labelsize": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
        }
    )


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", facecolor="white")
    fig.savefig(output_dir / f"{stem}.svg", facecolor="white")
    plt.close(fig)


def plot_ordinal_distributions(ratings: pd.DataFrame, output_dir: Path) -> None:
    """Plot the complete score distribution for each ordinal metric."""
    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    y_positions = np.arange(len(METRICS))
    left = np.zeros(len(METRICS))

    for score in [4, 3, 2, 1]:
        percentages = []
        counts = []
        for metric in METRICS:
            values = ratings[metric["score"]].dropna()
            count = int((values == score).sum())
            counts.append(count)
            percentages.append(100 * count / len(values) if len(values) else 0)

        bars = ax.barh(
            y_positions,
            percentages,
            left=left,
            color=TIER_COLORS[score],
            edgecolor="white",
            linewidth=0.8,
            height=0.68,
        )
        for bar, percentage, count in zip(bars, percentages, counts):
            if percentage >= 8:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_y() + bar.get_height() / 2,
                    f"{percentage:.0f}%\n(n={count})",
                    ha="center",
                    va="center",
                    fontsize=8.5,
                    color="white" if score in {4, 1} else "#1A1A1A",
                )
        left += np.asarray(percentages)

    ax.set_yticks(y_positions, [metric["short"] for metric in METRICS])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of ratings (%)")
    ax.set_ylabel("")
    fig.suptitle(
        "Complete ordinal rating distributions",
        x=0.26,
        y=0.98,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.26,
        0.925,
        f"All available ratings; n={len(ratings)}. Scores are ordered from 4 (best) to 1 (worst).",
        fontsize=9.5,
        color="#555555",
    )
    ax.legend(
        handles=[Patch(facecolor=TIER_COLORS[s], label=f"Score {s}") for s in [4, 3, 2, 1]],
        ncol=4,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
    )
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.26, bottom=0.23, top=0.84)
    save_figure(fig, output_dir, "ordinal_distributions")


def approach_top2_table(ratings: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for metric in METRICS:
        for approach in APPROACH_LABELS:
            values = ratings.loc[ratings["approach"] == approach, metric["score"]].dropna()
            total = len(values)
            successes = int((values >= 3).sum())
            low, high = wilson_interval(successes, total)
            rows.append(
                {
                    "metric": metric["short"],
                    "approach": approach,
                    "n": total,
                    "top2_pct": 100 * successes / total if total else np.nan,
                    "ci_low": low,
                    "ci_high": high,
                }
            )
    return pd.DataFrame(rows)


def plot_approach_comparison(ratings: pd.DataFrame, output_dir: Path) -> None:
    """Compare Top-2 rates with Wilson intervals, without treating ranks as means."""
    table = approach_top2_table(ratings)
    fig, ax = plt.subplots(figsize=(10.5, 6.1))
    base_y = np.arange(len(METRICS))
    offsets = {"MultiAgent-LLMChunking": -0.13, "SingleAgent": 0.13}

    for y, metric in zip(base_y, METRICS):
        subset = table[table["metric"] == metric["short"]].set_index("approach")
        rates = [subset.loc[approach, "top2_pct"] for approach in APPROACH_LABELS]
        ax.plot(rates, [y, y], color="#C8C8C8", linewidth=1.5, zorder=1)
        delta = rates[0] - rates[1]
        ax.text(101.5, y, f"Δ {delta:+.1f} pp", va="center", fontsize=9, color="#444444")

    for approach in APPROACH_LABELS:
        subset = table[table["approach"] == approach]
        y = base_y + offsets[approach]
        values = subset["top2_pct"].to_numpy()
        xerr = np.vstack(
            [values - subset["ci_low"].to_numpy(), subset["ci_high"].to_numpy() - values]
        )
        ax.errorbar(
            values,
            y,
            xerr=xerr,
            fmt="o",
            markersize=7,
            capsize=3,
            elinewidth=1.5,
            color=APPROACH_COLORS[approach],
            label=APPROACH_LABELS[approach],
            zorder=3,
        )
        for x, yy, n in zip(values, y, subset["n"]):
            ax.text(x, yy - 0.19, f"{x:.0f}% (n={n})", ha="center", va="top", fontsize=8)

    ax.set_yticks(base_y, [metric["short"] for metric in METRICS])
    ax.invert_yaxis()
    ax.set_xlim(0, 114)
    ax.set_xticks(np.arange(0, 101, 20))
    ax.set_xlabel("Top-2 ratings (%) with Wilson 95% CI")
    ax.set_ylabel("")
    fig.suptitle(
        "Top-2 performance by generation approach",
        x=0.26,
        y=0.98,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.26,
        0.925,
        "Top-2 means score ≥3. Delta is multi-agent minus single-agent; intervals are wide at this sample size.",
        fontsize=9.5,
        color="#555555",
    )
    ax.legend(loc="upper center", bbox_to_anchor=(0.48, -0.14), ncol=2)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.26, bottom=0.2, top=0.84, right=0.93)
    save_figure(fig, output_dir, "approach_top2_comparison")


def plot_error_taxonomy(errors: pd.DataFrame, output_dir: Path) -> None:
    """Show the prevalence of every coded error, faceted by evaluation metric."""
    ordered_metrics = [metric["full"] for metric in METRICS[:4]]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.8), sharex=True)

    for ax, metric_name, color in zip(axes.flat, ordered_metrics, METRIC_COLORS):
        subset = (
            errors[errors["metric"] == metric_name]
            .sort_values("pct_of_ratings", ascending=True)
            .copy()
        )
        ax.barh(subset["error"], subset["pct_of_ratings"], color=color, alpha=0.88)
        for y, (_, row) in enumerate(subset.iterrows()):
            ax.text(
                row["pct_of_ratings"] + 0.7,
                y,
                f"{row['pct_of_ratings']:.1f}% (n={int(row['n'])})",
                va="center",
                fontsize=8.5,
            )
        ax.set_title(SHORT_BY_FULL[metric_name], loc="left")
        ax.set_ylabel("")
        ax.set_xlim(0, max(45, errors["pct_of_ratings"].max() + 8))
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)

    for ax in axes[-1, :]:
        ax.set_xlabel("Share of all ratings carrying this error (%)")
    fig.suptitle("Error taxonomy reveals the most common failure modes", x=0.08, ha="left", fontweight="bold")
    fig.text(
        0.08,
        0.94,
        "Errors are multi-select, so percentages within a panel need not sum to 100%.",
        fontsize=9.5,
        color="#555555",
    )
    fig.subplots_adjust(left=0.19, right=0.96, bottom=0.1, top=0.88, hspace=0.48, wspace=0.32)
    save_figure(fig, output_dir, "error_taxonomy")


def plot_annotator_calibration(per_annotator: pd.DataFrame, output_dir: Path) -> None:
    """Plot Top-2 rates by annotator to make threshold differences visible."""
    table = per_annotator.copy()
    table["metric"] = table["metric"].map(SHORT_BY_FULL)
    metric_order = [metric["short"] for metric in METRICS[:4]]
    annotator_order = (
        table.groupby("annotator")["n"].sum().sort_values(ascending=False).index.tolist()
    )
    heatmap = table.pivot(index="annotator", columns="metric", values="top2_pct").reindex(
        index=annotator_order, columns=metric_order
    )
    sample_sizes = table.pivot(index="annotator", columns="metric", values="n").reindex(
        index=annotator_order, columns=metric_order
    )
    annotations = np.empty(heatmap.shape, dtype=object)
    for row in range(heatmap.shape[0]):
        for col in range(heatmap.shape[1]):
            value = heatmap.iloc[row, col]
            n = sample_sizes.iloc[row, col]
            annotations[row, col] = "—" if pd.isna(value) else f"{value:.0f}%\nn={int(n)}"

    fig, ax = plt.subplots(figsize=(10.5, 4.5))
    sns.heatmap(
        heatmap,
        ax=ax,
        annot=annotations,
        fmt="",
        cmap=sns.light_palette("#0072B2", as_cmap=True),
        vmin=0,
        vmax=100,
        linewidths=1,
        linecolor="white",
        cbar_kws={"label": "Top-2 ratings (%)", "shrink": 0.8},
    )
    ax.set_xlabel("")
    ax.set_ylabel("")
    compact_labels = [
        "Alignment /\ntrust",
        "Accessibility",
        "Educational /\nactionable",
        "Mental-health\nvalue",
    ]
    ax.set_xticklabels(compact_labels, rotation=0, ha="center")
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
    ax.set_title("Annotator calibration varies most on substantive value", loc="left", pad=28)
    ax.text(
        0,
        1.04,
        "Cell labels show Top-2 rate and ratings per annotator; differences may reflect rubric interpretation.",
        transform=ax.transAxes,
        fontsize=9.5,
        color="#555555",
    )
    fig.subplots_adjust(left=0.19, right=0.94, bottom=0.2, top=0.78)
    save_figure(fig, output_dir, "annotator_calibration")


def plot_agreement(agreement: pd.DataFrame, output_dir: Path) -> None:
    """Plot ordinal alpha beside exact and within-one agreement."""
    table = agreement.copy()
    table["metric"] = table["metric"].map(SHORT_BY_FULL)
    metric_order = [metric["short"] for metric in METRICS]
    table = table.set_index("metric").reindex(metric_order).reset_index()
    y = np.arange(len(table))

    fig, (alpha_ax, pct_ax) = plt.subplots(
        1,
        2,
        figsize=(11, 5.6),
        gridspec_kw={"width_ratios": [1, 1.25]},
    )

    alpha_ax.axvspan(0.8, 1.0, color="#009E73", alpha=0.10)
    alpha_ax.axvspan(0.67, 0.8, color="#E69F00", alpha=0.12)
    alpha_ax.axvline(0, color="#999999", linewidth=1)
    alpha_ax.scatter(table["alpha_ordinal"], y, color="#0072B2", s=55, zorder=3)
    for x, yy in zip(table["alpha_ordinal"], y):
        alpha_ax.text(x + 0.035, yy, f"{x:.2f}", va="center", fontsize=8.5)
    alpha_ax.set_xlim(-0.4, 1.08)
    alpha_ax.set_yticks(y, table["metric"])
    alpha_ax.invert_yaxis()
    alpha_ax.set_xlabel("Krippendorff ordinal α")
    alpha_ax.set_title("Reliability", loc="left")
    alpha_ax.grid(axis="y", visible=False)

    for yy, row in table.iterrows():
        pct_ax.plot(
            [row["exact_pct"], row["within1_pct"]],
            [yy, yy],
            color="#B8B8B8",
            linewidth=2,
            zorder=1,
        )
    pct_ax.scatter(table["exact_pct"], y, color="#D55E00", marker="o", s=52, zorder=3)
    pct_ax.scatter(table["within1_pct"], y, color="#0072B2", marker="D", s=46, zorder=3)
    for yy, row in table.iterrows():
        pct_ax.text(row["exact_pct"] - 2, yy - 0.19, f"{row['exact_pct']:.0f}%", ha="right", fontsize=8)
        pct_ax.text(row["within1_pct"] + 2, yy - 0.19, f"{row['within1_pct']:.0f}%", ha="left", fontsize=8)
    pct_ax.set_xlim(0, 112)
    pct_ax.set_yticks(y, [])
    pct_ax.invert_yaxis()
    pct_ax.set_xlabel("Agreement across overlapping pairs (%)")
    pct_ax.set_title("Observed agreement", loc="left")
    pct_ax.grid(axis="y", visible=False)
    pct_ax.legend(
        handles=[
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#D55E00", label="Exact"),
            Line2D([0], [0], marker="D", color="none", markerfacecolor="#0072B2", label="Within 1 point"),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=2,
    )

    fig.suptitle("Inter-annotator agreement remains uneven", x=0.08, ha="left", fontweight="bold")
    fig.text(
        0.08,
        0.92,
        f"Based on {int(table['units'].max())} overlapping QA pairs; α ≥0.80 is reliable and 0.67–0.80 is tentative.",
        fontsize=9.5,
        color="#555555",
    )
    fig.subplots_adjust(left=0.24, right=0.96, bottom=0.2, top=0.82, wspace=0.18)
    save_figure(fig, output_dir, "inter_annotator_agreement")


def parse_args() -> argparse.Namespace:
    root = find_repo_root()
    default_analysis = root / "eval" / "results" / "analysis"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=default_analysis,
        help="Directory containing the CSV tables from analyze_ratings.py.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot destination (default: <analysis-dir>/plots).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    analysis_dir = args.analysis_dir.resolve()
    output_dir = (args.output_dir or analysis_dir / "plots").resolve()

    required = {
        "ratings": analysis_dir / "ratings_scored.csv",
        "errors": analysis_dir / "error_counts.csv",
        "annotators": analysis_dir / "per_annotator.csv",
        "agreement": analysis_dir / "agreement.csv",
    }
    missing = [path for path in required.values() if not path.exists()]
    if missing:
        joined = "\n  ".join(str(path) for path in missing)
        raise SystemExit(f"Missing required analysis table(s):\n  {joined}")

    setup_style()
    ratings = pd.read_csv(required["ratings"])
    errors = pd.read_csv(required["errors"])
    annotators = pd.read_csv(required["annotators"])
    agreement = pd.read_csv(required["agreement"])

    plot_ordinal_distributions(ratings, output_dir)
    plot_approach_comparison(ratings, output_dir)
    plot_error_taxonomy(errors, output_dir)
    plot_annotator_calibration(annotators, output_dir)
    plot_agreement(agreement, output_dir)

    print(f"Wrote 5 figures as PNG and SVG to {output_dir}")


if __name__ == "__main__":
    main()
