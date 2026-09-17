#!/usr/bin/env python3
"""Build an offline HTML/Markdown evaluation report from Claude judge CSVs.

The report intentionally resembles the structure of the earlier DeepSeek
ablation artifact while remaining deterministic and reproducible. It supports:

* a v3-only Claude report;
* a Claude v1/v2/v3 prompt comparison;
* optional Claude-vs-DeepSeek agreement on the exact same Q&A pairs; and
* optional Claude-vs-human agreement from either a website CSV export or a
  Supabase ``ratings_v3_final`` CSV export.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


VERSION_BY_APPROACH = {
    "SingleAgent-v1": "v1",
    "SingleAgent-v2": "v2",
    "SingleAgent-v3": "v3",
}
VERSION_ORDER = ["v1", "v2", "v3"]

METRICS = [
    {
        "name": "Trustworthiness",
        "binary": "Q&A Trustworthiness Yes/No",
        "attribute": "Q&A Trustworthiness Attribute",
        "issue": "Q&A Trustworthiness Error Type",
        "good": "Yes",
        "scores": {"Excellent": 4, "Good": 3, "Fair": 2, "Poor": 1},
    },
    {
        "name": "Clarity",
        "binary": "Q&A Clarity Yes/No",
        "attribute": "Q&A Clarity Attribute",
        "issue": "Q&A Clarity Error Type",
        "good": "Yes",
        "scores": {
            "Very easy to understand": 4,
            "Easy": 3,
            "Somewhat difficult": 2,
            "Difficult": 1,
        },
    },
    {
        "name": "Usefulness",
        "binary": "Q&A Usefulness Yes/No",
        "attribute": "Q&A Usefulness Attribute",
        "issue": "Q&A Usefulness Error Type",
        "good": "Yes",
        "scores": {"Highly useful": 4, "Useful": 3, "Limited useful": 2, "Not useful": 1},
    },
    {
        "name": "Care Safety — no concern",
        "binary": "Q&A Care Safety Yes/No",
        "attribute": "",
        "issue": "Q&A Care Safety Error Type",
        "good": "No",  # The question asks whether a concern exists.
        "scores": {},
    },
    {
        "name": "Standalone",
        "binary": "Q&A Standalone Yes/No",
        "attribute": "",
        "issue": "Q&A Standalone Error Type",
        "good": "Yes",
        "scores": {},
    },
]

SUPABASE_MAP = {
    "qa_uid": "QA ID",
    "dataset": "Dataset",
    "video": "Video Index",
    "approach": "Approach",
    "question_text": "Question",
    "answer_text": "Answer",
    "qna_trustworthiness_attribute": "Q&A Trustworthiness Attribute",
    "qna_trustworthiness_issue": "Q&A Trustworthiness Error Type",
    "qna_trustworthiness_binary": "Q&A Trustworthiness Yes/No",
    "qna_clarity_attribute": "Q&A Clarity Attribute",
    "qna_clarity_issue": "Q&A Clarity Error Type",
    "qna_clarity_binary": "Q&A Clarity Yes/No",
    "qna_usefulness_attribute": "Q&A Usefulness Attribute",
    "qna_usefulness_issue": "Q&A Usefulness Error Type",
    "qna_usefulness_binary": "Q&A Usefulness Yes/No",
    "qna_care_safety_issue": "Q&A Care Safety Error Type",
    "qna_care_safety_binary": "Q&A Care Safety Yes/No",
    "qna_standalone_issue": "Q&A Standalone Error Type",
    "qna_standalone_binary": "Q&A Standalone Yes/No",
    "caregiver_recommendation": "Caregiver Recommendation",
    "annotator": "Annotator",
    "session_id": "Session ID",
}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [normalize_row(row) for row in csv.DictReader(handle)]
    return [row for row in rows if row.get("QA ID")]


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    clean = {str(k).strip(): "" if v is None else str(v).strip() for k, v in row.items()}
    if "QA ID" in clean:
        return clean
    normalized = dict(clean)
    for source, target in SUPABASE_MAP.items():
        if source in clean:
            normalized[target] = clean[source]
    return normalized


def version_of(row: dict[str, str]) -> str:
    approach = row.get("Approach", "")
    if approach in VERSION_BY_APPROACH:
        return VERSION_BY_APPROACH[approach]
    # A deployed v3 human export uses the shorter approach label.
    if approach == "SingleAgent":
        return "v3"
    match = re.search(r"SingleAgent[-_ ]?(v[123])", approach, flags=re.I)
    return match.group(1).lower() if match else approach or "unknown"


def dedupe(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    for row in rows:
        latest[row["QA ID"]] = row
    return list(latest.values())


def pct(numerator: int, denominator: int) -> float:
    return 100.0 * numerator / denominator if denominator else math.nan


def fmt_pct(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.1f}%"


def fmt_number(value: float) -> str:
    return "—" if math.isnan(value) else f"{value:.2f}"


def binary_rate(rows: list[dict[str, str]], metric: dict[str, Any]) -> float:
    values = [row.get(metric["binary"], "") for row in rows]
    values = [value for value in values if value in ("Yes", "No")]
    return pct(sum(value == metric["good"] for value in values), len(values))


def attribute_mean(rows: list[dict[str, str]], metric: dict[str, Any]) -> float:
    values = [metric["scores"].get(row.get(metric["attribute"], "")) for row in rows]
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else math.nan


def recommendation_rate(rows: list[dict[str, str]], accepted: set[str]) -> float:
    values = [row.get("Caregiver Recommendation", "") for row in rows]
    values = [value for value in values if value]
    return pct(sum(value in accepted for value in values), len(values))


def issue_rates(
    rows: list[dict[str, str]], metric: dict[str, Any]
) -> list[tuple[str, int, float]]:
    counts: Counter[str] = Counter()
    for row in rows:
        raw = row.get(metric["issue"], "")
        if not raw or raw == "No issue":
            continue
        for issue in (part.strip() for part in raw.split(";")):
            if issue and issue != "No issue":
                counts[issue] += 1
    return [(issue, count, pct(count, len(rows))) for issue, count in counts.most_common()]


def mode_or_blank(values: list[str]) -> str:
    values = [value for value in values if value]
    if not values:
        return ""
    counts = Counter(values)
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return ""  # Exclude unresolved ties from model-vs-human agreement.
    return top[0][0]


def consensus_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Collapse multiple human rows per QA into per-field majority consensus."""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["QA ID"]].append(row)
    output = []
    categorical = [metric["binary"] for metric in METRICS]
    categorical += [metric["attribute"] for metric in METRICS if metric["attribute"]]
    categorical.append("Caregiver Recommendation")
    for uid, group in grouped.items():
        base = dict(group[-1])
        for column in categorical:
            base[column] = mode_or_blank([row.get(column, "") for row in group])
        output.append(base)
    return output


def cohen_kappa(left: list[str], right: list[str]) -> float:
    if not left or len(left) != len(right):
        return math.nan
    labels = sorted(set(left) | set(right))
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    expected = 0.0
    for label in labels:
        expected += (left.count(label) / len(left)) * (right.count(label) / len(right))
    if expected == 1:
        return 1.0 if observed == 1 else math.nan
    return (observed - expected) / (1 - expected)


def exact_text_key(row: dict[str, str]) -> tuple[str, str, str]:
    """Identify a pair without relying on deployment-specific QA IDs.

    The deployed human v3 package uses ``SingleAgent`` in its IDs while the
    generation corpus uses ``SingleAgent-v3``.  Exact normalized question and
    answer text is therefore a safe fallback, but only within the same prompt
    version.  We intentionally do not position-match different questions.
    """

    normalize = lambda value: " ".join(value.split()).casefold()
    return (
        version_of(row),
        normalize(row.get("Question", "")),
        normalize(row.get("Answer", "")),
    )


def agreement_rows(
    claude: list[dict[str, str]], other: list[dict[str, str]]
) -> list[dict[str, Any]]:
    claude_unique = dedupe(claude)
    other_unique = dedupe(other)
    other_by_id = {row["QA ID"]: row for row in other_unique}
    other_by_text = {
        exact_text_key(row): row
        for row in other_unique
        if row.get("Question") and row.get("Answer")
    }
    shared: list[tuple[dict[str, str], dict[str, str]]] = []
    for claude_row in claude_unique:
        other_row = other_by_id.get(claude_row["QA ID"])
        if other_row is None and claude_row.get("Question") and claude_row.get("Answer"):
            other_row = other_by_text.get(exact_text_key(claude_row))
        if other_row is not None:
            shared.append((claude_row, other_row))
    output = []
    for metric in METRICS:
        left, right = [], []
        for claude_row, other_row in shared:
            a = claude_row.get(metric["binary"], "")
            b = other_row.get(metric["binary"], "")
            if a in ("Yes", "No") and b in ("Yes", "No"):
                left.append(a)
                right.append(b)
        output.append(
            {
                "metric": metric["name"],
                "n": len(left),
                "agreement": pct(sum(a == b for a, b in zip(left, right)), len(left)),
                "kappa": cohen_kappa(left, right),
            }
        )
    return output


def version_groups(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in dedupe(rows):
        groups[version_of(row)].append(row)
    return groups


def ordered_versions(groups: dict[str, Any]) -> list[str]:
    return [version for version in VERSION_ORDER if version in groups] + sorted(
        set(groups) - set(VERSION_ORDER)
    )


def load_metadata(csv_paths: list[Path]) -> list[dict[str, Any]]:
    metadata = []
    for path in csv_paths:
        candidate = path.with_suffix(".meta.json")
        if candidate.exists():
            metadata.append(json.loads(candidate.read_text(encoding="utf-8")))
    return metadata


def summary_sentence(groups: dict[str, list[dict[str, str]]], versions: list[str]) -> str:
    if len(versions) == 1:
        rows = groups[versions[0]]
        standalone = binary_rate(rows, METRICS[-1])
        trust = binary_rate(rows, METRICS[0])
        return (
            f"Claude judged {len(rows)} {versions[0]} pairs: {standalone:.1f}% passed "
            f"Standalone and {trust:.1f}% passed Trustworthiness."
        )
    first, last = versions[0], versions[-1]
    start = binary_rate(groups[first], METRICS[-1])
    end = binary_rate(groups[last], METRICS[-1])
    trust_start = binary_rate(groups[first], METRICS[0])
    trust_end = binary_rate(groups[last], METRICS[0])
    return (
        f"Standalone moved from {start:.1f}% in {first} to {end:.1f}% in {last} "
        f"({end - start:+.1f} points). Trustworthiness moved from "
        f"{trust_start:.1f}% to {trust_end:.1f}% ({trust_end - trust_start:+.1f} points)."
    )


def html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{esc(value)}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<div class='table-wrap'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def build_html(
    claude_rows: list[dict[str, str]],
    title: str,
    baseline: list[dict[str, str]],
    human_rows: list[dict[str, str]],
    metadata: list[dict[str, Any]],
    source_names: list[str],
) -> str:
    groups = version_groups(claude_rows)
    versions = ordered_versions(groups)
    if not versions:
        raise SystemExit("No recognized SingleAgent v1/v2/v3 rows were found")

    metric_table = []
    for metric in METRICS:
        metric_table.append(
            [metric["name"]]
            + [fmt_pct(binary_rate(groups[v], metric)) for v in versions]
            + (
                [
                    f"{binary_rate(groups[versions[-1]], metric) - binary_rate(groups[versions[0]], metric):+.1f}"
                ]
                if len(versions) > 1
                else []
            )
        )
    metric_table.append(
        ["Recommend: strict Yes"]
        + [fmt_pct(recommendation_rate(groups[v], {"Yes"})) for v in versions]
        + (
            [
                f"{recommendation_rate(groups[versions[-1]], {'Yes'}) - recommendation_rate(groups[versions[0]], {'Yes'}):+.1f}"
            ]
            if len(versions) > 1
            else []
        )
    )
    metric_table.append(
        ["Recommend: Yes or minor edits"]
        + [
            fmt_pct(
                recommendation_rate(
                    groups[v], {"Yes", "Yes, but with minor edits (meaning unchanged)"}
                )
            )
            for v in versions
        ]
        + (
            [
                f"{recommendation_rate(groups[versions[-1]], {'Yes', 'Yes, but with minor edits (meaning unchanged)'}) - recommendation_rate(groups[versions[0]], {'Yes', 'Yes, but with minor edits (meaning unchanged)'}):+.1f}"
            ]
            if len(versions) > 1
            else []
        )
    )

    attr_table = []
    for metric in (m for m in METRICS if m["attribute"]):
        attr_table.append(
            [metric["name"]]
            + [fmt_number(attribute_mean(groups[v], metric)) for v in versions]
        )

    issue_sections = []
    for metric in METRICS:
        rows = []
        issues = sorted(
            {
                issue
                for version in versions
                for issue, _, _ in issue_rates(groups[version], metric)
            }
        )
        lookup = {
            version: {issue: rate for issue, _, rate in issue_rates(groups[version], metric)}
            for version in versions
        }
        for issue in issues:
            rows.append([issue] + [fmt_pct(lookup[v].get(issue, 0.0)) for v in versions])
        if not rows:
            rows = [["No failures recorded"] + ["0.0%" for _ in versions]]
        issue_sections.append(
            f"<h3>{esc(metric['name'])} failure labels</h3>"
            + html_table(["Error"] + versions, rows)
        )

    agreement_sections = []
    if baseline:
        rows = [
            [item["metric"], item["n"], fmt_pct(item["agreement"]), fmt_number(item["kappa"])]
            for item in agreement_rows(claude_rows, baseline)
        ]
        agreement_sections.append(
            "<h3>Claude versus DeepSeek</h3>"
            "<p>Agreement is calculated only on identical Q&A pairs, matched by QA ID or exact normalized question-and-answer text, with non-empty binary ratings.</p>"
            + html_table(["Metric", "Shared pairs", "Exact agreement", "Cohen κ"], rows)
        )
    if human_rows:
        consensus = consensus_rows(human_rows)
        rows = [
            [item["metric"], item["n"], fmt_pct(item["agreement"]), fmt_number(item["kappa"])]
            for item in agreement_rows(claude_rows, consensus)
        ]
        agreement_sections.append(
            "<h3>Claude versus human consensus</h3>"
            "<p>Only identical Q&A pairs are compared. When multiple humans rated the same pair, a per-field majority vote is used; tied fields are excluded.</p>"
            + html_table(["Metric", "Shared pairs", "Exact agreement", "Cohen κ"], rows)
        )
    if not agreement_sections:
        agreement_sections.append(
            "<p class='note'>No DeepSeek or human CSV was supplied. Add those files when rebuilding the report to calculate agreement.</p>"
        )

    flagged = [
        row
        for row in claude_rows
        if row.get("Q&A Trustworthiness Yes/No") == "No"
    ]
    flagged_html = []
    for row in flagged[:30]:
        flagged_html.append(
            "<details><summary>"
            f"{esc(row.get('QA ID'))} · {esc(row.get('Q&A Trustworthiness Error Type'))}"
            "</summary>"
            f"<p><b>Question:</b> {esc(row.get('Question'))}</p>"
            f"<p><b>Answer:</b> {esc(row.get('Answer'))}</p>"
            "</details>"
        )
    if not flagged_html:
        flagged_html = ["<p>No Trustworthiness failures were recorded.</p>"]

    pair_count = len(dedupe(claude_rows))
    video_count = len({(r.get("Dataset"), r.get("Video Index")) for r in claude_rows})
    model = next((m.get("model") for m in metadata if m.get("model")), "claude-opus-5")
    cost = sum(float(m.get("estimated_standard_cost_usd", 0) or 0) for m in metadata)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = "".join(
        f"<div class='card'><span>{esc(v)}</span><strong>{len(groups[v])}</strong><small>pairs judged</small></div>"
        for v in versions
    )
    cards += (
        f"<div class='card'><span>Corpus</span><strong>{video_count}</strong><small>source videos</small></div>"
        f"<div class='card'><span>Judge</span><strong class='small'>{esc(model)}</strong><small>high effort</small></div>"
    )
    if metadata:
        cards += f"<div class='card'><span>Measured API cost</span><strong>${cost:.2f}</strong><small>from usage records</small></div>"

    delta_header = [f"{versions[0]}→{versions[-1]} (points)"] if len(versions) > 1 else []
    source_list = " · ".join(esc(name) for name in source_names)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
:root{{--bg:#07111f;--panel:#0d1b2a;--panel2:#13263a;--text:#e8f1f8;--muted:#94a9bc;--line:#29445d;--accent:#55d6be;--accent2:#ffcf70;--bad:#ff7b85}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(145deg,#06101c,#0a1726 45%,#101f2f);color:var(--text);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:48px 22px 90px}} h1{{font-size:clamp(34px,6vw,68px);line-height:1.03;margin:12px 0 18px;letter-spacing:-.04em}} h2{{font-size:27px;margin:56px 0 14px}} h3{{font-size:19px;margin:28px 0 10px;color:var(--accent)}} p{{max-width:850px}} .eyebrow{{text-transform:uppercase;letter-spacing:.16em;color:var(--accent);font-weight:700;font-size:12px}} .lead{{font-size:19px;color:#c4d4e0;max-width:900px}} .note{{padding:14px 16px;border-left:3px solid var(--accent2);background:#122334;color:#d9e4ec}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:30px 0}} .card{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:14px;padding:16px}} .card span,.card small{{display:block;color:var(--muted)}} .card strong{{display:block;font-size:30px;margin:4px 0;color:var(--accent2)}} .card strong.small{{font-size:16px;word-break:break-word}} .table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:12px;background:rgba(7,17,31,.55)}} table{{border-collapse:collapse;width:100%;min-width:600px}} th,td{{padding:11px 13px;border-bottom:1px solid var(--line);text-align:left}} th{{background:#13283d;color:#bcd0de;position:sticky;top:0}} tr:last-child td{{border-bottom:0}} details{{border:1px solid var(--line);background:var(--panel);border-radius:10px;margin:8px 0;padding:10px 13px}} summary{{cursor:pointer;color:var(--accent2);font-weight:650}} code{{color:var(--accent)}} .footer{{margin-top:64px;color:var(--muted);font-size:13px}} a{{color:var(--accent)}}
</style></head><body><main>
<div class="eyebrow">MedicalQA · SingleAgent evaluation</div>
<h1>{esc(title)}</h1>
<p class="lead">{esc(summary_sentence(groups, versions))}</p>
<div class="cards">{cards}</div>

<h2>1. Scope and method</h2>
<p>Each Q&A was scored with the current five-metric human rubric. Trustworthiness, Clarity, Usefulness and Care Safety received the source transcript. Standalone was scored in a separate call that physically did not contain the transcript, then passed forward only for the overall caregiver recommendation.</p>
<p class="note">The prompt revision and approach labels were hidden from the judge. Care Safety is inverted in the table below: its percentage means <b>no safety concern</b>. Standalone and Care Safety are binary-only.</p>

<h2>2. Headline results</h2>
{html_table(["Metric"] + versions + delta_header, metric_table)}

<h2>3. Four-level attribute scores</h2>
<p>Means use 4 for the strongest label and 1 for the weakest. Binary-only metrics are intentionally absent.</p>
{html_table(["Metric"] + versions, attr_table)}

<h2>4. Failure types</h2>
{''.join(issue_sections)}

<h2>5. Agreement checks</h2>
{''.join(agreement_sections)}

<h2>6. Trustworthiness flags requiring manual review</h2>
<p>{len(flagged)} pairs were marked Trustworthiness = No. These are candidates for transcript-based human adjudication; an LLM flag is not proof of hallucination.</p>
{''.join(flagged_html)}

<h2>7. Interpretation limits</h2>
<ul>
<li>The LLM judge is a secondary measurement instrument, not ground truth. Human ratings remain the primary reference.</li>
<li>When multiple prompt versions are shown, their corpora are near-equal but not question-paired. Small percentage-point differences should not be interpreted as causal signal.</li>
<li>Model aliases may change over time. Preserve the raw JSONL, model response metadata, prompt code and run date with the paper artifacts.</li>
<li>Binary rates and error labels should be accompanied by agreement against the shared human subset before publication.</li>
</ul>

<div class="footer">Compiled {esc(generated)} · {pair_count} pair-version records · Source CSVs: {source_list}</div>
</main></body></html>"""


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    safe = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    output = ["| " + " | ".join(map(safe, headers)) + " |"]
    output.append("| " + " | ".join("---" for _ in headers) + " |")
    output.extend("| " + " | ".join(map(safe, row)) + " |" for row in rows)
    return "\n".join(output)


def build_markdown(
    claude_rows: list[dict[str, str]],
    title: str,
    baseline: list[dict[str, str]],
    human_rows: list[dict[str, str]],
) -> str:
    groups = version_groups(claude_rows)
    versions = ordered_versions(groups)
    result_rows = []
    for metric in METRICS:
        result_rows.append(
            [metric["name"]] + [fmt_pct(binary_rate(groups[v], metric)) for v in versions]
        )
    result_rows.append(
        ["Recommend: strict Yes"]
        + [fmt_pct(recommendation_rate(groups[v], {"Yes"})) for v in versions]
    )
    sections = [
        f"# {title}",
        "",
        summary_sentence(groups, versions),
        "",
        "## Headline results",
        "",
        markdown_table(["Metric"] + versions, result_rows),
        "",
        "Standalone was judged without the transcript. Care Safety is reported as the percentage with no concern.",
    ]
    if baseline:
        rows = [
            [item["metric"], item["n"], fmt_pct(item["agreement"]), fmt_number(item["kappa"])]
            for item in agreement_rows(claude_rows, baseline)
        ]
        sections += ["", "## Claude versus DeepSeek", "", markdown_table(
            ["Metric", "Shared pairs", "Exact agreement", "Cohen kappa"], rows
        )]
    if human_rows:
        rows = [
            [item["metric"], item["n"], fmt_pct(item["agreement"]), fmt_number(item["kappa"])]
            for item in agreement_rows(claude_rows, consensus_rows(human_rows))
        ]
        sections += ["", "## Claude versus human consensus", "", markdown_table(
            ["Metric", "Shared pairs", "Exact agreement", "Cohen kappa"], rows
        )]
    sections += [
        "",
        "## Interpretation limits",
        "",
        "- Human ratings remain the primary reference; the LLM judge is secondary.",
        "- Cross-version corpora are near-equal but not question-paired.",
        "- Manually adjudicate Trustworthiness failures before reporting hallucination prevalence.",
        "",
    ]
    return "\n".join(sections)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--claude-csv", nargs="+", type=Path, required=True)
    parser.add_argument("--deepseek-csv", nargs="*", type=Path, default=[])
    parser.add_argument("--human-csv", nargs="*", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True, help="HTML output path")
    parser.add_argument("--title", default="Claude Opus 5 Evaluation")
    args = parser.parse_args()

    claude_rows = [row for path in args.claude_csv for row in read_csv(path)]
    baseline = [row for path in args.deepseek_csv for row in read_csv(path)]
    human_rows = [row for path in args.human_csv for row in read_csv(path)]
    metadata = load_metadata(args.claude_csv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    html_text = build_html(
        claude_rows,
        args.title,
        baseline,
        human_rows,
        metadata,
        [path.name for path in args.claude_csv],
    )
    args.output.write_text(html_text, encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(
        build_markdown(claude_rows, args.title, baseline, human_rows), encoding="utf-8"
    )
    print(f"HTML report: {args.output}")
    print(f"Markdown   : {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
