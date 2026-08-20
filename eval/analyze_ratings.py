#!/usr/bin/env python3
"""Analyse the human evaluation ratings collected by the web eval UI.

Reads every ratings export in ``eval/results/`` (CSV or XLSX — both use the
column names written by ``eval/web/index.html`` and ``supabase_schema.sql``),
maps the annotators' text labels onto ordinal scores, and reports the
distributions.

The four QA metrics are 4-point *ordinal* scales, best label first:

    Q&A Trustworthiness            Excellent 4 .. Poor 1
    Q&A Clarity                    Very easy to understand 4 .. Difficult 1
    Q&A Usefulness                 Highly useful 4 .. Not useful 1
    Q&A Care Safety                binary safety screen: No safe / Yes unsafe

**No means are reported anywhere.** The distance between "Good" and "Fair" is
not the distance between "Fair" and "Poor", so an average of these codes is not
a meaningful quantity. Everything here is rank- or count-based instead:

    * the full label distribution (n and %) per metric;
    * median, mode and quartiles;
    * % Top-2 ("acceptable", score >= 3) with a Wilson confidence interval;
    * Yes/No pass rates for the per-metric binary questions;
    * Mann-Whitney U + Cliff's delta when comparing approaches (rank-based);
    * Fisher's exact test for the binary/Top-2 proportions;
    * error-taxonomy counts (multi-label, stored joined with "; ");
    * per-annotator distributions, to expose leniency differences;
    * inter-annotator agreement on the pairs more than one person rated
      (Krippendorff's ordinal alpha, plus exact and within-1 agreement).

Empty cells are ignored per metric, so a metric an annotator deselected — and
the unused legacy columns from the pre-pilot schema — never enter a count.

Run from anywhere inside the repo:

    python eval/analyze_ratings.py                  # report to stdout
    python eval/analyze_ratings.py --out eval/results/analysis
    python eval/analyze_ratings.py --by dataset     # extra breakdown column
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Label -> score maps. Order matches the METRICS table in eval/web/index.html;
# the best label always scores highest so "higher is better" holds everywhere.
# ---------------------------------------------------------------------------
METRICS = {
    "qa_alignment": {
        "label": "Q&A Trustworthiness",
        "scale": {"Excellent": 4, "Good": 3, "Fair": 2, "Poor": 1},
        "binary_question": "Does the Q&A align with the video?",
        "errors": ["Source Misinterpretation", "Hallucinating", "Contradiction", "Missing Key Information"],
    },
    "qa_accessibility": {
        "label": "Q&A Clarity",
        "scale": {"Very easy to understand": 4, "Easy": 3, "Somewhat difficult": 2, "Difficult": 1},
        "binary_question": "Is the Q&A easy for a caregiver to understand?",
        "errors": ["Difficult vocabulary", "Too long", "Ambiguous", "Poor organization"],
    },
    "qa_edu_actionable": {
        "label": "Q&A Usefulness",
        "scale": {"Highly useful": 4, "Useful": 3, "Limited useful": 2, "Not useful": 1},
        "aliases": {
            "Highly actionable": "Highly useful",
            "Very useful": "Highly useful",
            "Actionable": "Useful",
            "Limited usefulness": "Limited useful",
        },
        "binary_question": "Does the Q&A provide useful or actionable guidance for caregivers?",
        "errors": ["Not actionable", "Missing Explanation", "Generic Advice", "Low Relevance to Caregiver Needs"],
    },
    "qa_mental_health": {
        "label": "Q&A Care Safety",
        "scale": {},
        "aliases": {
            "Highly supportive": "Very safe and respectful",
            "Very supportive": "Very safe and respectful",
            "Supportive": "Safe and respectful",
            "Limited support": "Some concerns",
            "Unsupportive / potentially harmful": "Unsafe or inappropriate",
            "Not supportive or could be harmful": "Unsafe or inappropriate",
        },
        "binary_question": "Does the Q&A contain guidance that could lead to unsafe or inappropriate care?",
        "binary_map": {"No": 1, "Yes": 0},
        "positive_label": "No",
        "errors": [
            "Unsafe medical or health advice",
            "Physical safety risk",
            "Blaming or judgmental language",
            "Discourages professional care",
        ],
        "error_aliases": {
            "Unsafe Medical or Health Advice": "Unsafe medical or health advice",
            "Physical Safety Risk": "Physical safety risk",
            "Dismissive or Harmful Communication": "Blaming or judgmental language",
            "Discourages Professional Care": "Discourages professional care",
        },
    },
}

# Overall verdict, same 4-point treatment.
RECOMMENDATION = {
    "Yes": 4,
    "Yes, but with minor edits (meaning unchanged)": 3,
    "No, it needs major edits": 2,
    "No": 1,
}
RECOMMENDATION_ALIASES = {
    "Yes, but with minor edits": "Yes, but with minor edits (meaning unchanged)",
    "Yes, but with minor edits (does not significantly alter the substance or meaning of the content)": "Yes, but with minor edits (meaning unchanged)",
    "Only after major revisions": "No, it needs major edits",
}

BINARY = {"Yes": 1, "No": 0}

NO_ISSUE = "No issue"
TOP2_MIN = 3          # score >= 3 counts as "acceptable"
ERROR_SEP = "; "      # index.html joins multi-select problems with this

# Columns from the pre-pilot schema. Kept in the export, never populated by the
# pilot form; dropped so they cannot be mistaken for missing data.
LEGACY_COLUMNS = [
    "q_fluency", "a_fluency", "q_clarity", "a_clarity",
    "qa_alignment", "q_edu_value", "a_edu_value", "standalone",
]

V2_COLUMN_ALIASES = {
    "qna_trustworthiness_attribute": "qa_alignment_attribute",
    "qna_trustworthiness_issue": "qa_alignment_error",
    "qna_trustworthiness_binary": "qa_alignment_binary",
    "qna_clarity_attribute": "qa_accessibility_attribute",
    "qna_clarity_issue": "qa_accessibility_error",
    "qna_clarity_binary": "qa_accessibility_binary",
    "qna_usefulness_attribute": "qa_edu_actionable_attribute",
    "qna_usefulness_issue": "qa_edu_actionable_error",
    "qna_usefulness_binary": "qa_edu_actionable_binary",
    "qna_care_safety_issue": "qa_mental_health_error",
    "qna_care_safety_binary": "qa_mental_health_binary",
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def find_repo_root() -> Path:
    for cand in [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents]:
        if (cand / "eval" / "web").exists():
            return cand
    raise SystemExit("Could not locate the repo root (no eval/web/ found).")


def load_ratings(results_dir: Path) -> pd.DataFrame:
    """Read every export in ``results_dir`` and return one de-duplicated frame."""
    files = sorted(
        p for p in results_dir.iterdir()
        if p.suffix.lower() in {".csv", ".xlsx"} and not p.name.startswith("~$")
    )
    if not files:
        raise SystemExit(f"No .csv/.xlsx ratings files in {results_dir}")

    frames = []
    for path in files:
        df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
        df["source_file"] = path.name
        frames.append(df)
        print(f"  loaded {path.name}: {len(df)} rows", file=sys.stderr)

    df = pd.concat(frames, ignore_index=True)

    # Blank-ish strings are missing data, not labels.
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].replace(r"^\s*$", np.nan, regex=True)

    # Supabase v2 exports use current Q&A-facing column names. Internally, keep
    # the older analysis keys so historical CSV exports and v2 DB exports can be
    # analyzed together.
    for new_col, analysis_col in V2_COLUMN_ALIASES.items():
        if new_col in df.columns and analysis_col not in df.columns:
            df[analysis_col] = df[new_col]

    # Rating tables are append-only: the Previous button re-submits a Q&A. The
    # *_final views already collapse this, but exports get combined and
    # re-exported, so collapse defensively on the same key the views use.
    if {"session_id", "qa_uid"}.issubset(df.columns):
        order = "created_at" if "created_at" in df.columns else "id"
        before = len(df)
        df = (
            df.sort_values(order)
              .drop_duplicates(subset=["session_id", "qa_uid"], keep="last")
              .reset_index(drop=True)
        )
        if before != len(df):
            print(f"  collapsed {before - len(df)} re-rated duplicates", file=sys.stderr)

    dropped = [c for c in LEGACY_COLUMNS if c in df.columns and df[c].notna().sum() == 0]
    df = df.drop(columns=dropped)
    if dropped:
        print(f"  dropped unused legacy columns: {', '.join(dropped)}", file=sys.stderr)

    return df


def score_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``<metric>_score`` / ``<metric>_pass`` / ``recommendation_score`` columns.

    Unrecognised labels become NaN and are reported loudly — a silent typo
    would quietly shrink every denominator.
    """
    unmapped: Counter = Counter()

    def apply_map(col: str, mapping: dict, new_col: str) -> None:
        if col not in df.columns:
            return
        vals = df[col]
        df[new_col] = vals.map(mapping)
        bad = vals.notna() & df[new_col].isna()
        for v in vals[bad]:
            unmapped[f"{col}: {v!r}"] += 1

    def normalize_error_cell(value: object, aliases: dict[str, str]) -> object:
        if pd.isna(value):
            return value
        labels = [label.strip() for label in str(value).split(ERROR_SEP)]
        return ERROR_SEP.join(aliases.get(label, label) for label in labels)

    for key, spec in METRICS.items():
        attr_col = f"{key}_attribute"
        if attr_col in df.columns and spec.get("aliases"):
            df[attr_col] = df[attr_col].replace(spec["aliases"])
        error_col = f"{key}_error"
        if error_col in df.columns and spec.get("error_aliases"):
            df[error_col] = df[error_col].map(lambda value: normalize_error_cell(value, spec["error_aliases"]))
        if spec["scale"]:
            apply_map(f"{key}_attribute", spec["scale"], f"{key}_score")
        apply_map(f"{key}_binary", spec.get("binary_map", BINARY), f"{key}_pass")

    if "caregiver_recommendation" in df.columns:
        df["caregiver_recommendation"] = df["caregiver_recommendation"].replace(RECOMMENDATION_ALIASES)
    apply_map("caregiver_recommendation", RECOMMENDATION, "recommendation_score")

    if unmapped:
        print("\n!! Unmapped labels (excluded from all counts):", file=sys.stderr)
        for k, n in unmapped.most_common():
            print(f"   {n:>4}  {k}", file=sys.stderr)

    return df


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves at the 0% / 100% ends, unlike normal approx."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> tuple[float, str]:
    """Rank-based effect size: P(a>b) - P(a<b). Magnitude thresholds are Romano's."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) == 0 or len(b) == 0:
        return float("nan"), "n/a"
    diff = np.sign(a[:, None] - b[None, :])
    d = float(diff.sum() / (len(a) * len(b)))
    m = abs(d)
    size = "negligible" if m < 0.147 else "small" if m < 0.33 else "medium" if m < 0.474 else "large"
    return d, size


def krippendorff_alpha_ordinal(units: list[list[float]]) -> float:
    """Krippendorff's alpha with the ordinal difference function.

    ``units`` is one list of scores per unit (Q&A); units with fewer than
    two ratings contribute nothing, exactly as the coefficient specifies.
    """
    units = [u for u in units if len(u) >= 2]
    if not units:
        return float("nan")

    values = sorted({v for u in units for v in u})
    idx = {v: i for i, v in enumerate(values)}
    k = len(values)
    if k < 2:
        return 1.0  # everyone used one value: no observable disagreement

    # Coincidence matrix: each unit contributes pairs weighted by 1/(m_u - 1).
    coincidence = np.zeros((k, k))
    for u in units:
        m = len(u)
        for i, vi in enumerate(u):
            for j, vj in enumerate(u):
                if i != j:
                    coincidence[idx[vi], idx[vj]] += 1.0 / (m - 1)

    n_c = coincidence.sum(axis=1)
    n_total = coincidence.sum()

    # Ordinal metric: distance depends on the mass of the categories in between.
    delta = np.zeros((k, k))
    for c in range(k):
        for d_ in range(k):
            lo, hi = min(c, d_), max(c, d_)
            g = n_c[lo:hi + 1].sum() - (n_c[lo] + n_c[hi]) / 2.0
            delta[c, d_] = g * g

    d_obs = (coincidence * delta).sum()
    d_exp = sum(
        n_c[c] * n_c[d_] * delta[c, d_]
        for c in range(k) for d_ in range(k) if c != d_
    ) / (n_total - 1)

    if d_exp == 0:
        return 1.0
    return 1.0 - d_obs / d_exp


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def fmt_pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "0/0 (—)"


def rule(title: str, char: str = "=") -> None:
    print(f"\n{char * 78}\n{title}\n{char * 78}")


def describe_ordinal(scores: pd.Series, scale: dict) -> dict:
    """Median / mode / IQR / Top-2 for one ordinal column. Never a mean."""
    s = scores.dropna()
    n = len(s)
    if n == 0:
        return {"n": 0}
    inv = {v: k for k, v in scale.items()}
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    mode_val = s.mode().iloc[0]
    top2 = int((s >= TOP2_MIN).sum())
    lo, hi = wilson_ci(top2, n)
    return {
        "n": n,
        "median": s.median(),
        "median_label": inv.get(s.median(), f"{s.median():.1f} (between labels)"),
        "mode": mode_val,
        "mode_label": inv.get(mode_val, str(mode_val)),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "top2_n": top2,
        "top2_pct": 100 * top2 / n,
        "top2_ci": (100 * lo, 100 * hi),
        "bottom_n": int((s <= 1).sum()),
    }


def distribution_table(df: pd.DataFrame, col: str, scale: dict, group: str | None) -> pd.DataFrame:
    """Label counts (and % within group) ordered best label first."""
    order = [k for k, _ in sorted(scale.items(), key=lambda kv: -kv[1])]
    sub = df[df[col].notna()]
    if group:
        tab = pd.crosstab(sub[group], sub[col])
        tab = tab.reindex(columns=order, fill_value=0)
        pct = tab.div(tab.sum(axis=1), axis=0) * 100
        out = tab.astype(int).astype(str) + " (" + pct.round(0).astype(int).astype(str) + "%)"
        out["n"] = tab.sum(axis=1)
        return out
    counts = sub[col].value_counts().reindex(order, fill_value=0)
    return pd.DataFrame({
        "n": counts,
        "%": (100 * counts / counts.sum()).round(1),
    })


def report_overall(df: pd.DataFrame) -> pd.DataFrame:
    rule("1. ORDINAL DISTRIBUTIONS (all annotators pooled)")
    rows = []
    for key, spec in METRICS.items():
        col, score_col = f"{key}_attribute", f"{key}_score"
        pass_col = f"{key}_pass"
        binary = ""
        if not spec["scale"]:
            if pass_col not in df.columns:
                continue
            b = df[pass_col].dropna()
            if not len(b):
                continue
            k = int(b.sum())
            lo, hi = wilson_ci(k, len(b))
            binary = f"{100 * k / len(b):.0f}%"
            positive_label = spec.get("positive_label", "Yes")
            print(f'\n{spec["label"]}  (n={len(b)})')
            print(f'  "{spec["binary_question"]}"  {positive_label}: {fmt_pct(k, len(b))}'
                  f"  [95% CI {100 * lo:.0f}-{100 * hi:.0f}%]")
            rows.append({
                "metric": spec["label"], "n": len(b), "median": "",
                "mode": "", "IQR": "", "top2_pct": "", "binary_yes_pct": binary,
            })
            continue
        if score_col not in df.columns:
            continue
        d = describe_ordinal(df[score_col], spec["scale"])
        if not d["n"]:
            continue
        print(f"\n{spec['label']}  (n={d['n']})")
        print(distribution_table(df, col, spec["scale"], None).to_string())
        print(f"  median  {d['median']:.1f}  ({d['median_label']})"
              f"   mode {d['mode']:.0f} ({d['mode_label']})"
              f"   IQR {d['q1']:.1f}-{d['q3']:.1f}")
        print(f"  Top-2 (>= {TOP2_MIN}): {d['top2_pct']:.0f}%"
              f"  [95% CI {d['top2_ci'][0]:.0f}-{d['top2_ci'][1]:.0f}%]"
              f"   worst-label share: {fmt_pct(d['bottom_n'], d['n'])}")

        if pass_col in df.columns:
            b = df[pass_col].dropna()
            if len(b):
                k = int(b.sum())
                lo, hi = wilson_ci(k, len(b))
                binary = f"{100 * k / len(b):.0f}%"
                positive_label = spec.get("positive_label", "Yes")
                print(f'  "{spec["binary_question"]}"  {positive_label}: {fmt_pct(k, len(b))}'
                      f"  [95% CI {100 * lo:.0f}-{100 * hi:.0f}%]")

        rows.append({
            "metric": spec["label"], "n": d["n"], "median": d["median"],
            "mode": d["mode_label"], "IQR": f"{d['q1']:.1f}-{d['q3']:.1f}",
            "top2_pct": round(d["top2_pct"], 1), "binary_yes_pct": binary,
        })

    if "recommendation_score" in df.columns:
        d = describe_ordinal(df["recommendation_score"], RECOMMENDATION)
        if d["n"]:
            rule("Caregiver recommendation — would you give this to a caregiver?", "-")
            print(distribution_table(df, "caregiver_recommendation", RECOMMENDATION, None).to_string())
            print(f"  median {d['median']:.1f} ({d['median_label']})"
                  f"   usable as-is or with minor edits (>= {TOP2_MIN}): {d['top2_pct']:.0f}%"
                  f"  [95% CI {d['top2_ci'][0]:.0f}-{d['top2_ci'][1]:.0f}%]")
            rows.append({
                "metric": "Caregiver recommendation", "n": d["n"], "median": d["median"],
                "mode": d["mode_label"], "IQR": f"{d['q1']:.1f}-{d['q3']:.1f}",
                "top2_pct": round(d["top2_pct"], 1), "binary_yes_pct": "",
            })

    return pd.DataFrame(rows)


def report_by_group(df: pd.DataFrame, group: str) -> pd.DataFrame:
    """Distributions split by ``group``, plus a rank-based comparison if it has 2 levels."""
    if group not in df.columns:
        return pd.DataFrame()
    levels = sorted(df[group].dropna().unique())
    if len(levels) < 2:
        return pd.DataFrame()

    rule(f"2. BY {group.upper()}  ({', '.join(map(str, levels))})")
    rows = []
    scored = list(METRICS.items()) + [("recommendation", {"label": "Caregiver recommendation",
                                                          "scale": RECOMMENDATION})]
    for key, spec in scored:
        score_col = f"{key}_score"
        attr_col = "caregiver_recommendation" if key == "recommendation" else f"{key}_attribute"
        if score_col not in df.columns or df[score_col].notna().sum() == 0:
            continue

        print(f"\n{spec['label']}")
        print(distribution_table(df, attr_col, spec["scale"], group).to_string())

        line = []
        for lv in levels:
            s = df.loc[df[group] == lv, score_col].dropna()
            if len(s) == 0:
                continue
            top2 = int((s >= TOP2_MIN).sum())
            line.append(f"{lv}: median {s.median():.1f}, Top-2 {fmt_pct(top2, len(s))}")
            rows.append({
                "metric": spec["label"], group: lv, "n": len(s),
                "median": s.median(), "top2_pct": round(100 * top2 / len(s), 1),
            })
        print("  " + " | ".join(line))

        if len(levels) == 2:
            a = df.loc[df[group] == levels[0], score_col].dropna().to_numpy()
            b = df.loc[df[group] == levels[1], score_col].dropna().to_numpy()
            if len(a) and len(b):
                u = stats.mannwhitneyu(a, b, alternative="two-sided")
                d, size = cliffs_delta(a, b)
                print(f"  Mann-Whitney U={u.statistic:.0f}, p={u.pvalue:.3f}"
                      f"   Cliff's delta={d:+.2f} ({size}, + favours {levels[0]})")

                # Same comparison on the Top-2 proportion, which is what a
                # "how many are good enough" claim actually rests on.
                table = [[int((a >= TOP2_MIN).sum()), int((a < TOP2_MIN).sum())],
                         [int((b >= TOP2_MIN).sum()), int((b < TOP2_MIN).sum())]]
                _, p_fisher = stats.fisher_exact(table)
                print(f"  Top-2 proportions: Fisher exact p={p_fisher:.3f}")

    # Binary questions side by side.
    if len(levels) == 2:
        rule(f"Binary pass rates by {group}", "-")
        for key, spec in METRICS.items():
            pass_col = f"{key}_pass"
            if pass_col not in df.columns:
                continue
            cells, table = [], []
            for lv in levels:
                b = df.loc[df[group] == lv, pass_col].dropna()
                k, n = int(b.sum()), len(b)
                cells.append(f"{lv}: {fmt_pct(k, n)}")
                table.append([k, n - k])
            if all(sum(r) for r in table):
                _, p = stats.fisher_exact(table)
                print(f"{spec['label']:<34} " + " | ".join(cells) + f"   Fisher p={p:.3f}")

    return pd.DataFrame(rows)


def report_errors(df: pd.DataFrame, group: str | None) -> pd.DataFrame:
    """Error taxonomy counts. Multi-select, so one rating can raise several flags."""
    rule("3. ERROR TAXONOMY (multi-label; a rating can raise more than one)")
    rows = []
    for key, spec in METRICS.items():
        col = f"{key}_error"
        if col not in df.columns:
            continue
        sub = df[df[col].notna()]
        if sub.empty:
            continue

        exploded = (
            sub.assign(_err=sub[col].str.split(ERROR_SEP))
               .explode("_err")
               .assign(_err=lambda d: d["_err"].str.strip())
        )
        clean = exploded[exploded["_err"] != NO_ISSUE]
        n_ratings = len(sub)
        n_flagged = sub[col].ne(NO_ISSUE).sum()

        print(f"\n{spec['label']}  — {fmt_pct(int(n_flagged), n_ratings)} of ratings flagged a problem")
        counts = clean["_err"].value_counts().reindex(spec["errors"]).dropna().astype(int)
        for err, n in counts.items():
            share = f"{100 * n / n_ratings:.0f}% of ratings"
            if group and group in clean.columns:
                per = clean[clean["_err"] == err][group].value_counts().to_dict()
                share += "  [" + ", ".join(f"{k}: {v}" for k, v in sorted(per.items())) + "]"
            print(f"    {err:<28} {n:>3}   {share}")
            rows.append({"metric": spec["label"], "error": err, "n": int(n),
                         "pct_of_ratings": round(100 * n / n_ratings, 1)})
        unexpected = set(clean["_err"]) - set(spec["errors"])
        if unexpected:
            print(f"    (unrecognised labels: {sorted(unexpected)})")
    return pd.DataFrame(rows)


def report_annotators(df: pd.DataFrame) -> pd.DataFrame:
    """Leniency check — are people using the scale the same way?"""
    if "annotator" not in df.columns:
        return pd.DataFrame()
    rule("4. PER-ANNOTATOR CALIBRATION (leniency check)")
    rows = []
    for name, g in df.groupby("annotator"):
        parts = []
        for key, spec in METRICS.items():
            s = g.get(f"{key}_score", pd.Series(dtype=float)).dropna()
            if len(s) == 0:
                continue
            parts.append(f"{spec['label'].replace('QA ', '')[:14]:<14} "
                         f"med {s.median():.1f} top2 {100 * (s >= TOP2_MIN).mean():>3.0f}%")
            rows.append({"annotator": name, "metric": spec["label"], "n": len(s),
                         "median": s.median(), "top2_pct": round(100 * (s >= TOP2_MIN).mean(), 1)})
        secs = g["seconds_spent"].dropna() if "seconds_spent" in g else pd.Series(dtype=float)
        timing = f"   median {secs.median():.0f}s/pair" if len(secs) else ""
        print(f"\n{name}  ({len(g)} pairs{timing})")
        for p in parts:
            print(f"    {p}")

    if "seconds_spent" in df.columns:
        fast = df[df["seconds_spent"].notna() & (df["seconds_spent"] < 15)]
        if len(fast):
            print(f"\n  !! {len(fast)} rating(s) submitted in under 15s — check for click-through:")
            for _, r in fast.iterrows():
                print(f"     {r.get('annotator', '?')}  {r.get('qa_uid', '?')}  {r['seconds_spent']:.0f}s")
    return pd.DataFrame(rows)


def report_agreement(df: pd.DataFrame) -> pd.DataFrame:
    """Agreement on the anchor pairs — the ones rated by more than one person."""
    if not {"qa_uid", "annotator"}.issubset(df.columns):
        return pd.DataFrame()

    counts = df.groupby("qa_uid")["annotator"].nunique()
    shared = counts[counts > 1].index
    rule(f"5. INTER-ANNOTATOR AGREEMENT  ({len(shared)} pairs rated by >1 annotator)")
    if len(shared) == 0:
        print("  No overlapping pairs yet — anchors are needed for agreement.")
        return pd.DataFrame()

    overlap = df[df["qa_uid"].isin(shared)]
    rows = []
    for key, spec in METRICS.items():
        score_col = f"{key}_score"
        if score_col not in overlap.columns:
            continue
        units = [
            g[score_col].dropna().tolist()
            for _, g in overlap.groupby("qa_uid")
        ]
        units = [u for u in units if len(u) >= 2]
        if not units:
            continue
        alpha = krippendorff_alpha_ordinal(units)
        exact = np.mean([len(set(u)) == 1 for u in units])
        within1 = np.mean([max(u) - min(u) <= 1 for u in units])
        rows.append({"metric": spec["label"], "units": len(units), "alpha_ordinal": round(alpha, 3),
                     "exact_pct": round(100 * exact, 1), "within1_pct": round(100 * within1, 1)})
        print(f"{spec['label']:<34} alpha={alpha:+.2f}   exact {100 * exact:>3.0f}%"
              f"   within-1 {100 * within1:>3.0f}%   ({len(units)} pairs)")

    if "recommendation_score" in overlap.columns:
        units = [g["recommendation_score"].dropna().tolist() for _, g in overlap.groupby("qa_uid")]
        units = [u for u in units if len(u) >= 2]
        if units:
            alpha = krippendorff_alpha_ordinal(units)
            exact = np.mean([len(set(u)) == 1 for u in units])
            within1 = np.mean([max(u) - min(u) <= 1 for u in units])
            print(f"{'Caregiver recommendation':<34} alpha={alpha:+.2f}   exact {100 * exact:>3.0f}%"
                  f"   within-1 {100 * within1:>3.0f}%   ({len(units)} pairs)")
            rows.append({"metric": "Caregiver recommendation", "units": len(units),
                         "alpha_ordinal": round(alpha, 3), "exact_pct": round(100 * exact, 1),
                         "within1_pct": round(100 * within1, 1)})

    print("\n  alpha >= 0.80 reliable, 0.67-0.80 tentative, below that unreliable.")
    if len(shared) < 20:
        print(f"  With only {len(shared)} overlapping pairs these alphas are very unstable —")
        print("  read them as a smoke test, not a reliability claim.")
    return pd.DataFrame(rows)


def report_coverage(df: pd.DataFrame) -> None:
    rule("0. COVERAGE")
    print(f"  ratings: {len(df)}   unique Q&As: {df['qa_uid'].nunique()}"
          f"   annotators: {df['annotator'].nunique()}   sessions: {df['session_id'].nunique()}")
    for col in ("approach", "dataset", "batch"):
        if col in df.columns:
            counts = df[col].value_counts()
            print(f"  {col:<9} " + ", ".join(f"{k}: {v}" for k, v in counts.items()))
    if "annotator" in df.columns and "approach" in df.columns:
        print("\n" + pd.crosstab(df["annotator"], df["approach"]).to_string())
    if len(df) < 100:
        print(f"\n  !! n={len(df)}. Everything below is a pilot-sized signal: report proportions"
              "\n     with their intervals and treat non-significant comparisons as inconclusive,"
              "\n     not as evidence the approaches are equivalent.")


# ---------------------------------------------------------------------------
def main() -> None:
    root = find_repo_root()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=root / "eval" / "results",
                    help="directory of ratings exports (default: eval/results)")
    ap.add_argument("--by", default="approach",
                    help="grouping column for the comparison section (default: approach)")
    ap.add_argument("--out", type=Path, default=None,
                    help="directory to write the tidy scored data + summary tables")
    args = ap.parse_args()

    print(f"Reading {args.results}", file=sys.stderr)
    df = score_ratings(load_ratings(args.results))

    report_coverage(df)
    summary = report_overall(df)
    by_group = report_by_group(df, args.by)
    errors = report_errors(df, args.by)
    annotators = report_annotators(df)
    agreement = report_agreement(df)

    # Secondary breakdown: the two corpora are different source material.
    if args.by != "dataset" and "dataset" in df.columns and df["dataset"].nunique() > 1:
        report_by_group(df, "dataset")

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out / "ratings_scored.csv", index=False)
        for name, table in [("summary_overall", summary), (f"summary_by_{args.by}", by_group),
                            ("error_counts", errors), ("per_annotator", annotators),
                            ("agreement", agreement)]:
            if not table.empty:
                table.to_csv(args.out / f"{name}.csv", index=False)
        print(f"\nWrote scored data + summary tables to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
