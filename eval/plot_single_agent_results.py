#!/usr/bin/env python3
"""Plot the current single-agent human-evaluation analysis.

This plotter matches the tables produced by ``eval/analyze_ratings.py`` for
the current evaluation, which contains one generation approach.  It therefore
does not create approach-comparison or annotator-calibration figures.

Run from anywhere in the repository::

    python eval/plot_single_agent_results.py
    python eval/plot_single_agent_results.py --analysis-dir eval/results/analysis

PNG and SVG copies are written to ``<analysis-dir>/plots`` by default.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

# Keep Matplotlib/font caches in a writable location on shared systems.
os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "medicalqa-mpl")
)
os.environ.setdefault(
    "XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "medicalqa-cache")
)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


ORDINAL_METRICS = [
    ("Trustworthiness", "qa_alignment_score"),
    ("Clarity", "qa_accessibility_score"),
    ("Usefulness", "qa_edu_actionable_score"),
    ("Caregiver recommendation", "recommendation_score"),
]

PASS_METRICS = [
    ("Trustworthiness", "qa_alignment_pass"),
    ("Clarity", "qa_accessibility_pass"),
    ("Usefulness", "qa_edu_actionable_pass"),
    ("Care safety", "qa_mental_health_pass"),
    ("Standalone", "qa_standalone_pass"),
]

METRIC_LABELS = {
    "Q&A Trustworthiness": "Trustworthiness",
    "Q&A Clarity": "Clarity",
    "Q&A Usefulness": "Usefulness",
    "Q&A Care Safety": "Care safety",
    "Q&A Standalone": "Standalone",
    "Caregiver recommendation": "Caregiver recommendation",
}

TIER_COLORS = {4: "#0072B2", 3: "#56B4E9", 2: "#E69F00", 1: "#D55E00"}
METRIC_COLORS = {
    "Trustworthiness": "#0072B2",
    "Clarity": "#009E73",
    "Usefulness": "#E69F00",
    "Care safety": "#CC79A7",
    "Standalone": "#6B5CA5",
    "Caregiver recommendation": "#6B5CA5",
}
OBSOLETE_STEMS = (
    "annotator_calibration",
    "approach_top2_comparison",
    "ordinal_distributions_by_approach",
    "ordinal_distributions",
    "error_taxonomy",
    "inter_annotator_agreement",
    "single_agent_dataset_top2",
)


def find_repo_root() -> Path:
    """Find the repository without requiring a particular working directory."""
    candidates = [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "eval" / "analyze_ratings.py").exists():
            return candidate
    raise SystemExit("Could not locate the repository root.")


def parse_args() -> argparse.Namespace:
    root = find_repo_root()
    default_analysis = root / "eval" / "results" / "analysis"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=default_analysis,
        help="Directory containing analyze_ratings.py output tables.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Plot destination (default: <analysis-dir>/plots).",
    )
    return parser.parse_args()


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


def require_columns(frame: pd.DataFrame, columns: list[str], source: Path) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise SystemExit(
            f"{source} is missing required column(s): {', '.join(missing)}"
        )


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        fig.savefig(output_dir / f"{stem}.{extension}", facecolor="white")
    plt.close(fig)


def remove_obsolete_plots(output_dir: Path) -> list[Path]:
    """Remove figures that are invalid or unwanted for the one-approach analysis."""
    removed: list[Path] = []
    for stem in OBSOLETE_STEMS:
        for extension in ("png", "svg"):
            path = output_dir / f"{stem}.{extension}"
            if path.exists():
                path.unlink()
                removed.append(path)
    return removed


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Return a Wilson 95% confidence interval as percentages."""
    if total == 0:
        return float("nan"), float("nan")
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * np.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return 100 * (centre - half), 100 * (centre + half)


def analysis_label(ratings: pd.DataFrame) -> str:
    approaches = sorted(ratings["approach"].dropna().astype(str).unique())
    if not approaches:
        return "Single-agent evaluation"
    if len(approaches) != 1:
        raise SystemExit(
            "This plotter expects one generation approach; found: "
            + ", ".join(approaches)
        )
    return approaches[0]


def plot_ordinal_distributions(
    ratings: pd.DataFrame, output_dir: Path, approach: str
) -> None:
    """Plot all four levels for the scored ordinal outcomes."""
    rows = []
    for label, column in ORDINAL_METRICS:
        values = ratings[column].dropna()
        rows.append((label, values))

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    positions = np.arange(len(rows))
    left = np.zeros(len(rows))

    for score in (4, 3, 2, 1):
        counts = [int((values == score).sum()) for _, values in rows]
        percentages = [
            100 * count / len(values) if len(values) else 0
            for count, (_, values) in zip(counts, rows)
        ]
        bars = ax.barh(
            positions,
            percentages,
            left=left,
            height=0.68,
            color=TIER_COLORS[score],
            edgecolor="white",
            linewidth=0.8,
        )
        for bar, percentage, count in zip(bars, percentages, counts):
            if percentage >= 7:
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

    ax.set_yticks(positions, [label for label, _ in rows])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("Share of ratings (%)")
    ax.set_ylabel("")
    fig.suptitle(
        "Single-agent ordinal rating distributions",
        x=0.25,
        y=0.98,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.25,
        0.925,
        f"{approach}; all available ratings. Scores run from 4 (best) to 1 (worst).",
        fontsize=9.5,
        color="#555555",
    )
    ax.legend(
        handles=[
            Patch(facecolor=TIER_COLORS[score], label=f"Score {score}")
            for score in (4, 3, 2, 1)
        ],
        ncol=4,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
    )
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.25, bottom=0.23, top=0.84)
    save_figure(fig, output_dir, "single_agent_ordinal_distributions")


def plot_pass_rates(ratings: pd.DataFrame, output_dir: Path, approach: str) -> None:
    """Plot the positive result for each binary evaluation question."""
    rows = []
    for label, column in PASS_METRICS:
        values = ratings[column].dropna()
        total = len(values)
        successes = int(values.sum())
        low, high = wilson_interval(successes, total)
        rows.append(
            {
                "metric": label,
                "n": total,
                "rate": 100 * successes / total if total else np.nan,
                "low": low,
                "high": high,
            }
        )
    table = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    positions = np.arange(len(table))
    values = table["rate"].to_numpy()
    xerr = np.vstack(
        [values - table["low"].to_numpy(), table["high"].to_numpy() - values]
    )
    colors = [METRIC_COLORS[label] for label in table["metric"]]
    ax.errorbar(
        values,
        positions,
        xerr=xerr,
        fmt="none",
        ecolor="#555555",
        capsize=4,
        elinewidth=1.5,
        zorder=2,
    )
    ax.scatter(values, positions, s=80, color=colors, zorder=3)
    for value, y, total in zip(values, positions, table["n"]):
        ax.text(
            value,
            y - 0.16,
            f"{value:.1f}% (n={int(total)})",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    ax.set_yticks(positions, table["metric"])
    ax.invert_yaxis()
    ax.set_xlim(70, 110)
    ax.set_xticks(np.arange(70, 101, 5))
    ax.set_xlabel("Positive response (%) with Wilson 95% CI")
    ax.set_ylabel("")
    fig.suptitle(
        "Binary evaluation pass rates",
        x=0.25,
        y=0.98,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.25,
        0.925,
        f"{approach}; positive means Yes, except care safety where No unsafe guidance passes.",
        fontsize=9.5,
        color="#555555",
    )
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.25, right=0.88, bottom=0.16, top=0.84)
    save_figure(fig, output_dir, "single_agent_pass_rates")


def plot_error_taxonomy(errors: pd.DataFrame, output_dir: Path) -> None:
    """Plot only issue labels that appeared in the latest analysis."""
    table = errors.copy()
    table["metric_short"] = table["metric"].map(METRIC_LABELS).fillna(table["metric"])
    table = table.sort_values(["metric_short", "pct_of_ratings", "error"])
    table["axis_label"] = table["error"] + "  ·  " + table["metric_short"]

    fig_height = max(4.6, 0.58 * len(table) + 2.0)
    fig, ax = plt.subplots(figsize=(11, fig_height))
    positions = np.arange(len(table))
    colors = [METRIC_COLORS.get(metric, "#777777") for metric in table["metric_short"]]
    bars = ax.barh(positions, table["pct_of_ratings"], color=colors, alpha=0.9)
    for bar, (_, row) in zip(bars, table.iterrows()):
        ax.text(
            bar.get_width() + 0.35,
            bar.get_y() + bar.get_height() / 2,
            f"{row['pct_of_ratings']:.1f}% (n={int(row['n'])})",
            va="center",
            fontsize=9,
        )

    ax.set_yticks(positions, table["axis_label"])
    ax.set_xlim(0, max(20, float(table["pct_of_ratings"].max()) + 5))
    ax.set_xlabel("Share of all ratings carrying this issue (%)")
    ax.set_ylabel("")
    ax.set_title("Observed issue taxonomy", loc="left", pad=34)
    ax.text(
        0,
        1.035,
        "Only observed non-zero issues are shown; responses could select multiple issues.",
        transform=ax.transAxes,
        fontsize=9.5,
        color="#555555",
    )
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.39, right=0.93, bottom=0.15, top=0.82)
    save_figure(fig, output_dir, "single_agent_error_taxonomy")


def plot_agreement(agreement: pd.DataFrame, output_dir: Path) -> None:
    """Plot ordinal alpha alongside exact and within-one agreement."""
    table = agreement.copy()
    table["metric_short"] = table["metric"].map(METRIC_LABELS).fillna(table["metric"])
    order = [label for label, _ in ORDINAL_METRICS]
    table["_order"] = table["metric_short"].map({label: i for i, label in enumerate(order)})
    table = table.sort_values("_order").reset_index(drop=True)
    positions = np.arange(len(table))

    fig, (alpha_ax, percent_ax) = plt.subplots(
        1,
        2,
        figsize=(11, 5.6),
        gridspec_kw={"width_ratios": [1, 1.25]},
    )

    alpha_ax.axvspan(0.8, 1.0, color="#009E73", alpha=0.10)
    alpha_ax.axvspan(0.67, 0.8, color="#E69F00", alpha=0.12)
    alpha_ax.axvline(0, color="#999999", linewidth=1)
    alpha_ax.scatter(table["alpha_ordinal"], positions, color="#0072B2", s=55, zorder=3)
    for value, y in zip(table["alpha_ordinal"], positions):
        alpha_ax.text(value + 0.035, y, f"{value:.2f}", va="center", fontsize=8.5)
    alpha_ax.set_xlim(-0.4, 1.08)
    alpha_ax.set_yticks(positions, table["metric_short"])
    alpha_ax.invert_yaxis()
    alpha_ax.set_xlabel("Krippendorff ordinal α")
    alpha_ax.set_title("Reliability", loc="left")
    alpha_ax.grid(axis="y", visible=False)

    for y, row in table.iterrows():
        percent_ax.plot(
            [row["exact_pct"], row["within1_pct"]],
            [y, y],
            color="#B8B8B8",
            linewidth=2,
            zorder=1,
        )
    percent_ax.scatter(table["exact_pct"], positions, color="#D55E00", s=52, zorder=3)
    percent_ax.scatter(
        table["within1_pct"], positions, color="#0072B2", marker="D", s=46, zorder=3
    )
    for y, row in table.iterrows():
        percent_ax.text(
            row["exact_pct"] - 2,
            y - 0.19,
            f"{row['exact_pct']:.0f}%",
            ha="right",
            fontsize=8,
        )
        percent_ax.text(
            row["within1_pct"] + 2,
            y - 0.19,
            f"{row['within1_pct']:.0f}%",
            ha="left",
            fontsize=8,
        )
    percent_ax.set_xlim(0, 112)
    percent_ax.set_yticks(positions, [])
    percent_ax.invert_yaxis()
    percent_ax.set_xlabel("Agreement across overlapping Q&As (%)")
    percent_ax.set_title("Observed agreement", loc="left")
    percent_ax.grid(axis="y", visible=False)
    percent_ax.legend(
        handles=[
            Line2D([0], [0], marker="o", color="none", markerfacecolor="#D55E00", label="Exact"),
            Line2D(
                [0],
                [0],
                marker="D",
                color="none",
                markerfacecolor="#0072B2",
                label="Within 1 point",
            ),
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=2,
    )

    units = int(table["units"].max())
    fig.suptitle(
        "Inter-annotator agreement on ordinal ratings",
        x=0.08,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.08,
        0.92,
        f"Based on {units} overlapping Q&As; α ≥0.80 is reliable and 0.67–0.80 is tentative.",
        fontsize=9.5,
        color="#555555",
    )
    fig.subplots_adjust(left=0.24, right=0.96, bottom=0.2, top=0.82, wspace=0.18)
    save_figure(fig, output_dir, "single_agent_inter_annotator_agreement")


def dataset_top2_table(ratings: pd.DataFrame) -> pd.DataFrame:
    """Return every ordinal score's count and share by metric and dataset."""
    rows = []
    for metric, column in ORDINAL_METRICS:
        for dataset, group in ratings.groupby("dataset", sort=True):
            values = group[column].dropna()
            total = len(values)
            for score in (4, 3, 2, 1):
                count = int((values == score).sum())
                rows.append(
                    {
                        "metric": metric,
                        "dataset": str(dataset),
                        "score": score,
                        "count": count,
                        "n": total,
                        "pct": 100 * count / total if total else np.nan,
                    }
                )
    return pd.DataFrame(rows)


def plot_dataset_comparison(ratings: pd.DataFrame, output_dir: Path) -> bool:
    """Plot complete ordinal distributions by dataset."""
    if "dataset" not in ratings or ratings["dataset"].nunique(dropna=True) < 2:
        return False

    table = dataset_top2_table(ratings)
    datasets = sorted(table["dataset"].unique())
    offsets = np.linspace(-0.19, 0.19, len(datasets))
    positions = np.arange(len(ORDINAL_METRICS))

    fig, ax = plt.subplots(figsize=(11, 7.0))
    for dataset, offset in zip(datasets, offsets):
        y = positions + offset
        left = np.zeros(len(ORDINAL_METRICS))
        for score in (4, 3, 2, 1):
            subset = (
                table[(table["dataset"] == dataset) & (table["score"] == score)]
                .set_index("metric")
                .loc[[label for label, _ in ORDINAL_METRICS]]
            )
            percentages = subset["pct"].to_numpy()
            counts = subset["count"].to_numpy()
            bars = ax.barh(
                y,
                percentages,
                left=left,
                height=0.34,
                color=TIER_COLORS[score],
                edgecolor="white",
                linewidth=0.8,
            )
            for bar, percentage, count in zip(bars, percentages, counts):
                if percentage >= 9:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_y() + bar.get_height() / 2,
                        f"{percentage:.0f}%\n(n={int(count)})",
                        ha="center",
                        va="center",
                        fontsize=7.5,
                        color="white" if score in {4, 1} else "#1A1A1A",
                    )
            left += percentages

        for yy, (_, column) in zip(y, ORDINAL_METRICS):
            total = int(ratings.loc[ratings["dataset"] == dataset, column].notna().sum())
            ax.text(
                101.4,
                yy,
                f"{dataset} (n={total})",
                va="center",
                fontsize=8.5,
                color="#444444",
            )

    for boundary in positions[:-1] + 0.5:
        ax.axhline(boundary, color="#E3E3E3", linewidth=0.8, zorder=0)

    ax.set_yticks(positions, [label for label, _ in ORDINAL_METRICS])
    ax.invert_yaxis()
    ax.set_ylim(len(ORDINAL_METRICS) - 0.5, -0.5)
    ax.set_xlim(0, 100)
    ax.set_xticks(np.arange(0, 101, 20))
    ax.set_xlabel("Share of ratings within each dataset (%)")
    ax.set_ylabel("")
    fig.suptitle(
        "Single-agent ordinal distributions by source dataset",
        x=0.25,
        y=0.98,
        ha="left",
        fontweight="bold",
    )
    fig.text(
        0.25,
        0.925,
        "Every rating is shown; scores run from 4 (best) to 1 (worst).",
        fontsize=9.5,
        color="#555555",
    )
    ax.legend(
        handles=[
            Patch(facecolor=TIER_COLORS[score], label=f"Score {score}")
            for score in (4, 3, 2, 1)
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=4,
    )
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#E3E3E3", linewidth=0.8)
    fig.subplots_adjust(left=0.25, right=0.83, bottom=0.18, top=0.86)
    save_figure(fig, output_dir, "single_agent_dataset_distributions")
    return True


def main() -> None:
    args = parse_args()
    analysis_dir = args.analysis_dir.resolve()
    output_dir = (args.output_dir or analysis_dir / "plots").resolve()
    paths = {
        "ratings": analysis_dir / "ratings_scored.csv",
        "errors": analysis_dir / "error_counts.csv",
        "agreement": analysis_dir / "agreement.csv",
    }
    missing = [path for path in paths.values() if not path.exists()]
    if missing:
        joined = "\n  ".join(str(path) for path in missing)
        raise SystemExit(f"Missing required analysis table(s):\n  {joined}")

    ratings = pd.read_csv(paths["ratings"])
    errors = pd.read_csv(paths["errors"])
    agreement = pd.read_csv(paths["agreement"])
    require_columns(
        ratings,
        [
            "approach",
            *[column for _, column in ORDINAL_METRICS],
            *[column for _, column in PASS_METRICS],
        ],
        paths["ratings"],
    )
    require_columns(errors, ["metric", "error", "n", "pct_of_ratings"], paths["errors"])
    require_columns(
        agreement,
        ["metric", "units", "alpha_ordinal", "exact_pct", "within1_pct"],
        paths["agreement"],
    )
    if ratings.empty:
        raise SystemExit(f"No ratings in {paths['ratings']}")
    if errors.empty:
        raise SystemExit(f"No observed errors in {paths['errors']}")
    if agreement.empty:
        raise SystemExit(f"No agreement results in {paths['agreement']}")

    approach = analysis_label(ratings)
    setup_style()
    output_dir.mkdir(parents=True, exist_ok=True)
    removed = remove_obsolete_plots(output_dir)

    plot_ordinal_distributions(ratings, output_dir, approach)
    plot_pass_rates(ratings, output_dir, approach)
    plot_error_taxonomy(errors, output_dir)
    plot_agreement(agreement, output_dir)
    has_dataset_plot = plot_dataset_comparison(ratings, output_dir)

    count = 5 if has_dataset_plot else 4
    print(f"Wrote {count} figures as PNG and SVG to {output_dir}")
    if removed:
        print(f"Removed {len(removed)} obsolete plot files")


if __name__ == "__main__":
    main()
