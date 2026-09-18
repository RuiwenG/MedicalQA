#!/usr/bin/env python3
"""Compute and plot Fleiss' kappa for the human-evaluation ratings.

The calculation uses only Q&A pairs rated by more than one unique annotator.
Fleiss' kappa is nominal: disagreements between adjacent scale points receive
the same penalty as disagreements between the endpoints. Care safety and
standalone are binary; if every annotator selects the same category, kappa is
undefined because there is no variation to distinguish agreement from chance.

Examples
--------
python eval/plot_fleiss_agreement.py
python eval/plot_fleiss_agreement.py \
    --ratings eval/results \
    --output eval/results/analysis/plots/fleiss_agreement.png
"""

from __future__ import annotations

import argparse
import os
import tempfile
from collections import Counter
from pathlib import Path

# Keep Matplotlib's cache writable on shared/managed systems.
os.environ.setdefault(
    "MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "medicalqa-mpl")
)
os.environ.setdefault(
    "XDG_CACHE_HOME", str(Path(tempfile.gettempdir()) / "medicalqa-cache")
)

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


METRICS = {
    "Trustworthiness": (
        "qa_alignment_attribute",
        {"Poor": 1, "Fair": 2, "Good": 3, "Excellent": 4},
    ),
    "Clarity": (
        "qa_accessibility_attribute",
        {
            "Difficult": 1,
            "Somewhat difficult": 2,
            "Easy": 3,
            "Very easy to understand": 4,
        },
    ),
    "Usefulness": (
        "qa_edu_actionable_attribute",
        {
            "Not useful": 1,
            "Limited useful": 2,
            "Limited usefulness": 2,
            "Useful": 3,
            "Actionable": 3,
            "Highly useful": 4,
            "Very useful": 4,
            "Highly actionable": 4,
        },
    ),
    "Care safety": ("qa_mental_health_binary", {"Yes": 1, "No": 0}),
    "Standalone": ("qa_standalone_binary", {"No": 0, "Yes": 1}),
    "Caregiver recommendation": (
        "caregiver_recommendation",
        {
            "No": 1,
            "No, it needs major edits": 2,
            "Only after major revisions": 2,
            "Yes, but with minor edits": 3,
            "Yes, but with minor edits (meaning unchanged)": 3,
            (
                "Yes, but with minor edits (does not significantly alter the "
                "substance or meaning of the content)"
            ): 3,
            "Yes": 4,
        },
    ),
}

BINARY_METRICS = {"Care safety", "Standalone"}

# Supabase v2 uses Q&A-facing names; normalize them to the analysis names above.
V2_COLUMN_ALIASES = {
    "qna_trustworthiness_attribute": "qa_alignment_attribute",
    "qna_clarity_attribute": "qa_accessibility_attribute",
    "qna_usefulness_attribute": "qa_edu_actionable_attribute",
    "qna_care_safety_binary": "qa_mental_health_binary",
    "qna_standalone_binary": "qa_standalone_binary",
}


def find_repo_root() -> Path:
    """Locate the repository without requiring a particular working directory."""
    for candidate in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if (candidate / "eval" / "analyze_ratings.py").exists():
            return candidate
    raise SystemExit("Could not locate the MedicalQA repository root.")


def load_ratings(path: Path) -> pd.DataFrame:
    """Read one export or all exports in a directory and de-duplicate ratings."""
    if not path.exists():
        raise SystemExit(f"Ratings path not found: {path}")

    files = (
        sorted(
            file
            for file in path.iterdir()
            if file.suffix.lower() in {".csv", ".xlsx"}
            and not file.name.startswith("~$")
        )
        if path.is_dir()
        else [path]
    )
    if not files:
        raise SystemExit(f"No CSV or XLSX rating exports found in: {path}")

    frames = []
    for file in files:
        frame = (
            pd.read_csv(file)
            if file.suffix.lower() == ".csv"
            else pd.read_excel(file)
        )
        frame["source_file"] = file.name
        frames.append(frame)
    ratings = pd.concat(frames, ignore_index=True)

    for new_column, analysis_column in V2_COLUMN_ALIASES.items():
        if new_column not in ratings.columns:
            continue
        if analysis_column in ratings.columns:
            ratings[analysis_column] = ratings[analysis_column].combine_first(
                ratings[new_column]
            )
        else:
            ratings[analysis_column] = ratings[new_column]

    required = {"qa_uid", "annotator"}
    missing = required - set(ratings.columns)
    if missing:
        raise SystemExit(f"Ratings file is missing columns: {', '.join(sorted(missing))}")

    order_column = "created_at" if "created_at" in ratings.columns else "id"
    if order_column in ratings.columns:
        ratings = ratings.sort_values(order_column)
    return ratings.drop_duplicates(["qa_uid", "annotator"], keep="last")


def fleiss_kappa(units: list[list[int]]) -> tuple[float, float, float]:
    """Return Fleiss' kappa, observed agreement, and chance agreement.

    Each inner list contains the category selected by every rater for one unit.
    For unequal rater counts, the observed agreement is the mean of the
    per-unit agreeing-pair proportions. This reduces to standard Fleiss' kappa
    when every unit has the same number of raters.
    """
    if not units:
        raise ValueError("No multiply rated units were available.")
    if min(map(len, units)) < 2:
        raise ValueError("Every unit needs at least two ratings.")

    categories = sorted({value for unit in units for value in unit})
    counts = [Counter(unit) for unit in units]
    unit_agreement = [
        sum(count * (count - 1) for count in row.values())
        / (len(unit) * (len(unit) - 1))
        for row, unit in zip(counts, units)
    ]
    observed = float(np.mean(unit_agreement))
    totals = np.asarray(
        [sum(row.get(category, 0) for row in counts) for category in categories],
        dtype=float,
    )
    category_proportions = totals / totals.sum()
    expected = float(np.square(category_proportions).sum())
    kappa = (observed - expected) / (1 - expected) if expected < 1 else float("nan")
    return kappa, observed, expected


def calculate_agreement(ratings: pd.DataFrame) -> pd.DataFrame:
    """Calculate per-metric and pooled agreement on shared Q&A pairs."""
    shared_counts = ratings.groupby("qa_uid")["annotator"].nunique()
    shared_ids = shared_counts[shared_counts > 1].index
    overlap = ratings[ratings["qa_uid"].isin(shared_ids)]
    if overlap.empty:
        raise SystemExit("No Q&A pairs were rated by more than one annotator.")

    rows: list[dict[str, float | int | str]] = []
    pooled_units: list[list[int]] = []
    for metric, (column, score_map) in METRICS.items():
        if column not in overlap.columns:
            continue

        scored = overlap.assign(_score=overlap[column].map(score_map))
        unmapped = sorted(
            scored.loc[
                scored[column].notna() & scored["_score"].isna(), column
            ].unique()
        )
        if unmapped:
            raise SystemExit(f"Unmapped labels in {column}: {unmapped}")

        units = [
            group["_score"].dropna().astype(int).tolist()
            for _, group in scored.groupby("qa_uid", sort=True)
        ]
        units = [unit for unit in units if len(unit) >= 2]
        kappa, observed, expected = fleiss_kappa(units)
        if metric not in BINARY_METRICS:
            pooled_units.extend(units)
        rows.append(
            {
                "metric": metric,
                "scale_type": "binary" if metric in BINARY_METRICS else "ordinal",
                "units": len(units),
                "min_raters": min(map(len, units)),
                "max_raters": max(map(len, units)),
                "fleiss_kappa": kappa,
                "observed_agreement": observed,
                "expected_agreement": expected,
            }
        )

    kappa, observed, expected = fleiss_kappa(pooled_units)
    rows.append(
        {
            "metric": "Pooled (ordinal metrics)",
            "scale_type": "ordinal",
            "units": len(pooled_units),
            "min_raters": min(map(len, pooled_units)),
            "max_raters": max(map(len, pooled_units)),
            "fleiss_kappa": kappa,
            "observed_agreement": observed,
            "expected_agreement": expected,
        }
    )
    return pd.DataFrame(rows)


def plot_agreement(results: pd.DataFrame, output: Path) -> None:
    """Create a horizontal bar chart and save PNG and SVG copies."""
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "savefig.dpi": 240,
        }
    )
    fig, ax = plt.subplots(figsize=(10, 6.8))
    positions = np.arange(len(results))
    values = results["fleiss_kappa"].to_numpy()
    colors = [
        "#999999"
        if not np.isfinite(value)
        else "#6B5CA5"
        if metric.startswith("Pooled")
        else "#D55E00"
        if value < 0
        else "#0072B2"
        for metric, value in zip(results["metric"], values)
    ]

    ax.axvline(0, color="#666666", linewidth=1.1, zorder=1)
    bars = ax.barh(
        positions, np.nan_to_num(values, nan=0.0), color=colors, height=0.62, zorder=2
    )
    for bar, value, metric in zip(bars, values, results["metric"]):
        if not np.isfinite(value):
            ax.text(
                0.018,
                bar.get_y() + bar.get_height() / 2,
                "N/A (no variation)",
                ha="left",
                va="center",
                fontsize=10,
                color="#666666",
            )
            continue
        offset = 0.018 if value >= 0 else -0.018
        ax.text(
            value + offset,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.3f}",
            ha="left" if value >= 0 else "right",
            va="center",
            fontsize=10,
            fontweight="bold" if metric.startswith("Pooled") else "normal",
        )

    ax.set_yticks(positions, results["metric"])
    ax.invert_yaxis()
    finite_values = values[np.isfinite(values)]
    ax.set_xlim(
        min(-0.5, finite_values.min() - 0.12),
        max(0.5, finite_values.max() + 0.12),
    )
    ax.set_xlabel("Fleiss’ κ  (0 = chance-level agreement; higher is better)")
    ax.set_ylabel("")
    ax.grid(axis="x", color="#E4E4E4", linewidth=0.8, zorder=0)
    ax.grid(axis="y", visible=False)

    metric_results = results.loc[~results["metric"].str.startswith("Pooled")]
    shared_units = int(metric_results["units"].max())
    min_raters = int(metric_results["min_raters"].min())
    max_raters = int(metric_results["max_raters"].max())
    rater_text = str(min_raters) if min_raters == max_raters else f"{min_raters}–{max_raters}"
    fig.suptitle(
        "Human inter-annotator agreement",
        x=0.19,
        y=0.98,
        ha="left",
        fontsize=16,
        fontweight="bold",
    )
    fig.text(
        0.19,
        0.92,
        (
            f"Fleiss’ κ on {shared_units} shared Q&As ({rater_text} raters each); "
            "ordinal and binary outcomes"
        ),
        fontsize=10,
        color="#555555",
    )
    fig.text(
        0.19,
        0.02,
        "N/A means every rating used one category; pooled κ includes ordinal metrics only.",
        fontsize=9,
        color="#666666",
    )
    fig.subplots_adjust(left=0.30, right=0.96, top=0.84, bottom=0.15)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", facecolor="white")
    svg_output = output.with_suffix(".svg")
    fig.savefig(svg_output, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    root = find_repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ratings",
        type=Path,
        default=root / "eval" / "results",
        help="Ratings CSV/XLSX export or directory containing exports.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "eval" / "results" / "analysis" / "plots" / "fleiss_agreement.png",
        help="PNG destination; an SVG copy and a CSV summary are also written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = calculate_agreement(load_ratings(args.ratings))
    plot_agreement(results, args.output)
    table_output = args.output.with_suffix(".csv")
    results.to_csv(table_output, index=False, float_format="%.6f")

    print(results.to_string(index=False, formatters={
        "fleiss_kappa": "{:.3f}".format,
        "observed_agreement": "{:.3f}".format,
        "expected_agreement": "{:.3f}".format,
    }))
    print(f"\nWrote {args.output}")
    print(f"Wrote {args.output.with_suffix('.svg')}")
    print(f"Wrote {table_output}")


if __name__ == "__main__":
    main()
